"""
MADDx 黑板通信协议的结构化 Schema
------------------------------------------------
所有跨 Agent 消息必须符合此处定义的 TypedDict，配合 LLM 的
`response_format={"type":"json_object"}` 做 schema-enforced communication。
这是论文第 4 章的核心设计点：避免 Agent 之间自然语言误传。

D8 扩展（工具增强辩论）：
  - 新增 ToolCall / ToolResult：每次 agent 调用工具都留痕
  - Objection.evidence_refs: List[str] → List[int]（指向 Blackboard ToolResult 的 version）
  - Candidate 新增 evidence_refs 字段
  - TerminationReason 新增 NO_NEW_EVIDENCE
"""
from typing import TypedDict, Literal, List, Optional, Any


# ====== 基础病例输入 ======

class Symptom(TypedDict):
    name: str
    duration_days: Optional[int]
    severity: Literal["mild", "moderate", "severe"]


class PatientProfile(TypedDict, total=False):
    age: int
    gender: Literal["male", "female", "other"]
    chronic_conditions: List[str]
    allergies: List[str]


# ====== 工具层（D8 新增） ======

ToolName = Literal["kg_query", "rag_search", "web_search", "pubmed_search", "social_search"]


class ToolCall(TypedDict):
    tool: ToolName
    args: dict                    # 工具入参，如 {"mode": "disease_symptoms", "disease": "偏头痛"}
    caller_agent: str             # proposer / critic / defender
    caller_round: int             # 调用发生在第几轮辩论


class ToolHit(TypedDict, total=False):
    """工具命中的单条证据。不同工具字段不同，用 total=False 允许缺省。"""
    ref: str                      # 外部引用 ID（如 "kg:Disease:偏头痛"、"rag:chunk_42"、"web:<url>"）
    subject: str                  # KG: 主语
    predicate: str                # KG: 谓词
    object: str                   # KG: 宾语
    text: str                     # RAG / web: 文本内容
    source: str                   # RAG / web: 来源
    score: float                  # rerank 置信度


class ToolResult(TypedDict):
    call_ref: int                 # 指向 Blackboard 中对应 ToolCall 条目的 version
    tool: ToolName
    hits: List[ToolHit]
    hit_count: int
    cached: bool                  # 是否命中会话级缓存（不计入"新证据"）


# ====== Proposer / Defender 的输出 ======

class Candidate(TypedDict, total=False):
    disease: str                       # 标准化疾病名（经 KG 别名归一化后）
    icd10: Optional[str]               # 可选，若 KG 命中
    reasoning: str                     # 一句话诊断依据
    supporting_symptoms: List[str]     # 从输入症状中选取
    confidence: float                  # [0, 1]
    evidence_refs: List[int]           # D8 新增：支持该候选的 Blackboard version 列表


# ====== Critic 的输出 ======

ObjectionType = Literal[
    "missing_symptom",          # 缺少该诊断的典型症状
    "contradicting_symptom",    # 现有症状与诊断矛盾
    "epidemiology_mismatch",    # 流行病学不匹配（如年龄/性别）
    "kg_refutation",            # KG 中不存在该症状-疾病关系
    "guideline_mismatch",       # 指南/文献不支持（D8 新增，搭配 rag_search）
]


class Objection(TypedDict, total=False):
    target_disease: str                # 针对哪个候选
    type: ObjectionType
    detail: str                        # 质疑的具体内容
    evidence_refs: List[int]           # D8 改：指向 Blackboard ToolResult 的 version（原来是 List[str]）
    triggered_by_tool: Optional[int]   # D8 新增：本条 objection 由哪次 tool_call 触发


# ====== Defender 对 Objection 的回应 ======

RebuttalStance = Literal["accept", "partial_accept", "reject"]
RebuttalAction = Literal["drop", "demote_confidence", "keep", "add_new"]


class Rebuttal(TypedDict, total=False):
    target_objection_idx: int       # 回应黑板中第几条 objection
    stance: RebuttalStance
    action: RebuttalAction
    justification: str
    evidence_refs: List[int]        # D8 新增：反驳所依据的新证据


# ====== Moderator 的最终输出 ======

TerminationReason = Literal[
    "MAX_ROUNDS_REACHED",
    "TOP1_STABLE_HIGH_CONF",
    "NO_VALID_OBJECTIONS",
    "NO_NEW_EVIDENCE",             # D8 新增：连续两轮 agent 未取得新证据
]


class FinalDiagnosis(TypedDict, total=False):
    primary_dx: str
    differential_dx: List[str]           # 次要鉴别诊断
    confidence: float
    supporting_evidence: List[str]
    recommended_tests: List[str]
    termination_reason: TerminationReason
    rounds_used: int
    narrative: str                       # D7 引入：科主任口吻叙事 markdown

    # D8 新增：全局证据溯源
    total_tool_calls: int
    total_evidence_hits: int
