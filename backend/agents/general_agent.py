# backend/agents/general_agent.py
import os
import json
import logging
import asyncio
import re
from typing import List, Dict, Tuple, Any, Optional
from dotenv import load_dotenv, find_dotenv
from core.llm_client import shared_client as client, DEFAULT_MODEL
from core.blackboard import Blackboard
from core.sse_emitter import emit as sse_emit

from scripts.tools_search import search_dynamic_medical_info
from scripts.kg_pruner import KnowledgeGraphPruner
from core.intent_ontology import render_attr_focus as _render_attr_focus, describe as _describe_intent
from rag.service import retrieve_medical_evidence

load_dotenv(find_dotenv(usecwd=True))
logger = logging.getLogger("GeneralAgent")

# 实例化图谱工具
kg_pruner = KnowledgeGraphPruner()

DEFAULT_TOOL_TIMEOUTS = {
    "search_local_guidelines": float(os.getenv("GENERAL_LOCAL_RAG_TIMEOUT_SECONDS", "18")),
    "search_medical_graph": float(os.getenv("GENERAL_KG_TIMEOUT_SECONDS", "5")),
    "search_public_internet": float(os.getenv("GENERAL_WEB_TIMEOUT_SECONDS", "10")),
    "search_pubmed": float(os.getenv("GENERAL_PUBMED_TIMEOUT_SECONDS", "8")),
}

DEFAULT_TOOL_CALL_LIMITS = {
    "search_local_guidelines": int(os.getenv("GENERAL_LOCAL_RAG_MAX_CALLS", "2")),
    "search_medical_graph": int(os.getenv("GENERAL_KG_MAX_CALLS", "1")),
    "search_public_internet": int(os.getenv("GENERAL_WEB_MAX_CALLS", "1")),
    "search_pubmed": int(os.getenv("GENERAL_PUBMED_MAX_CALLS", "0")),
}


async def _run_tool_with_timeout(tool_name: str, handler, args: dict) -> str:
    timeout = DEFAULT_TOOL_TIMEOUTS.get(tool_name, 8.0)
    try:
        return await asyncio.wait_for(handler(args), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"[General] tool timeout: {tool_name} after {timeout:.1f}s args={args}")
        return f"{tool_name} 检索超时（{timeout:.0f}s），本轮跳过该来源。"


def _contains_tool_markup(text: str) -> bool:
    text = text or ""
    return any(marker in text for marker in ("DSML", "tool_calls", "invoke name=", "parameter name="))


def _rule_based_general_answer(query: str, sources: list) -> str:
    evidence_note = "系统未能完成足够证据交叉校验" if not sources else f"系统已参考 {len(sources)} 条检索线索"
    return (
        f"### 综合分析建议\n\n"
        f"关于“{query}”，{evidence_note}，因此这里给出保守的通用健康建议。\n\n"
        f"- 传染病防治的核心是减少暴露、切断传播途径、提高个人防护和及时就医。\n"
        f"- 日常应重点做好手卫生、通风、呼吸道礼仪、食品和饮水安全，必要时按规范佩戴口罩。\n"
        f"- 疫苗接种、慢病管理、充足睡眠和规律运动有助于降低感染及重症风险。\n"
        f"- 如果出现持续高热、呼吸困难、意识异常、严重腹泻脱水或疑似聚集性发病，应尽快线下就医或联系公共卫生机构。\n\n"
        f"> 系统提示：本轮检索链路不完整，以上为通用科普，不能替代医生或疾控部门的具体判断。"
    )


def _terms_for_relevance(text: str) -> set[str]:
    text = (text or "").lower()
    terms = set(re.findall(r"[\u4e00-\u9fff]{2,8}|[a-z][a-z0-9\-]{2,}", text))
    # Add overlapping Chinese bigrams to avoid one long token hiding useful overlap.
    for token in list(terms):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) > 2:
            terms.update(token[i : i + 2] for i in range(len(token) - 1))
    return {term for term in terms if term.strip()}


