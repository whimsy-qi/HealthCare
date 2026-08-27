"""
MADDx ↔ 现有 symptom 流程 的适配层
=================================
职责：
1. 把 symptom_agent 的 slot-filling 输出 (Dict[str, str]) 转成 MADDx 的结构化 symptoms
2. 构建简单的 evidence provider，复用 symptom_controller 已经检索到的 KG 路径 + 本地指南
3. 把 MADDx 的 FinalDiagnosis 结构格式化为现有前端期望的 Markdown 报告
"""
import logging
import json
import os
from typing import Dict, List, Optional

from core.blackboard import Blackboard
from core.blackboard_schema import FinalDiagnosis
from core.sse_emitter import set_collector, reset_collector
from .workflow import run_maddx
from .tools import ToolRegistry
from .kg_candidate_ranker import rank_disease_candidates

logger = logging.getLogger("MADDx.Integration")


def _medrag_ranker_enabled() -> bool:
    return os.getenv("USE_MEDRAG_RANKER", "true").lower() not in {"0", "false", "no", "off"}


# ==================== 输入转换 ====================

def slots_to_symptoms(slots: Dict[str, str]) -> List[dict]:
    """
    把 slot-filling 的 {槽位名: 值} 转成结构化症状列表。
    Slot 的语义对齐规则（对应 symptom_agent 的槽位定义）：
      - "主诉症状" / "伴随症状" → 拆为独立 Symptom 条目
      - "持续时间" → duration_days（粗粒度换算）
      - "严重程度" → severity
      - 其他描述性槽位 → 附加到最近一个 Symptom 的 name 上

    为保证健壮性，本函数采用宽松策略：凡是 value 非空的槽位都转成一条 Symptom。
    """
    symptoms: List[dict] = []
    severity_guess = "moderate"
    duration_days = None

    for k, v in slots.items():
        if not v or not isinstance(v, str):
            continue
        v = v.strip()
        if not v:
            continue

        # 粗略提取 severity 和 duration
        if "严重" in k or "程度" in k:
            vl = v.lower()
            if any(x in vl for x in ["轻", "mild"]):
                severity_guess = "mild"
            elif any(x in vl for x in ["重", "severe", "剧烈"]):
                severity_guess = "severe"
            else:
                severity_guess = "moderate"
            continue
        if "时间" in k or "病程" in k or "持续" in k:
            # 简单解析："3天" "1周" "2个月"
            try:
                import re
                m = re.search(r"(\d+)\s*个?\s*(天|日|周|月|年)", v)
                if m:
                    num = int(m.group(1))
                    unit = m.group(2)
                    factor = {"天": 1, "日": 1, "周": 7, "月": 30, "年": 365}[unit]
                    duration_days = num * factor
            except Exception:
                pass
            continue

        symptoms.append({
            "name": f"{k}: {v}" if k else v,
            "duration_days": None,
            "severity": "moderate",
        })

    # 把全局 severity / duration 回填到所有条目
    for s in symptoms:
        s["severity"] = severity_guess
        if duration_days is not None:
            s["duration_days"] = duration_days

    if not symptoms:
        # Fallback：至少留一条，避免 MADDx 收到空输入
        symptoms.append({"name": json.dumps(slots, ensure_ascii=False), "duration_days": None, "severity": "moderate"})

    return symptoms


def build_tool_registry(
    bb: Blackboard,
    enabled_tools: Optional[List[str]] = None,
) -> ToolRegistry:
    """
    D8：构造 ToolRegistry。替代旧版 build_evidence_providers 的静态快照方案。

    Args:
        bb: 当前会话 Blackboard
        enabled_tools: 可选白名单，如 ["kg_query","rag_search"]（不含 web_search 省延迟）。
                       None = 全部 3 个工具都启用。
                       消融实验时用 [] 可禁用所有工具（MADDx-static 对照组）。

    symptom_controller 已经预取的 kg_context / local_guide_context **不再被传给 MADDx**——
    每个 agent 会在自己的回合内按需取证，而不是消费上游预取的快照。
    """
    return ToolRegistry(bb=bb, enabled=enabled_tools)


# ==================== 输出格式化 ====================

