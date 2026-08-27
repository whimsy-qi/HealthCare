"""
Proposer 节点（D8：Tool-Augmented）
=================================
职责：冷启动给出 Top-3 鉴别诊断。允许少量工具取证，但不强制。
与 Defender 的区别：
  - Proposer 不看 objections，只看症状 + profile
  - 工具预算较小（2 次），鼓励直接给出判断
  - D8 之后已从 Defender 拆开，参见 defender.py
"""
import json
import logging
import re
from typing import List, Optional

from core.blackboard import Blackboard
from core.blackboard_schema import Candidate

from .prompts import PROPOSER_SYSTEM, PROPOSER_USER_TEMPLATE
from .agent_loop import run_agent_with_tools
from .tools import ToolRegistry

logger = logging.getLogger("MADDx.Proposer")

PROPOSER_MAX_TOOLS = 2
PROPOSER_TEMPERATURE = 0.3

FINAL_SCHEMA_HINT = """【再次强调输出契约】finish.result 必须包含 "candidates" 键，列表长度恰好为 3。"""


def _norm_name(value) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _format_candidate_priors(candidate_priors: Optional[List[dict]]) -> str:
    if not candidate_priors:
        return ""
    lines = [
        "",
        "【KG prior candidates（MedRAG-style）】",
        "以下候选由知识图谱按症状覆盖度和 IDF 排序得到，只能作为 evidence-backed prior；你可以采纳或反驳。",
        "如果不采纳高分 KG prior，请在 reasoning 中说明临床理由。",
    ]
    for item in candidate_priors[:5]:
        refs = item.get("evidence_refs") or []
        symptoms = item.get("matched_symptoms") or []
        lines.append(
            f"- rank={item.get('rank')} disease={item.get('disease')} "
            f"score={item.get('kg_prior_score')} matched={symptoms} refs={refs[:3]}"
        )
    return "\n".join(lines)


def _merge_candidate_priors(
    candidates: List[Candidate],
    candidate_priors: Optional[List[dict]],
    candidate_prior_ref: Optional[int] = None,
) -> List[Candidate]:
    if not candidate_priors:
        return candidates

    priors_by_name = {
        _norm_name(p.get("disease")): p
        for p in candidate_priors
        if p.get("disease")
    }
    merged_names = set()

    fixed: List[Candidate] = []
    for raw in candidates or []:
        if not isinstance(raw, dict):
            continue
        c = dict(raw)
        key = _norm_name(c.get("disease"))
        prior = priors_by_name.get(key)
        if prior:
            merged_names.add(key)
            c["kg_prior_score"] = prior.get("kg_prior_score", 0.0)
            c["kg_evidence_refs"] = prior.get("evidence_refs", [])
            c["kg_matched_symptoms"] = prior.get("matched_symptoms", [])
            refs = list(c.get("evidence_refs") or [])
            if isinstance(candidate_prior_ref, int) and candidate_prior_ref > 0 and candidate_prior_ref not in refs:
                refs.append(candidate_prior_ref)
            c["evidence_refs"] = refs
            supporting = list(c.get("supporting_symptoms") or [])
            for symptom in prior.get("matched_symptoms") or []:
                if symptom not in supporting:
                    supporting.append(symptom)
            c["supporting_symptoms"] = supporting
        fixed.append(c)

    for prior in candidate_priors:
        if len(fixed) >= 3:
            break
        key = _norm_name(prior.get("disease"))
        if not key or key in merged_names:
            continue
        score = float(prior.get("kg_prior_score") or 0.0)
        refs = [candidate_prior_ref] if isinstance(candidate_prior_ref, int) and candidate_prior_ref > 0 else []
        fixed.append({
            "disease": prior.get("disease"),
            "icd10": None,
            "reasoning": "KG prior candidate from symptom-disease graph; requires debate validation.",
            "supporting_symptoms": list(prior.get("matched_symptoms") or []),
            "confidence": round(min(0.75, max(0.5, score)), 4),
            "evidence_refs": refs,
            "kg_prior_score": score,
            "kg_evidence_refs": prior.get("evidence_refs", []),
            "kg_matched_symptoms": prior.get("matched_symptoms", []),
        })
        merged_names.add(key)

    return fixed


async def run_proposer(
    bb: Blackboard,
    tools: ToolRegistry,
    symptoms: list,
    patient_profile: dict,
    parent_refs: Optional[List[int]] = None,
    model_id: str = None,  # 🆕
    candidate_priors: Optional[List[dict]] = None,
    candidate_prior_ref: Optional[int] = None,
) -> List[Candidate]:
    """
    冷启动 Proposer。返回候选列表并写入黑板。
    """
    user_prompt = PROPOSER_USER_TEMPLATE.format(
        symptoms_json=json.dumps(symptoms, ensure_ascii=False, indent=2),
        profile_json=json.dumps(patient_profile, ensure_ascii=False, indent=2),
    )
    user_prompt += _format_candidate_priors(candidate_priors)

    logger.info(f"🧑‍⚕️ [Proposer] 启动，症状 {len(symptoms)} 项，工具预算 {PROPOSER_MAX_TOOLS}")

    result = await run_agent_with_tools(
        bb=bb,
        tools=tools,
        agent_name="proposer",
        round_idx=0,
        base_system=PROPOSER_SYSTEM,
        user_prompt=user_prompt,
        final_schema_hint=FINAL_SCHEMA_HINT,
        temperature=PROPOSER_TEMPERATURE,
        max_tool_calls=PROPOSER_MAX_TOOLS,
        model_id=model_id,
    )

    candidates: List[Candidate] = result.get("candidates", []) if isinstance(result, dict) else []
    candidates = _merge_candidate_priors(candidates, candidate_priors, candidate_prior_ref)

    # 强制排序 + 截断到 Top-3，并修正非法置信度
    for c in candidates:
        c.setdefault("evidence_refs", [])
        if not c.get("evidence_refs") and c.get("confidence", 0) > 0.75:
            c["confidence"] = 0.75    # 硬约束：无证据候选上限 0.75
    candidates = sorted(candidates, key=lambda c: c.get("confidence", 0), reverse=True)[:3]

    await bb.append(
        key="candidate_dx",
        value=candidates,
        agent_id="proposer",
        parent_refs=parent_refs or [],
    )
    logger.info(f"🧑‍⚕️ [Proposer] 产出 {len(candidates)} 候选: {[c.get('disease') for c in candidates]}")
    return candidates
