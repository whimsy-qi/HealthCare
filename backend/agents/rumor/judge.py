"""
R4 — Rumor Judge (Weighted Adjudication)
=========================================
终审法官：从黑板汇总 Advocate/Skeptic 证据，按 WeightProfile 加权计算
belief / dissent / confidence，再让 LLM 把数值包装成 Markdown 报告。

核心原则：**数值裁决由 Python 精确计算**（可复现、可审计），
LLM 只负责把数值 + 证据摘要转成自然语言，不允许改写 belief/verdict。
"""
import json
import logging
from typing import Dict, Any, List, Optional, Tuple

from core.blackboard import Blackboard
from core.llm_client import shared_client as client, REASONING_MODEL
from core.evidence import build_chain, dedupe_refs  # 🔗 证据链 schema

from .prompts import JUDGE_SYSTEM
from .weight_policy import (
    WeightProfile, Source,
    compute_weighted_belief, classify_verdict, calibrated_confidence,
    evidence_sufficiency, enrich_evidence_metadata, normalize_source_type,
    score_hits_per_source,
)

logger = logging.getLogger("Rumor.Judge")

MODEL = REASONING_MODEL
JUDGE_TEMPERATURE = 0.3


# ---------------------------------------------------------------------
# 证据汇总
# ---------------------------------------------------------------------

def _count_hits_per_source(evidence_list: List[dict]) -> Dict[str, int]:
    """按 source_type 分桶计数（kg/rag/web/social）。"""
    buckets = {"kg": 0, "rag": 0, "web": 0, "social": 0, "unknown": 0}
    for e in evidence_list:
        if not isinstance(e, dict):
            continue
        st = normalize_source_type(e.get("source_type"), e.get("evidence_type"))
        buckets[st] += 1
    return buckets


def _gather_from_bb(bb: Blackboard) -> Tuple[List[dict], List[dict], List[dict]]:
    """
    从黑板聚合：
      - 所有 rumor_support 条目里的 supporting_evidence
      - 所有 rumor_refute 条目里的 refuting_evidence
      - 所有 rumor_refute 条目里的 objections
    """
    support_ev: List[dict] = []
    refute_ev:  List[dict] = []
    objections: List[dict] = []

    for e in bb.all_by_key("rumor_support"):
        val = e.get("value") or {}
        support_ev.extend(val.get("supporting_evidence") or [])
    for e in bb.all_by_key("rumor_refute"):
        val = e.get("value") or {}
        refute_ev.extend(val.get("refuting_evidence") or [])
        objections.extend(val.get("objections") or [])
    for e in bb.all_by_key("rumor_social_evidence"):
        val = e.get("value") or {}
        polarity = (val.get("polarity") or val.get("stance") or "neutral").strip().lower()
        items = val.get("evidence") or []
        if polarity == "support":
            for item in items:
                if isinstance(item, dict):
                    enrich_evidence_metadata(item, stance="support")
            support_ev.extend(items)
        elif polarity == "refute":
            for item in items:
                if isinstance(item, dict):
                    enrich_evidence_metadata(item, stance="refute")
            refute_ev.extend(items)
        else:
            for item in items:
                if isinstance(item, dict):
                    enrich_evidence_metadata(item, stance="neutral")

    return support_ev, refute_ev, objections


# ---------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------

