"""
R3 — Skeptic Agent
==================
质疑方，为谣言命题寻找反驳证据 + 提结构化 objection。
复用 MADDx 的 agent_loop + ToolRegistry。
"""
import json
import logging
from typing import List, Optional, Dict, Any

from core.blackboard import Blackboard
from agents.maddx.agent_loop import run_agent_with_tools
from agents.maddx.tools import ToolRegistry

from .prompts import SKEPTIC_SYSTEM
from .weight_policy import WeightProfile, enrich_evidence_metadata, normalize_source_type

logger = logging.getLogger("Rumor.Skeptic")

SKEPTIC_TEMPERATURE = 0.2

VALID_OBJECTION_TYPES = {
    "MISSING_EVIDENCE", "CONTRADICTORY_EVIDENCE",
    "OVERGENERALIZATION", "TEMPORAL_STALE", "DOSAGE_MISREPRESENT",
}

FINAL_SCHEMA_HINT = (
    '【再次强调】finish.result 必须包含 "refuting_evidence"、"objections"、"final_stance" 三键。'
    '每条 refuting_evidence 与 objection 都必须有非空 evidence_refs。'
)


async def run_skeptic(
    bb: Blackboard,
    tools: ToolRegistry,
    claim: str,
    profile: WeightProfile,
    round_idx: int,
    prior_advocate_stance: Optional[str] = None,
    prior_advocate_support: Optional[List[dict]] = None,
    social_context_hint: Optional[str] = None,
    parent_refs: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """运行 Skeptic 并返回结构化结果（refuting_evidence + objections）。"""
    # ⚠️ 不能用 str.format：SKEPTIC_SYSTEM 内含 JSON {...} 字面量会被当占位符解析
    base_system = (
        SKEPTIC_SYSTEM
        .replace("{budget_kg}", str(profile.budget_kg))
        .replace("{budget_rag}", str(profile.budget_rag))
        .replace("{budget_web}", str(profile.budget_web))
    )
    total_budget = profile.budget_kg + profile.budget_rag + profile.budget_web

    prior_context = ""
    if prior_advocate_stance or prior_advocate_support:
        prior_context = (
            "\n\n【Advocate 本轮立场与支持证据，请针对性质疑】\n"
            f"立场：{prior_advocate_stance or '(无)'}\n"
            f"支持证据：{json.dumps(prior_advocate_support or [], ensure_ascii=False, indent=2)}"
        )

    user_prompt = (
        f"【谣言命题】{claim}\n"
        f"【本次权重档】claim_type={profile.claim_type}, "
        f"w=(KG {profile.w_kg}, RAG {profile.w_rag}, Web {profile.w_web}, Social {profile.w_social})"
        + prior_context
    )
    if social_context_hint:
        user_prompt += (
            "\n\n【已采集的舆情上下文】\n"
            + social_context_hint
            + "\n这些材料只能作为 social 证据，用于说明传播和常见论据，不能单独证明医学有效性。"
        )

    logger.info(
        f"🗡️ [Skeptic] round={round_idx} claim_type={profile.claim_type} "
        f"预算=kg{profile.budget_kg}/rag{profile.budget_rag}/web{profile.budget_web}"
    )

    result = await run_agent_with_tools(
        bb=bb,
        tools=tools,
        agent_name="skeptic",
        round_idx=round_idx,
        base_system=base_system,
        user_prompt=user_prompt,
        final_schema_hint=FINAL_SCHEMA_HINT,
        temperature=SKEPTIC_TEMPERATURE,
        max_tool_calls=total_budget,
    )

    raw_refuting = result.get("refuting_evidence", []) if isinstance(result, dict) else []
    raw_objections = result.get("objections", []) if isinstance(result, dict) else []

    # ---- 过滤 refuting_evidence ----
    refuting: List[dict] = []
    dropped_ev = 0
    for e in raw_refuting:
        if not isinstance(e, dict):
            continue
        refs = e.get("evidence_refs") or []
        if not refs:
            dropped_ev += 1
            continue
        st = normalize_source_type(e.get("source_type"), e.get("evidence_type"))
        e["source_type"] = st
        enrich_evidence_metadata(e, stance="refute")
        refuting.append(e)

    # ---- 过滤 objections ----
    objections: List[dict] = []
    dropped_obj = 0
    for o in raw_objections:
        if not isinstance(o, dict):
            continue
        otype = (o.get("type") or "").strip().upper()
        if otype not in VALID_OBJECTION_TYPES:
            dropped_obj += 1
            continue
        refs = o.get("evidence_refs") or []
        if not refs:
            dropped_obj += 1
            continue
        o["type"] = otype
        o.setdefault("triggered_by_tool", refs[0])
        objections.append(o)

    if dropped_ev or dropped_obj:
        logger.warning(
            f"🗡️ [Skeptic] 过滤：refuting_dropped={dropped_ev}, objections_dropped={dropped_obj}"
        )

    packet = {
        "claim": claim,
        "refuting_evidence": refuting,
        "objections": objections,
        "final_stance": result.get("final_stance", "") if isinstance(result, dict) else "",
        "round": round_idx,
    }
    await bb.append(
        key="rumor_refute",
        value=packet,
        agent_id="skeptic",
        parent_refs=parent_refs or [],
    )

    logger.info(
        f"🗡️ [Skeptic] round={round_idx} 产出 {len(refuting)} 反驳证据 + {len(objections)} 有效 objection"
    )
    for o in objections:
        logger.info(f"   └─ [{o['type']}] {o.get('target_aspect','')[:40]}: {o.get('description','')[:50]}")
    return packet