def _source_card_relevant(query: str, card: dict) -> bool:
    query_terms = _terms_for_relevance(query)
    if not query_terms:
        return False
    body = " ".join(
        str(card.get(key) or "")
        for key in ("title", "label", "snippet", "content", "display_text", "raw_chunk")
    )
    body_terms = _terms_for_relevance(body)
    overlap = query_terms & body_terms
    return len(overlap) >= 2 or any(len(term) >= 3 and term in body for term in query_terms)

# ==========================================
# 🌟 核心升级 1：定义大模型的“武器库” (Tools Schema)
# ==========================================
MEDICAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_local_guidelines",
            "description": "搜索本地三甲医院权威医学指南与文献库。当你需要查阅疾病的常规治疗方案、症状解析、权威用药指导时，优先调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {"type": "string", "description": "高度凝练的医学检索词，例如 '糖尿病 饮食禁忌'"}
                },
                "required": ["search_query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_medical_graph",
            "description": "搜索底层医学知识图谱。当你需要进行严密的病理逻辑推演、查询多跳并发症、或绝对用药禁忌关系时，调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "核心医学实体数组，例如 ['高血压', '布洛芬']"
                    }
                },
                "required": ["keywords"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_public_internet",
            "description": "搜索公网最新医疗资讯与百科。仅当本地指南和图谱无法满足需求，或者需要查询最新医疗新闻、偏方辟谣时调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {"type": "string", "description": "互联网检索关键词"}
                },
                "required": ["search_query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_pubmed",
            "description": "检索 PubMed 学术文献数据库。当你需要查证最新医学研究、罕见病文献或高证据等级的原始论文时调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {"type": "string", "description": "PubMed 检索词，建议使用英文 MeSH 术语或中文核心词"}
                },
                "required": ["search_query"]
            }
        }
    }
]


