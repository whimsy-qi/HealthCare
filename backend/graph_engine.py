# backend/graph_engine.py
import asyncio
import logging
import json
import os
import httpx
from typing import TypedDict, List, Dict, Any, Optional, Annotated, Literal
import operator
from langgraph.graph import StateGraph, START, END
from openai import AsyncOpenAI

from scripts.vision_tool import analyze_image_with_vision

from agents.triage_agent import triage_query
from agents.symptom_controller import run_symptom_track
from agents.general_agent import run_general_agent
from agents.rumor_agent import run_rumor_controller as run_rumor_legacy
from agents.rumor.integration import run_rumor_ctaew

# D9 开关：默认走新版 CTAEW 流程；设 RUMOR_USE_LEGACY=1 回退到旧版
_USE_LEGACY_RUMOR = os.getenv("RUMOR_USE_LEGACY", "0").lower() in ("1", "true", "yes")
run_rumor_controller = run_rumor_legacy if _USE_LEGACY_RUMOR else run_rumor_ctaew
logger_boot = logging.getLogger("GraphEngine.Boot")
logger_boot.info(
    f"[Rumor] 使用 {'旧版 legacy RumorAgent' if _USE_LEGACY_RUMOR else '新版 CTAEW workflow (D9)'}"
)
from agents.medication_agent import run_med_extractor, run_med_pharmacist, run_med_reviewer, \
    log_medication_reflection_data
from agents.report_agent import ReportAgent, VectorGuidelineRetriever
from agents.hallucination_agent import guard_answer as _halluc_guard
from core.evidence import build_chain, dedupe_refs
from core.blackboard import Blackboard
from core.insight_memory import (
    harvest_from_hallucination_report as _insight_harvest,
    retrieve_insights as _insight_retrieve,
    render_insights_as_fewshot as _insight_render,
)

logger = logging.getLogger("GraphEngine")

client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_API_BASE"),
    http_client=httpx.AsyncClient(verify=False)
)

# ReportAgent 在此处统一初始化，避免 graph_engine ↔ api_server 循环导入
report_agent_instance = ReportAgent(
    retriever=VectorGuidelineRetriever(),
    llm_client=client
)

# ==========================================
# 🌟 1. 智能体黄页
# ==========================================
AGENT_REGISTRY = {
    "symptom": {"name": "症状追踪专家", "desc": "负责收集患者症状、推演潜在疾病。"},
    "rumor_subgraph": {"name": "辟谣核心子网", "desc": "独立辟谣流水线（包含侦察、考证、裁决）。"},
    "medication_subgraph": {"name": "用药审查子网", "desc": "独立用药流水线（包含提取、药师、终审）。"},
    "chitchat": {"name": "前台接待护士", "desc": "负责处理日常打招呼、非医疗话题的闲聊。"},
    "general": {"name": "全科主任大夫", "desc": "复杂医疗问题的综合兜底专家。"},
    "emergency": {"name": "急诊分诊哨兵", "desc": "极高风险拦截。"},
    "report": {"name": "多模态影像检验科医生", "desc": "影像解析。"}
}


def merge_trace_data(existing: dict, new: dict) -> dict:
    if not existing: return new
    if not new: return existing
    res = existing.copy()
    res.update(new)
    return res


def _is_kg_constraint_source(source: Any) -> bool:
    if not isinstance(source, dict):
        return False
    return (
        source.get("type") == "kg"
        or source.get("evidence_role") == "constraint"
        or source.get("citation_allowed") is False
    )


def _is_milvus_rag_source(source: Any) -> bool:
    if not isinstance(source, dict) or _is_kg_constraint_source(source):
        return False
    role = source.get("role")
    return bool(
        source.get("rag_trace")
        or source.get("knowledge_card")
        or role in ("evidence", "background")
    )


def _build_rag_trace_from_sources(raw_sources: list) -> dict:
    rag_cards = [src for src in (raw_sources or []) if _is_milvus_rag_source(src)]
    if not rag_cards:
        return {}
    rag_runtime_trace = next(
        (
            src.get("rag_trace")
            for src in rag_cards
            if isinstance(src.get("rag_trace"), dict) and src.get("rag_trace")
        ),
        {},
    )
    return {
        **rag_runtime_trace,
        "evidence_count": len([src for src in rag_cards if src.get("role", "evidence") == "evidence"]),
        "background_count": len([src for src in rag_cards if src.get("role") == "background"]),
        "items": rag_cards[:8],
    }


