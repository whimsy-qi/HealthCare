"""
Defender 节点（D8：独立文件，Tool-Augmented）
===========================================
职责：面对 Critic 的 objections，决定 accept / partial_accept / reject，
并输出修正后的 Top-3 候选。与 Proposer 明确区别：
  - Proposer 是冷启动，Defender 是回应式
  - Defender 必须对每条 objection 给出 stance + action 的结构化响应
  - Defender 的工具预算更大（默认 3），鼓励真正取证反驳
"""
import json
import logging
from typing import List, Optional

from core.blackboard import Blackboard
from core.blackboard_schema import Candidate, Objection, Rebuttal

from .prompts import DEFENDER_SYSTEM, DEFENDER_USER_TEMPLATE
from .agent_loop import run_agent_with_tools, MAX_TOOL_CALLS_PER_TURN
from .tools import ToolRegistry

logger = logging.getLogger("MADDx.Defender")

DEFENDER_MAX_TOOLS = 3
DEFENDER_TEMPERATURE = 0.3

FINAL_SCHEMA_HINT = (
    '【再次强调输出契约】finish.result 必须同时包含 "rebuttals" 和 "candidates" 两个键。'
    'candidates 长度恰好 3。rebuttals 长度 = 输入 objections 长度，逐条回应。'
)


async def run_defender(
    bb: Blackboard,
    tools: ToolRegistry,
    prev_candidates: List[Candidate],
    objections: List[Objection],
    symptoms: list,
    patient_profile: dict,
    round_idx: int,
    parent_refs: Optional[List[int]] = None,
    model_id: str = None,  # 🆕
) -> tuple[List[Candidate], List[Rebuttal]]:
    """
    Defender：工具增强反驳 + 候选修正。返回 (新候选, rebuttals) 并写入黑板。
    """
    # 给 defender system prompt 动态填充 max_tools
    system = DEFENDER_SYSTEM.replace("{max_tools}", str(DEFENDER_MAX_TOOLS))

    user_prompt = DEFENDER_USER_TEMPLATE.format(
        symptoms_json=json.dumps(symptoms, ensure_ascii=False, indent=2),
        profile_json=json.dumps(patient_profile, ensure_ascii=False, indent=2),
        prev_candidates_json=json.dumps(prev_candidates, ensure_ascii=False, indent=2),
        objections_json=json.dumps(objections, ensure_ascii=False, indent=2),
    )

    logger.info(
        f"⚔️ [Defender] round={round_idx} 回应 {len(objections)} 条质疑，"
        f"工具预算 {DEFENDER_MAX_TOOLS}"
    )

    result = await run_agent_with_tools(
        bb=bb,
        tools=tools,
        agent_name="defender",
        round_idx=round_idx,
        base_system=system,
        user_prompt=user_prompt,
        final_schema_hint=FINAL_SCHEMA_HINT,
        temperature=DEFENDER_TEMPERATURE,
        max_tool_calls=DEFENDER_MAX_TOOLS,
        model_id=model_id,
    )

    candidates: List[Candidate] = result.get("candidates", []) if isinstance(result, dict) else []
    rebuttals: List[Rebuttal]  = result.get("rebuttals", []) if isinstance(result, dict) else []

    # ---- 候选后处理：与 Proposer 相同的硬约束 ----
    for c in candidates:
        c.setdefault("evidence_refs", [])
        if not c.get("evidence_refs") and c.get("confidence", 0) > 0.75:
            c["confidence"] = 0.75
    candidates = sorted(candidates, key=lambda c: c.get("confidence", 0), reverse=True)[:3]

    # ---- Rebuttal 后处理：reject 但无证据的视同 accept ----
    fixed_rebuttals: List[Rebuttal] = []
    forced_accept = 0
    for r in rebuttals:
        if not isinstance(r, dict):
            continue
        if r.get("stance") == "reject" and not (r.get("evidence_refs") or []):
            r["stance"] = "accept"
            r["justification"] = (r.get("justification", "") + "（系统：无证据反驳，自动降为 accept）").strip()
            forced_accept += 1
        fixed_rebuttals.append(r)

    if forced_accept:
        logger.warning(f"[Defender] {forced_accept} 条无证据的 reject 被强制改为 accept")

    # ---- 写黑板 ----
    v_cand = await bb.append(
        key="candidate_dx",
        value=candidates,
        agent_id="defender",
        parent_refs=parent_refs or [],
    )
    await bb.append(
        key="rebuttals",
        value=fixed_rebuttals,
        agent_id="defender",
        parent_refs=[v_cand],
    )

    logger.info(
        f"⚔️ [Defender] round={round_idx} 产出 {len(candidates)} 候选 "
        f"+ {len(fixed_rebuttals)} rebuttals: {[c.get('disease') for c in candidates]}"
    )
    return candidates, fixed_rebuttals