async def run_general_agent(
        query: str,
        entities: List[str],
        messages_history: List[Dict[str, str]],
        patient_profile: dict,
        internal_scratchpad: List[Dict[str, str]],
        vision_context: str = None,  # 🌟 必须加上这个接收口
        user_id: int = None,  # 🧠 见解知识库私有桶 ID（None = 仅查共享桶）
        act_intent: str = "",     # 🆕 行为轴（决定 prompt 偏重）
        attr_intent: str = "",    # 🆕 属性轴（决定 prompt 偏重）
        blackboard: Any = None,         # 🗒️ 共享黑板（None = 不写入）
        bb_parent_version: int = 0,     # 🗒️ 父版本号（来自 triage 的 intent_classification）
        force_tools: Optional[List[str]] = None,  # 强制先执行的工具名列表
        min_tool_calls: int = 0,                  # 最少证据检索次数，用于 single_react_kg
) -> Tuple[str, list, list, list, dict]:  # 🔗 第 5 元素：evidence_chain


    """
    🌟 ReAct 全科主任大夫智能体：具备自主推理、连续工具调用、动态观测的能力。
    """
    logger.info("👨‍⚕️ [General Agent] 纯正 ReAct 全科大夫已接管，启动自主推理循环...")
    audit_logs = ["[General] 启动 ReAct 自主决策引擎。"]
    force_tools = list(force_tools or [])
    if min_tool_calls > 0 and not force_tools:
        force_tools = ["search_medical_graph", "search_local_guidelines"][:min_tool_calls]
    if force_tools:
        audit_logs.append(f"[General] 强制证据检索: {', '.join(force_tools)}")

    # 🆕 二维内容轴侧重（来自 triage 的 act_intent / attr_intent）
    intent_desc = _describe_intent(act_intent, attr_intent)
    if intent_desc != "未识别":
        audit_logs.append(f"🎯 [General/Intent] {intent_desc}")

    # 🧠 见解知识库：检索 top-k 相似历史案例（成功 + 反例）
    insight_fewshot = ""
    try:
        from core.insight_memory import retrieve_insights, render_insights_as_fewshot
        _insights = await retrieve_insights(
            query=query, user_id=user_id, domain="general",
            top_k=3, min_similarity=0.78, include_shared=True,
        )
        if _insights:
            insight_fewshot = render_insights_as_fewshot(_insights, max_chars=1200)
            audit_logs.append(
                f"🧠 [Insight] 命中 {len(_insights)} 条相似案例（"
                f"{sum(1 for i in _insights if i.polarity == 'SUCCESS')} 正例 + "
                f"{sum(1 for i in _insights if i.polarity == 'FAILURE')} 反例）"
            )
    except Exception as _e:
        logger.warning(f"[General/Insight] 检索异常（不阻断）: {_e}")

    # 收集全局状态用于最终的前端渲染
    collected_sources = []
    collected_images = []

    # 🔗 证据链追踪（reasoning_path + refs）
    from core.evidence import build_chain, dedupe_refs
    chain_steps: List[Dict] = []        # ReasoningStep 列表
    chain_refs: List[Dict] = []          # EvidenceRef 列表（按 ref_id 去重）

    # 🗒️ 黑板写入：tool 调用之间用 last_bb_version 形成串行因果链
    bb_active = isinstance(blackboard, Blackboard)
    last_bb_version: int = bb_parent_version or 0
    tool_bb_versions: List[int] = []     # 所有 tool_call 版本号，留给 synthesis 做 parents

    async def _bb_log_tool(tool_name: str, args: dict, n_results: int, result_preview: str):
        """记录一次 tool 调用到黑板，并更新 last_bb_version。"""
        nonlocal last_bb_version
        if not bb_active:
            return
        try:
            v = await blackboard.append(
                "general_tool_call",
                {
                    "tool": tool_name,
                    "args": args,
                    "n_results": n_results,
                    "preview": (result_preview or "")[:160],
                },
                agent_id=f"general.{tool_name}",
                parent_refs=[last_bb_version] if last_bb_version else [],
            )
            if v:
                last_bb_version = v
                tool_bb_versions.append(v)
        except Exception as e:
            logger.warning(f"[General/BB] tool_call append 失败: {e}")

    def _card_to_ref(card: Dict, source_kind: str) -> Dict:
        """把 source card 转 EvidenceRef。card 形态参考 multimodal/web 检索返回。"""
        title = (card.get("title") or card.get("name") or "未命名").strip()
        snippet = (card.get("snippet") or card.get("content") or card.get("summary") or "").strip()
        url = card.get("url") or card.get("link") or ""
        doc_name = card.get("doc") or card.get("source") or card.get("collection") or ""
        page = card.get("page")
        if source_kind == "web":
            ref_id = "web:" + str(abs(hash(url or title)) % (10 ** 10))
            locator = {"url": url} if url else {}
            ref_type = "web"
        else:  # local guidelines / pdf chunk
            ref_id = f"doc:{doc_name}#{abs(hash(snippet[:64])) % 10**8}"
            locator = {"doc": doc_name}
            if page is not None:
                locator["page"] = page
            ref_type = "pdf"
        return {
            "ref_id": ref_id, "type": ref_type, "label": title[:80],
            "locator": locator,
            "snippet": snippet[:300] if snippet else None,
        }

    # ==========================================
    # 🌟 工具注册表（一次性构建，循环内复用）
    # 闭包捕获 collected_sources / collected_images / chain_*，所以必须建在函数体内
    # ==========================================
    async def _run_local_guidelines(args):
        search_query = args.get("search_query") or state.get("query") or ""
        try:
            rag_result = await retrieve_medical_evidence(search_query, intent="guideline_qa", top_k=4)
            cards = [item.to_source_card(i + 1) for i, item in enumerate(rag_result.items)]
            cards = [card for card in cards if _source_card_relevant(search_query, card)]
            imgs = []
            ctx = "\n\n".join(
                f"{card.get('title', '')}\n{card.get('snippet') or card.get('raw_chunk') or card.get('content') or ''}"
                for card in cards
            )
        except Exception as exc:
            logger.warning(f"local guideline retrieval degraded: {type(exc).__name__}: {exc}")
            ctx, cards, imgs = "", [], []
        collected_sources.extend(cards)
        collected_images.extend(imgs)
        # 🔗 把命中卡转成 ref，记录到 reasoning step
        new_refs = [_card_to_ref(c, "doc") for c in (cards or [])]
        chain_refs.extend(new_refs)
        chain_steps.append({
            "step": len(chain_steps) + 1,
            "actor": "general.search_local_guidelines",
            "action": "查询本地指南库",
            "input_summary": (args.get("search_query") or "")[:80],
            "output_summary": f"命中 {len(cards or [])} 条指南卡片",
            "cited_refs": [r["ref_id"] for r in new_refs],
        })
        await _bb_log_tool("search_local_guidelines", args, len(cards or []), ctx or "")
        return ctx if ctx else "本地指南未找到相关信息。"

    async def _run_medical_graph(args):
        ctx = await asyncio.to_thread(kg_pruner.execute_pruning, args["keywords"], 3)
        # 🔗 KG 输出作为单条 kg ref（locator 用关键字，snippet 截前 300 字）
        kw_join = ",".join(args.get("keywords", []))
        kg_ref = {
            "ref_id": f"kg:{abs(hash(kw_join)) % 10**8}",
            "type": "kg",
            "evidence_role": "constraint",
            "citation_allowed": False,
            "label": f"图谱推演 · {kw_join[:50]}",
            "locator": {"keywords": args.get("keywords", [])},
            "snippet": (ctx or "")[:300] if ctx else None,
        }
        if ctx:
            chain_refs.append(kg_ref)
        chain_steps.append({
            "step": len(chain_steps) + 1,
            "actor": "general.search_medical_graph",
            "action": "查询知识图谱",
            "input_summary": kw_join[:80],
            "output_summary": ("命中 KG 路径" if ctx else "图谱无相关路径"),
            "cited_refs": [kg_ref["ref_id"]] if ctx else [],
        })
        await _bb_log_tool("search_medical_graph", args, 1 if ctx else 0, ctx or "")
        return ctx if ctx else "知识图谱中无明显推演路径。"

    async def _run_public_internet(args):
        ctx, cards = await search_dynamic_medical_info(args["search_query"], 5, 2)
        collected_sources.extend(cards)
        new_refs = [_card_to_ref(c, "web") for c in (cards or [])]
        chain_refs.extend(new_refs)
        chain_steps.append({
            "step": len(chain_steps) + 1,
            "actor": "general.search_public_internet",
            "action": "公网医学检索",
            "input_summary": (args.get("search_query") or "")[:80],
            "output_summary": f"召回 {len(cards or [])} 条公开来源",
            "cited_refs": [r["ref_id"] for r in new_refs],
        })
        await _bb_log_tool("search_public_internet", args, len(cards or []), ctx or "")
        return ctx if ctx else "公网搜索无有效结果。"

    async def _run_pubmed(args):
        """PubMed 学术文献检索。"""
        from agents.maddx.tools import _pubmed_search_impl
        hits = await _pubmed_search_impl({"query": args["search_query"], "top_k": 3})
        # 格式化输出
        if not hits or hits[0].get("ref", "").startswith("error"):
            return "PubMed 未找到相关文献。"
        lines = ["【PubMed 检索结果】"]
        for h in hits[:3]:
            lines.append(f"- PMID:{h['ref'].replace('pubmed:','')} | {h.get('title','')}")
            if h.get("text"):
                lines.append(f"  摘要: {h['text'][:200]}")
        ctx = "\n".join(lines)
        # 记录到证据链
        for h in hits:
            chain_refs.append({
                "ref_id": h["ref"], "type": "pubmed",
                "label": h.get("title", "PubMed文献")[:80],
                "locator": {"pmid": h["ref"].replace("pubmed:", "")},
                "snippet": h.get("text", "")[:300],
            })
        chain_steps.append({
            "step": len(chain_steps) + 1, "actor": "general.search_pubmed",
            "action": "检索 PubMed 学术文献",
            "input_summary": (args.get("search_query") or "")[:80],
            "output_summary": f"命中 {len(hits)} 篇文献",
            "cited_refs": [h["ref"] for h in hits],
        })
        await _bb_log_tool("pubmed_search", args, len(hits), ctx or "")
        return ctx

    TOOL_REGISTRY = {
        "search_local_guidelines": _run_local_guidelines,
        "search_medical_graph":    _run_medical_graph,
        "search_public_internet":  _run_public_internet,
        "search_pubmed":           _run_pubmed,
    }

    async def _synthesize_from_collected_sources(reason: str) -> str:
        evidence_lines = []
        for idx, source in enumerate(collected_sources[:6], 1):
            if not isinstance(source, dict):
                continue
            title = source.get("title") or source.get("label") or f"来源{idx}"
            snippet = source.get("snippet") or source.get("summary") or source.get("content") or ""
            evidence_lines.append(f"{idx}. {title}\n{str(snippet)[:500]}")
        evidence_text = "\n\n".join(evidence_lines) or "本轮没有可稳定使用的检索证据。"
        messages = [
            {
                "role": "system",
                "content": (
                    "你是全科医生。请基于用户问题和已收集证据生成最终中文回答。"
                    "禁止输出任何工具调用、DSML、XML、JSON、invoke、tool_calls 或内部推理标记。"
                    "如果证据不足，请明确说明证据不足，并给出保守的通用建议。"
                    "使用 Markdown，但不要使用 H1/H2。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户问题：{query}\n\n"
                    f"终止原因：{reason}\n\n"
                    f"已收集证据：\n{evidence_text}\n\n"
                    "请直接输出面向用户的最终回答。"
                ),
            },
        ]
        try:
            response = await client.chat.completions.create(
                model=DEFAULT_MODEL,
                messages=messages,
                temperature=0.1,
            )
            answer = response.choices[0].message.content or ""
        except Exception as exc:
            logger.warning(f"[General] clean synthesis failed: {type(exc).__name__}: {exc}")
            answer = ""
        if not answer.strip() or _contains_tool_markup(answer):
            logger.warning("[General] clean synthesis produced invalid tool markup, using rule fallback.")
            return _rule_based_general_answer(query, collected_sources)
        return answer

    def _forced_tool_args(tool_name: str) -> dict:
        if tool_name == "search_medical_graph":
            keywords = [str(e).strip() for e in (entities or []) if str(e).strip()]
            if not keywords:
                keywords = [query.strip()[:40] or "综合医学咨询"]
            return {"keywords": keywords[:5]}
        if tool_name in ("search_local_guidelines", "search_public_internet", "search_pubmed"):
            return {"search_query": query.strip()[:120] or "综合医学咨询"}
        return {}

    # 1. 构建系统上下文沙盒
    system_prompt = f"""
        你是一位严谨的【全科数字大夫】。你现在拥有三个工具：本地指南库、医学图谱、公网搜索引擎。
        请基于用户的提问，自主思考需要查阅什么资料，并调用对应的工具。

        【你的工作流 (ReAct 范式)】：
         1. 思考 (Thought)：我需要了解什么信息？我自身的医学常识是否已经足够回答？
         2. 行动 (Action)：如果常识不足或需要查证具体禁忌，调用工具获取信息；如果已有十足把握（如基础的饮食建议、安慰患者），请直接跳到第 4 步。
         3. 观察 (Observation)：阅读工具返回的信息。
         4. 最终答复：停止工具调用，直接输出富有同理心的、人情味的 Markdown 诊断/建议报告。严禁生硬拼接搜索结果！

        【⚠️ 最终输出红线 (防泄露与排版约束)】
         1. 🚨 严禁在面向患者的最终回复中输出你的内部思考过程（绝对不要出现“观察：”、“思考：”、“最终答复：”等废话前缀）！
         2. 请使用温和、专业的医生口吻直接与患者对话。
         3. 报告必须具有清晰的 Markdown 层次，但🚨绝对禁止使用 H1 (#) 或 H2 (##) 这种超大标题。请统一使用 H3 (###) 作为主模块标题，使用加粗 (**内容**) 强调重点。

        【建议的报告框架 (保持柔性，视情况调整)】
         ### 🩺 综合分析建议
         (结合患者症状、多模态体征、图谱和指南，给出通俗易懂的病情推导)

         ### 🏥 就医与科室指引
         (如果需要，明确建议优选挂什么科室，可能需要做哪些检查)

         ### 🍎 生活与护理处方
         (针对性的生活、饮食、用药注意事项。若是急症请直接建议就医，跳过此项)

         > *⚠️ 系统免责声明：本报告由 AI 全科大夫生成，仅供参考，不构成绝对的临床诊断。如有严重不适，请立即线下就诊。*

        【患者隐私档案】：{json.dumps(patient_profile, ensure_ascii=False) if patient_profile else "无"}
        【内部专科医生留言】：{json.dumps(internal_scratchpad, ensure_ascii=False) if internal_scratchpad else "无"}
        【多模态视觉体征提取】：{vision_context if vision_context else "无视觉影像上传"}

        {insight_fewshot if insight_fewshot else ""}
        {_render_attr_focus(attr_intent)}
        """

    # 2. 组装短期记忆
    recent_history = messages_history[-4:] if len(messages_history) > 4 else messages_history
    llm_messages = [{"role": "system", "content": system_prompt}]
    for m in recent_history:
        llm_messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
    llm_messages.append({"role": "user", "content": f"患者最新提问：{query}"})

    # single_react_kg 的关键兑现：在 ReAct 自主循环前先完成最小证据检索。
    forced_context_blocks = []
    tool_call_counts = {name: 0 for name in TOOL_REGISTRY}
    valid_force_tools = []
    for tool_name in force_tools:
        if tool_name in TOOL_REGISTRY and tool_name not in valid_force_tools:
            valid_force_tools.append(tool_name)
    if valid_force_tools:
        await sse_emit(
            "agent_step",
            agent="general",
            phase="forced_retrieval",
            message=f"📚 正在执行强制证据检索（{len(valid_force_tools)} 个工具）",
            tools=valid_force_tools,
        )
        for tool_name in valid_force_tools:
            args = _forced_tool_args(tool_name)
            try:
                if tool_call_counts.get(tool_name, 0) >= DEFAULT_TOOL_CALL_LIMITS.get(tool_name, 1):
                    audit_logs.append(f"[General] 强制工具 {tool_name} 已达到调用上限，跳过。")
                    continue
                tool_call_counts[tool_name] = tool_call_counts.get(tool_name, 0) + 1
                tool_result = await _run_tool_with_timeout(tool_name, TOOL_REGISTRY[tool_name], args)
                forced_context_blocks.append(
                    f"【{tool_name}】\n输入: {json.dumps(args, ensure_ascii=False)}\n结果:\n{tool_result[:1800]}"
                )
                audit_logs.append(f"[General] 已完成强制工具 {tool_name} 检索。")
            except Exception as e:
                logger.warning(f"[General] 强制工具 {tool_name} 失败: {e}")
                audit_logs.append(f"[General] 强制工具 {tool_name} 失败: {type(e).__name__}")
    if forced_context_blocks:
        llm_messages.append({
            "role": "system",
            "content": (
                "【系统已完成的强制查证结果】\n"
                "下面内容必须作为本轮回答的证据约束。若证据不足，请明确说明不确定性，不要越权开处方。\n\n"
                + "\n\n".join(forced_context_blocks)
            )
        })

    # ==========================================
    # 🌟 核心升级 2：ReAct 动态推演循环 (最大防死循环限制 = 4)
    # ==========================================
    MAX_ITERATIONS = int(os.getenv("GENERAL_AGENT_MAX_ITERATIONS", "3"))
    if attr_intent in ("PREVENT", "BASIC"):
        MAX_ITERATIONS = min(MAX_ITERATIONS, 2)

    for iteration in range(MAX_ITERATIONS):
        logger.info(f"🔄 [General Agent] 第 {iteration + 1} 轮推理思考中...")
        await sse_emit("agent_step", agent="general", phase="react_iter",
                       message=f"🧠 ReAct 第 {iteration + 1} 轮推理…",
                       iteration=iteration + 1, max_iter=MAX_ITERATIONS)

        try:
            response = await client.chat.completions.create(
                model=DEFAULT_MODEL,
                messages=llm_messages,
                tools=MEDICAL_TOOLS,
                tool_choice="auto",  # 让大模型自主决定是否调工具
                temperature=0.1
            )

            response_message = response.choices[0].message
            llm_messages.append(response_message)  # 将助手的思考和工具调用指令加入历史

            # 🌟 分支 A：大模型决定调用工具 (Acting)
            if response_message.tool_calls:
                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    arguments = json.loads(tool_call.function.arguments)

                    logger.info(f"🛠️ [General Agent] 决定调用工具: {function_name} | 参数: {arguments}")
                    audit_logs.append(f"🛠️ [思考与行动]: 决定调用 {function_name} 获取 {arguments}")
                    # 把工具名映射到中文友好文案
                    tool_msg_map = {
                        "search_local_guidelines": "📚 查询本地指南库",
                        "search_medical_graph":    "🧬 查询知识图谱",
                        "search_public_internet":  "🌐 公网医学检索",
                    }
                    await sse_emit("agent_step", agent="general", phase="tool_call",
                                   message=tool_msg_map.get(function_name, f"🛠️ 调用工具：{function_name}"),
                                   tool=function_name, args=arguments,
                                   iteration=iteration + 1)

                    tool_result = ""
                    handler = TOOL_REGISTRY.get(function_name)
                    if handler:
                        if tool_call_counts.get(function_name, 0) >= DEFAULT_TOOL_CALL_LIMITS.get(function_name, 1):
                            tool_result = f"{function_name} 已达到本轮调用上限，停止继续检索该来源。"
                            audit_logs.append(f"[General] {function_name} 达到调用上限，跳过。")
                        else:
                            tool_call_counts[function_name] = tool_call_counts.get(function_name, 0) + 1
                            tool_result = await _run_tool_with_timeout(function_name, handler, arguments)
                    else:
                        tool_result = f"未知工具: {function_name}"
                        logger.warning(f"⚠️ [General Agent] 调用了未注册的工具: {function_name}")

                    # 将工具执行的“观察结果”喂给大模型
                    llm_messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": tool_result
                    })
                    audit_logs.append(f"👀 [观察]: {function_name} 返回了 {len(tool_result)} 字符的参考数据。")

            # 🌟 分支 B：大模型认为信息充足，输出最终答案 (Final Answer)
            else:
                logger.info("✅ [General Agent] 证据收集完毕，生成最终处方！")
                audit_logs.append("[General] 证据链闭环，推理结束,生成最终报告。")
                await sse_emit("agent_step", agent="general", phase="synthesis",
                               message=f"✅ 证据链闭环（{len(tool_bb_versions)} 次工具调用），生成最终报告",
                               n_tool_calls=len(tool_bb_versions))
                final_answer = response_message.content or ""
                if _contains_tool_markup(final_answer):
                    logger.warning("[General] final answer contained tool markup, switching to clean synthesis.")
                    final_answer = await _synthesize_from_collected_sources("model_returned_tool_markup")
                # 🔗 加最后一步"综合推演"
                chain_steps.append({
                    "step": len(chain_steps) + 1,
                    "actor": "general.synthesis",
                    "action": "综合 ReAct 证据生成最终答复",
                    "input_summary": f"工具调用 {len([s for s in chain_steps if s['actor']!='general.synthesis'])} 次,引用 {len(chain_refs)} 条",
                    "output_summary": (final_answer or "")[:80],
                    "cited_refs": [r["ref_id"] for r in chain_refs],
                })
                evidence_chain = build_chain(
                    triples=_build_general_meta_triples(entities, query, chain_refs),
                    reasoning_path=chain_steps,
                    refs=dedupe_refs(chain_refs),
                    final_claim=(final_answer or "")[:120],
                    confidence=0.85,  # ReAct 闭环正常完成给 0.85，熔断给更低
                )
                # 🗒️ 黑板：synthesis 终结条目，parents = 所有 tool_call 版本号
                if bb_active:
                    try:
                        await blackboard.append(
                            "general_synthesis",
                            {
                                "preview": (final_answer or "")[:160],
                                "n_tool_calls": len(tool_bb_versions),
                                "n_refs": len(chain_refs),
                                "termination": "CLOSED",
                            },
                            agent_id="general.synthesis",
                            parent_refs=tool_bb_versions or ([bb_parent_version] if bb_parent_version else []),
                        )
                    except Exception as e:
                        logger.warning(f"[General/BB] synthesis append 失败: {e}")
                return final_answer, collected_sources, collected_images, audit_logs, evidence_chain

        except Exception as e:
            logger.error(f"❌ ReAct 循环发生异常: {e}")
            audit_logs.append(f"❌ [异常断点]: {str(e)}")
            break

    # 兜底：如果超出最大循环次数，强行终止并总结
    logger.warning("⚠️ [General Agent] 达到最大思考轮次，强制熔断输出。")
    audit_logs.append("⚠️ [熔断]: 思考轮次超限，系统强制截断推演。")

    if llm_messages and getattr(llm_messages[-1], "tool_calls", None):
        llm_messages.pop()

    fallback_prompt = [
        {"role": "system", "content": "由于系统超时，请基于你刚才收集到的所有线索，立即给出一份最终的医疗指导报告。"}]
    fallback_resp = await client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {
                "role": "system",
                "content": "你是全科医生。禁止输出 DSML、tool_calls、invoke、XML 或内部工具调用标记。请直接输出面向用户的中文回答。",
            },
            {
                "role": "user",
                "content": f"用户问题：{query}\n\n系统达到最大推理轮数，请基于已收集证据给出保守最终回答。证据不足时必须说明。",
            },
        ],
        temperature=0.1
    )
    fallback_answer = fallback_resp.choices[0].message.content
    if _contains_tool_markup(fallback_answer):
        logger.warning("[General] fallback answer contained tool markup, switching to clean synthesis.")
        fallback_answer = await _synthesize_from_collected_sources("max_iterations_tool_markup")

    # 🔗 兜底也构造 evidence_chain（confidence 略低）
    chain_steps.append({
        "step": len(chain_steps) + 1,
        "actor": "general.fallback",
        "action": "熔断兜底（达到最大思考轮次）",
        "input_summary": f"已用工具 {len(chain_steps)} 次",
        "output_summary": (fallback_answer or "")[:80],
        "cited_refs": [r["ref_id"] for r in chain_refs],
    })
    evidence_chain = build_chain(
        triples=_build_general_meta_triples(entities, query, chain_refs),
        reasoning_path=chain_steps,
        refs=dedupe_refs(chain_refs),
        final_claim=(fallback_answer or "")[:120],
        confidence=0.55,  # 熔断置信度调低
    )
    # 🗒️ 黑板：synthesis 兜底条目（与闭环路径区分 termination）
    if bb_active:
        try:
            await blackboard.append(
                "general_synthesis",
                {
                    "preview": (fallback_answer or "")[:160],
                    "n_tool_calls": len(tool_bb_versions),
                    "n_refs": len(chain_refs),
                    "termination": "FALLBACK",
                },
                agent_id="general.fallback",
                parent_refs=tool_bb_versions or ([bb_parent_version] if bb_parent_version else []),
            )
        except Exception as e:
            logger.warning(f"[General/BB] fallback synthesis append 失败: {e}")
    return fallback_answer, collected_sources, collected_images, audit_logs, evidence_chain


