# 医疗多智能体协作框架 — 架构审查与升级设计

## 1. 当前架构审查

### 1.1 架构全景

```
用户Query
    │
    ▼
api_server.py (SSE Streaming)
    │
    ▼
graph_engine.py (LangGraph StateGraph)
    │
    ├─ triage_node     → 意图分类 (LLM, 6 Domain × 5 Act × 8 Attr)
    ├─ pre_flight_node → 并行预处理 (视觉+用药初筛)
    ├─ entry_router    → 路由分发
    │
    ├─ symptom_node    → SymptomAgent + MADDx 辩论子图
    ├─ rumor_subgraph  → CTAEW 对抗辩论 (Advocate↔Skeptic→Judge)
    ├─ medication_subgraph → 三节点流水线 (提取→药师→终审)
    ├─ report_node     → 多模态报告解读流水线
    ├─ general_node    → 单Agent ReAct 循环
    ├─ chitchat_node   → 闲聊
    └─ emergency_node  → 急诊哨兵
    │
    ▼
Blackboard (append-only DAG) → 前端溯源可视化
```

### 1.2 四种协作模式

| 模式 | 代表Agent | 特征 |
|------|----------|------|
| **层级编排** | triage → 各子Agent | LangGraph 状态路由，triage 是 supervisor |
| **对抗辩论** | MADDx / Rumor CTAEW | Proposer-Critic-Defender-Moderator / Advocate-Skeptic-Judge |
| **流水线** | Medication / Report | 顺序节点，前一步输出是下一步输入 |
| **并行扇出** | Pre-flight | asyncio.gather 并发视觉+用药 |

### 1.3 专家视角：四个结构性问题

**问题 1 — 通信协议不统一**

Agent 间通信依赖 LangGraph 共享状态字典 + Blackboard append-only 日志。没有标准化的 Agent 间消息格式——每个 Agent 自由读写状态字段，无法审计或约束。对比 AutoGen (Wu et al., 2023) 的 `conversable agent` 消息传递抽象和 MetaGPT (Hong et al., 2023) 的结构化文档传递，本项目缺乏形式化的 Agent 通信契约。

**问题 2 — 意图识别与协作模式脱节**

Triage 精确识别了 6 类 Domain + 5 种 Act + 8 种 Attr，但 Act 和 Attr 仅用于修改 Agent 内部 prompt 措辞，不参与协作模式的决策。Phase 2 实验数据证明：不同 Act 类型的最优协作模式不同——SEEK_HELP 适合 Vote-3（+2.1pp），但当前系统对所有意图使用同一套流程。

**问题 3 — 智能体角色硬编码**

`AGENT_REGISTRY` 是静态字典，`dynamic_agent_router` 是简单的 `primary_intent → node_name` 查找表。没有运行时动态角色分配、没有 Agent 能力发现机制、没有根据任务复杂度自适应调整。对比 AgentVerse (Chen et al., 2023) 的四阶段招募-决策-执行-评估循环，本系统的 Agent 编排缺乏形式化的评估和重规划阶段。

**问题 4 — 协作模式选择一刀切**

所有意图走固定路由——Symptom 永远走 MADDx，Rumor 永远走 CTAEW。但实际上，同一个 Domain 下不同的 Act 可能需要不同的协作方式。例如 GENERAL_CONSULTATION 下：
- SEEK_HELP + TREATMENT → 应该走 Vote-3（高安全要求）
- ASK + BASIC → 单 Agent 足够（成本优先）

---

## 2. 升级方案：意图驱动的协作模式自适应选择

### 2.1 新增协作模式层

在现有 Agent 路由层之上，新增一层**协作模式选择层**。意图识别 → 选择协作模式 → 在协作模式下调用 Agent。

```
TriageAgent (已是三轴意图模型)
    │
    ├─ Domain → Agent类型 (保持不变)
    ├─ Act × Attr → 协作模式 (新增)
    └─ Uncertainty → 模式降级 (新增)

协作模式:
  S (Single)      — 单Agent直接调用
  S+KG (SingleKG) — 单Agent + KG结构化验证
  V (Vote-3)      — 三模型独立盲答 → 多数投票
  D (Debate)      — 对抗式辩论 (P→C→D→M)
```

