"""
R6 — Rumor ↔ graph_engine 适配层（R10 增强：Risk-Routing + Hallucination Guard）
=========================================================
接收命题 → 分类 → 风险评估 → 分流到 Fast-Path 或 Full CTAEW
          → 🛡️ Hallucination Guard (claim 级证据对齐)
          → 统一打包返回。

架构：
    query → classify_claim (共用)
          → assess_risk (claim_type + confidence + length)
          → IF LOW  → run_fast_path (单 LLM, 1-2s)
          → IF HIGH → run_rumor (完整 CTAEW, 80s)
          → run_hallucination_guard (claim 拆解 + 证据对齐, ≤ 8s)
          → 统一 judgment packet 适配旧 3 元组接口

旧签名（保持不变）：
    async def run_rumor_ctaew(query, entities, history)
        -> Tuple[str, dict, list]   # (markdown, trace_dict, audit_logs)
"""
import asyncio
import json
import logging
import re
import time
from urllib.parse import urlparse
from typing import Any, Dict, List, Tuple, Optional

from core.blackboard import Blackboard
from core.llm_client import SUMMARY_MODEL, shared_client as llm_client
from core.sse_emitter import set_collector, reset_collector, emit as sse_emit

from agents.hallucination_agent import (
    check_answer as _halluc_check,
    ABSTAIN_TEMPLATE,
    HallucinationReport,
    RiskTier,
)

from .workflow import run_rumor
from .claim_classifier import classify_claim
from .risk_router import assess_risk
from .weight_policy import resolve_composite_weights
from core.evidence import build_chain, dedupe_refs

logger = logging.getLogger("Rumor.Integration")


_QUERY_STOPWORDS = {
    "真的", "是否", "是不是", "可以", "能不能", "有没有", "每天", "一杯",
    "一个", "一种", "这个", "那个", "什么", "怎么", "为什么", "需要",
    "放在", "旁边", "真的能", "吗", "呢", "的", "了",
}
_HEALTH_DOMAIN_TERMS = {
    "病", "症", "痛", "疼", "药", "癌", "瘤", "血", "心", "肝", "肾",
    "肺", "脑", "发烧", "退烧", "感冒", "感染", "治疗", "预防", "血管",
    "血压", "血糖", "过敏", "中毒", "孕妇", "儿童", "老人",
}


def _claim_terms(text: str) -> set:
    cleaned = (text or "").lower()
    for stop in _QUERY_STOPWORDS:
        cleaned = cleaned.replace(stop, " ")
    chunks = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z][a-zA-Z0-9-]{2,}", cleaned)
    terms = set(chunks)
    for chunk in chunks:
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk) and len(chunk) > 4:
            for n in (2, 3, 4):
                terms.update(chunk[i:i + n] for i in range(0, len(chunk) - n + 1))
    return {t for t in terms if len(t.strip()) >= 2}


def _looks_nonmedical_folklore(query: str, classification: Any) -> bool:
    primary = getattr(classification, "primary", None)
    secondary = getattr(classification, "secondary", None)
    if primary != "FOLKLORE" and secondary != "FOLKLORE":
        return False
    return not any(term in query for term in _HEALTH_DOMAIN_TERMS)


def _tool_call_args(bb: Blackboard) -> Dict[int, dict]:
    out: Dict[int, dict] = {}
    for entry in bb.all_by_key("tool_call"):
        value = entry.get("value") or {}
        out[int(entry.get("v") or 0)] = value.get("args") or {}
    return out


def _first_url(hit: dict) -> str:
    for raw in (hit.get("open_url"), hit.get("url"), hit.get("link"), hit.get("source"), hit.get("ref")):
        if not raw:
            continue
        text = str(raw)
        if text.startswith("web:http"):
            text = text[4:]
        if text.startswith(("http://", "https://")) and not _is_probable_image_url(text):
            return text
    return ""


