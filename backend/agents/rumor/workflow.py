"""
R5 — Rumor Workflow（D9 · CTAEW 总控）
=======================================
串联 Claim Classifier → WeightPolicy → Advocate↔Skeptic 辩论 → Judge。

执行时序：
    Step 0: classify_claim(claim)           → ClassifyResult
    Step 1: resolve_weights(claim_type)     → WeightProfile
    Step 2: Round 1:
              Advocate 取证（w/ 权重预算）  → rumor_support v1
              Skeptic 取证 + 提 objection    → rumor_refute v1
              收敛检查（Rule A/B/C/D）
    Step 3: Round 2（如未收敛）:
              Advocate 回应 objection        → rumor_support v2
              Skeptic 再质疑                  → rumor_refute v2
              收敛检查
    Step 4: Judge 加权裁决 → rumor_judgment

收敛规则（对标 MADDx 四规则 + 谣言特有调整）：
  Rule A  MAX_ROUNDS_REACHED     达到轮数上限
  Rule B  NO_VALID_OBJECTIONS    Skeptic 无有效 objection
  Rule C  NO_NEW_EVIDENCE        本轮双方都未取得新 tool 命中
  Rule D  STRONG_CONSENSUS       belief 已越过阈值且 dissent 低
"""
import logging
from typing import Optional, List, Dict, Any

from core.blackboard import Blackboard
from core.sse_emitter import emit as sse_emit

from .claim_classifier import classify_claim
from .weight_policy import (
    WeightProfile, resolve_composite_weights, resolve_weights,
    compute_weighted_belief, classify_verdict,
    BELIEF_THRESHOLD_TRUE, BELIEF_THRESHOLD_FALSE,
    enrich_evidence_metadata, score_hits_per_source,
)
from .advocate import run_advocate
from .skeptic import run_skeptic
from .judge import run_rumor_judge, _count_hits_per_source
from agents.maddx.tools import ToolRegistry

logger = logging.getLogger("Rumor.Workflow")

MAX_ROUNDS = 2                  # 辩论最大轮数（对标 MADDx）
STRONG_CONSENSUS_DISSENT = 0.25 # Rule D: 强共识要求 dissent 低于此阈值
SOCIAL_CONTEXT_TYPES = {"FOLKLORE", "NOVEL_TREND"}


def _needs_forced_social_context(classify_result: Any) -> bool:
    """民俗/热点类 claim 需要显式采集社交传播证据。"""
    return (
        classify_result.primary in SOCIAL_CONTEXT_TYPES
        or classify_result.secondary in SOCIAL_CONTEXT_TYPES
    )


def _bb_used_network_tool(bb: Blackboard) -> bool:
    for entry in bb.all_by_key("tool_result"):
        tool = ((entry.get("value") or {}).get("tool") or "").strip()
        if tool in ("web_search", "social_search"):
            return True
    return False


def _bb_has_xhs_result(bb: Blackboard) -> bool:
    for entry in bb.all_by_key("tool_result"):
        value = entry.get("value") or {}
        if value.get("tool") != "social_search":
            continue
        for hit in value.get("hits") or []:
            if not isinstance(hit, dict):
                continue
            marker = " ".join(str(hit.get(k, "")) for k in ("ref", "source", "platform", "url"))
            if "xhs" in marker.lower() or "小红书" in marker:
                return True
    return False


def _bb_has_xhs_and_ugc_result(bb: Blackboard) -> bool:
    has_xhs = False
    has_ugc = False
    for entry in bb.all_by_key("tool_result"):
        value = entry.get("value") or {}
        if value.get("tool") != "social_search":
            continue
        for hit in value.get("hits") or []:
            if not isinstance(hit, dict):
                continue
            marker = " ".join(str(hit.get(k, "")) for k in ("ref", "source", "platform", "url", "fetch_method"))
            marker_l = marker.lower()
            has_xhs = has_xhs or ("xhs" in marker_l or "xiaohongshu" in marker_l or "小红书" in marker)
            has_ugc = has_ugc or ("tavily_ugc" in marker_l or "ugc" in marker_l)
    return has_xhs and has_ugc


def _social_context_hint(bb: Blackboard, limit: int = 3) -> str:
    lines: List[str] = []
    for entry in bb.all_by_key("tool_result"):
        value = entry.get("value") or {}
        if value.get("tool") != "social_search":
            continue
        call_ref = value.get("call_ref")
        for hit in (value.get("hits") or [])[:limit]:
            if not isinstance(hit, dict):
                continue
            title = str(hit.get("title") or hit.get("display_title") or hit.get("source") or "舆情结果")[:80]
            body = str(hit.get("post_body") or hit.get("content") or hit.get("text") or "")[:220]
            status = str(hit.get("content_status") or hit.get("fetch_method") or "")
            lines.append(f"- call_ref={call_ref} title={title} status={status} content={body}")
            if len(lines) >= limit:
                return "\n".join(lines)
    return "\n".join(lines)