# ==========================================
# 🔗 元三元组构造器（保持与 rumor / report / medication 一致的 triple 形态）
# 全科是纯 ReAct，无法做精细的关系抽取。退而求其次：把 entity / query 与
# 命中的 refs 关联成 (head, "参考依据", ref_label)，让前端"关键事实"区块不为空。
# ==========================================
def _build_general_meta_triples(
    entities: List[str],
    query: str,
    chain_refs: List[Dict],
) -> List[Dict]:
    if not chain_refs:
        return []

    # 取强类型 refs 优先（kg > pdf > web）；图谱命中的关联性更强
    type_priority = {"kg": 0, "pdf": 1, "web": 2}
    sorted_refs = sorted(chain_refs, key=lambda r: type_priority.get(r.get("type"), 9))
    top_refs = sorted_refs[:3]  # 最多挂 3 条 ref，避免链路爆炸

    # 选 head：实体存在用实体（最多 3 个），否则用 query 截 24 字
    if entities:
        heads = [e for e in entities[:3] if e and isinstance(e, str)]
    else:
        heads = [query[:24] + ("…" if len(query) > 24 else "")]

    # 置信度按 ref 类型衰减
    type_conf = {"kg": 0.85, "pdf": 0.75, "web": 0.6}

    triples: List[Dict] = []
    for h in heads:
        for ref in top_refs:
            triples.append({
                "head": h,
                "relation": "参考依据",
                "tail": (ref.get("label") or "未命名来源")[:40],
                "tail_type": ref.get("type", "Source").upper(),
                "source_id": ref.get("ref_id"),
                "confidence": type_conf.get(ref.get("type"), 0.6),
            })
            if len(triples) >= 6:
                return triples
    return triples