def format_maddx_report_as_markdown(
    report: FinalDiagnosis,
    vision_context: Optional[str] = None,
    med_precheck: Optional[dict] = None,
) -> str:
    """
    把 MADDx 的 FinalDiagnosis 结构化输出渲染成前端期望的 Markdown 报告。
    保持与旧版 generate_final_diagnosis 输出结构一致，避免前端改动。
    """
    rounds = report.get("rounds_used", 0)
    reason = report.get("termination_reason", "")
    narrative = (report.get("narrative") or "").strip()

    lines = []

    if narrative:
        # 首选路径：Moderator 已产出科主任口吻的叙事 markdown，直接用
        lines.append(narrative)
    else:
        # 降级路径：Moderator 没给 narrative（JSON 解析失败等），用结构化字段拼一份
        primary = report.get("primary_dx", "未能确诊")
        confidence = report.get("confidence", 0.0)
        differentials = report.get("differential_dx", []) or []
        evidence = report.get("supporting_evidence", []) or []
        tests = report.get("recommended_tests", []) or []

        lines.append("### 🩺 全科综合会诊研判报告")
        lines.append("")
        lines.append(f"根据您的症状描述，经过多轮鉴别分析，目前倾向考虑 **{primary}**（置信度 {confidence:.0%}）。")
        lines.append(f"本次 MADDx 完成 {rounds} 轮内部辩论。")
        if differentials:
            lines.append(f"\n同时需要与以下情况鉴别：{' / '.join(differentials)}，建议在就诊时一并排查。")
        if evidence:
            lines.append("")
            lines.append("**🔍 关键诊断依据：**")
            for e in evidence:
                lines.append(f"- {e}")
        if tests:
            lines.append("")
            lines.append("**🔬 建议完善以下检查：**")
            for t in tests:
                lines.append(f"- {t}")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("**💡 日常建议：** 保持规律作息，避免诱因。如症状持续或加重，请及时至对应科室就诊。")
        lines.append("")
        lines.append("**⚠️ 危险信号：** 若出现症状急剧恶化、新发剧烈疼痛、意识改变等情况，请立即急诊就医。")

    # 附加模块（影像/用药），若存在则追加到叙事之后
    if vision_context:
        lines.append("")
        lines.append("---")
        lines.append("**📷 影像识别补充：**")
        lines.append(vision_context)

    if med_precheck:
        warnings = med_precheck.get("kg_warnings", "")
        manual = med_precheck.get("manual_summary", "")
        if warnings or manual:
            lines.append("")
            lines.append("---")
            lines.append("**💊 联合用药提示：**")
            if warnings:
                lines.append(f"- ⚠️ {warnings}")
            if manual:
                lines.append(f"- {manual[:200]}")

    return "\n".join(lines)


# ==================== 主入口 ====================

async def run_maddx_for_symptom_report(
    slots: Dict[str, str],
    patient_profile: Optional[dict],
    kg_context: str,
    local_guide_context: str,
    vision_context: Optional[str] = None,
    med_precheck: Optional[dict] = None,
    enabled_tools: Optional[List[str]] = None,
    agent_models: Optional[List[str]] = None,  # 🆕 [proposer, critic, defender, moderator]
) -> tuple[str, Blackboard, list]:
    """
    D8 主入口：给它 slots，返回 (Markdown 报告, Blackboard 实例, 辩论事件流)。

    与 D7 的差异：
      - kg_context / local_guide_context 仍在签名里以保持 symptom_controller 调用兼容，
        但**不再传给 MADDx**——agent 会自主取证。这两个字段仅用于给用户的报告末尾
        做参考信息展示（可选）。
      - 新增 enabled_tools 参数：生产环境默认全开；消融实验可通过此参数精确控制。
    """
    bb = Blackboard(session_id=f"maddx-{id(slots)}")
    symptoms = slots_to_symptoms(slots)
    profile = patient_profile or {}
    logger.info(
        f"[MADDx 入口] 转换后症状数={len(symptoms)}, profile keys={list(profile.keys())}, "
        f"enabled_tools={enabled_tools}"
    )

    tools = build_tool_registry(bb=bb, enabled_tools=enabled_tools)
    candidate_ranking = None
    if _medrag_ranker_enabled():
        try:
            ranking = await rank_disease_candidates(symptoms, profile, top_k=8)
            if ranking.get("candidates"):
                candidate_ranking = ranking
                logger.info(
                    "[MADDx MedRAG] KG prior candidates=%s stats=%s",
                    len(ranking.get("candidates") or []),
                    ranking.get("stats"),
                )
            else:
                logger.info("[MADDx MedRAG] fallback/no candidates stats=%s", ranking.get("stats"))
        except Exception as e:
            logger.warning("[MADDx MedRAG] ranker failed, falling back to original MADDx: %s", e)

    # 同时收集事件用于历史回放
    events_log: list = []
    collector_token = set_collector(events_log)
    try:
        report = await run_maddx(
            bb=bb,
            symptoms=symptoms,
            patient_profile=profile,
            tools=tools,
            agent_models=agent_models,
            candidate_ranking=candidate_ranking,
        )
    finally:
        reset_collector(collector_token)

    markdown = format_maddx_report_as_markdown(report, vision_context, med_precheck)
    return markdown, bb, events_log