def _is_probable_image_url(url: str) -> bool:
    lowered = (url or "").strip().lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    image_markers = ("xhscdn.com", "sns-webpic", "sns-img", "sns-avatar", "imageview2", "format/webp")
    if any(marker in lowered for marker in image_markers):
        return True
    return bool(re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|#|$)", lowered))


def _source_label_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
        return host.replace("www.", "") or url[:40]
    except Exception:
        return url[:40]


def _hit_text(hit: dict) -> str:
    return str(
        hit.get("post_body")
        or hit.get("content")
        or hit.get("text")
        or " ".join(str(hit.get(k, "")) for k in ("subject", "predicate", "object"))
    ).strip()


def _hit_title(hit: dict, tool: str) -> str:
    title = str(
        hit.get("title")
        or hit.get("subject")
        or tool
    ).strip()
    if title.startswith(("http://", "https://", "web:http")):
        return tool
    return title


def _clean_card_text(text: str) -> str:
    text = str(text or "")
    text = text.replace("\\n", " ").replace("\\t", " ").replace("\\r", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^小红书搜索结果标题：", "", text).strip()
    return text


def _clean_summary_input(text: str, limit: int = 700) -> str:
    text = str(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\\n", " ").replace("\\t", " ").replace("\\r", " ")
    text = text.replace("\n", " ").replace("\t", " ").replace("\r", " ")
    text = re.sub(r"([，。；、,.!?！？])\1+", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"([A-Za-z])\s+([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", text)
    text = re.sub(r"^小红书搜索结果标题：", "", text).strip()
    if len(text) <= limit:
        return text
    return _sentence_clip(text, limit)


def _sentence_clip(text: str, limit: int = 240) -> str:
    text = _clean_card_text(text)
    if not text:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_punc = max(cut.rfind("。"), cut.rfind("；"), cut.rfind("，"), cut.rfind("."), cut.rfind(";"), cut.rfind(","))
    if last_punc >= 60:
        cut = cut[:last_punc + 1]
    return cut.rstrip() + "..."


def _is_url_like_title(title: str) -> bool:
    title = (title or "").strip().lower()
    return title.startswith(("http://", "https://", "web:http")) or title in {"web_search", "social_search"}


def _build_display_title(title: str, content: str, url: str, platform: str) -> str:
    title = _clean_card_text(title)
    content = _clean_card_text(content)
    if title and not _is_url_like_title(title):
        return title[:80]
    if content:
        return _sentence_clip(content, 48).strip("。.,，;；")[:80]
    host = _source_label_from_url(url)
    return f"{platform or host or '网络来源'}线索"


def _source_kind_label(card: Dict[str, Any]) -> str:
    platform = str(card.get("platform") or card.get("source") or "")
    source_tool = str(card.get("source_tool") or "")
    url = str(card.get("open_url") or card.get("url") or "").lower()
    fetch_method = str(card.get("fetch_method") or "")
    if "小红书" in platform or "xiaohongshu" in url or "xhs" in fetch_method:
        return "小红书内容"
    if fetch_method == "tavily_ugc" or "ugc" in platform.lower():
        return "UGC舆情内容"
    if "pmc.ncbi.nlm.nih.gov" in url or "pubmed" in url:
        return "论文页面"
    if source_tool == "web_search":
        return "网页核查来源"
    return "网络来源"


def _clean_overview_body(text: str, limit: int = 180) -> str:
    text = _clean_summary_input(text, 500)
    text = re.sub(r"https?://\S+", "", text).strip()
    text = re.sub(r"#\S+", "", text).strip()
    text = re.sub(r"该(?:小红书内容|UGC舆情内容|网页核查来源|网络来源)来源围绕[^，。；]*[，。；]?", "", text)
    return _sentence_clip(text, limit)


def _should_skip_llm_summary(card: Dict[str, Any]) -> bool:
    status = str(card.get("content_status") or "")
    body = _clean_summary_input(card.get("raw_excerpt") or card.get("content") or "", 120)
    return status in {"title_only", "search_only", "read_failed"} or len(body) < 24


def _build_scout_summary_fields(
    query: str,
    title: str,
    content: str,
    url: str,
    platform: str,
    evidence_type: str,
    summary_note: str,
) -> Dict[str, str]:
    display_title = _build_display_title(title, content, url, platform)
    clean_content = _clean_card_text(content)
    is_social = evidence_type == "social_opinion"
    title_only = summary_note in ("title_only", "search_only") or not clean_content

    if title_only and is_social:
        overview = f"目前只获得到这条小红书搜索标题：「{display_title}」。系统没有抓取到帖子正文、评论区或互动数据。"
        conclusion = "这条结果只能说明相关说法在小红书搜索结果中出现，不能证明该说法正确，也不能替代医学或科学证据。"
        limit = "仅标题可用，正文不可核验；不能据此判断帖子作者的完整观点。"
    elif is_social:
        overview = _sentence_clip(clean_content, 260)
        conclusion = "这类内容可以反映大众讨论中的常见说法和传播角度，但经验帖不能单独证明医学结论。"
        limit = "社交平台内容存在个体经验偏差，需要和权威医学或科学来源交叉验证。"
    else:
        overview = _sentence_clip(clean_content, 300) or f"该网页与「{query[:40]}」相关，但返回内容较短。"
        conclusion = "这条网页可作为网络核查线索；是否能支持最终结论，仍要看其来源质量和与问题的直接相关性。"
        limit = "网页内容可能是科普或转载材料，不能自动等同于临床指南。"

    return {
        "display_title": display_title,
        "content_overview": overview,
        "post_conclusion": conclusion,
        "claim_relation": "用于观察该说法在网络中的传播、常见论据和可核验线索。",
        "evidence_limit": limit,
    }


def _medical_hit_relevant(hit: dict, tool: str, query_terms: set) -> bool:
    text = f"{_hit_title(hit, tool)} {_hit_text(hit)} {hit.get('source', '')}".lower()
    if not text.strip():
        return False
    if "no_results" in str(hit.get("ref", "")) or "未找到" in text or "error" == str(hit.get("ref", "")):
        return False
    if tool == "kg_query":
        return bool(query_terms and any(t.lower() in text for t in query_terms))
    if tool in ("rag_search", "pubmed_search"):
        return bool(query_terms and any(t.lower() in text for t in query_terms))
    return False


def _dedupe_cards(cards: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for card in cards:
        url = (card.get("open_url") or card.get("url") or "").strip().lower()
        title = re.sub(r"\s+", "", str(card.get("display_title") or card.get("title") or "").lower())
        content = re.sub(r"\s+", "", str(card.get("content") or "").lower())[:80]
        key = url or f"{title}|{content}"
        if not key.strip("|"):
            continue
        if key not in merged:
            merged[key] = dict(card)
            merged[key]["source_tools"] = [card.get("source_tool")] if card.get("source_tool") else []
            continue
        prev = merged[key]
        if float(card.get("score") or 0) > float(prev.get("score") or 0):
            prev["score"] = card.get("score")
        if len(str(card.get("content") or "")) > len(str(prev.get("content") or "")):
            prev["content"] = card.get("content")
            prev["summary"] = card.get("summary")
        if card.get("source_tool") and card.get("source_tool") not in prev["source_tools"]:
            prev["source_tools"].append(card.get("source_tool"))
    return sorted(merged.values(), key=lambda x: float(x.get("score") or 0), reverse=True)[:limit]


def _status_limit_text(status: str, platform: str = "") -> str:
    status = str(status or "")
    if status == "full_text":
        return f"{platform or '社交平台'}正文属于用户经验帖或平台讨论，能反映传播语境，不能单独证明医学或科学结论。"
    if status == "title_only":
        return "仅获得标题，未抓取正文、评论和互动数据，不能判断作者完整观点。"
    if status == "search_only":
        return "仅获得搜索入口，未抓取具体帖子正文。"
    if status == "read_failed":
        return "尝试读取正文失败，该来源只能作为舆情线索。"
    return "网络内容可能存在个体经验偏差，需要和权威医学或科学来源交叉验证。"


def _coerce_relevance(value: Any, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def _first_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ValueError("no json object")
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError("unterminated json object")


def _parse_llm_json_object(raw: str) -> Dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S | re.I)
    if fenced:
        try:
            data = json.loads(fenced.group(1))
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    data = json.loads(_first_json_object(raw))
    return data if isinstance(data, dict) else {}


def _chunk_cards(cards: List[Dict[str, Any]], size: int = 4) -> List[List[Dict[str, Any]]]:
    return [cards[i:i + size] for i in range(0, len(cards), size)]


def _core_sentence(text: str, query: str = "", limit: int = 180) -> str:
    clean = _clean_summary_input(text, 900)
    if not clean:
        return ""
    terms = _claim_terms(query)
    pieces = [p.strip() for p in re.split(r"(?<=[。！？；;.!?])", clean) if p.strip()]
    if not pieces:
        return _sentence_clip(clean, limit)
    for piece in pieces:
        lowered = piece.lower()
        if any(t.lower() in lowered for t in terms):
            return _sentence_clip(piece, limit)
    return _sentence_clip(pieces[0], limit)


def _rule_fallback_scout(card: Dict[str, Any], query: str) -> Dict[str, Any]:
    status = str(card.get("content_status") or "")
    platform = str(card.get("platform") or "网络平台")
    source_label = _source_kind_label(card)
    title = _build_display_title(
        str(card.get("display_title") or card.get("title") or ""),
        str(card.get("raw_excerpt") or card.get("content") or ""),
        str(card.get("open_url") or card.get("url") or ""),
        platform,
    )
    body = _clean_overview_body(card.get("raw_excerpt") or card.get("content") or "", 170)
    if status in {"title_only", "search_only", "read_failed"} or not body:
        overview = f"可见信息只有标题或搜索入口：「{title}」。当前没有可核验的完整正文。"
    else:
        overview = f"{source_label}可见内容显示：{body}"
    return {
        "display_title": title,
        "content_overview": overview,
        "post_conclusion": f"这条{source_label}最多说明相关说法的传播表达或讨论角度，不能单独证明医学或科学结论。",
        "claim_relation": "用于观察该说法的网络传播、常见论据和可核验线索。",
        "evidence_limit": card.get("evidence_limit") or _status_limit_text(status, platform),
        "relevance_score": float(card.get("score") or 0.35),
    }


def _rule_fallback_medical(card: Dict[str, Any], query: str) -> Dict[str, Any]:
    title = _clean_card_text(card.get("title") or card.get("source_tool") or "医学证据")
    raw = re.sub(r"https?://\S+", "", str(card.get("raw_excerpt") or card.get("content") or ""))
    body = _core_sentence(raw, query=query, limit=190)
    if body:
        summary = f"该证据片段提到：{body}"
    else:
        summary = "该证据为检索命中的医学片段，但可读正文不足，需结合其他证据判断。"
    return {
        "medical_summary": summary,
        "key_takeaway": "这条证据只能支持与片段直接相关的有限结论，不能单独推出完整医学判断。",
        "why_relevant": card.get("why_relevant") or "与用户命题中的关键词或检索问题直接匹配。",
        "evidence_limit": card.get("evidence_limit") or "该证据为检索命中的局部片段，最终结论需结合其他证据交叉判断。",
        "relevance_score": float(card.get("score") or 0.45),
        "display_title": title,
    }


async def _summarize_batch_with_llm(
    *,
    query: str,
    cards: List[Dict[str, Any]],
    kind: str,
    retry: bool = True,
) -> Dict[str, Dict[str, Any]]:
    if not cards:
        return {}
    started = time.perf_counter()
    if kind == "scout":
        payload = [{
            "id": card["_summary_id"],
            "title": card.get("display_title") or card.get("title") or "",
            "platform": card.get("platform") or "",
            "content_status": card.get("content_status") or "",
            "url": card.get("open_url") or card.get("url") or "",
            "open_url_type": card.get("open_url_type") or "",
            "source_tool": card.get("source_tool") or "",
            "evidence_type": card.get("evidence_type") or "",
            "fetch_method": card.get("fetch_method") or "",
            "source_kind": _source_kind_label(card),
            "text": _clean_summary_input(card.get("raw_excerpt") or card.get("content") or "", 560),
        } for card in cards]
        schema_hint = (
            "输出 {\"items\":[{\"id\":\"...\",\"display_title\":\"...\","
            "\"content_overview\":\"...\",\"post_conclusion\":\"...\","
            "\"claim_relation\":\"...\",\"evidence_limit\":\"...\",\"relevance_score\":0.0}]}。"
            "这些来源是小红书/UGC/网页舆情，不能单独证明医学结论。"
            "content_overview 要用 1-2 句概括可见内容，不要复述 URL、话题标签或无关营销语。"
            "不要使用“该来源围绕”句式。普通网页、PMC、MedSci 不要称为帖子。"
        )
    else:
        payload = [{
            "id": card["_summary_id"],
            "title": card.get("title") or "",
            "source_tool": card.get("source_tool") or "",
            "evidence_type": card.get("evidence_type") or "",
            "query_used": card.get("query_used") or "",
            "text": _clean_summary_input(card.get("raw_excerpt") or card.get("content") or "", 680),
        } for card in cards]
        schema_hint = (
            "输出 {\"items\":[{\"id\":\"...\",\"medical_summary\":\"...\","
            "\"key_takeaway\":\"...\",\"why_relevant\":\"...\","
            "\"evidence_limit\":\"...\",\"relevance_score\":0.0}]}。"
            "只总结证据与用户问题的关系和最多能支持什么，不要照搬原文。"
        )

    system_prompt = (
        "你是医学辟谣系统的证据摘要员。只输出 JSON object，不要 Markdown，不要解释。"
        "必须基于输入提炼总结，清理 OCR/乱码/换行噪声，不编造未提供的信息。"
        + schema_hint
    )
    user_prompt = json.dumps({"claim": query, "cards": payload}, ensure_ascii=False)
    try:
        resp = await llm_client.chat.completions.create(
            model=SUMMARY_MODEL,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = resp.choices[0].message.content or "{}"
        data = _parse_llm_json_object(raw)
        items = data.get("items") if isinstance(data, dict) else []
        out: Dict[str, Dict[str, Any]] = {}
        for item in items or []:
            if isinstance(item, dict) and item.get("id"):
                out[str(item["id"])] = item
        missing_ids = [card["_summary_id"] for card in cards if card["_summary_id"] not in out]
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "[Rumor/Panels] summary_batch kind=%s status=%s model=%s batch_size=%s elapsed_ms=%s items=%s missing_ids=%s",
            kind, "ok" if out else "parse_empty", SUMMARY_MODEL, len(cards), elapsed_ms, len(out), missing_ids,
        )
        if out or not retry:
            return out
        retry_prompt = (
            "上一次输出无法解析或 items 为空。请只输出 JSON object，"
            "不要解释，不要 Markdown。输入如下：\n" + user_prompt
        )
        resp = await llm_client.chat.completions.create(
            model=SUMMARY_MODEL,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": retry_prompt},
            ],
        )
        data = _parse_llm_json_object(resp.choices[0].message.content or "{}")
        items = data.get("items") if isinstance(data, dict) else []
        out = {
            str(item["id"]): item
            for item in items or []
            if isinstance(item, dict) and item.get("id")
        }
        missing_ids = [card["_summary_id"] for card in cards if card["_summary_id"] not in out]
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "[Rumor/Panels] summary_batch kind=%s status=%s model=%s batch_size=%s elapsed_ms=%s items=%s missing_ids=%s",
            kind, "retry_ok" if out else "retry_empty", SUMMARY_MODEL, len(cards), elapsed_ms, len(out), missing_ids,
        )
        return out
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "[Rumor/Panels] summary_batch kind=%s status=api_error error=%s model=%s batch_size=%s elapsed_ms=%s ids=%s",
            kind,
            type(e).__name__,
            SUMMARY_MODEL,
            len(cards),
            elapsed_ms,
            [c.get("_summary_id") for c in cards],
        )
        return {}


async def summarize_scout_cards(query: str, cards: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    llm_cards = [card for card in cards[:8] if not _should_skip_llm_summary(card)]
    skipped_ids = [card.get("_summary_id") for card in cards[:8] if _should_skip_llm_summary(card)]
    if skipped_ids:
        logger.info("[Rumor/Panels] scout 跳过 LLM 摘要 ids=%s reason=title_only_or_empty", skipped_ids)
    for batch in _chunk_cards(llm_cards, 3):
        try:
            out.update(await asyncio.wait_for(
                _summarize_batch_with_llm(query=query, cards=batch, kind="scout"),
                timeout=18.0,
            ))
        except Exception as e:
            logger.warning(
                "[Rumor/Panels] summary_batch kind=scout status=batch_timeout error=%s size=%s ids=%s",
                type(e).__name__, len(batch), [c.get("_summary_id") for c in batch],
            )
    return out


async def summarize_medical_cards(query: str, cards: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for batch in _chunk_cards(cards[:10], 3):
        try:
            out.update(await asyncio.wait_for(
                _summarize_batch_with_llm(query=query, cards=batch, kind="medical"),
                timeout=18.0,
            ))
        except Exception as e:
            logger.warning(
                "[Rumor/Panels] summary_batch kind=medical status=batch_timeout error=%s size=%s ids=%s",
                type(e).__name__, len(batch), [c.get("_summary_id") for c in batch],
            )
    return out


async def _summarize_rumor_evidence_panels(
    query: str,
    scout_cards: List[Dict[str, Any]],
    medical_cards: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    for card in scout_cards:
        raw_excerpt = _clean_summary_input(card.get("raw_excerpt") or card.get("content") or "", 1600)
        card["raw_excerpt"] = raw_excerpt[:1600]
        card.setdefault("llm_summary_status", "pending")
    for card in medical_cards:
        raw_excerpt = _clean_summary_input(card.get("raw_excerpt") or card.get("content") or "", 1800)
        card["raw_excerpt"] = raw_excerpt[:1800]
        card.setdefault("llm_summary_status", "pending")

    for idx, card in enumerate(scout_cards):
        card["_summary_id"] = f"scout-{idx}"
    for idx, card in enumerate(medical_cards):
        card["_summary_id"] = f"medical-{idx}"

    scout_summaries: Dict[str, Dict[str, Any]] = {}
    medical_summaries: Dict[str, Dict[str, Any]] = {}
    try:
        scout_summaries = await summarize_scout_cards(query, scout_cards)
    except Exception as e:
        logger.warning("[Rumor/Panels] scout 摘要阶段异常，保留已完成结果并对剩余卡片降级: %s", type(e).__name__)
    try:
        medical_summaries = await summarize_medical_cards(query, medical_cards)
    except Exception as e:
        logger.warning("[Rumor/Panels] medical 摘要阶段异常，保留已完成结果并对剩余卡片降级: %s", type(e).__name__)

    for idx, card in enumerate(scout_cards):
        item = scout_summaries.get(card.get("_summary_id", f"scout-{idx}"))
        status = str(card.get("content_status") or "")
        if item:
            fallback = _rule_fallback_scout(card, query)
            card["display_title"] = _clean_card_text(item.get("display_title") or fallback["display_title"])[:100]
            card["content_overview"] = _clean_summary_input(item.get("content_overview") or fallback["content_overview"], 360)
            card["post_conclusion"] = _clean_card_text(item.get("post_conclusion")) or "该来源只能作为网络线索，不能单独决定最终结论。"
            card["claim_relation"] = _clean_card_text(item.get("claim_relation")) or fallback["claim_relation"]
            card["evidence_limit"] = _clean_card_text(item.get("evidence_limit")) or fallback["evidence_limit"]
            card["relevance_score"] = _coerce_relevance(item.get("relevance_score"), float(card.get("score") or 0.5))
            card["llm_summary_status"] = "summarized"
        else:
            fallback = _rule_fallback_scout(card, query)
            card.update(fallback)
            logger.info(
                "[Rumor/Panels] card_fallback kind=scout id=%s reason=%s",
                card.get("_summary_id", f"scout-{idx}"),
                "title_only" if _should_skip_llm_summary(card) else "missing_llm_item",
            )
            card["llm_summary_status"] = "rule_fallback"
        card["summary"] = card["content_overview"]
        card["content"] = card["content_overview"]
        card.pop("_summary_id", None)

    for idx, card in enumerate(medical_cards):
        item = medical_summaries.get(card.get("_summary_id", f"medical-{idx}"))
        if item:
            fallback = _rule_fallback_medical(card, query)
            card["medical_summary"] = _clean_summary_input(item.get("medical_summary") or fallback["medical_summary"], 360)
            card["key_takeaway"] = _clean_card_text(item.get("key_takeaway")) or fallback["key_takeaway"]
            card["why_relevant"] = _clean_card_text(item.get("why_relevant")) or fallback["why_relevant"]
            card["evidence_limit"] = _clean_card_text(item.get("evidence_limit")) or fallback["evidence_limit"]
            card["relevance_score"] = _coerce_relevance(item.get("relevance_score"), float(card.get("score") or 0.5))
            card["llm_summary_status"] = "summarized"
        else:
            fallback = _rule_fallback_medical(card, query)
            card.update(fallback)
            logger.info(
                "[Rumor/Panels] card_fallback kind=medical id=%s reason=missing_llm_item",
                card.get("_summary_id", f"medical-{idx}"),
            )
            card["llm_summary_status"] = "rule_fallback"
        card["summary"] = card["medical_summary"]
        card["content"] = card["medical_summary"]
        card.pop("_summary_id", None)

    scout_sorted = sorted(
        scout_cards,
        key=lambda c: (float(c.get("relevance_score") or 0.0), float(c.get("score") or 0.0)),
        reverse=True,
    )[:8]
    medical_sorted = sorted(
        medical_cards,
        key=lambda c: (float(c.get("relevance_score") or 0.0), float(c.get("score") or 0.0)),
        reverse=True,
    )[:10]
    return scout_sorted, medical_sorted


def _build_rumor_evidence_panels(
    bb: Blackboard,
    query: str,
    classification: Any,
    judgment: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    call_args = _tool_call_args(bb)
    hide_medical = _looks_nonmedical_folklore(query, classification)
    scout_sources: List[Dict[str, Any]] = []
    medical_sources: List[Dict[str, Any]] = []

    for entry in bb.filter(key="tool_result"):
        value = entry.get("value") or {}
        hits = value.get("hits") or []
        tool = value.get("tool", "")
        args = call_args.get(int(value.get("call_ref") or 0), {})
        terms = _claim_terms(query) | _claim_terms(str(args.get("query") or args.get("name") or ""))

        for hit in hits:
            if not isinstance(hit, dict):
                continue
            title = _hit_title(hit, tool)[:120]
            raw_content = _hit_text(hit)
            content = raw_content[:360]
            url = _first_url(hit)
            open_url = hit.get("open_url") or url
            if _is_probable_image_url(str(open_url)):
                open_url = ""
            open_url_type = hit.get("open_url_type") or ("post" if tool == "social_search" and open_url else "web")
            if open_url_type == "post" and "search_result" in str(open_url):
                open_url_type = "search"
            score = float(hit.get("score", 0.5) or 0.0)

            if tool in ("web_search", "social_search"):
                platform = str(hit.get("platform") or hit.get("source") or _source_label_from_url(url) or "网络来源")
                evidence_type = "social_opinion" if tool == "social_search" else "scientific_web"
                summary_fields = _build_scout_summary_fields(
                    query=query,
                    title=title,
                    content=content,
                    url=url,
                    platform=platform,
                    evidence_type=evidence_type,
                    summary_note=str(hit.get("summary_note") or ""),
                )
                if hit.get("evidence_limit"):
                    summary_fields["evidence_limit"] = str(hit.get("evidence_limit"))
                scout_sources.append({
                    "title": title,
                    "content": content,
                    "summary": summary_fields["content_overview"],
                    "raw_excerpt": raw_content,
                    "url": url,
                    "open_url": open_url,
                    "open_url_type": open_url_type if open_url else "none",
                    "score": score,
                    "platform": platform,
                    "stance": "舆情线索" if tool == "social_search" else "网络核查",
                    "evidence_type": evidence_type,
                    "source_tool": tool,
                    "content_status": hit.get("content_status"),
                    "fetch_method": hit.get("fetch_method"),
                    "post_body": hit.get("post_body"),
                    "why_relevant": summary_fields["claim_relation"],
                    "llm_summary_status": "pending",
                    "relevance_score": score,
                    **summary_fields,
                })
                continue

            if hide_medical or not _medical_hit_relevant(hit, tool, terms):
                continue
            evidence_type = {
                "kg_query": "kg_structured",
                "rag_search": "rag_local",
                "pubmed_search": "medical_authority",
            }.get(tool, "medical_authority")
            medical_sources.append({
                "title": title,
                "content": content,
                "summary": content[:220],
                "raw_excerpt": raw_content,
                "medical_summary": "",
                "key_takeaway": "",
                "url": url,
                "score": score,
                "is_internal": tool in ("kg_query", "rag_search"),
                "department": "核实证据",
                "evidence_type": evidence_type,
                "source_tool": tool,
                "query_used": args.get("query") or args.get("name") or "",
                "why_relevant": "与用户命题中的关键词或检索问题直接匹配。",
                "evidence_limit": "该证据为检索命中的局部片段，最终结论需结合其他证据交叉判断。",
                "llm_summary_status": "pending",
                "relevance_score": score,
            })

    scout_all = _dedupe_cards(scout_sources, 50)
    xhs_cards = [
        c for c in scout_all
        if "小红书" in str(c.get("platform", "")) or "xiaohongshu" in str(c.get("open_url") or c.get("url", "")).lower()
    ]
    other_scout_cards = [c for c in scout_all if c not in xhs_cards]
    scout = (xhs_cards[:3] + other_scout_cards)[:8]
    medical = _dedupe_cards(medical_sources, 10)
    medical_truth_text = ""
    if medical:
        medical_truth_text = (
            f"本次核查共调用 {judgment.get('total_tool_calls', 0)} 次检索，"
            f"命中 {judgment.get('total_evidence_hits', 0)} 条证据。"
            f"下列医学证据为与当前问题直接相关的片段。"
        )
    return scout, medical, medical_truth_text


# ---------------------------------------------------------------------
# 🛡️ Hallucination Guard：claim 级幻觉检测员
# ---------------------------------------------------------------------

def _gather_evidence_from_bb(bb: Blackboard) -> List[Dict[str, str]]:
    """
    把黑板上 advocate/skeptic 收集到的支持/反驳证据展开为 hallucination_agent
    可对齐的【证据卡片列表】。每张卡片含 title + content。
    """
    cards: List[Dict[str, str]] = []
    for key, label in (("rumor_support", "支持证据"), ("rumor_refute", "反驳证据")):
        for e in bb.all_by_key(key):
            val = e.get("value") or {}
            ev_key = "supporting_evidence" if key == "rumor_support" else "refuting_evidence"
            for item in (val.get(ev_key) or []):
                if not isinstance(item, dict):
                    continue
                title = (
                    item.get("title")
                    or item.get("source")
                    or f"{label}（{(item.get('source_type') or '').lower()}）"
                )
                body = (
                    item.get("summary")
                    or item.get("content")
                    or item.get("snippet")
                    or ""
                )
                if not body:
                    continue
                cards.append({"title": str(title)[:120], "content": str(body)[:600]})
    for e in bb.all_by_key("rumor_social_evidence"):
        val = e.get("value") or {}
        for item in (val.get("evidence") or []):
            if not isinstance(item, dict):
                continue
            body = item.get("summary") or ""
            if body:
                cards.append({"title": "网络舆情证据（social）", "content": str(body)[:600]})
    return cards


# ---------------------------------------------------------------------
# 🔗 D6/D7：证据链构造器（统一契约见 core/evidence.py）
# ---------------------------------------------------------------------

def _hash_str(s: str) -> str:
    return hex(abs(hash(s)) & 0xFFFFFFFF)[2:]


def _collect_polar_evidence(bb: Blackboard, polarity: str) -> List[Dict[str, Any]]:
    """
    从黑板拉取一极（support / refute）的全部证据条目，按统一形态返回：
      [{aspect, source_type, strength, summary, ref_index}]
    """
    bb_key = "rumor_support" if polarity == "support" else "rumor_refute"
    field = "supporting_evidence" if polarity == "support" else "refuting_evidence"
    out: List[Dict[str, Any]] = []
    for entry in bb.all_by_key(bb_key):
        val = entry.get("value") or {}
        for i, ev in enumerate(val.get(field) or []):
            if not isinstance(ev, dict):
                continue
            out.append({
                "aspect": (ev.get("claim_aspect") or "").strip(),
                "source_type": (ev.get("source_type") or "web").lower(),
                "strength": (ev.get("strength") or "moderate").lower(),
                "summary": (ev.get("summary") or "").strip(),
                "polarity": polarity,
                "idx": i,
            })
    for entry in bb.all_by_key("rumor_social_evidence"):
        val = entry.get("value") or {}
        social_polarity = (val.get("polarity") or val.get("stance") or "neutral").strip().lower()
        if social_polarity != polarity:
            continue
        for i, ev in enumerate(val.get("evidence") or []):
            if not isinstance(ev, dict):
                continue
            out.append({
                "aspect": (ev.get("claim_aspect") or "").strip(),
                "source_type": "social",
                "strength": (ev.get("strength") or "weak").lower(),
                "summary": (ev.get("summary") or "").strip(),
                "polarity": polarity,
                "idx": i,
            })
    return out


def _build_rumor_evidence_chain(
    bb: Blackboard,
    query: str,
    classification: Any,
    risk: Any,
    judgment: Dict[str, Any],
    halluc_report: Optional[Dict[str, Any]],
    route: str,
) -> dict:
    """
    把 CTAEW 全流程黑板事件 + judgment + halluc_report 组装成统一证据链。

    triples 设计：
      - (claim, 命题分类, primary_type)        confidence = classify primary_confidence
      - (claim, 风险等级, final_risk)          confidence = 1.0
      - (claim, 支持依据, top_support_summary) confidence = strength→数值
      - (claim, 反驳依据, top_refute_summary)
      - (claim, 实际事实, final_verdict)       confidence = belief 归一化
      - (claim, 幻觉裁定, halluc.action)       仅在 action != PASS 时
    """
    # ----- refs：claim 自身 + 双极证据卡片 + 幻觉检测 -----
    chain_refs: List[dict] = []

    # 1) claim 自身（用户原话）
    claim_ref_id = "claim:rumor_query"
    chain_refs.append({
        "ref_id": claim_ref_id,
        "type": "profile",
        "label": "用户提交的待核查命题",
        "locator": {"raw_query": query[:120]},
        "snippet": (query or "")[:280],
    })

    # 2) advocate / skeptic 证据
    support_evs = _collect_polar_evidence(bb, "support")
    refute_evs = _collect_polar_evidence(bb, "refute")

    def _evidence_to_ref(ev: Dict[str, Any]) -> Dict[str, Any]:
        st = ev["source_type"]      # kg / rag / web
        # 把内部 source_type 映射到统一 ref type
        ref_type = {"kg": "kg", "rag": "pdf", "web": "web"}.get(st, "web")
        polarity = ev["polarity"]
        polarity_emoji = "🟢" if polarity == "support" else "🔴"
        polarity_cn = "支持" if polarity == "support" else "反驳"
        sig = _hash_str(f"{polarity}|{st}|{ev['idx']}|{ev['summary']}")
        return {
            "ref_id": f"rumor:{polarity}:{st}:{sig}",
            "type": ref_type,
            "label": f"{polarity_emoji} {polarity_cn}·{st.upper()}·{ev['strength']}",
            "locator": {
                "polarity": polarity,
                "source_type": st,
                "strength": ev["strength"],
                "claim_aspect": ev["aspect"],
            },
            "snippet": ev["summary"][:280] if ev["summary"] else ev["aspect"],
        }

    for ev in support_evs:
        chain_refs.append(_evidence_to_ref(ev))
    for ev in refute_evs:
        chain_refs.append(_evidence_to_ref(ev))

    # 3) 幻觉裁定（仅在 action ≠ PASS 时进 refs）
    halluc_action = (halluc_report or {}).get("action") if halluc_report else None
    if halluc_action and halluc_action != "PASS":
        chain_refs.append({
            "ref_id": "rumor:halluc_guard",
            "type": "profile",
            "label": f"🛡️ 幻觉检测员·{halluc_action}",
            "locator": {
                "action": halluc_action,
                "score": (halluc_report or {}).get("hallucination_score"),
                "stats": (halluc_report or {}).get("stats", {}),
            },
            "snippet": (halluc_report or {}).get("summary", "")[:280],
        })

    chain_refs = dedupe_refs(chain_refs)

    # ----- triples -----
    chain_triples: List[dict] = []

    # (claim, 命题分类, primary)
    primary = getattr(classification, "primary", None) or judgment.get("claim_type")
    primary_conf = float(getattr(classification, "primary_confidence", 0.0) or 0.0)
    if primary:
        chain_triples.append({
            "head": query[:40] + ("…" if len(query) > 40 else ""),
            "relation": "命题分类",
            "tail": str(primary),
            "tail_type": "ClaimType",
            "source_id": claim_ref_id,
            "confidence": round(primary_conf, 3) or 0.8,
        })

    # (claim, 风险等级, final_risk)
    final_risk = getattr(risk, "final_risk", None) or \
                 (judgment.get("risk_assessment") or {}).get("final_risk")
    if final_risk:
        chain_triples.append({
            "head": query[:40] + ("…" if len(query) > 40 else ""),
            "relation": "风险等级",
            "tail": str(final_risk),
            "tail_type": "RiskTier",
            "source_id": claim_ref_id,
            "confidence": 1.0,
        })

    # 把 strength 映射为数值置信度
    _strength_to_conf = {"strong": 0.9, "moderate": 0.7, "weak": 0.5}

    # (claim, 支持依据, summary)：取最多 3 条强度最高的
    for ev in sorted(support_evs, key=lambda e: -_strength_to_conf.get(e["strength"], 0.5))[:3]:
        ref = _evidence_to_ref(ev)
        if not ev["summary"]:
            continue
        chain_triples.append({
            "head": query[:40] + ("…" if len(query) > 40 else ""),
            "relation": "支持依据",
            "tail": ev["summary"][:60],
            "tail_type": ev["source_type"].upper(),
            "source_id": ref["ref_id"],
            "confidence": _strength_to_conf.get(ev["strength"], 0.6),
        })

    # (claim, 反驳依据, summary)：取最多 3 条
    for ev in sorted(refute_evs, key=lambda e: -_strength_to_conf.get(e["strength"], 0.5))[:3]:
        ref = _evidence_to_ref(ev)
        if not ev["summary"]:
            continue
        chain_triples.append({
            "head": query[:40] + ("…" if len(query) > 40 else ""),
            "relation": "反驳依据",
            "tail": ev["summary"][:60],
            "tail_type": ev["source_type"].upper(),
            "source_id": ref["ref_id"],
            "confidence": _strength_to_conf.get(ev["strength"], 0.6),
        })

    # (claim, 实际事实, final_verdict)
    verdict = judgment.get("final_verdict") or "尚无定论"
    belief = float(judgment.get("belief_score") or 0.0)
    confidence = float(judgment.get("confidence") or 0.0)
    chain_triples.append({
        "head": query[:40] + ("…" if len(query) > 40 else ""),
        "relation": "实际事实",
        "tail": str(verdict),
        "tail_type": "Verdict",
        "source_id": claim_ref_id,
        "confidence": round(confidence, 3),
    })

    # (claim, 幻觉裁定, action) —— 仅在 action 非 PASS
    if halluc_action and halluc_action != "PASS":
        chain_triples.append({
            "head": query[:40] + ("…" if len(query) > 40 else ""),
            "relation": "幻觉裁定",
            "tail": halluc_action,
            "tail_type": "GuardAction",
            "source_id": "rumor:halluc_guard",
            "confidence": float((halluc_report or {}).get("confidence") or 0.5),
        })

    # ----- reasoning_path -----
    chain_path: List[dict] = []
    chain_path.append({
        "step": 1, "actor": "rumor.classify_claim", "action": "命题分类",
        "input_summary": (query or "")[:80],
        "output_summary": f"primary={primary or '?'} (conf={primary_conf:.2f})",
        "cited_refs": [claim_ref_id],
    })
    chain_path.append({
        "step": 2, "actor": "rumor.risk_router", "action": "风险评估与路由",
        "input_summary": f"claim_type={primary}",
        "output_summary": f"final_risk={final_risk} → {route}",
        "cited_refs": [claim_ref_id],
    })

    if route == "FAST_PATH":
        chain_path.append({
            "step": 3, "actor": "rumor.fast_path", "action": "单 LLM 快速核查",
            "input_summary": "低风险路径",
            "output_summary": f"verdict={verdict} belief={belief:+.2f}",
            "cited_refs": [claim_ref_id],
        })
    else:
        # CTAEW 完整流程
        adv_hits = judgment.get("advocate_hits") or {}
        skp_hits = judgment.get("skeptic_hits") or {}
        chain_path.append({
            "step": 3, "actor": "rumor.advocate", "action": "辩护方收集支持证据",
            "input_summary": "调用 KG/RAG/Web 工具检索",
            "output_summary": f"命中 {sum(adv_hits.values()) if adv_hits else len(support_evs)} 条 / {len(support_evs)} 条入选",
            "cited_refs": [r["ref_id"] for r in chain_refs if r["ref_id"].startswith("rumor:support:")],
        })
        chain_path.append({
            "step": 4, "actor": "rumor.skeptic", "action": "质疑方收集反驳证据",
            "input_summary": "调用 KG/RAG/Web 工具检索",
            "output_summary": f"命中 {sum(skp_hits.values()) if skp_hits else len(refute_evs)} 条 / {len(refute_evs)} 条入选",
            "cited_refs": [r["ref_id"] for r in chain_refs if r["ref_id"].startswith("rumor:refute:")],
        })
        chain_path.append({
            "step": 5, "actor": "rumor.judge", "action": "加权裁决",
            "input_summary": "supporting + refuting + objections",
            "output_summary": f"verdict={verdict} belief={belief:+.2f} confidence={confidence:.2f} "
                              f"rounds={judgment.get('rounds_completed', 0)}",
            "cited_refs": [r["ref_id"] for r in chain_refs
                           if r["ref_id"].startswith(("rumor:support:", "rumor:refute:"))],
        })

    if halluc_action:
        chain_path.append({
            "step": len(chain_path) + 1,
            "actor": "rumor.hallucination_guard", "action": "幻觉检测员复核",
            "input_summary": f"对终审 markdown 拆解 claim 对齐 {len(support_evs) + len(refute_evs)} 条证据",
            "output_summary": f"action={halluc_action}",
            "cited_refs": ["rumor:halluc_guard"] if halluc_action != "PASS" else [],
        })

    # ----- final_claim -----
    verdict_emoji = {
        "属实": "✅", "不实": "❌", "部分属实": "🟡", "尚无定论": "⚪",
        "TRUE": "✅", "FALSE": "❌", "PARTIAL": "🟡", "UNCERTAIN": "⚪",
    }.get(str(verdict), "")
    final_claim = (
        f"{verdict_emoji} 命题「{query[:40]}{'…' if len(query) > 40 else ''}」"
        f" 经核查 → {verdict}"
        f"（信念分 {belief:+.2f} / 可信度 {int(round(confidence * 100))}%）"
    )

    # 整链置信度：
    #   FAST_PATH 略低；CTAEW 更高；hallucination override 后再压缩
    base_conf = confidence
    if halluc_action == "ABSTAIN":
        base_conf = min(base_conf, 0.1)
    elif halluc_action in ("WARN", "REGENERATE", "WARN_TIMEOUT"):
        base_conf = min(base_conf, float((halluc_report or {}).get("confidence") or base_conf))
    base_conf = max(0.05, min(1.0, float(base_conf)))

    return build_chain(
        triples=chain_triples,
        reasoning_path=chain_path,
        refs=chain_refs,
        final_claim=final_claim,
        confidence=base_conf,
    )


def _risk_tier_from_assessment(risk_dict: Dict[str, Any]) -> RiskTier:
    """把 R10 的 final_risk 字段（LOW/MEDIUM/HIGH）适配为检测器的风险层。"""
    if not risk_dict:
        return "MEDIUM"
    final = (risk_dict.get("final_risk") or "MEDIUM").upper()
    if final in ("LOW", "MEDIUM", "HIGH"):
        return final  # type: ignore[return-value]
    return "MEDIUM"


async def run_hallucination_guard(
    markdown: str,
    bb: Blackboard,
    judgment: Dict[str, Any],
    audit_logs: List[str],
) -> Tuple[str, Dict[str, Any]]:
    """
    对 rumor 终审输出做 claim 级证据对齐检测。

    返回：
        (rewritten_markdown, hallucination_report_dict)

    行为：
        - PASS       → 原文不动
        - WARN       → 在原文顶部加 🟡 横幅
        - REGENERATE → 现版本暂同 WARN（v2 实现重新裁决）；记录在 audit
        - ABSTAIN    → 替换为弃答模板，保留原 belief/risk 摘要
    """
    if not markdown or not markdown.strip():
        return markdown, {}

    evidence = _gather_evidence_from_bb(bb)
    domain_risk = _risk_tier_from_assessment(judgment.get("risk_assessment") or {})

    await sse_emit(
        "hallucination_check", phase="start",
        message=f"🛡️ 启动幻觉检测员（domain_risk={domain_risk}, 证据 {len(evidence)} 条）…",
    )

    try:
        report: HallucinationReport = await asyncio.wait_for(
            _halluc_check(
                answer=markdown,
                evidence=evidence,
                domain="rumor",
                domain_risk=domain_risk,
            ),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        logger.warning("[Rumor/Halluc] 检测超时 → WARN_TIMEOUT，保留原文并降低置信修正")
        await sse_emit("hallucination_check", phase="timeout",
                       action="WARN_TIMEOUT", message="检测超时，已标记为守门员超时。")
        audit_logs.append("[Halluc] 检测超时，标记为 WARN_TIMEOUT。")
        return markdown, {"action": "WARN_TIMEOUT", "timeout": True, "confidence": 0.5}

    rep_dict = report.as_dict()
    audit_logs.append(
        f"[Halluc] action={report.action} score={report.hallucination_score} "
        f"claims={report.stats.get('n_claims', 0)} "
        f"contra={report.stats.get('n_contradicted', 0)} "
        f"unsup={report.stats.get('n_unsupported', 0)}"
    )

    await sse_emit(
        "hallucination_check", phase="done",
        action=report.action,
        score=report.hallucination_score,
        confidence=report.confidence,
        n_claims=report.stats.get("n_claims", 0),
        n_contradicted=report.stats.get("n_contradicted", 0),
        n_unsupported=report.stats.get("n_unsupported", 0),
        summary=report.summary,
        domain_risk=domain_risk,
    )

    if report.action == "ABSTAIN":
        new_md = (
            ABSTAIN_TEMPLATE
            + "\n\n目前这条回答没有通过证据一致性复核，系统暂时不应给出确定结论。"
            + "你可以换一种问法，或补充更具体的场景、剂量、人群和时间范围后再核查。"
        )
        return new_md, rep_dict

    if report.action in ("WARN", "REGENERATE"):
        if report.action == "REGENERATE":
            audit_logs.append("[Halluc] 检测建议重新生成，本期降级为 WARN 并压低置信度")
        return markdown, rep_dict

    # PASS：原文不动
    return markdown, rep_dict


async def run_rumor_ctaew(
    query: str,
    entities: Optional[list] = None,
    history: Optional[list] = None,
    user_id: Optional[int] = None,
    blackboard: Optional[Blackboard] = None,    # 🗒️ D6: 共享黑板（None = 自创）
    bb_parent_version: int = 0,                 # 🗒️ 父版本号（来自 triage 的 intent_classification）
) -> Tuple[str, dict, list]:
    """
    谣言验证入口（R10 Risk-Routing 版）。

    Args:
        query:    待验证的谣言命题
        entities: NER 实体列表（预留）
        history:  对话历史（预留，谣言验证通常单轮）
        blackboard: 主图共享黑板。提供时，rumor 把所有 entry 写到主黑板，与
                    medication/symptom/general 等模块的 entry 在同一张 DAG 里。
                    None 时，按旧逻辑自创新 bb（向后兼容、单测用）。
    """
    if blackboard is not None:
        bb = blackboard
    else:
        bb = Blackboard(session_id=f"rumor-{id(query)}")
    audit_logs: List[str] = [f"🛡️ [Rumor/CTAEW] 接管命题：{query[:60]}"]

    # SSE 事件收集（供前端 <RumorLiveDebate> 历史回放）
    rumor_events_log: list = []
    collector_token = set_collector(rumor_events_log)

    try:
        # ==================================================================
        # Phase 0：分类 + 风险评估（所有路径都要走）
        # ==================================================================
        await sse_emit("rumor_step", phase="start",
                       message="谣言核查启动，开始风险评估…",
                       claim=query)

        # 🗒️ rumor_claim 接 triage 的 intent_classification（如果存在）
        v_claim = await bb.append(
            "rumor_claim",
            {"claim": query},
            agent_id="perception",
            parent_refs=[bb_parent_version] if bb_parent_version else [],
        )

        await sse_emit("rumor_step", phase="classify_start",
                       message="正在识别谣言类型…")
        try:
            classification = await classify_claim(query)
        except Exception as e:
            logger.exception("[Rumor/Integration] 分类异常")
            audit_logs.append(f"[Rumor] 分类异常熔断: {type(e).__name__}: {e}")
            fallback_md = _exception_fallback_md(query, "分类流程异常")
            fallback_trace = {"rumor_events": list(rumor_events_log)}
            return fallback_md, fallback_trace, audit_logs

        v_cls = await bb.append(
            "rumor_classification",
            classification.as_dict(),
            agent_id="claim_classifier",
            parent_refs=[v_claim],
        )
        await sse_emit("rumor_step", phase="classify_done",
                       claim_type=classification.primary,
                       confidence=classification.primary_confidence,
                       secondary=classification.secondary)

        risk = assess_risk(query, classification)
        v_risk = await bb.append(
            "rumor_risk_assessment",
            risk.as_dict(),
            agent_id="risk_router",
            parent_refs=[v_cls],
        )
        # 🧠 见解知识库：检索相似历史案例（成功 + 反例），喂给 judge 作参考
        prior_insights_text = ""
        try:
            from core.insight_memory import (
                retrieve_insights as _ri, render_insights_as_fewshot as _rd,
            )
            _insights = await _ri(
                query=query, user_id=user_id, domain="rumor",
                top_k=3, min_similarity=0.78, include_shared=True,
            )
            if _insights:
                prior_insights_text = _rd(_insights, max_chars=1200)
                audit_logs.append(
                    f"🧠 [Insight] rumor 检索命中 {len(_insights)} 条历史案例"
                )
                await sse_emit(
                    "rumor_step", phase="insight_hit",
                    n_hits=len(_insights),
                    n_success=sum(1 for i in _insights if i.polarity == "SUCCESS"),
                    n_failure=sum(1 for i in _insights if i.polarity == "FAILURE"),
                    message=f"🧠 命中 {len(_insights)} 条相似历史案例，作为先验参考",
                )
        except Exception as _ie:
            logger.warning(f"[Rumor/Insight] 检索异常（不阻断）: {_ie}")
        await sse_emit("rumor_step", phase="risk_routed",
                       base_risk=risk.base_risk,
                       final_risk=risk.final_risk,
                       route=risk.route,
                       upgrade_reasons=risk.upgrade_reasons,
                       message=(
                           f"风险等级 {risk.final_risk}（基础 {risk.base_risk}）→ "
                           f"{'轻量证据核查' if risk.route == 'LIGHT_CHECK' else '深度 CTAEW 核查'}"
                       ))
        audit_logs.append(
            f"[Risk] {classification.primary}({classification.primary_confidence:.2f}) → "
            f"base={risk.base_risk} final={risk.final_risk} route={risk.route}"
            + (f" [升级：{'; '.join(risk.upgrade_reasons)}]" if risk.upgrade_reasons else "")
        )

        # ==================================================================
        # Phase 1：按风险分流
        # ==================================================================
        try:
            light_check = risk.route == "LIGHT_CHECK"
            active_weight_profile = resolve_composite_weights(
                classification.primary,
                classification.secondary,
                classification.primary_confidence,
                classification.secondary_confidence,
                total_budget=6 if light_check else 10,
            )
            judgment = await run_rumor(
                bb=bb,
                claim=query,
                prior_insights_text=prior_insights_text,
                classification=classification,
                risk_assessment=risk,
                weight_profile=active_weight_profile,
                claim_version=v_claim,
                classification_version=v_cls,
                risk_version=v_risk,
                total_budget=6 if light_check else 10,
                max_rounds=1 if light_check else 2,
            )
            judgment["risk_assessment"] = risk.as_dict()
            if light_check:
                judgment["termination_reason"] = f"LIGHT_CHECK:{judgment.get('termination_reason', '')}"
        except Exception as e:
            logger.exception("[Rumor/Integration] 核查分支异常")
            audit_logs.append(f"[Rumor] 分支异常熔断: {type(e).__name__}: {e}")
            fallback_md = _exception_fallback_md(query, "核查流程异常")
            fallback_trace = {"rumor_events": list(rumor_events_log)}
            return fallback_md, fallback_trace, audit_logs

    finally:
        reset_collector(collector_token)

    # ==================================================================
    # Phase 2：从 Blackboard 提取证据，填充旧前端面板
    # ==================================================================
    scout_sources, medical_sources, medical_truth_text = _build_rumor_evidence_panels(
        bb=bb,
        query=query,
        classification=classification,
        judgment=judgment,
    )
    scout_sources, medical_sources = await _summarize_rumor_evidence_panels(
        query=query,
        scout_cards=scout_sources,
        medical_cards=medical_sources,
    )

    dag = bb.to_trace_dag()
    trace_dict = {
        # 旧前端字段 — 从 CTAEW 辩论证据中提取
        "scout_data": scout_sources,
        "medical_data": medical_sources,
        "medical_truth_text": medical_truth_text,
        "critic_reasoning": judgment.get("debate_highlights", ""),

        # D9 / R10 新增字段
        "rumor_ctaew": {
            "claim_type":         judgment.get("claim_type"),
            "classification":     judgment.get("classification"),
            "risk_assessment":    judgment.get("risk_assessment"),
            "weights":            judgment.get("weights"),
            "belief_score":       judgment.get("belief_score"),
            "dissent_score":      judgment.get("dissent_score"),
            "confidence":         judgment.get("confidence"),
            "final_verdict":      judgment.get("final_verdict"),
            "per_source_net":     judgment.get("per_source_net"),
            "advocate_hits":      judgment.get("advocate_hits"),
            "skeptic_hits":       judgment.get("skeptic_hits"),
            "weighted_support":   judgment.get("weighted_support"),
            "weighted_refute":    judgment.get("weighted_refute"),
            "source_breakdown":   judgment.get("source_breakdown"),
            "evidence_quality_summary": judgment.get("evidence_quality_summary"),
            "evidence_coverage":  judgment.get("evidence_coverage"),
            "termination_reason": judgment.get("termination_reason"),
            "rounds_completed":   judgment.get("rounds_completed"),
            "total_tool_calls":   judgment.get("total_tool_calls"),
            "total_evidence_hits":judgment.get("total_evidence_hits"),
        },
        # 黑板 DAG（供审计追溯）
        "rumor_blackboard_trace": dag,
        # SSE 事件流（供 <RumorLiveDebate> 历史回放）
        "rumor_events": list(rumor_events_log),
    }

    audit_logs.extend([
        f"[Rumor] 路由 → {judgment.get('termination_reason')} "
        f"(claim_type={judgment.get('claim_type')})",
        f"[Rumor] 终审 → belief={judgment.get('belief_score')} "
        f"verdict={judgment.get('final_verdict')} "
        f"confidence={judgment.get('confidence')} "
        f"rounds={judgment.get('rounds_completed', 0)} "
        f"tool_calls={judgment.get('total_tool_calls', 0)}",
    ])

    markdown = judgment.get("final_markdown_report") or ""
    if not markdown.strip():
        markdown = (
            "### 核查结论\n\n"
            f"针对“{query}”，当前结论是：**{judgment.get('final_verdict', '尚无定论')}**。\n\n"
            "这个判断来自本轮可检索到的支持与反驳材料。社交平台内容只用于观察传播和常见说法，不能单独证明医学或科学结论。\n\n"
            "如果这个问题涉及症状、用药、疾病风险或特殊人群，请结合自身情况咨询医生。"
        )

    # ==================================================================
    # Phase 3：🛡️ Hallucination Guard
    # 在终审 markdown 出锅之后、返回前端之前，过一道独立的"幻觉检测员"
    # —— 把回答拆成原子声明，逐条对齐 advocate/skeptic 收集到的证据
    # —— 发现 HIGH+CONTRADICTED 直接弃答；WARN 加横幅；PASS 透传
    # —— ABSTAIN/WARN 同步把 verdict/confidence 推回 trace.rumor_ctaew，
    #    这样消融实验和下游能看到检测员真正的可信度修正效果。
    # ==================================================================
    try:
        markdown, halluc_report = await run_hallucination_guard(
            markdown=markdown,
            bb=bb,
            judgment=judgment,
            audit_logs=audit_logs,
        )
        if halluc_report:
            trace_dict["hallucination_check"] = halluc_report

            # 🧠 见解知识库：fire-and-forget 收割（不阻塞 rumor 返回）
            try:
                from core.insight_memory import harvest_from_hallucination_report as _ih
                evidence_count = (
                    int(judgment.get("total_evidence_hits") or 0)
                    or len(_gather_evidence_from_bb(bb))
                )
                claim_type = judgment.get("claim_type", "")
                asyncio.create_task(_ih(
                    query=query,
                    domain="rumor",
                    user_id=user_id,
                    final_answer=markdown,
                    halluc_report=halluc_report,
                    evidence_count=evidence_count,
                    agent_path=f"rumor:{judgment.get('termination_reason', 'CTAEW')}",
                    tags=[claim_type] if claim_type else [],
                ))
            except Exception as _harvest_err:
                logger.warning(f"[Rumor/Insight] 收割异常: {_harvest_err}")
            # 把幻觉检测员的 action 反馈到 ctaew 字段：
            #   ABSTAIN → 强制改为"尚无定论" + 极低 confidence（最严苛）
            #   WARN/REGENERATE → 按检测员 confidence 与原 confidence 取较小值
            ctaew = trace_dict.get("rumor_ctaew") or {}
            action = halluc_report.get("action")
            halluc_conf = float(halluc_report.get("confidence") or 0.0)

            if action == "ABSTAIN":
                ctaew["final_verdict"] = "尚无定论"
                ctaew["confidence"] = round(min(0.05, halluc_conf), 4)
                ctaew["belief_score"] = 0.0
                ctaew["hallucination_override"] = "ABSTAIN"
                audit_logs.append(
                    "[Halluc] 触发 ABSTAIN：判决降级为『尚无定论』，confidence 归零，"
                    "用于校准聚合与 ECE 评估。"
                )
            elif action in ("WARN", "REGENERATE", "WARN_TIMEOUT"):
                old_conf = float(ctaew.get("confidence") or 0.0)
                new_conf = round(min(old_conf, halluc_conf), 4)
                ctaew["confidence"] = new_conf
                ctaew["hallucination_override"] = action
                audit_logs.append(
                    f"[Halluc] {action}：confidence {old_conf:.3f} → {new_conf:.3f}（取与检测员置信度的较小值）"
                )

            trace_dict["rumor_ctaew"] = ctaew
    except Exception as e:
        logger.exception("[Rumor/Halluc] 检测器异常（不阻断主流程）")
        audit_logs.append(f"[Halluc] 检测器异常: {type(e).__name__}: {e}")

    # ==================================================================
    # Phase 4：🔗 D6/D7 证据链组装
    # 在所有 verdict / halluc_override 都尘埃落定之后，把 BB 黑板事件
    # + judgment + halluc_report 全部编织成统一证据链交给前端。
    # 异常吞掉，不阻断 rumor 主流程。
    # ==================================================================
    try:
        rumor_chain = _build_rumor_evidence_chain(
            bb=bb,
            query=query,
            classification=classification,
            risk=risk,
            judgment=judgment,
            halluc_report=trace_dict.get("hallucination_check"),
            route=getattr(risk, "route", "FULL"),
        )
        if rumor_chain and (rumor_chain.get("triples") or rumor_chain.get("reasoning_path")):
            trace_dict["evidence_chain"] = rumor_chain
            audit_logs.append(
                f"[Rumor/Chain] 证据链：{len(rumor_chain.get('triples', []))} triples / "
                f"{len(rumor_chain.get('refs', []))} refs / "
                f"{len(rumor_chain.get('reasoning_path', []))} steps"
            )
    except Exception as e:
        logger.exception("[Rumor/Chain] 证据链组装异常（不阻断）")
        audit_logs.append(f"[Rumor/Chain] 组装异常: {type(e).__name__}: {e}")

    return markdown, trace_dict, audit_logs


# ---------------------------------------------------------------------
# 内部工具：异常兜底 Markdown
# ---------------------------------------------------------------------

def _exception_fallback_md(query: str, reason: str) -> str:
    return (
        "### 🛡️ AI 智能健康核查报告\n\n"
        "#### 📌 核查结论\n\n"
        f"> **【核查定性】** ❓ 尚无定论\n>\n"
        f"> 针对「{query}」的核查流程发生异常（{reason}），系统已熔断。\n"
        f"> 建议您稍后重试或改问人工健康顾问。\n"
    )