def _build_kg_trace_from_sources(raw_sources: list, evidence_chain: Optional[dict] = None) -> dict:
    paths = []
    for src in (raw_sources or []):
        if not _is_kg_constraint_source(src):
            continue
        paths.append({
            "head": src.get("head") or src.get("label") or src.get("title") or "KG",
            "relation": src.get("relation") or src.get("type") or "constraint",
            "tail": src.get("tail") or src.get("snippet") or src.get("content") or "",
            "source_id": src.get("ref_id") or src.get("source_id"),
            "confidence": src.get("confidence", 1.0),
            "evidence_role": "constraint",
            "citation_allowed": False,
        })

    for ref in ((evidence_chain or {}).get("refs") or []):
        if not isinstance(ref, dict):
            continue
        if ref.get("type") != "kg" and ref.get("evidence_role") != "constraint":
            continue
        paths.append({
            "head": ref.get("label") or "KG",
            "relation": "constraint",
            "tail": ref.get("snippet") or "",
            "source_id": ref.get("ref_id"),
            "confidence": ref.get("confidence", 1.0),
            "evidence_role": "constraint",
            "citation_allowed": False,
        })

    deduped = []
    seen = set()
    for item in paths:
        key = (item.get("source_id"), item.get("head"), item.get("relation"), item.get("tail"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return {
        "paths": deduped[:12],
        "evidence_role": "constraint",
        "citation_allowed": False,
        "degraded": bool(not deduped),
        "path_count": len(deduped),
    }


def _filter_legacy_sources_for_panel(raw_sources: list) -> list:
    return [
        src
        for src in (raw_sources or [])
        if isinstance(src, dict)
        and not _is_milvus_rag_source(src)
        and not _is_kg_constraint_source(src)
    ]


# 🌟 全新升级：支持多意图与并发编排的全局状态机
# ==========================================
# 🔒 状态字段常量（避免字符串拼写漂移）
# ==========================================
# symptom_status 的合法值。symptom_node 退出时设置，entry_router 读取。
# 用 Literal 而不是 Enum：TypedDict 字段需要 hashable type 提示，且和 LangGraph 的 dict 序列化兼容。
SYMPTOM_STATUS_CLARIFYING = "clarifying"        # 槽位仍未填满，需继续追问
SYMPTOM_STATUS_DIAGNOSIS_DONE = "diagnosis_done"  # 槽位齐全 → 转交全科生成最终报告
SymptomStatus = Literal["", "clarifying", "diagnosis_done"]


class AgentState(TypedDict, total=False):
    session_id: int
    user_id: int             # 🔒 用于见解知识库私有桶 + 个性化注入
    query: str
    messages_history: Annotated[List[Dict[str, str]], operator.add]
    image_url: Optional[str]
    patient_profile: dict

    # 🌟 替换为新版的三元组意图体系
    urgency: str
    primary_intent: str
    sub_intent: str
    # 🆕 二维内容轴：与 primary_intent 正交，仅影响 prompt 偏重
    act_intent: str           # ASK / CONFIRM / SEEK_HELP / DEBUNK / ANALYZE
    attr_intent: str          # CAUSE / SYMPTOM / BASIC / CHECKUP / VISIT / PREVENT / DIAGNOSE / CAUTION
    parallel_intents: List[str]
    extracted_entities: list
    next_agent: Any

    # 跨模态中间态记忆 (保留你的原样)
    symptom_status: SymptomStatus
    current_slots: dict
    turn_count: int
    vision_context: str
    med_precheck_result: dict

    # 兜底与最终输出 (保留你的原样)
    internal_scratchpad: Annotated[List[Dict[str, str]], operator.add]
    agent_audit_log: Annotated[List[str], operator.add]
    final_answer: str
    options: list
    is_finished: bool
    current_route: str
    trace_data: Annotated[dict, merge_trace_data]
    response_images: list

    # 🆕 MADDx 辩论事件流（每条 {type, phase, round, ...}），供历史回放
    maddx_events: list

    # 🌟 证据链：每轮覆盖语义（最终也会塞进 trace_data.evidence_chain 给前端）
    evidence_chain: dict

    # 🗒️ 共享黑板：跨 agent 的 append-only 工作记忆，由 triage 创建，所有 agent 复用。
    blackboard: Any
    bb_intent_version: int    # triage 写入 intent_classification 后的版本号，下游 parent_refs 用

    # 🆕 意图驱动的协作模式选择
    collab_mode: str          # single_react / single_react_kg / fusion / debate
    collab_models: list       # 参与协作的模型列表 ["deepseek","qwen","glm"]
    collab_reason: str        # 协作模式选择原因
    collab_policy: dict       # 完整结构化策略

    # 🚀 多意图并行调度（P2）
    intents: list             # [{domain, sub, confidence, act, attr}, ...]


# 2.2 辟谣专属子状态 (Rumor Sub-State)
# class RumorState(TypedDict, total=False):
#    query: str
 #   scout_evidence: list
  #  medical_evidence: list
   # medical_truth_text: str

    # 子网输出项
    #agent_audit_log: Annotated[List[str], operator.add]
    #final_answer: str
    #trace_data: Annotated[dict, merge_trace_data]
    #response_images: list


# 2.3 用药专属子状态 (Medication Sub-State)
class MedicationState(TypedDict, total=False):
    query: str
    user_id: int            # 🧠 见解知识库私有桶 ID（透传自主图）
    act_intent: str         # 🆕 二维内容轴（透传自主图）
    attr_intent: str
    image_url: Optional[str]
    extracted_entities: list
    patient_profile: dict

    med_intent: str
    med_kg_context: str
    med_vector_context: dict
    med_sources: list
    med_kg_triples: list      # 🌟 KG 命中的结构化三元组（供证据链组装）

    # 子网输出项
    agent_audit_log: Annotated[List[str], operator.add]
    final_answer: str
    trace_data: Annotated[dict, merge_trace_data]
    internal_scratchpad: Annotated[List[Dict[str, str]], operator.add]
    next_agent: Any
    evidence_chain: dict      # 🌟 证据链（覆盖语义，子图返回时主图直接吃）

    # 🗒️ 共享黑板 + 父版本指针（与主图同名，由 wrapper 透传进来）
    blackboard: Any
    bb_intent_version: int
    bb_extract_version: int   # extractor 写完后的版本，pharmacist 用作 parent_refs
    bb_kg_version: int        # pharmacist 写 KG 三元组后的版本
    bb_vec_version: int       # pharmacist 写向量召回后的版本


async def with_timeout(coro, timeout=120.0, fallback_data=None):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return fallback_data


# ==========================================
# 🗒️ 黑板工具：所有 agent 拿黑板都走这一个入口
# - 主图入口（triage）首次调用时创建 bb；后续节点拿到的是同一对象
# - 子图 wrapper 透传，子图内部节点也用同一 bb
# ==========================================
def get_or_create_blackboard(state: dict, session_id_hint: str = "") -> Blackboard:
    """
    取或建主图共享黑板。
    state: 当前节点拿到的 LangGraph state（dict）。
    session_id_hint: 用于新建时的 id 标识，建议使用 session_id 或 query 哈希。
    """
    bb = state.get("blackboard")
    if isinstance(bb, Blackboard):
        return bb
    # 兼容字符串或其他错误类型——保险起见新建一个
    sid = session_id_hint or f"session-{state.get('session_id', 'unknown')}"
    return Blackboard(session_id=str(sid))


async def bb_safe_append(bb: Blackboard, key: str, value: Any,
                         agent_id: str, parent_refs: Optional[List[int]] = None) -> Optional[int]:
    """
    防御式写入：异常吞掉返回 None，确保黑板写入永不阻断主流程。
    """
    if not isinstance(bb, Blackboard):
        return None
    try:
        return await bb.append(key, value, agent_id=agent_id, parent_refs=parent_refs or [])
    except Exception as e:
        logger.warning(f"[Blackboard] append({key}) 失败（忽略）：{type(e).__name__}: {e}")
        return None


def bb_dag_safe(bb: Any) -> Optional[dict]:
    """安全导出 DAG。非 Blackboard 实例返回 None。"""
    if not isinstance(bb, Blackboard):
        return None
    try:
        return bb.to_trace_dag()
    except Exception as e:
        logger.warning(f"[Blackboard] to_trace_dag 失败：{e}")
        return None


async def pre_flight_node(state: AgentState):
    """
    预处理并发枢纽：拿到 Task Planner 的并行任务单，并发执行，并将结果汇总到 State 中。
    带有状态锁，防止多轮追问时重复执行耗时任务，并带有严格的异常透传机制。
    """
    logger.info("🛫 [Pre-flight] 触发预处理枢纽，准备并发执行辅助任务...")

    # 提取意图和上下文
    parallel_tasks = state.get("parallel_intents", [])
    extracted_entities = state.get("extracted_entities", [])
    image_url = state.get("image_url")

    # 🌟 状态锁：检查 State 中是否已经存在中间态结果
    existing_vision = state.get("vision_context")
    existing_med = state.get("med_precheck_result")

    tasks = []
    audit_logs = ["[Pre-flight] 接收到任务编排指令，检查并发执行状态..."]

    # ==========================================
    # 1. 准备视觉分析任务
    # ==========================================
    async def fetch_vision():
        audit_logs.append("[Pre-flight] 📸 正在启动多模态视觉大模型提取体征...")

        # 🌟 核心改造：强约束输出排版
        prompt = """
        你是一位经验丰富的临床医生。请仔细观察图片，准确提取出医学体征或异常区域。
        【严格格式要求】
        请必须按照以下 Markdown 格式输出（不要有多余的客套话，如果没有相关信息写"未见明显异常"）：

        📍 **异常部位**：...
        🦠 **体征表现**：...
        🩺 **初步推测**：...
        """
        try:
            from scripts.vision_tool import analyze_image_with_vision
            vision_res = await analyze_image_with_vision(image_url, prompt)
            return "VISION", vision_res
        except Exception as e:
            logger.error(f"❌ [Pre-flight] 视觉提取失败: {e}")
            return "VISION", f"图片解析失败，未能提取体征: {str(e)}"

    # 仅在需要查、有图、且之前没查过的情况下加入并发队列
    if "VISION_ANALYSIS" in parallel_tasks and image_url and not existing_vision:
        tasks.append(fetch_vision())

    # ==========================================
    # 2. 准备用药初筛任务
    # ==========================================
    async def fetch_med_precheck():
        logger.info(f"💊 [Pre-flight] 正在对实体 {extracted_entities} 进行用药红线初筛...")
        audit_logs.append(f"[Pre-flight] 💊 正在对实体 {extracted_entities} 进行用药红线初筛...")
        try:
            from agents.medication_agent import search_kg_contraindications, search_drug_manual

            kg_text, _, _ = await search_kg_contraindications(extracted_entities)

            manual_texts = []
            for drug in extracted_entities:
                manual_text, _ = await search_drug_manual(drug, 1)
                if manual_text:
                    manual_texts.append(manual_text)

            logger.info("✅ [Pre-flight] 用药红线扫描成功完成！")
            return "MEDICATION", {
                "kg_warnings": kg_text,
                "manual_summary": "\n".join(manual_texts)[:500]
            }
        except Exception as e:
            logger.error(f"❌ [Pre-flight] 用药初筛发生极其严重的异常: {e}")
            return "MEDICATION", {
                "kg_warnings": "⚠️ 用药核查系统内部异常",
                "manual_summary": f"技术排错信息: {str(e)}"
            }

    if "MEDICATION_PRECHECK" in parallel_tasks and extracted_entities and not existing_med:
        tasks.append(fetch_med_precheck())

    # ==========================================
    # 3. 并发执行并组装结果
    # ==========================================
    updates = {}
    bb = get_or_create_blackboard(state)
    intent_v = state.get("bb_intent_version", 0)

    if tasks:
        results = await asyncio.gather(*tasks)
        for task_type, res in results:
            if task_type == "VISION" and res:
                updates["vision_context"] = res
                audit_logs.append(f"[Pre-flight] ✅ 视觉体征提取完成。")
                # 🗒️ 黑板：视觉提取条目
                await bb_safe_append(
                    bb, "vision_extracted",
                    {"summary": str(res)[:200], "has_image": True},
                    agent_id="pre_flight.vision",
                    parent_refs=[intent_v] if intent_v else [],
                )
            elif task_type == "MEDICATION" and res:
                updates["med_precheck_result"] = res
                audit_logs.append(f"[Pre-flight] ✅ 用药红线扫描完成。")
                # 🗒️ 黑板：用药红线初筛条目
                await bb_safe_append(
                    bb, "med_precheck",
                    {
                        "kg_warnings": (res.get("kg_warnings") or "")[:200],
                        "manual_summary": (res.get("manual_summary") or "")[:200],
                        "entities": extracted_entities,
                    },
                    agent_id="pre_flight.med",
                    parent_refs=[intent_v] if intent_v else [],
                )
    else:
        audit_logs.append("[Pre-flight] ⚡ 辅助任务已在历史轮次完成或无需执行，直接高速放行。")

    updates["agent_audit_log"] = audit_logs

    # 🌟 将游标精确指向 Task Planner 拆解出的主任务
    updates["next_agent"] = state.get("primary_intent", "GENERAL_CONSULTATION")

    return updates

# ==========================================
# 🌟 3. 构建独立子图 (Sub-Graphs)
# ==========================================

# ----------------- 💊 用药审查子网 -----------------
async def med_extractor_node(state: MedicationState):
    intent, drugs, logs = await with_timeout(
        run_med_extractor(state.get("query", ""), state.get("image_url"), state.get("extracted_entities", [])),
        timeout=120.0, fallback_data=("Safety_Check", state.get("extracted_entities", []), ["[Extractor] 超时降级"])
    )
    # 🗒️ 黑板：药物实体提取条目
    bb = get_or_create_blackboard(state)
    intent_v = state.get("bb_intent_version", 0)
    v_extract = await bb_safe_append(
        bb, "med_extracted_drugs",
        {"intent": intent, "drugs": drugs},
        agent_id="med_extractor",
        parent_refs=[intent_v] if intent_v else [],
    )
    return {
        "med_intent": intent,
        "extracted_entities": drugs,
        "agent_audit_log": logs,
        "bb_extract_version": v_extract or 0,
    }


async def med_pharmacist_node(state: MedicationState):
    kg, vector, sources, logs, kg_triples = await with_timeout(
        run_med_pharmacist(state.get("query", ""), state.get("extracted_entities", [])),
        timeout=120.0, fallback_data=("", {}, [], ["[Pharmacist] 检索超时"], [])
    )
    # 🗒️ 黑板：双路检索两条独立 entry，各自挂在 extract 之后
    bb = get_or_create_blackboard(state)
    extract_v = state.get("bb_extract_version", 0)
    parent = [extract_v] if extract_v else []

    v_kg = await bb_safe_append(
        bb, "med_kg_triples",
        {
            "n_triples": len(kg_triples or []),
            "preview": (kg_triples or [])[:5],
            "summary": (kg or "")[:150],
        },
        agent_id="med_pharmacist.kg",
        parent_refs=parent,
    )
    v_vec = await bb_safe_append(
        bb, "med_vector_chunks",
        {
            "n_drugs": len(vector or {}),
            "drug_names": list((vector or {}).keys()),
            "n_sources": len(sources or []),
        },
        agent_id="med_pharmacist.vector",
        parent_refs=parent,
    )

    return {
        "med_kg_context": kg,
        "med_vector_context": vector,
        "med_sources": sources,
        "med_kg_triples": kg_triples,    # 🌟 透传 KG 三元组给 reviewer 组装证据链
        "agent_audit_log": logs,
        "bb_kg_version": v_kg or 0,
        "bb_vec_version": v_vec or 0,
    }


async def med_reviewer_node(state: MedicationState):
    # 🌟 新增：如果 Extractor 决定转单，直接拦截！
    if state.get("med_intent") == "TRANSFER_TO_GENERAL":
        return {
            "internal_scratchpad": [{"from": "pharmacist", "to": "general",
                                     "msg": "患者未指定具体药物，属于求取处方建议，已越过药师权限，转交全科主任处理。"}],
            "next_agent": "general",
            "agent_audit_log": ["[Med-Reviewer] 接收到 Extractor 的越权警告，终止用药审查流程，转交全科网络。"]
        }

    result_dict, logs = await with_timeout(
        run_med_reviewer(
            state.get("query", ""), state.get("med_intent", "Safety_Check"), state.get("patient_profile", {}),
            state.get("med_kg_context", ""), state.get("med_vector_context", {}), state.get("extracted_entities", []),
            act_intent=state.get("act_intent", ""),
            attr_intent=state.get("attr_intent", ""),
        ), timeout=120.0,
        fallback_data=({"risk_level": "未知", "pharmacist_advice": "系统超时"}, ["[Reviewer] 裁决超时"])
    )

    agent_logs = logs
    risk_level = result_dict.get('risk_level', '未知')

    asyncio.create_task(log_medication_reflection_data(
        query=state.get("query", ""), intent=state.get("med_intent", "Safety_Check"),
        drugs=state.get("extracted_entities", []), report=result_dict, has_profile=bool(state.get("patient_profile"))
    ))

    if result_dict.get('confidence_score', 1.0) < 0.7 or risk_level == '未知':
        agent_logs.append("[Med-Reviewer] 知识盲区触发，转移至全科兜底。")
        return {"internal_scratchpad": [{"from": "med_reviewer", "to": "general", "msg": "审查存在盲区，转交全科。"}],
                "next_agent": "general", "agent_audit_log": agent_logs}

    intent = result_dict.get('intent', '用药审查')
    final_answer = f"### 📖 药品百科知识\n\n{result_dict.get('pharmacist_advice', '')}" if intent == "通用科普" else f"### 🛡️ 用药安全审查报告\n\n- **风险评级**: {risk_level}\n- **冲突预警**: {result_dict.get('conflict_detected', '无')}\n\n**💡 终审官指导建议**:\n{result_dict.get('pharmacist_advice', '')}"
    agent_logs.append(
        f"[Med-Reviewer] {'药品百科构建完毕' if intent == '通用科普' else f'审查完成！风险等级：[{risk_level}]'}。")

    # 🛡️ Hallucination Guard：用药审查是医疗系统中风险最高的子任务之一
    # 把 KG 推演 + 向量检索 + 药师建议三类证据合并喂给检测员
    med_evidence = []
    med_constraints = []
    if state.get("med_kg_context"):
        med_evidence.append({"title": "知识图谱推演", "content": state.get("med_kg_context", "")})
    med_vector = state.get("med_vector_context") or {}
    if isinstance(med_vector, dict):
        for k in ("vector_text", "guidelines", "context"):
            v = med_vector.get(k)
            if v:
                med_evidence.append({"title": f"向量检索/{k}", "content": str(v)[:600]})
    for src in (state.get("med_sources") or [])[:5]:
        if isinstance(src, dict):
            med_evidence.append(src)

    if state.get("med_kg_context"):
        med_constraints.append({"title": "KG constraint", "content": state.get("med_kg_context", "")})
    filtered_med_evidence = []
    for item in med_evidence:
        if isinstance(item, dict):
            if item.get("content") == state.get("med_kg_context"):
                continue
            if item.get("type") == "kg" or item.get("evidence_role") == "constraint":
                med_constraints.append(item)
                continue
        filtered_med_evidence.append(item)
    med_evidence = filtered_med_evidence

    final_answer, halluc_report = await _halluc_guard(
        answer=final_answer,
        evidence=med_evidence,
        constraints=med_constraints,
        domain="medication",
        domain_risk="HIGH",   # 用药审查永远 HIGH，不论 risk_level 是什么
        audit_logs=agent_logs,
    )

    # ==========================================
    # 🌟 组装证据链 EvidenceChain
    # ==========================================
    drugs_in = state.get("extracted_entities", []) or []
    raw_kg_triples = state.get("med_kg_triples", []) or []
    raw_sources = state.get("med_sources", []) or []
    rag_cards = [src for src in raw_sources if isinstance(src, dict) and (src.get("role") or src.get("rag_trace"))]
    rag_runtime_trace = next(
        (
            src.get("rag_trace")
            for src in rag_cards
            if isinstance(src, dict) and isinstance(src.get("rag_trace"), dict) and src.get("rag_trace")
        ),
        {},
    )

    # 1) refs：源池（去重 + locator）
    chain_refs = []
    for src in raw_sources:
        if not isinstance(src, dict):
            continue
        rid = src.get("ref_id")
        if not rid:
            # 兼容旧卡片：合成一个伪 id
            rid = f"legacy:{hash(src.get('title','') + str(src.get('url',''))) & 0xFFFFFFFF:08x}"
        chain_refs.append({
            "ref_id": rid,
            "type": src.get("type") or ("kg" if src.get("is_internal") and "Knowledge Graph" in str(src.get("url", "")) else
                                        "pdf" if src.get("is_internal") else "web"),
            "label": src.get("label") or src.get("title", "未命名来源"),
            "locator": src.get("locator", {}),
            "snippet": src.get("snippet") or src.get("content", ""),
            "evidence_role": src.get("evidence_role") or ("constraint" if src.get("type") == "kg" else "evidence"),
            "citation_allowed": src.get("citation_allowed", False if src.get("type") == "kg" else True),
        })

    # 2) 患者档案档案命中作为额外 ref（如果 conflict_detected 提及）
    profile = state.get("patient_profile") or {}
    conflict_str = (result_dict.get("conflict_detected") or "").strip()
    if conflict_str and conflict_str != "无" and profile:
        chain_refs.append({
            "ref_id": "profile:patient_profile",
            "type": "profile",
            "label": "患者健康档案",
            "locator": {"fields": list(profile.keys())},
            "snippet": json.dumps(profile, ensure_ascii=False)[:300],
        })

    chain_refs = dedupe_refs(chain_refs)

    # 3) triples：KG 命中的禁忌三元组（去掉内部 drug_id 字段）
    chain_triples = [
        {
            "head": t.get("head"),
            "relation": t.get("relation", "禁忌于"),
            "tail": t.get("tail"),
            "tail_type": t.get("tail_type"),
            "source_id": t.get("source_id"),
            "confidence": t.get("confidence", 1.0),
            "evidence_role": t.get("evidence_role", "constraint"),
            "citation_allowed": t.get("citation_allowed", False),
        }
        for t in raw_kg_triples
    ]
    # 当 conflict_detected 明确指向了档案命中，再补一条档案三元组
    if conflict_str and conflict_str != "无" and profile and drugs_in:
        chain_triples.append({
            "head": drugs_in[0],
            "relation": "档案命中",
            "tail": conflict_str,
            "source_id": "profile:patient_profile",
            "confidence": 0.9,
        })

    # 4) reasoning_path：三步固定流水
    risk_level = result_dict.get("risk_level", "未知")
    chain_path = [
        {
            "step": 1, "actor": "med_extractor", "action": "提取药物实体",
            "input_summary": (state.get("query", "") or "")[:80],
            "output_summary": f"药物={drugs_in or '未识别'}",
            "cited_refs": [],
        },
        {
            "step": 2, "actor": "med_pharmacist", "action": "Graph-RAG 双路检索",
            "input_summary": f"查询药物={drugs_in}",
            "output_summary": f"KG命中{len(chain_triples)}条 / 资料卡{len(chain_refs)}条",
            "cited_refs": [r["ref_id"] for r in chain_refs],
        },
        {
            "step": 3, "actor": "med_reviewer", "action": "综合裁决",
            "input_summary": "证据汇总 + 患者档案对照",
            "output_summary": f"风险={risk_level} | 冲突={conflict_str or '无'}",
            "cited_refs": [t["source_id"] for t in chain_triples if t.get("source_id")],
        },
    ]

    final_claim = (
        f"{'/'.join(drugs_in) if drugs_in else '该药物'} 在当前患者档案下风险等级为【{risk_level}】"
        + (f"；冲突：{conflict_str}" if conflict_str and conflict_str != "无" else "")
    )

    evidence_chain = build_chain(
        triples=chain_triples,
        reasoning_path=chain_path,
        refs=chain_refs,
        final_claim=final_claim,
        confidence=float(result_dict.get("confidence_score", 1.0)),
    )

    rag_trace_payload = {}
    if rag_cards:
        rag_trace_payload = {
            **rag_runtime_trace,
            "evidence_count": len([src for src in rag_cards if src.get("role", "evidence") == "evidence"]),
            "background_count": len([src for src in rag_cards if src.get("role") == "background"]),
            "items": rag_cards[:8],
        }
    kg_trace_payload = {
        "paths": chain_triples[:12],
        "evidence_role": "constraint",
        "citation_allowed": False,
        "degraded": bool(not raw_kg_triples),
        "path_count": len(chain_triples),
    }
    safety_trace_payload = {
        "degraded": bool((halluc_report or {}).get("degraded") or (halluc_report or {}).get("safety_check_degraded")),
        "action": (halluc_report or {}).get("action"),
        "timeout": bool((halluc_report or {}).get("timeout")),
        "summary": (halluc_report or {}).get("summary", ""),
        "constraint_count": len(med_constraints),
    }

    trace_payload = {
        "sources": raw_sources,
        "rag": rag_trace_payload,
        "kg": kg_trace_payload,
        "safety_check": safety_trace_payload,
        "evidence_chain": evidence_chain,    # 🌟 前端按统一契约渲染
    }
    if halluc_report:
        trace_payload["hallucination_check"] = halluc_report

    # 🧠 见解知识库：fire-and-forget 收割
    asyncio.create_task(_insight_harvest(
        query=state.get("query", ""),
        domain="medication",
        user_id=state.get("user_id"),
        final_answer=final_answer,
        halluc_report=halluc_report or {},
        evidence_count=len(med_evidence),
        agent_path="medication:reviewer",
    ))

    # 🗒️ 黑板：最终裁决条目，parent 指向 KG + 向量两条 entry
    bb = get_or_create_blackboard(state)
    parent_refs = [v for v in (state.get("bb_kg_version"), state.get("bb_vec_version")) if v]
    await bb_safe_append(
        bb, "med_final_verdict",
        {
            "risk_level": risk_level,
            "conflict_detected": conflict_str or "无",
            "confidence": result_dict.get("confidence_score", 1.0),
            "intent": intent,
            "advice_preview": (result_dict.get("pharmacist_advice") or "")[:160],
        },
        agent_id="med_reviewer",
        parent_refs=parent_refs,
    )

    return {
        "final_answer": final_answer,
        "trace_data": trace_payload,
        "evidence_chain": evidence_chain,    # 🌟 也写到子图顶层，方便 wrapper 透传
        "agent_audit_log": agent_logs,
    }


# 编译用药微服务子图
med_workflow = StateGraph(MedicationState)
med_workflow.add_node("med_extractor", med_extractor_node)
med_workflow.add_node("med_pharmacist", med_pharmacist_node)
med_workflow.add_node("med_reviewer", med_reviewer_node)
med_workflow.add_edge(START, "med_extractor")
med_workflow.add_edge("med_extractor", "med_pharmacist")
med_workflow.add_edge("med_pharmacist", "med_reviewer")
med_workflow.add_edge("med_reviewer", END)
med_app = med_workflow.compile()




async def triage_node(state: AgentState):
    has_image = bool(state.get("image_url"))
    triage_res = await with_timeout(
        triage_query(state.get("query", ""), state.get("messages_history"), has_image),
        timeout=120.0,
        fallback_data={
            "urgency": "NORMAL", "primary_intent": "GENERAL_CONSULTATION",
            "sub_intent": "GENERAL", "act_intent": "ASK", "attr_intent": "BASIC",
            "parallel_intents": [], "extracted_entities": []
        }
    )

    urgency = triage_res.get("urgency", "NORMAL")
    primary = triage_res.get("primary_intent", "GENERAL_CONSULTATION")
    sub_intent = triage_res.get("sub_intent", "GENERAL")
    act_intent = triage_res.get("act_intent", "") or ""
    attr_intent = triage_res.get("attr_intent", "") or ""
    parallel = triage_res.get("parallel_intents", [])
    entities = triage_res.get("extracted_entities", [])
    # 🚀 多意图列表（P2）
    intents_list = triage_res.get("intents", [])

    # 🚨 熔断拦截
    if urgency == "EMERGENCY":
        next_step = "emergency"
        audit_log = "[Triage] 🚨 触发紧急医疗熔断！直接绕过图谱与推理，导向急诊哨兵。"
    else:
        next_step = "pre_flight"
        audit_log = (
            f"[Triage] 意图解析: {primary}({sub_intent}) | "
            f"二维轴: act={act_intent or '-'}/attr={attr_intent or '-'} | "
            f"并行: {parallel}。移交预处理。"
        )

    # 🗒️ 黑板：在主图入口创建共享黑板，写入 query + intent，供下游节点 parent_refs 引用
    bb = get_or_create_blackboard(state, session_id_hint=f"chat-{state.get('session_id', 'x')}")
    v_query = await bb_safe_append(
        bb, "user_query",
        {"query": state.get("query", ""), "has_image": has_image},
        agent_id="triage.entry",
    )
    v_intent = await bb_safe_append(
        bb, "intent_classification",
        {
            "urgency": urgency,
            "primary": primary,
            "sub": sub_intent,
            "act": act_intent,
            "attr": attr_intent,
            "parallel": parallel,
            "entities": entities,
        },
        agent_id="triage",
        parent_refs=[v_query] if v_query else [],
    )

    # 🆕 意图驱动的协作模式选择
    from core.intent_ontology import select_collab_mode
    uncertainty = triage_res.get("uncertainty", min(0.5, len(parallel) * 0.15))
    collab_cfg = select_collab_mode(
        act_intent,
        attr_intent,
        uncertainty,
        domain=primary,
        sub_intent=sub_intent,
    )
    audit_log += (
        f" | 协作模式: {collab_cfg['mode']}"
        + (f" | 原因: {collab_cfg.get('reason')}" if collab_cfg.get("reason") else "")
        + (f" (uncertainty={uncertainty:.2f}, fallback)" if uncertainty > 0.3 else "")
        + (f" | 多意图: {len(intents_list)}个子任务" if len(intents_list) > 1 else "")
    )

    # 🚀 多意图并行调度：>1 个子意图时，立即并发分派
    if len(intents_list) > 1 and urgency != "EMERGENCY":
        logger.info(f"🚀 [Triage] 检测到 {len(intents_list)} 个子意图，启动并行调度")
        parallel_result = await dispatch_parallel_agents({
            "query": state.get("query", ""),
            "session_id": state.get("session_id"),
            "user_id": state.get("user_id"),
            "intents": intents_list,
            "messages_history": state.get("messages_history", []),
            "image_url": state.get("image_url"),
            "patient_profile": state.get("patient_profile", {}),
            "current_slots": state.get("current_slots", {}),
            "turn_count": state.get("turn_count", 1),
            "vision_context": state.get("vision_context"),
            "med_precheck_result": state.get("med_precheck_result"),
            "internal_scratchpad": state.get("internal_scratchpad", []),
            "extracted_entities": entities,
            "blackboard": bb,
            "bb_intent_version": v_intent or 0,
            "collab_mode": collab_cfg["mode"],
            "collab_models": collab_cfg["models"],
            "collab_reason": collab_cfg.get("reason", ""),
            "collab_policy": collab_cfg,
            "agent_audit_log": [audit_log],
        })
        parallel_result["blackboard"] = bb
        parallel_result["current_route"] = "PARALLEL_DISPATCH"
        return parallel_result

    return {
        "urgency": urgency,
        "primary_intent": primary,
        "sub_intent": sub_intent,
        "act_intent": act_intent,
        "attr_intent": attr_intent,
        "parallel_intents": parallel,
        "extracted_entities": entities,
        "intents": intents_list,
        "next_agent": next_step,
        "agent_audit_log": [audit_log],
        "blackboard": bb,
        "bb_intent_version": v_intent or 0,
        "collab_mode": collab_cfg["mode"],
        "collab_models": collab_cfg["models"],
        "collab_reason": collab_cfg.get("reason", ""),
        "collab_policy": collab_cfg,
    }


# 🌟 子网包装器：将主状态映射给用药微服务
async def medication_subgraph_wrapper(state: AgentState):
    collab_mode = state.get("collab_mode", "single_kg")
    audit_log = state.get("agent_audit_log", [])
    audit_log.append(f"[Medication] 协作模式: {collab_mode}")

    # 🧠 见解知识库注入
    try:
        med_insights = await _insight_retrieve(
            query=state.get("query", ""), user_id=state.get("user_id"),
            domain="medication", top_k=3, min_similarity=0.78, include_shared=True,
        )
        if med_insights:
            audit_log.append(f"🧠 [Med/Insight] 命中 {len(med_insights)} 条相似用药案例")
    except Exception:
        med_insights = []
    med_insight_text = _insight_render(med_insights, max_chars=600) if med_insights else ""

    med_input = {
        "query": state.get("query", ""),
        "user_id": state.get("user_id"),     # 🧠 透传给 harvest hook 用于隐私桶分流
        "act_intent": state.get("act_intent", ""),     # 🆕 二维内容轴
        "attr_intent": state.get("attr_intent", ""),
        "image_url": state.get("image_url"),
        "extracted_entities": state.get("extracted_entities", []),
        "patient_profile": state.get("patient_profile", {}),
        # 🗒️ 透传共享黑板与父版本号到子图
        "blackboard": state.get("blackboard"),
        "bb_intent_version": state.get("bb_intent_version", 0),
        "insight_text": med_insight_text,  # 🧠
    }
    res = await med_app.ainvoke(med_input)

    out = {"agent_audit_log": res.get("agent_audit_log", []), "next_agent": res.get("next_agent", "END")}
    if "final_answer" in res:
        # 🗒️ 把当前 DAG 快照塞进 trace_data，让前端能渲染
        trace_payload = res.get("trace_data", {}) or {}
        dag = bb_dag_safe(state.get("blackboard"))
        if dag:
            trace_payload["blackboard_dag"] = dag
        out.update(
            {"final_answer": res["final_answer"], "is_finished": True, "current_route": "MEDICATION_CONSULTATION",
             "trace_data": trace_payload})
    # 🌟 把子图组装好的证据链顶起到主状态（最新覆盖语义）
    if res.get("evidence_chain"):
        out["evidence_chain"] = res["evidence_chain"]
    if "internal_scratchpad" in res:
        out["internal_scratchpad"] = res["internal_scratchpad"]
    return out


# 🌟 子网包装器：将主状态映射给全新的辟谣微服务控制器
async def rumor_subgraph_wrapper(state: AgentState):
    from agents.rumor.risk_router import route_rumor_risk

    query = state.get("query", "")
    audit_log = []
    collab_mode = state.get("collab_mode", "debate")
    audit_log.append(f"[Rumor] 协作模式: {collab_mode}")

    # 🧠 见解知识库注入
    try:
        rumor_insights = await _insight_retrieve(
            query=query, user_id=state.get("user_id"),
            domain="rumor", top_k=3, min_similarity=0.78, include_shared=True,
        )
        if rumor_insights:
            audit_log.append(f"🧠 [Rumor/Insight] 命中 {len(rumor_insights)} 条相似辟谣案例")
    except Exception:
        rumor_insights = []

    # 🚀 快速通道：对低/中风险辟谣查询直接用 Single LLM
    try:
        risk_level, claim_type = await route_rumor_risk(query)
    except Exception:
        risk_level, claim_type = "MEDIUM", "GENERAL"

    # D10 调整：LOW risk 才走 FastPath；MEDIUM/HIGH 进 CTAEW 辩论（含取证）
    # EFFICACY/CAUSAL 在 MEDIUM 时需要 PubMed/社交搜索取证，不再走 FastPath
    if risk_level == "LOW" and claim_type not in ("INTERACTION", "NOVEL_TREND"):
        logger.info(f"⚡ [Rumor/FastPath] risk={risk_level} type={claim_type} → Single LLM 快速辟谣")
        audit_log.append(f"[Rumor] 快速通道: risk={risk_level} type={claim_type}")

        fast_answer = await _rumor_fastpath(query, state.get("extracted_entities", []))
        # 仍过 Hallucination Guard（但不 ABSTAIN）
        fast_answer, _ = await _halluc_guard(
            answer=fast_answer, evidence=None, domain="rumor", domain_risk=risk_level,
            audit_logs=audit_log, timeout_sec=15.0,
        )
        return {
            "final_answer": fast_answer,
            "is_finished": True, "current_route": "RUMOR_VERIFICATION",
            "next_agent": "END", "trace_data": {"rumor_fastpath": True, "risk_level": risk_level},
            "agent_audit_log": audit_log,
        }

    logger.info(f"🛫 [Graph Engine] 移交 Rumor CTAEW 全流程 (risk={risk_level})")
    audit_log.append(f"[Rumor] 全流程 CTAEW: risk={risk_level}")

    # CTAEW 版本支持 user_id + 共享黑板
    rumor_kwargs = dict(query=query, entities=state.get("extracted_entities", []), history=state.get("messages_history", []))
    import inspect
    sig_params = inspect.signature(run_rumor_controller).parameters
    if "user_id" in sig_params:
        rumor_kwargs["user_id"] = state.get("user_id")
    if "blackboard" in sig_params:
        rumor_kwargs["blackboard"] = state.get("blackboard")
        rumor_kwargs["bb_parent_version"] = state.get("bb_intent_version", 0)

    verdict, trace_dict, logs = await with_timeout(
        run_rumor_controller(**rumor_kwargs),
        timeout=300.0,
        fallback_data=("系统检索超时，请稍后再试。", [], ["[Rumor] 控制器超时熔断"])
    )

    # 🗒️ DAG 快照透出（rumor 已经写到 state.blackboard 里）
    if isinstance(trace_dict, dict):
        dag = bb_dag_safe(state.get("blackboard"))
        if dag:
            trace_dict["blackboard_dag"] = dag

    out = {
        "final_answer": verdict,
        "is_finished": True,
        "current_route": "RUMOR_VERIFICATION",
        "next_agent": "END",
        "trace_data": trace_dict,
        "agent_audit_log": audit_log + (logs or [])
    }
    # 🔗 D6/D7: 把 rumor 子网组装好的证据链顶到主状态（最新覆盖语义）
    if isinstance(trace_dict, dict) and trace_dict.get("evidence_chain"):
        out["evidence_chain"] = trace_dict["evidence_chain"]
    return out


async def symptom_node(state: AgentState):
    collab_mode = state.get("collab_mode", "debate")
    audit_log = state.get("agent_audit_log", [])
    audit_log.append(f"[Symptom] 协作模式: {collab_mode}")

    history = [{"role": m["role"], "content": m["content"]} for m in state.get("messages_history", [])] + [
        {"role": "user", "content": state.get("query", "")}]

    # 🌟 核心提取：拿出刚才预处理枢纽存下来的跨模态上下文
    vision_ctx = state.get("vision_context")
    med_precheck = state.get("med_precheck_result")

    # 🌟 新增：获取当前的会话轮次
    current_turn = state.get("turn_count", 1)

    # 🧠 见解知识库注入：检索相似历史 case
    insight_text = ""
    try:
        insights = await _insight_retrieve(
            query=state.get("query", ""), user_id=state.get("user_id"),
            domain="symptom", top_k=3, min_similarity=0.78, include_shared=True,
        )
        if insights:
            insight_text = _insight_render(insights, max_chars=800)
            audit_log.append(f"🧠 [Insight] 命中 {len(insights)} 条相似案例")
    except Exception:
        pass

    res = await with_timeout(
        run_symptom_track(
            messages_history=history,
            turn_count=current_turn,
            current_slots=state.get("current_slots", {}),
            vision_context=vision_ctx,
            med_precheck=med_precheck,
            patient_profile=state.get("patient_profile", {}),
            act_intent=state.get("act_intent", ""),
            attr_intent=state.get("attr_intent", ""),
            blackboard=state.get("blackboard"),
            bb_parent_version=state.get("bb_intent_version", 0),
            insight_text=insight_text,
            collab_models=state.get("collab_models"),  # 🆕 跨模型辩论
        ),
        timeout=300.0,
        fallback_data={"answer": "超时", "is_finished": True, "audit_logs": ["[Symptom] 系统处理超时发生熔断。"]}
    )

    # 🌟 核心组装：准备发给前端渲染的溯源数据
    trace_data = {
        "critic_reasoning": res.get("debug_kg_path", ""),
        "sources": res.get("local_cards", []),
        "kg_insight": res.get("kg_insight"),  # 🆕 KG 结构化洞察
    }

    # 🚨 核心改造点：生命周期拦截！
    # 只有在第 1 轮追问时，才将视觉和用药的重型卡片下发给前端。
    # 从第 2 轮开始，只保留基础诊断逻辑，不再下发这些沉余卡片。
    if current_turn == 1:
        if vision_ctx:
            trace_data["vision_insights"] = vision_ctx
        if med_precheck:
            trace_data["med_precheck"] = med_precheck

    # 🆕 MADDx 辩论溯源（仅在启用且报告生成完毕时存在）
    maddx_trace = res.get("blackboard_trace")
    if maddx_trace:
        trace_data["maddx_debate"] = maddx_trace

    # 🔗 D8-D9：把 controller 组装好的证据链塞进 trace_data。
    # 多轮覆盖语义：每轮的 chain 独立绑在当时的 ai 消息上，主图状态也按最新轮次覆盖。
    symptom_chain = res.get("evidence_chain") or {}
    if symptom_chain and (symptom_chain.get("reasoning_path") or symptom_chain.get("refs")):
        trace_data["evidence_chain"] = symptom_chain

    # 🗒️ DAG 快照（每轮都有，因为 symptom 是会话级的，bb 跨轮累积）
    dag = bb_dag_safe(state.get("blackboard"))
    if dag:
        trace_data["blackboard_dag"] = dag

    is_done = res.get("is_finished", True)
    return {
        "final_answer": res.get("answer"),
        "is_finished": is_done,
        "options": res.get("options", []),
        "turn_count": res.get("turn_count", 1),
        "current_slots": res.get("current_slots", {}),
        "current_route": "SYMPTOM_ANALYSIS" if not is_done else "",
        "symptom_status": SYMPTOM_STATUS_DIAGNOSIS_DONE if is_done else SYMPTOM_STATUS_CLARIFYING,
        "trace_data": trace_data,
        "next_agent": "END",
        "agent_audit_log": res.get("audit_logs", [f"[Symptom] 槽位追踪完成。"]),
        "maddx_events": res.get("maddx_events"),   # 🆕 MADDx 辩论事件流（落库供历史回放）
        "evidence_chain": symptom_chain,            # 🔗 主状态也存一份（最新覆盖）
    }

async def chitchat_node(state: AgentState):
    msgs = [{"role": "system", "content": "你是健康管家。闲聊引导回咨询。"}] + state.get("messages_history", []) + [
        {"role": "user", "content": state.get("query", "")}]
    try:
        resp = await client.chat.completions.create(model="deepseek-chat", messages=msgs, temperature=0.7)
        ans = resp.choices[0].message.content
    except:
        ans = "抱歉走神了，有什么能帮您？"
    return {"final_answer": ans, "is_finished": True, "current_route": "CHITCHAT_OR_REJECT", "next_agent": "END"}


# 修改 backend/graph_engine.py 中的 report_node
async def report_node(state: AgentState):
    collab_mode = state.get("collab_mode", "single")
    audit_log = state.get("agent_audit_log", [])
    audit_log.append(f"[Report] 协作模式: {collab_mode}")
    res = await with_timeout(
        report_agent_instance.analyze_medical_report(query=state.get("query", ""), image_url=state.get("image_url")),
        timeout=120.0,
        fallback_data={"answer": "影像解析系统超时，请稍后再试。", "sources": [], "evidence_chain": {}}
    )

    trace_data = {"sources": res.get("sources", [])}
    evidence_chain = res.get("evidence_chain") or {}
    # 🌟 D5: 把 ReportAgent 组装好的证据链塞进 trace_data，前端按统一契约渲染
    if evidence_chain and (evidence_chain.get("reasoning_path") or evidence_chain.get("refs")):
        trace_data["evidence_chain"] = evidence_chain

    # 🗒️ 黑板：3 条 entry 按因果链 ind → gl → synthesis 串起
    bb = get_or_create_blackboard(state)
    intent_v = state.get("bb_intent_version", 0)
    indicators = res.get("extracted_data", []) or []
    sources = res.get("sources", []) or []

    v_ind = await bb_safe_append(
        bb, "report_indicators",
        {
            "n_indicators": len(indicators),
            "preview": indicators[:5],
            "has_image": bool(state.get("image_url")),
            "skipped_reason": res.get("skipped_reason"),
        },
        agent_id="report.vision_ocr" if state.get("image_url") else "report.text_extract",
        parent_refs=[intent_v] if intent_v else [],
    )
    v_gl = await bb_safe_append(
        bb, "report_guidelines",
        {
            "n_cards": len(sources),
            "card_titles": [s.get("title", "") for s in sources[:5] if isinstance(s, dict)],
        },
        agent_id="report.medical_retrieve",
        parent_refs=[v_ind] if v_ind else ([intent_v] if intent_v else []),
    )
    await bb_safe_append(
        bb, "report_synthesis",
        {
            "answer_preview": (res.get("answer") or "")[:160],
            "format": res.get("format", "markdown"),
        },
        agent_id="report.editor",
        parent_refs=[v for v in (v_ind, v_gl) if v],
    )

    # 🗒️ DAG 快照透出
    dag = bb_dag_safe(bb)
    if dag:
        trace_data["blackboard_dag"] = dag

    return {
        "final_answer": res.get("answer"),
        "is_finished": True,
        "current_route": "REPORT_INTERPRETATION",
        "next_agent": "END",
        "trace_data": trace_data,
        "evidence_chain": evidence_chain,    # 🔗 主状态也存一份
        "agent_audit_log": [
            f"[Report] 影像与化验单多模态解析完成。"
            + (f"（证据链：{len(evidence_chain.get('triples', []))} triples / "
               f"{len(evidence_chain.get('refs', []))} refs）" if evidence_chain else "")
        ]
    }

async def general_node(state: AgentState):
    # 🆕 意图驱动的协作模式分派
    collab_mode = state.get("collab_mode", "single_react")
    collab_models = state.get("collab_models", ["deepseek"])
    query = state.get("query", "")
    collab_reason = state.get("collab_reason", "")
    audit_log = state.get("agent_audit_log", [])
    legacy_mode_map = {
        "single": "single_react",
        "single_kg": "single_react_kg",
    }
    collab_mode = legacy_mode_map.get(collab_mode, collab_mode)
    audit_log.append(
        f"[General] 协作模式: {collab_mode}"
        + (f" | 原因: {collab_reason}" if collab_reason else "")
    )

    if collab_mode == "debate":
        audit_log.append("[General] debate 仅用于辟谣/症状专线；GENERAL_CONSULTATION 已切换为 fusion。")
        collab_mode = "fusion"

    if collab_mode == "fusion":
        from agents.general_fusion import run_general_fusion

        fusion_res = await with_timeout(
            run_general_fusion(
                query=query,
                models=collab_models,
                patient_profile=state.get("patient_profile", {}),
                evidence_context=None,
                act_intent=state.get("act_intent", ""),
                attr_intent=state.get("attr_intent", ""),
            ),
            timeout=180.0,
            fallback_data={
                "answer": "多模型融合超时，已降级为基础全科建议。请补充更具体的症状、持续时间、既往病史和正在使用的药物；如有明显加重或急性不适，请优先线下就医。",
                "trace_data": {"sources": [], "collab_mode": "fusion", "collab_models": collab_models, "timeout": True},
                "sources": [],
                "response_images": [],
                "audit_logs": ["[General/Fusion] 融合超时，触发兜底。"],
            },
        )
        ans = fusion_res.get("answer", "")
        trace_data = dict(fusion_res.get("trace_data") or {})
        display_sources = trace_data.get("sources") or fusion_res.get("sources") or []
        imgs = fusion_res.get("response_images", [])
        fusion_logs = fusion_res.get("audit_logs", [])

        ans, halluc_report = await _halluc_guard(
            answer=ans,
            evidence=display_sources,
            domain="general",
            domain_risk="MEDIUM",
            audit_logs=fusion_logs,
        )

        rag_trace_payload = _build_rag_trace_from_sources(display_sources)
        kg_trace_payload = _build_kg_trace_from_sources(display_sources, trace_data.get("evidence_chain"))
        trace_data["sources"] = _filter_legacy_sources_for_panel(display_sources)
        if rag_trace_payload:
            trace_data["rag"] = rag_trace_payload
        if kg_trace_payload.get("paths"):
            trace_data["kg"] = kg_trace_payload
        trace_data["safety_check"] = {
            "degraded": bool((halluc_report or {}).get("degraded") or (halluc_report or {}).get("safety_check_degraded")),
            "action": (halluc_report or {}).get("action"),
            "timeout": bool((halluc_report or {}).get("timeout")),
            "summary": (halluc_report or {}).get("summary", ""),
        }
        trace_data["collab_mode"] = "fusion"
        trace_data["collab_models"] = collab_models
        if collab_reason:
            trace_data["collab_reason"] = collab_reason
        if halluc_report:
            trace_data["hallucination_check"] = halluc_report
        dag = bb_dag_safe(state.get("blackboard"))
        if dag:
            trace_data["blackboard_dag"] = dag

        asyncio.create_task(_insight_harvest(
            query=query,
            domain="general",
            user_id=state.get("user_id"),
            final_answer=ans,
            halluc_report=halluc_report or {},
            evidence_count=len(display_sources),
            agent_path="general:fusion",
        ))

        return {
            "final_answer": ans,
            "trace_data": trace_data,
            "response_images": imgs,
            "is_finished": True, "current_route": "GENERAL_CONSULTATION",
            "next_agent": "END", "agent_audit_log": fusion_logs,
        }

    # 默认：原有单 Agent ReAct 逻辑
    force_tools = []
    min_tool_calls = 0
    if collab_mode == "single_react_kg":
        force_tools = ["search_medical_graph", "search_local_guidelines"]
        min_tool_calls = 1
        audit_log.append("[General] single_react_kg 已启用强制 KG/指南检索。")
    elif collab_mode not in ("single_react", ""):
        audit_log.append(f"[General] 未知协作模式 {collab_mode}，降级为 single_react。")

    ans, sources, imgs, logs, evidence_chain = await with_timeout(
        run_general_agent(
            query=state.get("query", ""),
            entities=state.get("extracted_entities", []),
            messages_history=state.get("messages_history", []),
            patient_profile=state.get("patient_profile", {}),
            internal_scratchpad=state.get("internal_scratchpad", []),
            vision_context=state.get("vision_context"),  # 🌟 核心修复：把系统的"视神经"正式接入大模型的中枢大脑！
            user_id=state.get("user_id"),                 # 🧠 见解知识库私有桶 ID
            act_intent=state.get("act_intent", ""),       # 🆕 二维内容轴
            attr_intent=state.get("attr_intent", ""),
            blackboard=state.get("blackboard"),                # 🗒️ 共享黑板透传
            bb_parent_version=state.get("bb_intent_version", 0),
            force_tools=force_tools,
            min_tool_calls=min_tool_calls,
        ),
        timeout=120.0,
        fallback_data=("全科检索超时，请稍后再试。", [], [], ["[General] 检索超时"], {})
    )

    display_sources = sources[:5] if sources else []

    # 🛡️ Hallucination Guard：把 ReAct 收集到的 sources 当作证据做 claim 级对齐
    # 全科咨询是 MEDIUM 风险（比 rumor/medication 低，但比 chitchat 高）
    ans, halluc_report = await _halluc_guard(
        answer=ans,
        evidence=display_sources,
        domain="general",
        domain_risk="MEDIUM",
        audit_logs=logs,
    )

    rag_trace_payload = _build_rag_trace_from_sources(display_sources)
    kg_trace_payload = _build_kg_trace_from_sources(display_sources, evidence_chain)

    trace_data = {
        "sources": _filter_legacy_sources_for_panel(display_sources),
        "collab_mode": collab_mode if collab_mode in ("single_react", "single_react_kg") else "single_react",
        "collab_models": collab_models,
    }
    if rag_trace_payload:
        trace_data["rag"] = rag_trace_payload
    if kg_trace_payload.get("paths"):
        trace_data["kg"] = kg_trace_payload
    trace_data["safety_check"] = {
        "degraded": bool((halluc_report or {}).get("degraded") or (halluc_report or {}).get("safety_check_degraded")),
        "action": (halluc_report or {}).get("action"),
        "timeout": bool((halluc_report or {}).get("timeout")),
        "summary": (halluc_report or {}).get("summary", ""),
    }
    if collab_reason:
        trace_data["collab_reason"] = collab_reason
    if halluc_report:
        trace_data["hallucination_check"] = halluc_report
    # 🔗 把 ReAct 期间累积的证据链塞进 trace_data，供前端按统一契约渲染
    if evidence_chain and (evidence_chain.get("reasoning_path") or evidence_chain.get("refs")):
        trace_data["evidence_chain"] = evidence_chain
    # 🗒️ DAG 快照透出
    dag = bb_dag_safe(state.get("blackboard"))
    if dag:
        trace_data["blackboard_dag"] = dag

    # 🧠 见解知识库：fire-and-forget 收割（不阻塞主响应）
    asyncio.create_task(_insight_harvest(
        query=state.get("query", ""),
        domain="general",
        user_id=state.get("user_id"),
        final_answer=ans,
        halluc_report=halluc_report or {},
        evidence_count=len(display_sources),
        agent_path="general:react",
    ))

    return {
        "final_answer": ans,
        "trace_data": trace_data,
        "response_images": imgs,
        "is_finished": True,
        "current_route": "GENERAL_CONSULTATION",
        "next_agent": "END",
        "agent_audit_log": logs,
        "evidence_chain": evidence_chain or {},  # 🔗 主状态也存一份，便于跨节点回看
    }


async def emergency_node(state: AgentState):
    logger.info("🚨 [Emergency Node] 触发急诊静态安全模板，跳过 LLM 生成。")

    final_answer = """### 🚨 紧急情况：请立即联系线下急救

你现在描述的情况可能存在急性风险。请立刻拨打 **120**，或让身边的人替你拨打，并尽快前往最近医院急诊。

### 现在先做这几件事

- **不要独处**：请马上叫家人、同事、邻居或现场人员留在你身边。
- **等待专业救援**：保持电话畅通，按 120 调度员的指示行动。
- **不要自行处理**：不要自行催吐、不要自行服药、不要进食或饮水，也不要自行开车去医院。
- **保留关键信息**：把药物包装、化学品容器、检查资料或现场照片留好，交给急救人员或医生判断。
- **如果出现呼吸、意识异常**：让身边人继续和 120 保持通话，并只按照 120 调度员的实时指导处理。

### 重要提醒

这里不能替代急救人员和急诊医生的现场判断。请现在就拨打 **120**，不要等待线上回复继续分析。"""

    return {
        "final_answer": final_answer,
        "is_finished": True,
        "current_route": "EMERGENCY_TRIGGER",
        "next_agent": "END",
        "trace_data": {"emergency_static_template": True, "llm_used": False},
        "agent_audit_log": ["[Emergency] 静态安全模板已返回，未调用 LLM。"],
    }


# ==========================================
# 🌟 5. 动态路由网络
# ==========================================
def entry_router(state: AgentState) -> str:
    """
    入口路由：根据上一轮的 current_route + symptom_status 决定本轮去哪个节点。
    所有判断走显式状态字段（symptom_status 是 Literal 类型），不依赖任何 LLM 文本匹配。
    """
    route = state.get("current_route", "")
    symptom_phase = state.get("symptom_status", "")

    # 非 SYMPTOM_ANALYSIS 路由：直接进 triage 重新分诊
    if route and route != "SYMPTOM_ANALYSIS":
        return "triage"
    # 症状追问已完成 → 转交全科生成最终报告
    if route == "SYMPTOM_ANALYSIS" and symptom_phase == SYMPTOM_STATUS_DIAGNOSIS_DONE:
        return "general"
    # 症状追问仍在进行中 → 继续槽位填充
    if route == "SYMPTOM_ANALYSIS":
        return "symptom"
    # 全新会话（无 current_route）→ 从 triage 起步
    return "triage"


# ═══════════════════════════════════════════════════════
# 🚀 辟谣快速通道 (P0)
# ═══════════════════════════════════════════════════════

RUMOR_FASTPATH_PROMPT = """【角色】
你是北京协和医院临床营养科副主任医师，行医 15 年，兼任中国健康教育协会科普委员。
你特别擅长用生活化的比喻（炒菜、浇水、修路、堵车）来解释复杂的医学原理。
患者来问问题，往往带着困惑和一点焦虑——你的第一句话永远用来共情和安抚，而不是直接甩结论。
你的语气温暖但不油腻，专业但不吓人，像一个靠谱的医生朋友在耐心解答。

【工作流 — 请逐步思考】
Step 1: 理解用户的困惑点——为什么 ta 会问这个问题？网上什么说法让 ta 不确定？
Step 2: 拆解说法中的医学主张——关于因果、功效、成分还是安全性？
Step 3: 逐一评估每项主张的科学证据——有临床研究吗？医学原理支持吗？还是纯都市传说？
Step 4: 给出明确判定，然后用最通俗的语言解释为什么
Step 5: 给 2-3 条用户能马上用起来的建议，最后温暖收尾

【输出格式】
第一行必须是一句共情的话，不要用"您好"开头，用自然的口语，比如：
"这个问题问得特别好，网上关于它的说法确实让人摸不着头脑——"

然后是一个小标题 ### 🩺 结论：{判定}

接着 2-3 个自然段落做科学解释，每段 3-5 句。用比喻和例子，不要列术语清单。

然后 ### 💡 你可以这样做
- 建议 1（具体可操作）
- 建议 2
- 建议 3

最后一句温暖收尾。总字数 350-500 字。

【约束】
- 绝不输出"系统放弃回答"、"证据权重"、"KG"、"RAG"等内部术语
- 绝不用冷冰冰的模板化格式
- 不用"您"以外的称呼
- 每个段落不超过 5 句
- 比喻要贴切、不低级、不冒犯"""


async def _rumor_fastpath(query: str, entities: list) -> str:
    """单 LLM 快速辟谣，不经过 CTAEW 全流程。"""
    from core.llm_client import shared_client as _client, DEFAULT_MODEL
    try:
        resp = await _client.chat.completions.create(
            model=DEFAULT_MODEL, temperature=0.2, max_tokens=800,
            messages=[
                {"role": "system", "content": RUMOR_FASTPATH_PROMPT},
                {"role": "user", "content": f"请判定以下说法：{query}"},
            ]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"快速辟谣通道暂时不可用（{type(e).__name__}），请稍后重试。"


# ═══════════════════════════════════════════════════════
# 🚀 P2: 多意图并行调度 + 结果合成
# ═══════════════════════════════════════════════════════

SYNTHESIZER_PROMPT = """你是面向患者输出的全科医学答复生成器。请把上游子任务结果压缩成一份完整、收束、患者可读的最终答复。

硬性限制：
- 禁止提到“各专科医生”“上游子任务”“我阅读了意见”“作为全科主任医师”“我将这些意见整合”等内部流程话术。
- 禁止输出 H1/H2 标题，只使用以下 4 个三级标题，且顺序固定：
  ### 结论
  ### 风险解读
  ### 下一步建议
  ### 需要就医的情况
- 全文控制在 900-1300 个中文字符内，必须有完整收尾，不要写长篇病理机制。
- 对患者直接说话，语言简洁、专业，不要暴露多智能体、投票、合成器、证据链等系统内部概念。
- 不直接开处方；涉及药物时只给“需要医生评估/遵医嘱”的安全边界。
- 如果信息不足，明确说明需要补充哪些关键数值或症状。"""


SYNTH_REWRITE_PROMPT = """请把下面这段可能过长或被截断的医学答复重写成一份完整的患者可读最终答复。

要求：
- 只输出最终答复，不要解释你如何重写。
- 禁止出现“各专科医生”“上游子任务”“我阅读了意见”“作为全科主任医师”“整合意见”等内部流程话术。
- 固定使用 4 个标题：### 结论、### 风险解读、### 下一步建议、### 需要就医的情况。
- 900-1200 个中文字符内完整收尾。
- 保留关键风险与下一步建议，删除重复、跑题和长篇机制解释。"""


def _trim_parallel_answer(text: str, max_chars: int = 1600) -> str:
    """压缩每路子任务输出，避免单个长报告挤爆最终 synthesizer。"""
    raw = (text or "").strip()
    if len(raw) <= max_chars:
        return raw
    priority_patterns = [
        "### 结论", "### 风险", "### 风险解读", "### 下一步", "### 下一步建议",
        "### 需要就医", "### 就医", "### 建议", "### 注意",
        "结论", "风险", "下一步", "建议", "就医", "警惕",
    ]
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    picked = []
    for ln in lines:
        if any(p in ln for p in priority_patterns):
            picked.append(ln)
        if sum(len(x) for x in picked) >= max_chars:
            break
    if picked and sum(len(x) for x in picked) >= max_chars // 3:
        compact = "\n".join(picked)
        return compact[:max_chars] + "\n（该子任务原文较长，已截取关键结论。）"
    return raw[:max_chars] + "\n（该子任务原文较长，已截取前半部分。）"


_PARALLEL_DOMAIN_ROUTES = {
    "EMERGENCY_TRIGGER": "emergency",
    "RUMOR_VERIFICATION": "rumor_subgraph",
    "MEDICATION_REVIEW": "medication_subgraph",
    "SYMPTOM_ANALYSIS": "symptom",
    "CHITCHAT_OR_REJECT": "chitchat",
    "REPORT_INTERPRETATION": "report",
    "GENERAL_CONSULTATION": "general",
}


def _parallel_route_for_domain(domain: str) -> str:
    return _PARALLEL_DOMAIN_ROUTES.get(domain or "GENERAL_CONSULTATION", "general")


def _build_parallel_substate(state: dict, intent: dict, cfg: dict) -> AgentState:
    domain = intent.get("domain", "GENERAL_CONSULTATION")
    mini_state: AgentState = {
        "session_id": state.get("session_id"),
        "user_id": state.get("user_id"),
        "query": state.get("query", ""),
        "messages_history": state.get("messages_history", []),
        "image_url": state.get("image_url"),
        "patient_profile": state.get("patient_profile", {}),
        "urgency": state.get("urgency", "NON_EMERGENCY"),
        "primary_intent": domain,
        "sub_intent": intent.get("sub", ""),
        "act_intent": intent.get("act", ""),
        "attr_intent": intent.get("attr", ""),
        "parallel_intents": [],
        "intents": [intent],
        "extracted_entities": state.get("extracted_entities", intent.get("entities", [])),
        "current_slots": state.get("current_slots", {}),
        "turn_count": state.get("turn_count", 1),
        "vision_context": state.get("vision_context"),
        "med_precheck_result": state.get("med_precheck_result"),
        "internal_scratchpad": state.get("internal_scratchpad", []),
        "agent_audit_log": [
            f"[ParallelDispatch/{domain}] 子任务启动: sub={intent.get('sub', '-') or '-'} "
            f"act={intent.get('act', '-') or '-'} attr={intent.get('attr', '-') or '-'}"
        ],
        "blackboard": state.get("blackboard"),
        "bb_intent_version": state.get("bb_intent_version", 0),
        "collab_mode": cfg.get("mode", "single_react"),
        "collab_models": cfg.get("models", []),
        "collab_reason": cfg.get("reason", ""),
        "collab_policy": cfg,
    }
    return mini_state


async def _invoke_parallel_route(route: str, mini_state: AgentState) -> dict:
    if route == "symptom":
        return await symptom_node(mini_state)
    if route == "medication_subgraph":
        return await medication_subgraph_wrapper(mini_state)
    if route == "rumor_subgraph":
        return await rumor_subgraph_wrapper(mini_state)
    if route == "report":
        return await report_node(mini_state)
    if route == "chitchat":
        return await chitchat_node(mini_state)
    if route == "emergency":
        return await emergency_node(mini_state)
    return await general_node(mini_state)


def _format_parallel_partial(result: dict) -> str:
    domain = result.get("domain") or "GENERAL_CONSULTATION"
    answer = (result.get("answer") or result.get("final_answer") or "").strip()
    if not answer:
        answer = "该子任务未生成可用回答。"
    return f"### {domain}\n{answer}\n"


def _compact_parallel_result(result: dict) -> dict:
    trace_data = result.get("trace_data") or {}
    evidence_chain = result.get("evidence_chain") or {}
    return {
        "domain": result.get("domain"),
        "route": result.get("route"),
        "current_route": result.get("current_route"),
        "ok": not bool(result.get("error")),
        "error": result.get("error"),
        "trace_keys": list(trace_data.keys())[:12] if isinstance(trace_data, dict) else [],
        "evidence_refs": len(evidence_chain.get("refs", [])) if isinstance(evidence_chain, dict) else 0,
        "evidence_triples": len(evidence_chain.get("triples", [])) if isinstance(evidence_chain, dict) else 0,
    }


async def dispatch_parallel_agents(state: dict) -> dict:
    """
    多意图并发调度：对 intents 列表中的每个子意图，
    构建独立的 mini-state 并调用对应的 Agent 节点。
    各 Agent 并发执行，结果由 Synthesizer 合并。
    返回可直接合并到主 State 的 dict。
    """
    intents_list = state.get("intents", [])
    query = state.get("query", "")
    audit_log = state.get("agent_audit_log", [])
    audit_log.append(f"[ParallelDispatch] 并发调度 {len(intents_list)} 个子任务")

    # 意图→Agent 函数映射（简化版，复用现有入口）
    async def handle_one(intent: dict) -> dict:
        domain = intent.get("domain", "GENERAL_CONSULTATION")
        sub = intent.get("sub", "")
        act = intent.get("act", "")
        attr = intent.get("attr", "")
        conf = intent.get("confidence", 1.0)
        try:
            conf_value = float(conf)
        except Exception:
            conf_value = 1.0

        # 选择协作模式
        from core.intent_ontology import select_collab_mode
        cfg = select_collab_mode(
            act,
            attr,
            uncertainty=max(0.0, 1.0 - conf_value),
            domain=domain,
            sub_intent=sub,
        )

        route = _parallel_route_for_domain(domain)
        mini_state = _build_parallel_substate(state, intent, cfg)
        try:
            res = await _invoke_parallel_route(route, mini_state)
            trace_data = res.get("trace_data") or {}
            evidence_chain = res.get("evidence_chain") or {}
            return {
                "domain": domain,
                "route": route,
                "current_route": res.get("current_route") or domain,
                "answer": res.get("final_answer", ""),
                "trace_data": trace_data,
                "evidence_chain": evidence_chain,
                "agent_audit_log": res.get("agent_audit_log", []),
                "is_finished": res.get("is_finished", True),
                "next_agent": res.get("next_agent", "END"),
            }
        except Exception as e:
            return {
                "domain": domain,
                "route": route,
                "current_route": domain,
                "answer": f"（处理异常：{e}）",
                "trace_data": {},
                "evidence_chain": {},
                "agent_audit_log": [f"[ParallelDispatch/{domain}] exception={type(e).__name__}: {e}"],
                "is_finished": True,
                "next_agent": "END",
                "error": f"{type(e).__name__}: {e}",
            }

    # 并发执行所有子意图
    tasks = [handle_one(i) for i in intents_list]
    partial_results = await asyncio.gather(*tasks)
    for r in partial_results:
        audit_log.extend(r.get("agent_audit_log", []))
    partial_answers = [_format_parallel_partial(r) for r in partial_results]
    compact_partials = [_trim_parallel_answer(ans, max_chars=1600) for ans in partial_answers]

    # Synthesizer 合成
    from core.llm_client import shared_client as synth_client, DEFAULT_MODEL
    synth_input = (
        f"患者原始问题：{query}\n\n"
        "上游子任务结果（仅供你提炼，不得在最终答复中提及这些内部来源）：\n"
        + "\n\n---\n\n".join(compact_partials)
    )
    finish_reason = ""
    synth_retry = False
    try:
        synth_resp = await synth_client.chat.completions.create(
            model=DEFAULT_MODEL, temperature=0.2, max_tokens=1800,
            messages=[{"role": "system", "content": SYNTHESIZER_PROMPT},
                      {"role": "user", "content": synth_input}]
        )
        choice = synth_resp.choices[0]
        final_answer = (choice.message.content or "").strip()
        finish_reason = getattr(choice, "finish_reason", "") or ""
        if finish_reason == "length":
            synth_retry = True
            audit_log.append("[ParallelDispatch] Synthesizer 输出因 length 截断，触发压缩重写。")
            retry_resp = await synth_client.chat.completions.create(
                model=DEFAULT_MODEL,
                temperature=0.1,
                max_tokens=1600,
                messages=[
                    {"role": "system", "content": SYNTH_REWRITE_PROMPT},
                    {"role": "user", "content": f"患者原始问题：{query}\n\n待重写答复：\n{final_answer}"},
                ],
            )
            retry_choice = retry_resp.choices[0]
            retry_answer = (retry_choice.message.content or "").strip()
            if retry_answer:
                final_answer = retry_answer
                finish_reason = getattr(retry_choice, "finish_reason", "") or finish_reason
    except Exception as e:
        audit_log.append(f"[ParallelDispatch] Synthesizer 异常，使用子任务拼接兜底: {type(e).__name__}")
        final_answer = "\n---\n".join(partial_answers)

    final_answer, halluc_report = await _halluc_guard(
        answer=final_answer,
        evidence=compact_partials,
        domain="general",
        domain_risk="MEDIUM",
        audit_logs=audit_log,
    )

    audit_log.append(f"[ParallelDispatch] 合成完成，{len(intents_list)}路输出已合并")
    return {
        "final_answer": final_answer,
        "trace_data": {"parallel_dispatch": True, "n_intents": len(intents_list),
                       "partial_results": [_compact_parallel_result(r) for r in partial_results],
                       "partial_answers": partial_answers,
                       "compact_partials": compact_partials,
                       "partial_trace_data": [r.get("trace_data") or {} for r in partial_results],
                       "partial_evidence_chains": [r.get("evidence_chain") or {} for r in partial_results],
                       "finish_reason": finish_reason,
                       "synth_retry": synth_retry,
                       "hallucination_check": halluc_report},
        "is_finished": True,
        "next_agent": "END",
        "agent_audit_log": audit_log,
    }


def dynamic_agent_router(state: AgentState) -> str:
    agent_map = {
        "EMERGENCY_TRIGGER": "emergency",
        "RUMOR_VERIFICATION": "rumor_subgraph",
        "MEDICATION_REVIEW": "medication_subgraph",
        "SYMPTOM_ANALYSIS": "symptom",
        "CHITCHAT_OR_REJECT": "chitchat",
        "REPORT_INTERPRETATION": "report",
        "GENERAL_CONSULTATION": "general",
        "emergency": "emergency",
        "pre_flight": "pre_flight",
        END: END
    }

    next_target = state.get("next_agent", "END")
    return agent_map.get(next_target, END)


# ==========================================
# 🌟 6. 图谱编译与主干物理连接
# ==========================================
workflow = StateGraph(AgentState)

workflow.add_node("triage", triage_node)
workflow.add_node("pre_flight", pre_flight_node)
workflow.add_node("symptom", symptom_node)
workflow.add_node("chitchat", chitchat_node)
workflow.add_node("report", report_node)
workflow.add_node("general", general_node)
workflow.add_node("emergency", emergency_node)

# 🌟 核心：将编译好的子网作为包装器节点接入主网
workflow.add_node("rumor_subgraph", rumor_subgraph_wrapper)
workflow.add_node("medication_subgraph", medication_subgraph_wrapper)

workflow.add_conditional_edges(START, entry_router)
workflow.add_conditional_edges("triage", dynamic_agent_router)
workflow.add_conditional_edges("symptom", dynamic_agent_router)
workflow.add_conditional_edges("chitchat", dynamic_agent_router)
workflow.add_conditional_edges("report", dynamic_agent_router)
workflow.add_conditional_edges("general", dynamic_agent_router)
workflow.add_conditional_edges("emergency", dynamic_agent_router)

# 子网流转后直接进入动态路由（根据子图返回的 next_agent 决定去哪）
workflow.add_conditional_edges("rumor_subgraph", dynamic_agent_router)
workflow.add_conditional_edges("medication_subgraph", dynamic_agent_router)
workflow.add_conditional_edges("pre_flight", dynamic_agent_router)

app_graph = workflow.compile()
