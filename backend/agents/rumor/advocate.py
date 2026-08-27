"""
R3 — Advocate Agent
===================
辩护方，为谣言命题寻找支持证据。
复用 MADDx 的 agent_loop + ToolRegistry，保持与 Proposer/Critic/Defender 一致的证据溯源约定。
"""
import json
import logging
from typing import List, Optional, Dict, Any

from core.blackboard import Blackboard
from agents.maddx.agent_loop import run_agent_with_tools
from agents.maddx.tools import ToolRegistry

from .prompts import ADVOCATE_SYSTEM
from .weight_policy import WeightProfile, enrich_evidence_metadata, normalize_source_type

logger = logging.getLogger("Rumor.Advocate")

ADVOCATE_TEMPERATURE = 0.2

FINAL_SCHEMA_HINT = (
    '【再次强调】finish.result 必须包含 "supporting_evidence"（数组，可为空）'
    '和 "final_stance"（字符串）。每条 evidence 必须有非空 evidence_refs。'
)


async def run_advocate(
    bb: Blackboard,
    tools: ToolRegistry,
    claim: str,
    profile: WeightProfile,
    round_idx: int,
    prior_skeptic_objections: Optional[List[dict]] = None,
    social_context_hint: Optional[str] = None,
    parent_refs: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """运行 Advocate 并把结果写入黑板。"""
    # 根据 WeightProfile 个性化注入工具预算建议
    # ⚠️ 不能用 str.format：ADVOCATE_SYSTEM 内含 JSON {...} 字面量会被当占位符解析
    base_system = (
        ADVOCATE_SYSTEM
        .replace("{budget_kg}", str(profile.budget_kg))
        .replace("{budget_rag}", str(profile.budget_rag))
        .replace("{budget_web}", str(profile.budget_web))
    )
    total_budget = profile.budget_kg + profile.budget_rag + profile.budget_web

    objection_hint = ""
    if prior_skeptic_objections:
        objection_hint = (
            "\n\n【Skeptic 上一轮 objection（请针对性回应，用新证据反驳或承认）】\n"
            + json.dumps(prior_skeptic_objections, ensure_ascii=False, indent=2)
        )

    user_prompt = (
        f"【谣言命题】{claim}\n"
        f"【本次权重档】claim_type={profile.claim_type}, "
        f"w=(KG {profile.w_kg}, RAG {profile.w_rag}, Web {profile.w_web}, Social {profile.w_social})"
        + objection_hint
    )
    if social_context_hint:
        user_prompt += (
            "\n\n【已采集的舆情上下文】\n"
            + social_context_hint
            + "\n这些材料只能作为 social 证据，用于说明传播和常见论据，不能单独证明医学有效性。"
        )

    logger.info(
        f"⚖️ [Advocate] round={round_idx} claim_type={profile.claim_type} "
        f"预算=kg{profile.budget_kg}/rag{profile.budget_rag}/web{profile.budget_web}"
    )

    result = await run_agent_with_tools(
        bb=bb,
        tools=tools,
        agent_name="advocate",
        round_idx=round_idx,
        base_system=base_system,
        user_prompt=user_prompt,
        final_schema_hint=FINAL_SCHEMA_HINT,
        temperature=ADVOCATE_TEMPERATURE,
        max_tool_calls=total_budget,
    )

    supporting = result.get("supporting_evidence", []) if isinstance(result, dict) else []
    # 硬约束：过滤无 evidence_refs 的条目
    filtered: List[dict] = []
    dropped = 0
    for e in supporting:
        if not isinstance(e, dict):
            continue
        refs = e.get("evidence_refs") or []
        if not refs:
            dropped += 1
            continue
        st = normalize_source_type(e.get("source_type"), e.get("evidence_type"))
        e["source_type"] = st
        enrich_evidence_metadata(e, stance="support")
        filtered.append(e)

    if dropped:
        logger.warning(f"⚖️ [Advocate] 丢弃 {dropped} 条无证据支持项")

    packet = {
        "claim": claim,
        "supporting_evidence": filtered,
        "final_stance": result.get("final_stance", "") if isinstance(result, dict) else "",
        "round": round_idx,
    }
    await bb.append(
        key="rumor_support",
        value=packet,
        agent_id="advocate",
        parent_refs=parent_refs or [],
    )

    logger.info(
        f"⚖️ [Advocate] round={round_idx} 产出 {len(filtered)} 条支持证据"
    )
    return packet