async def _ensure_xhs_social_context(
    bb: Blackboard,
    tools: ToolRegistry,
    claim: str,
    parent_refs: Optional[List[int]] = None,
    reason: str = "required",
) -> Optional[int]:
    """
    采集小红书舆情并写入黑板。该证据参与 Judge，但 source_type=social，
    权重低于医学/科学证据，避免经验帖压过权威来源。
    """
    if "social_search" not in tools.enabled or _bb_has_xhs_and_ugc_result(bb):
        return None

    await sse_emit(
        "rumor_step", phase="social_context_start",
        message="正在补充小红书与 UGC 舆情证据…",
    )
    result = await tools.invoke(
        tool="social_search",
        args={"query": f"{claim} 小红书 社交平台", "sources": ["xiaohongshu", "ugc"], "top_k": 5},
        caller_agent="rumor_scout",
        caller_round=0,
    )
    hits = result.get("hits") or []
    call_ref = int(result.get("call_ref") or -1)
    if not hits or call_ref <= 0:
        return None

    evidence = [{
        "claim_aspect": "网络传播与经验帖依据",
        "evidence_refs": [call_ref],
        "source_type": "social",
        "evidence_type": "social_opinion",
        "strength": "weak",
        "stance": "neutral",
        "directness": "background",
        "content_status": "snippet",
        "relevance_score": 0.65,
        "summary": "小红书结果只能说明该说法在传播，不能直接证明其医学或科学有效性。",
    }]
    for item in evidence:
        enrich_evidence_metadata(item, stance="neutral")
    version = await bb.append(
        "rumor_social_evidence",
        {"claim": claim, "polarity": "neutral", "stance": "neutral", "reason": reason, "evidence": evidence},
        agent_id="rumor_scout",
        parent_refs=[call_ref] + (parent_refs or []),
    )
    await sse_emit(
        "rumor_step", phase="social_context_done",
        hit_count=len(hits), reason=reason,
        message=f"已补充 {len(hits)} 条小红书舆情结果。",
    )
    return version


# ---------------------------------------------------------------------
# 中间判定：基于当前黑板快照算一次 belief，用于 Rule D
# ---------------------------------------------------------------------

def _interim_belief(bb: Blackboard, profile: WeightProfile) -> tuple:
    """复用 judge 的聚合逻辑做一次中间态裁决（不写黑板）。"""
    from .judge import _gather_from_bb
    support_ev, refute_ev, _ = _gather_from_bb(bb)
    adv_hits = _count_hits_per_source(support_ev)
    skp_hits = _count_hits_per_source(refute_ev)
    adv_scores, _ = score_hits_per_source(support_ev)
    skp_scores, _ = score_hits_per_source(refute_ev)
    belief, _, dissent = compute_weighted_belief(
        adv_scores, skp_scores, profile.weights_dict(),
    )
    return belief, dissent, adv_hits, skp_hits


# ---------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------