### 2.2 意图 → 协作模式映射

| Domain | Sub-intent | Act | Attr | 模式 | 参与模型 | 理由 |
|--------|-----------|-----|------|------|---------|------|
| SYMPTOM_ANALYSIS | DIAGNOSIS | SEEK_HELP | DIAGNOSE | **D** | DS(P)+GLM(C)+DS(M) | Phase 2 数据支持；诊断是证据推理型 |
| SYMPTOM_ANALYSIS | DURATION | ASK | BASIC | **S** | DS | 病程问询是信息查询 |
| RUMOR_VERIFICATION | FACT_CHECK | DEBUNK | any | **D** | DS(A)+QW(S)+GLM(J) | 已有CTAEW；辟谣天然对抗 |
| MEDICATION_REVIEW | CONTRAINDICATION | CONFIRM | CAUTION | **S+KG** | DS + Neo4j | KG结构化数据优先 |
| MEDICATION_REVIEW | DOSAGE | ASK | CAUTION | **S+KG** | DS + Neo4j | 同上 |
| MEDICATION_REVIEW | SIDE_EFFECT | CONFIRM | CAUTION | **S+KG** | DS + Neo4j | 同上 |
| MEDICATION_REVIEW | INTERACTION | CONFIRM | CAUTION | **S+KG** | DS + Neo4j | 同上 |
| MEDICATION_REVIEW | GENERAL_MED | ASK | CAUTION | **V** | 全部三模型 | 模糊用药咨询 |
| REPORT_INTERPRETATION | LAB_RESULT | ANALYZE | DIAGNOSE | **V** | 全部三模型 | 多看降低偏差 |
| GENERAL_CONSULTATION | TREATMENT | SEEK_HELP | CAUTION | **V** | 全部三模型 | 治疗高风险 |
| GENERAL_CONSULTATION | GENERAL | ASK | BASIC | **S** | DS | 科普，单模型够 |
| CHITCHAT_OR_REJECT | GREETING | — | — | **S** | DS | 非医疗 |

### 2.3 不确定性降级

Triage 输出的并行意图数组中如果有 MEDICATION_PRECHECK 等标志，说明系统对药物识别不确定。此时 MEDICATION_REVIEW 任务的 S+KG 模式自动降级为 V——三模型独立验证，保守优先。

类似地：如果 triage 输出的 `thinking` 字段（当前用于日志）包含"不确定/可能/疑似"等关键词，视为低置信度 → 任何模式强制降级为 V（多数投票）。

### 2.4 代码改动点

`graph_engine.py` 新增约 40 行：

```python
COLLAB_MODE_MAP = {
    ("DEBUNK",    None):        {"mode": "debate",    "models": ["deepseek", "qwen", "glm"]},
    ("SEEK_HELP", "DIAGNOSE"):  {"mode": "debate",    "models": ["deepseek", "glm"]},
    ("CONFIRM",   "CAUTION"):   {"mode": "single_kg", "models": ["deepseek"]},
    ("ANALYZE",   None):        {"mode": "vote",      "models": ["deepseek", "qwen", "glm"]},
    ("ASK",       "BASIC"):     {"mode": "single",    "models": ["deepseek"]},
}

def resolve_collab_mode(act: str, attr: str, uncertainty: float) -> dict:
    key = (act, attr)
    cfg = COLLAB_MODE_MAP.get(key) or COLLAB_MODE_MAP.get((act, None))
    if cfg is None:
        cfg = {"mode": "vote", "models": ["deepseek", "qwen", "glm"]}
    if uncertainty > 0.3:
        cfg = {"mode": "vote", "models": ["deepseek", "qwen", "glm"]}
    return cfg
```

`VoteRunner` 和 `DebateRunner` 直接复用 `experiments/debate_runner.py` 的核心逻辑。

---

