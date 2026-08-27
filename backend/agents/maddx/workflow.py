"""
MADDx 主控流程（D8：Tool-Augmented Debate）
==========================================
串联 Proposer → (Critic → Defender)×N → Moderator，实现 2 轮辩论协议。

D8 关键变化：
  - 删除 kg_neighbors_provider / rag_chunks_provider（静态预取被废弃）
  - 新增 ToolRegistry 依赖注入，供每个 agent 在自己回合内自主取证
  - Defender 拆到独立文件 defender.py
  - 新增 Rule 4: NO_NEW_EVIDENCE
      连续 1 轮（Critic + Defender 合计）0 次非缓存工具命中 → 证据空间耗尽，提前终止

执行时序：
    Round 0: Proposer 冷启动（可选少量工具）→ candidate_dx_v0
    Round 1: Critic(v0) 主动取证 → objections_1
             if objections_1 == []:         收敛 (NO_VALID_OBJECTIONS)
             Defender(v0, obj1) 取证反驳 → candidate_dx_v1
             check_convergence + Rule 4 检查
    Round 2: ...同上
    Moderator → final_diagnosis
"""
import logging
from typing import Optional

from core.blackboard import Blackboard
from core.blackboard_schema import FinalDiagnosis
from core.sse_emitter import emit as sse_emit

from .proposer import run_proposer
from .critic import run_critic
from .defender import run_defender
from .moderator import run_moderator, check_convergence, MAX_ROUNDS
from .tools import ToolRegistry

logger = logging.getLogger("MADDx.Workflow")