async def run_rumor_judge(
    bb: Blackboard,
    claim: str,
    profile: WeightProfile,
    parent_refs: Optional[List[int]] = None,
    prior_insights_text: Optional[str] = None,
) -> Dict[str, Any]:
    """
    终审：读黑板 → 加权裁决 → LLM 包装 Markdown 报告。

    Returns:
        {
          "claim": str,
          "claim_type": str,
          "belief_score": float,
          "dissent_score": float,
          "confidence": float,
          "final_verdict": str,
          "per_source_net": {kg, rag, web},
          "advocate_hits": {...},
          "skeptic_hits": {...},
          "final_markdown_report": str,
          "debate_highlights": str,
        }
    """
    support_ev, refute_ev, objections = _gather_from_bb(bb)

    adv_hits = _count_hits_per_source(support_ev)
    skp_hits = _count_hits_per_source(refute_ev)
    adv_scores, adv_quality = score_hits_per_source(support_ev)
    skp_scores, skp_quality = score_hits_per_source(refute_ev)

    belief, per_source_net, dissent = compute_weighted_belief(
        advocate_hits_per_source=adv_scores,
        skeptic_hits_per_source=skp_scores,
        weights=profile.weights_dict(),
    )

    total_hits = sum(adv_hits.values()) + sum(skp_hits.values())
    total_budget = profile.budget_kg + profile.budget_rag + profile.budget_web
    # 旧线性 coverage 保留仅作 telemetry（观察证据利用率），不再进 confidence
    evidence_coverage = min(1.0, total_hits / max(1, total_budget * 2))
    weighted_support = sum(
        profile.weights_dict().get(src, 0.0) * adv_scores.get(src, 0.0)
        for src in ("kg", "rag", "web", "social")
    )
    weighted_refute = sum(
        profile.weights_dict().get(src, 0.0) * skp_scores.get(src, 0.0)
        for src in ("kg", "rag", "web", "social")
    )
    weighted_total = weighted_support + weighted_refute
    # 新：质量加权后的饱和式 sufficiency，避免纯数量堆叠影响置信度
    sufficiency = evidence_sufficiency(weighted_total, k=1.2)

    verdict = classify_verdict(belief, dissent)
    confidence = calibrated_confidence(belief, dissent, sufficiency)

    logger.info(
        f"⚖️ [RumorJudge] belief={belief:+.3f} dissent={dissent:.3f} "
        f"coverage={evidence_coverage:.2f} → {verdict} (conf={confidence:.2f})"
    )
    logger.info(
        f"   adv_hits={adv_hits} skp_hits={skp_hits} "
        f"weighted=({weighted_support:.3f},{weighted_refute:.3f}) objections={len(objections)}"
    )

    source_breakdown = {
        src: {
            "support_count": adv_hits.get(src, 0),
            "refute_count": skp_hits.get(src, 0),
            "support_score": round(adv_scores.get(src, 0.0), 4),
            "refute_score": round(skp_scores.get(src, 0.0), 4),
            "net": round(per_source_net.get(src, 0.0), 3),
            "weight": profile.weights_dict().get(src, 0.0),
        }
        for src in ("kg", "rag", "web", "social", "unknown")
    }
    evidence_quality_summary = {
        "support": adv_quality,
        "refute": skp_quality,
        "weighted_support": round(weighted_support, 4),
        "weighted_refute": round(weighted_refute, 4),
        "weighted_total": round(weighted_total, 4),
    }

    # ---- 构造给 LLM 的只读数值上下文 ----
    evidence_summary = {
        "supporting_evidence_count": len(support_ev),
        "refuting_evidence_count":   len(refute_ev),
        "objection_count":           len(objections),
        "advocate_hits_by_source":   adv_hits,
        "skeptic_hits_by_source":    skp_hits,
        "weighted_support":          round(weighted_support, 4),
        "weighted_refute":           round(weighted_refute, 4),
        "source_breakdown":          source_breakdown,
        "evidence_quality_summary":  evidence_quality_summary,
        # 只抽摘要给 LLM 看，不给完整数据免得幻觉改写
        "top_supporting_summaries":  [e.get("summary", "") for e in support_ev[:5]],
        "top_refuting_summaries":    [e.get("summary", "") for e in refute_ev[:5]],
        "objection_summaries": [
            {"type": o.get("type"), "desc": o.get("description", "")}
            for o in objections[:5]
        ],
    }

    # 🧠 见解知识库注入（仅参考、不替代当前证据；判官仍应以本次证据为准）
    if prior_insights_text:
        # 截短到 1500 字以内避免冲淡当次证据
        prior_text = prior_insights_text[:1500]
    else:
        prior_text = ""

    user_payload = {
        "claim": claim,
        "claim_type": profile.claim_type,
        "belief_score": round(belief, 3),
        "dissent_score": round(dissent, 3),
        "final_verdict": verdict,
        "confidence": confidence,
        "prior_similar_cases_for_reference_only": prior_text,
        "weights": {"kg": profile.w_kg, "rag": profile.w_rag, "web": profile.w_web, "social": profile.w_social},
        "evidence_summary": evidence_summary,
    }

    # ---- LLM 包装 Markdown ----
    markdown_report = ""
    debate_highlights = ""
    try:
        resp = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, indent=2)},
            ],
            temperature=JUDGE_TEMPERATURE,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        markdown_report = str(data.get("final_markdown_report", "")).strip()
        debate_highlights = str(data.get("debate_highlights", "")).strip()
    except Exception as e:
        logger.error(f"[RumorJudge] LLM 包装失败，走本地兜底模板: {e}")
        markdown_report = _fallback_markdown(claim, profile, verdict, confidence, belief, dissent)
        debate_highlights = f"Advocate 命中 {sum(adv_hits.values())} 条；Skeptic 命中 {sum(skp_hits.values())} 条，提 {len(objections)} 个 objection。"

    # 🔗 ====== 构造统一 EvidenceChain ======
    # advocate / skeptic / judge 三步推理路径 + refs 池
    chain_refs: List[Dict] = []
    chain_triples: List[Dict] = []

    def _ev_to_ref(ev: dict, polarity: str, idx: int) -> Optional[Dict]:
        if not isinstance(ev, dict):
            return None
        st = (ev.get("source_type") or "").strip().lower() or "web"
        url = ev.get("url") or ev.get("link") or ""
        title = (ev.get("title") or ev.get("source") or f"{polarity}-evidence-{idx}")[:80]
        snippet = (ev.get("summary") or ev.get("snippet") or ev.get("content") or "")[:300]
        if st == "kg":
            ref_id = f"kg:{abs(hash(title + snippet[:64])) % 10**8}"
            ref_type, locator = "kg", {"node": ev.get("node_id") or title}
        elif st == "rag":
            doc = ev.get("doc") or ev.get("collection") or "guideline"
            ref_id = f"doc:{doc}#{abs(hash(snippet[:64])) % 10**8}"
            ref_type, locator = "pdf", {"doc": doc}
            if ev.get("page") is not None:
                locator["page"] = ev["page"]
        else:
            ref_id = f"web:{abs(hash(url or title)) % 10**10}"
            ref_type, locator = "web", {"url": url} if url else {"label": title}
        return {
            "ref_id": ref_id, "type": ref_type, "label": title,
            "locator": locator, "snippet": snippet or None,
        }

    sup_refs = [r for i, e in enumerate(support_ev) if (r := _ev_to_ref(e, "support", i))]
    ref_refs = [r for i, e in enumerate(refute_ev) if (r := _ev_to_ref(e, "refute", i))]
    chain_refs.extend(sup_refs)
    chain_refs.extend(ref_refs)

    # 三元组：基于关系词表 — claim → 声称功效；判决后 → 实际事实
    if support_ev:
        chain_triples.append({
            "head": claim[:50], "relation": "声称功效",
            "tail": "（用户主张）", "source_id": sup_refs[0]["ref_id"] if sup_refs else "",
            "confidence": float(belief if belief > 0 else 0.0),
        })
    if verdict in ("谣言", "误导", "属实"):
        chain_triples.append({
            "head": claim[:50], "relation": "实际事实",
            "tail": verdict, "source_id": (ref_refs[0]["ref_id"] if ref_refs else (sup_refs[0]["ref_id"] if sup_refs else "")),
            "confidence": float(confidence),
        })

    chain_path = [
        {
            "step": 1, "actor": "rumor_advocate",
            "action": "搜集支持性证据",
            "input_summary": claim[:80],
            "output_summary": f"召回 {len(support_ev)} 条支持证据 (KG={adv_hits.get('kg',0)} RAG={adv_hits.get('rag',0)} WEB={adv_hits.get('web',0)} SOCIAL={adv_hits.get('social',0)})",
            "cited_refs": [r["ref_id"] for r in sup_refs],
        },
        {
            "step": 2, "actor": "rumor_skeptic",
            "action": "搜集反驳证据 + 提 objection",
            "input_summary": "对照 claim 找反例 / 局限",
            "output_summary": f"召回 {len(refute_ev)} 条反驳 + {len(objections)} 个 objection",
            "cited_refs": [r["ref_id"] for r in ref_refs],
        },
        {
            "step": 3, "actor": "rumor_judge",
            "action": "加权裁决（CTAEW）",
            "input_summary": f"belief={belief:+.3f} dissent={dissent:.3f} sufficiency={sufficiency:.3f}",
            "output_summary": f"verdict={verdict} confidence={confidence:.2f}",
            "cited_refs": [r["ref_id"] for r in (sup_refs + ref_refs)],
        },
    ]

    final_claim_text = f"针对「{claim[:40]}」的判决：{verdict}（置信度 {confidence:.2f}）"
    evidence_chain = build_chain(
        triples=chain_triples,
        reasoning_path=chain_path,
        refs=dedupe_refs(chain_refs),
        final_claim=final_claim_text,
        confidence=float(confidence),
    )

    packet = {
        "claim": claim,
        "claim_type": profile.claim_type,
        "belief_score": round(belief, 3),
        "dissent_score": round(dissent, 3),
        "confidence": confidence,
        "final_verdict": verdict,
        "per_source_net": {k: round(v, 3) for k, v in per_source_net.items()},
        "advocate_hits": adv_hits,
        "skeptic_hits": skp_hits,
        "weighted_support": round(weighted_support, 4),
        "weighted_refute": round(weighted_refute, 4),
        "source_breakdown": source_breakdown,
        "evidence_quality_summary": evidence_quality_summary,
        "evidence_coverage": round(evidence_coverage, 3),
        "evidence_sufficiency": round(sufficiency, 3),
        "weights": {"kg": profile.w_kg, "rag": profile.w_rag, "web": profile.w_web, "social": profile.w_social},
        "final_markdown_report": markdown_report,
        "debate_highlights": debate_highlights,
        "evidence_chain": evidence_chain,  # 🔗 packet 里也带一份，便于上游直接消费
    }

    await bb.append(
        key="rumor_judgment",
        value=packet,
        agent_id="rumor_judge",
        parent_refs=parent_refs or [],
    )
    return packet


# ---------------------------------------------------------------------
# 本地兜底模板（LLM 挂掉也要能出报告）
# ---------------------------------------------------------------------

_VERDICT_EMOJI = {
    "属实":     "✅ 经核实属实",
    "谣言":     "❌ 纯属谣言",
    "误导":     "⚠️ 存在误导/片面",
    "尚无定论": "❓ 尚无定论",
}


def _fallback_markdown(
    claim: str, profile: WeightProfile, verdict: str,
    confidence: float, belief: float, dissent: float,
) -> str:
    head = _VERDICT_EMOJI.get(verdict, "❓ 尚无定论")
    return (
        "### 核查结论\n\n"
        f"针对「{claim}」，当前结论是：**{head}**。\n\n"
        "这份判断来自本轮可检索到的支持与反驳材料。社交平台内容只能说明相关说法在传播，"
        "不能单独证明医学或科学结论。\n\n"
        "如果这个问题涉及症状、用药或疾病风险，建议结合自身情况咨询医生。\n\n"
        "> *以上为健康科普内容，仅供了解参考，不构成临床建议。如有不适请就医。*\n"
    )