async def run_rumor(
    bb: Blackboard,
    claim: str,
    tools: Optional[ToolRegistry] = None,
    enabled_tools: Optional[List[str]] = None,
    uniform_weights: bool = False,
    total_budget: int = 10,
    max_rounds: int = MAX_ROUNDS,
    prior_insights_text: Optional[str] = None,
    classification: Optional[Any] = None,
    risk_assessment: Optional[Any] = None,
    weight_profile: Optional[WeightProfile] = None,
    claim_version: Optional[int] = None,
    classification_version: Optional[int] = None,
    risk_version: Optional[int] = None,
) -> Dict[str, Any]:
    """
    主入口。前端或 graph_engine 传入 claim，返回结构化裁决包。

    Args:
        bb:              会话黑板
        claim:           待验证的谣言命题
        tools:           可选外部 ToolRegistry；None 则内部新建
        enabled_tools:   工具白名单（消融实验用，e.g. [] 关所有工具）
        uniform_weights: True 强制均衡权重（消融 B 组）
        total_budget:    每个 agent 单轮检索总预算
        max_rounds:      辩论最大轮数（消融 D 组可设 0）

    Returns:
        Judge 的最终裁决 packet（含 markdown_report / belief / verdict / ...）
    """
    if tools is None:
        tools = ToolRegistry(bb=bb, enabled=enabled_tools)

    logger.info(f"🚀 [RumorWF] 启动谣言验证：claim='{claim[:40]}...' uniform={uniform_weights}")
    await sse_emit(
        "rumor_step", phase="start",
        message=f"谣言验证启动，工具集={sorted(tools.enabled)}",
        claim=claim,
    )

    # 黑板留痕：命题本体
    if claim_version is not None:
        v_claim = claim_version
    else:
        v_claim = await bb.append("rumor_claim", {"claim": claim}, agent_id="perception")

    # ---------- Step 0: 分类 ----------
    classification_supplied = classification is not None
    if classification is None:
        await sse_emit("rumor_step", phase="classify_start", message="正在识别谣言类型…")
        classify_result = await classify_claim(claim)
        v_cls = await bb.append(
            "rumor_classification",
            classify_result.as_dict(),
            agent_id="claim_classifier",
            parent_refs=[v_claim],
        )
    else:
        classify_result = classification
        v_cls = classification_version
        if v_cls is None:
            v_cls = await bb.append(
                "rumor_classification",
                classify_result.as_dict(),
                agent_id="claim_classifier",
                parent_refs=[v_claim],
            )
    logger.info(
        f"🎯 [RumorWF] 分类={classify_result.primary}({classify_result.primary_confidence:.2f})"
        f" secondary={classify_result.secondary}"
    )
    if not classification_supplied:
        await sse_emit(
            "rumor_step", phase="classify_done",
            claim_type=classify_result.primary,
            confidence=classify_result.primary_confidence,
            secondary=classify_result.secondary,
        )

    # ---------- Step 1: 权重解析 ----------
    profile: WeightProfile = weight_profile or resolve_composite_weights(
        classify_result.primary,
        getattr(classify_result, "secondary", None),
        getattr(classify_result, "primary_confidence", 1.0),
        getattr(classify_result, "secondary_confidence", 0.0),
        total_budget=total_budget,
        uniform=uniform_weights,
    )
    v_prof = await bb.append(
        "rumor_weight_profile",
        profile.as_dict(),
        agent_id="weight_policy",
        parent_refs=[v for v in (v_cls, risk_version) if v is not None],
    )
    logger.info(
        f"📊 [RumorWF] 权重 w=({profile.w_kg},{profile.w_rag},{profile.w_web},social={profile.w_social}) "
        f"预算=kg{profile.budget_kg}/rag{profile.budget_rag}/web{profile.budget_web}"
    )
    await sse_emit(
        "rumor_step", phase="weights_resolved",
        weights={"kg": profile.w_kg, "rag": profile.w_rag, "web": profile.w_web, "social": profile.w_social},
        budgets={"kg": profile.budget_kg, "rag": profile.budget_rag, "web": profile.budget_web},
    )

    social_context_v: Optional[int] = None
    if _needs_forced_social_context(classify_result):
        social_context_v = await _ensure_xhs_social_context(
            bb=bb,
            tools=tools,
            claim=claim,
            parent_refs=[v_cls, v_prof],
            reason="claim_type_or_secondary",
        )

    # ---------- Step 2-3: 辩论循环 ----------
    prior_advocate_stance: Optional[str] = None
    prior_advocate_support: Optional[list] = None
    prior_skeptic_objections: Optional[list] = None

    last_support_v: Optional[int] = None
    last_refute_v: Optional[int] = None

    termination_reason: Optional[str] = None
    rounds_completed = 0

    for round_idx in range(1, max_rounds + 1):
        logger.info(f"─── Rumor Round {round_idx} ───")
        round_start_version = bb.version
        round_start_calls = tools.total_calls
        social_hint = _social_context_hint(bb)

        # -------- Advocate --------
        await sse_emit(
            "rumor_step", phase="advocate_start", round=round_idx,
            message=f"辩护方开始取证（第 {round_idx} 轮）…",
        )
        support_packet = await run_advocate(
            bb=bb, tools=tools,
            claim=claim, profile=profile, round_idx=round_idx,
            prior_skeptic_objections=prior_skeptic_objections,
            social_context_hint=social_hint,
            parent_refs=[v_prof] + ([last_refute_v] if last_refute_v else []),
        )
        last_support_v = bb.latest("rumor_support")["v"]
        prior_advocate_stance = support_packet.get("final_stance", "")
        prior_advocate_support = support_packet.get("supporting_evidence", [])
        await sse_emit(
            "rumor_step", phase="advocate_done", round=round_idx,
            support_count=len(prior_advocate_support or []),
            stance=prior_advocate_stance,
        )

        # -------- Skeptic --------
        await sse_emit(
            "rumor_step", phase="skeptic_start", round=round_idx,
            message=f"质疑方开始反驳并提 objection（第 {round_idx} 轮）…",
        )
        refute_packet = await run_skeptic(
            bb=bb, tools=tools,
            claim=claim, profile=profile, round_idx=round_idx,
            prior_advocate_stance=prior_advocate_stance,
            prior_advocate_support=prior_advocate_support,
            social_context_hint=social_hint,
            parent_refs=[last_support_v],
        )
        last_refute_v = bb.latest("rumor_refute")["v"]
        prior_skeptic_objections = refute_packet.get("objections", [])
        await sse_emit(
            "rumor_step", phase="skeptic_done", round=round_idx,
            refute_count=len(refute_packet.get("refuting_evidence") or []),
            objection_count=len(prior_skeptic_objections or []),
        )

        rounds_completed = round_idx

        # -------- 收敛检查 --------
        # Rule B: Skeptic 无有效 objection → 认为 Advocate 论点未被驳倒
        if len(prior_skeptic_objections or []) == 0:
            termination_reason = "NO_VALID_OBJECTIONS"
            logger.info(f"[RumorWF] 提前收敛: {termination_reason}")
            await sse_emit(
                "rumor_step", phase="converged", reason=termination_reason,
                message="质疑方无有效反驳，Advocate 论点占优",
            )
            break

        # Rule C: 本轮无新 tool 调用
        new_calls = tools.total_calls - round_start_calls
        if new_calls == 0:
            termination_reason = "NO_NEW_EVIDENCE"
            logger.info(f"[RumorWF] 证据耗尽收敛: {termination_reason}")
            await sse_emit(
                "rumor_step", phase="converged", reason=termination_reason,
                message="本轮双方均未取得新证据，证据空间耗尽",
            )
            break

        # Rule D: 强共识（belief 越过阈值且 dissent 低）
        belief_now, dissent_now, adv_h, skp_h = _interim_belief(bb, profile)
        if dissent_now <= STRONG_CONSENSUS_DISSENT and (
            belief_now >= BELIEF_THRESHOLD_TRUE or belief_now <= BELIEF_THRESHOLD_FALSE
        ):
            termination_reason = "STRONG_CONSENSUS"
            logger.info(
                f"[RumorWF] 强共识收敛: belief={belief_now:+.3f} dissent={dissent_now:.3f}"
            )
            await sse_emit(
                "rumor_step", phase="converged", reason=termination_reason,
                belief=round(belief_now, 3), dissent=round(dissent_now, 3),
            )
            break

        # Rule A 在 for 循环自然退出时兜底
        logger.info(
            f"[RumorWF] Round {round_idx} 进行中：belief={belief_now:+.3f} dissent={dissent_now:.3f} "
            f"adv={sum(adv_h.values())} skp={sum(skp_h.values())} 新调用={new_calls}"
        )

    if termination_reason is None:
        termination_reason = "MAX_ROUNDS_REACHED"

    if _bb_used_network_tool(bb) and not _bb_has_xhs_and_ugc_result(bb):
        social_context_v = await _ensure_xhs_social_context(
            bb=bb,
            tools=tools,
            claim=claim,
            parent_refs=[v for v in (last_support_v, last_refute_v, v_prof) if v is not None],
            reason="network_tool_used",
        ) or social_context_v

    # ---------- Step 4: Judge ----------
    await sse_emit(
        "rumor_step", phase="judge_start",
        message="终审法官汇总加权裁决…",
    )
    judgment = await run_rumor_judge(
        bb=bb, claim=claim, profile=profile,
        parent_refs=[v for v in (last_support_v, last_refute_v, social_context_v) if v is not None] or [v_prof],
        prior_insights_text=prior_insights_text,
    )

    # 追加元信息
    judgment["termination_reason"] = termination_reason
    judgment["rounds_completed"] = rounds_completed
    judgment["total_tool_calls"] = tools.total_calls
    judgment["total_evidence_hits"] = tools.total_hits
    judgment["classification"] = classify_result.as_dict()
    if risk_assessment is not None:
        judgment["risk_assessment"] = risk_assessment.as_dict()

    logger.info(
        f"✅ [RumorWF] 结束：verdict={judgment['final_verdict']} "
        f"belief={judgment['belief_score']:+.3f} conf={judgment['confidence']} "
        f"rounds={rounds_completed} calls={tools.total_calls} hits={tools.total_hits} "
        f"reason={termination_reason}"
    )
    await sse_emit(
        "rumor_step", phase="done",
        verdict=judgment["final_verdict"],
        belief=judgment["belief_score"],
        confidence=judgment["confidence"],
        rounds=rounds_completed,
        termination_reason=termination_reason,
    )

    return judgment