async def run_maddx(
    bb: Blackboard,
    symptoms: list,
    patient_profile: dict,
    tools: Optional[ToolRegistry] = None,
    enabled_tools: Optional[list] = None,
    agent_models: Optional[list] = None,  # 🆕 [proposer_model, critic_model, moderator_model]
    candidate_ranking: Optional[dict] = None,
) -> FinalDiagnosis:
    """
    主入口。symptom_agent 完成槽位填充后调用此函数。

    Args:
        bb: 会话黑板实例
        symptoms: 标准化症状列表
        patient_profile: 患者档案
        tools: 可选，外部传入的 ToolRegistry（消融实验时可禁用工具）。
               如果为 None，内部按 enabled_tools 新建。
        enabled_tools: 可选，工具白名单，如 ["kg_query", "rag_search"]。
                       None = 全部启用。用于消融对照。
    """
    if tools is None:
        tools = ToolRegistry(bb=bb, enabled=enabled_tools)

    # 🆕 跨模型辩论: agent_models = [proposer, critic, defender, moderator]
    if agent_models is None:
        agent_models = [None, None, None, None]  # 默认全部用 REASONING_MODEL
    pm, cm, dm, mm = agent_models[0] if len(agent_models) > 0 else None, \
                     agent_models[1] if len(agent_models) > 1 else None, \
                     agent_models[2] if len(agent_models) > 2 else None, \
                     agent_models[3] if len(agent_models) > 3 else None

    logger.info(
        f"🚀 [MADDx] 启动辩论：症状数={len(symptoms)}，启用工具={sorted(tools.enabled)}"
    )
    await sse_emit(
        "maddx_step", phase="start",
        message=f"多智能体鉴别诊断启动（症状 {len(symptoms)} 项，工具集 {sorted(tools.enabled)}）",
    )

    # 记录输入到黑板（感知层 stub）
    v_sym = await bb.append("symptoms", symptoms, agent_id="perception")
    v_prof = await bb.append("patient_profile", patient_profile, agent_id="perception")
    candidate_priors = []
    candidate_prior_ref = None
    if isinstance(candidate_ranking, dict):
        candidate_priors = candidate_ranking.get("candidates") or []
        if candidate_priors:
            candidate_prior_ref = await bb.append(
                "medrag_candidate_ranking",
                candidate_ranking,
                agent_id="medrag.kg_ranker",
                parent_refs=[v_sym],
            )
            await sse_emit(
                "maddx_step",
                phase="medrag_ranker_done",
                candidate_count=len(candidate_priors),
                top_candidates=candidate_priors[:3],
                message=f"KG 候选疾病排序完成：Top-{min(3, len(candidate_priors))} 已作为 MADDx prior",
            )

    # ---------- Round 0: Proposer 冷启动 ----------
    await sse_emit(
        "maddx_step", phase="proposer_start", round=0,
        message="住院医师正在进行初步鉴别诊断…",
    )
    candidates = await run_proposer(
        bb, tools=tools,
        symptoms=symptoms,
        patient_profile=patient_profile,
        parent_refs=[v for v in (v_sym, v_prof, candidate_prior_ref) if v],
        model_id=pm,
        candidate_priors=candidate_priors,
        candidate_prior_ref=candidate_prior_ref,
    )
    candidate_history = [candidates]
    last_candidate_v = bb.latest("candidate_dx")["v"]
    await sse_emit("maddx_step", phase="proposer_done", round=0, candidates=candidates)

    termination_reason = None
    rounds_completed = 0

    # ---------- 辩论循环 ----------
    for round_idx in range(1, MAX_ROUNDS + 1):
        logger.info(f"─── Round {round_idx} ───")

        round_start_version = bb.version
        round_start_calls = tools.total_calls

        # ---------- Critic ----------
        await sse_emit(
            "maddx_step", phase="critic_start", round=round_idx,
            message=f"主治医师开始审查并主动取证（第 {round_idx} 轮）…",
        )
        objections = await run_critic(
            bb, tools=tools,
            candidates=candidates,
            symptoms=symptoms,
            patient_profile=patient_profile,
            round_idx=round_idx,
            parent_refs=[last_candidate_v],
            model_id=cm,
        )
        last_critic_v = bb.latest("objections")["v"]
        await sse_emit("maddx_step", phase="critic_done", round=round_idx, objections=objections)

        # 早停：Critic 无反驳
        converged, reason = check_convergence(candidate_history, objections, round_idx - 1)
        if converged and reason == "NO_VALID_OBJECTIONS":
            termination_reason = reason
            rounds_completed = round_idx - 1
            logger.info(f"[Workflow] 提前收敛: {reason}")
            await sse_emit(
                "maddx_step", phase="converged", reason=reason,
                message="主治医师无有效质疑，提前达成共识",
            )
            break

        # ---------- Defender ----------
        await sse_emit(
            "maddx_step", phase="defender_start", round=round_idx,
            message=f"住院医师正在据理反驳（第 {round_idx} 轮）…",
        )
        candidates, _rebuttals = await run_defender(
            bb, tools=tools,
            prev_candidates=candidate_history[-1],
            objections=objections,
            symptoms=symptoms,
            patient_profile=patient_profile,
            round_idx=round_idx,
            parent_refs=[last_candidate_v, last_critic_v],
            model_id=dm,
        )
        candidate_history.append(candidates)
        last_candidate_v = bb.latest("candidate_dx")["v"]
        await sse_emit("maddx_step", phase="defender_done", round=round_idx, candidates=candidates)

        # ---------- 收敛判断 ----------
        # Rule 4 (D8 新增): NO_NEW_EVIDENCE
        # 本轮 Critic + Defender 是否取得了任何非缓存的新证据？
        new_calls_this_round = tools.total_calls - round_start_calls
        tool_results_this_round = bb.count_since("tool_result", round_start_version)
        logger.info(
            f"[Workflow] Round {round_idx} 取证统计：新调用={new_calls_this_round}, "
            f"tool_result 写入={tool_results_this_round}"
        )

        if new_calls_this_round == 0 and round_idx >= 1:
            # Critic 和 Defender 都没查任何东西 → 证据空间耗尽
            termination_reason = "NO_NEW_EVIDENCE"
            rounds_completed = round_idx
            logger.info(f"[Workflow] 证据耗尽收敛: {termination_reason}")
            await sse_emit(
                "maddx_step", phase="converged", reason=termination_reason,
                message="本轮无新证据被引入，证据空间耗尽，辩论提前终止",
            )
            break

        # Rule 1/2: 常规收敛
        converged, reason = check_convergence(candidate_history, objections, round_idx)
        rounds_completed = round_idx
        if converged:
            termination_reason = reason
            logger.info(f"[Workflow] 收敛: {reason}")
            await sse_emit(
                "maddx_step", phase="converged", reason=reason,
                message=f"辩论收敛：{reason}",
            )
            break

    if termination_reason is None:
        termination_reason = "MAX_ROUNDS_REACHED"

    # ---------- Moderator 收尾 ----------
    await sse_emit(
        "maddx_step", phase="moderator_start",
        message="科主任正在综合辩论结论、撰写会诊意见…",
    )
    debate_history = {
        "candidate_versions": candidate_history,
        "candidate_priors":   candidate_priors,
        "objections_log":     [e["value"] for e in bb.all_by_key("objections")],
        "rebuttals_log":      [e["value"] for e in bb.all_by_key("rebuttals")],
        "tool_calls":         [e["value"] for e in bb.all_by_key("tool_call")],
        "tool_results":       [
            {k: v for k, v in e["value"].items() if k != "hits"}     # 摘要：不喂全部 hits 到 Moderator
            for e in bb.all_by_key("tool_result")
        ],
    }
    report = await run_moderator(
        bb,
        final_candidates=candidates,
        debate_history=debate_history,
        termination_reason=termination_reason,
        rounds_used=rounds_completed,
        parent_refs=[last_candidate_v],
        model_id=mm,
    )

    # D8 新增：把全局工具统计塞到 report 里（供前端 & 论文统计）
    if isinstance(report, dict):
        report["total_tool_calls"] = tools.total_calls
        report["total_evidence_hits"] = tools.total_hits
        if isinstance(candidate_ranking, dict):
            report["medrag_candidate_count"] = len(candidate_priors)
            report["medrag_ranker_stats"] = candidate_ranking.get("stats", {})

    logger.info(
        f"✅ [MADDx] 辩论结束，{rounds_completed} 轮，"
        f"共调用工具 {tools.total_calls} 次，命中证据 {tools.total_hits} 条"
    )
    await sse_emit("maddx_step", phase="moderator_done", report=report)
    return report
