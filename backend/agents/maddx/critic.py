"""
Critic 节点（D8：强制工具取证）
============================
核心设计（对应论文 4.4.1 Evidence-Grounded Critic）：
  - Critic 必须通过工具（kg_query / rag_search / web_search）主动取证后才能反驳
  - evidence_refs 为空的 objection 会在后处理阶段被系统丢弃
  - 若所有候选合理，诚实返回空列表，触发 NO_VALID_OBJECTIONS 终止
"""
import json
import logging
from typing import List, Optional

from core.blackboard import Blackboard
from core.blackboard_schema import Candidate, Objection

from .prompts import CRITIC_SYSTEM, CRITIC_USER_TEMPLATE
from .agent_loop import run_agent_with_tools
from .tools import ToolRegistry

logger = logging.getLogger("MADDx.Critic")

CRITIC_MAX_TOOLS = 3
CRITIC_TEMPERATURE = 0.2       # 低温：要求稳定、可复现的证据引用

FINAL_SCHEMA_HINT = (
    '【再次强调输出契约】finish.result 必须包含 "objections" 键（数组，可为空）。'
    '每条 objection 必须有非空 evidence_refs。'
)


async def run_critic(
    bb: Blackboard,
    tools: ToolRegistry,
    candidates: List[Candidate],
    symptoms: list,
    patient_profile: dict,
    round_idx: int,
    parent_refs: Optional[List[int]] = None,
    model_id: str = None,  # 🆕
) -> List[Objection]:
    """
    Critic：工具增强版。返回经过过滤的 objections 列表并写入黑板。
    """
    user_prompt = CRITIC_USER_TEMPLATE.format(
        candidates_json=json.dumps(candidates, ensure_ascii=False, indent=2),
        symptoms_json=json.dumps(symptoms, ensure_ascii=False, indent=2),
        profile_json=json.dumps(patient_profile, ensure_ascii=False, indent=2),
    )

    logger.info(
        f"🩺 [Critic] round={round_idx} 审查 {len(candidates)} 候选，"
        f"工具预算 {CRITIC_MAX_TOOLS}"
    )

    result = await run_agent_with_tools(
        bb=bb,
        tools=tools,
        agent_name="critic",
        round_idx=round_idx,
        base_system=CRITIC_SYSTEM,
        user_prompt=user_prompt,
        final_schema_hint=FINAL_SCHEMA_HINT,
        temperature=CRITIC_TEMPERATURE,
        max_tool_calls=CRITIC_MAX_TOOLS,
        model_id=model_id,
    )

    raw_objections: List[Objection] = result.get("objections", []) if isinstance(result, dict) else []

    # ---- 后处理：三重过滤 + missing_symptom 禁用 ----
    valid_diseases = {c.get("disease") for c in candidates}
    filtered: List[Objection] = []
    dropped_no_evidence = 0
    dropped_unknown_disease = 0
    dropped_missing_symptom = 0

    for o in raw_objections:
        if not isinstance(o, dict):
            continue
        if o.get("target_disease") not in valid_diseases:
            dropped_unknown_disease += 1
            continue
        refs = o.get("evidence_refs") or []
        if not refs:
            dropped_no_evidence += 1
            continue
        # 硬禁用 missing_symptom：KG 稀疏，症状缺失 ≠ 临床不存在
        if o.get("type") == "missing_symptom":
            dropped_missing_symptom += 1
            continue
        o.setdefault("triggered_by_tool", refs[0] if refs else None)
        filtered.append(o)

    if dropped_no_evidence or dropped_unknown_disease or dropped_missing_symptom:
        logger.warning(
            f"[Critic] 过滤 objection: no_evidence={dropped_no_evidence}, "
            f"unknown_disease={dropped_unknown_disease}, "
            f"missing_symptom={dropped_missing_symptom}"
        )

    await bb.append(
        key="objections",
        value=filtered,
        agent_id="critic",
        parent_refs=parent_refs or [],
    )
    logger.info(f"🩺 [Critic] round={round_idx} 产出 {len(filtered)} 条有效反对")
    for o in filtered:
        logger.info(f"   └─ [{o.get('type')}] {o.get('target_disease')}: {o.get('detail','')[:60]}")
    return filtered