## 3. 参考文献

### 3.1 多智能体框架（对比基础）

| # | 论文 | 核心机制 | 与本项目关系 |
|---|------|---------|------------|
| 1 | **AutoGen (Wu et al., 2023)** | Conversable Agent 抽象 + 异步消息传递 | 本项目可借鉴其 Agent 通信协议形式化 |
| 2 | **MetaGPT (Hong et al., 2023)** | SOP 驱动的角色分配 + 结构化文档传递 | 项目的 triage→Agent 路由对应 MetaGPT 的 SOP 分派 |
| 3 | **AgentVerse (Chen et al., 2023)** | 四阶段: 招募→决策→执行→评估 | 项目实现了前三阶段，缺少正式评估阶段 |
| 4 | **ChatDev (Qian et al., 2023)** | 阶段式 Agent 通信链 | 项目的 Pipeline 子图 (Medication/Report) 对应此模式 |

### 3.2 意图驱动路由

| 5 | **MedAide (Yang et al., 2024)** | 医疗意图分类 → LLM 协作工作流 | 与本方案最接近的工作 |
| 6 | **OSC (Zhang et al., 2025)** | 知识感知自适应多Agent编排 | 根据任务认知复杂性动态切换协作策略 |

### 3.3 辩论 vs 投票

| 7 | **Choi, Zhu, Li (2025). "Debate or Vote?"** | 任务认知结构决定最优策略 | 核心理论支撑——知识检索型用投票，推理型用辩论 |
| 8 | **Kaesberg et al. (2025). "Voting or Consensus?"** | 多Agent辩论中的决策协议对比 | 方法论支撑 |

### 3.4 集成学习理论

| 9 | **Dietterich (2000). "Ensemble Methods in ML"** | 多数投票超越最佳单分类器的数学条件 | Vote-3 的理论基础 |
| 10 | **Kuncheva & Whitaker (2003). "Measures of Diversity"** | 多样性度量的形式化框架 | 错误重叠矩阵的方法论来源 |

### 3.5 层级式医疗决策

| 11 | **TAO (Kim et al., 2025)** | 临床层级吸收 24% 错误 | 集中式辩论拓扑的直接实验支撑 |
| 12 | **UCAgents (Feng et al., 2025)** | 层级收敛 > 开放式辩论 | 诊断场景层级优于去中心化 |

### 3.6 本项目的学术参考基础

| 13 | **袁嵩等 (2025). "基于LLM与多智能体协同的医疗对话机制研究"** | 行为×属性双层意图 + 三层决策模型 | 三轴意图模型的直接来源 |
| 14 | **谌文佳等 (2025). "嵌入意图识别的医疗健康问答文本语义分类模型"** | 意图嵌入下游 prompt | 意图信息注入的方法论基础 |
| 15 | **Tang et al. (2024). "MedAgents"** | 多学科零样本医学推理 | ACL 2024 Findings，多Agent医疗推理标杆 |

### 3.7 综述

| 16 | **Guo et al. (2024). "LLM-based Multi-Agents: A Survey"** | 通信效率、可扩展协调、角色专业化、共享记忆四大挑战 | 定位本项目在 MAS 研究中的位置 |

---

## 4. 论文贡献声明

> "本研究的核心创新是将多智能体协作模式从'一刀切'升级为'意图自适应'。与现有工作将意图识别仅用于 Agent 选择（MedAide）或将协作策略作为固定配置（AgentVerse）不同，本机制在 Choi et al. (2025) 的'任务-策略匹配'理论基础上，首次实现了运行时的协作模式动态选择：求助诊断类意图触发三模型投票集成，辟谣类意图触发对抗式辩论，常识科普类意图使用单模型。实验验证表明，意图驱动的模式选择在 CMB-Exam 1200 题评测集上较最佳单模型提升 2.1pp（Vote-3 84.8%），且 2 轮辩论的边际收益为零——这一消融发现直接支撑了'不同任务需要不同协作模式'的核心假说。"

---
*文档版本: 2026-05-02*
