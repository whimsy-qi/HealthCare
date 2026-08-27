# 意图驱动的多智能体协作模式自适应选择

## 1. 设计依据

### 1.1 实验数据支撑

Phase 2 实验在 CMB-Exam 1200 题诊断选择题上的结果：

| 模式 | 准确率 | vs 单模型 |
|------|--------|----------|
| Vote-3 | 84.8% | **+2.1pp** |
| Debate-Best (DS+GLM, 1轮) | 83.9% | +1.2pp |
| Debate-Worst (DS+Qwen, 2轮) | 81.8% | -0.8pp |

**核心发现**：诊断选择题是知识检索型任务——三个模型 97.3% 共识率，辩论的通信环节不产生增量。但这不是"辩论无用论"，而是"知识检索型任务不需要辩论"。

### 1.2 任务认知结构分类

不同类型的医疗子任务对应不同的认知结构，需要不同的协作模式：

```
知识检索型（知道就知道）  → 投票集成
  诊断选择题、用药禁忌、常识问答
  模型知识重叠率高，投票捕获 ensemble 增益
  
证据推理型（需要论证）    → 对抗辩论
  辟谣验证、鉴别诊断、复杂病例讨论
  模型知识差异大，辩论产生信息交换增量
  
确定型（流水线足够）      → 单Agent
  报告OCR、信息提取、幻觉检测
  多Agent增加延迟而无增益
```

---

## 2. 协作模式定义

### 模式 S：Single（单模型）

- 一个 LLM 独立处理，无通信，无集成
- 适用：确定性流水线、闲聊、低风险任务
- 延迟：1×

### 模式 V：Vote-N（多模型投票集成）

- N 个异构模型独立盲答 → 多数投票
- 适用：知识检索型任务
- 延迟：N×（并行），成本：N×

### 模式 D：Debate（对抗式辩论）

- Proposer 初诊 → Critic 独立二诊并审查 → Defender 回应 → Moderator 终裁
- 适用：证据推理型任务
- 延迟：3-5×（串行+并行），成本：3-5×

---

## 3. 意图 → 协作模式映射

### 3.1 完整映射表

| 主意图 | 子意图 | Act | Attr | 协作模式 | 参与模型 | 理由 |
|--------|--------|-----|------|---------|---------|------|
| SYMPTOM_ANALYSIS | DIAGNOSIS | SEEK_HELP | DIAGNOSE | **D (Debate)** | DeepSeek(P) + GLM-5.1(C) + DeepSeek(M) | 鉴别诊断是证据推理型任务；临床场景需要可追溯推理路径 |
| SYMPTOM_ANALYSIS | DURATION | ASK | BASIC | **S (Single)** | DeepSeek | 病程问询是信息查询，不需要多Agent |
| RUMOR_VERIFICATION | FACT_CHECK | DEBUNK | any | **D (Debate)** | DeepSeek(Advocate) + Qwen(Skeptic) + GLM-5.1(Judge) | 辟谣是教科书级的对抗式辩论场景；已有 CTAEW 架构 |
| MEDICATION_REVIEW | CONTRAINDICATION | CONFIRM | CAUTION | **S + KG** | DeepSeek + Neo4j | 用药禁忌是确定性查询——KG 结构化数据优先，LLM 做解读 |
| MEDICATION_REVIEW | DOSAGE | ASK | CAUTION | **S + KG** | DeepSeek + Neo4j | 同上 |
| MEDICATION_REVIEW | SIDE_EFFECT | CONFIRM | CAUTION | **S + KG** | DeepSeek + Neo4j | 同上 |
| MEDICATION_REVIEW | INTERACTION | CONFIRM | CAUTION | **S + KG** | DeepSeek + Neo4j | 同上 |
| MEDICATION_REVIEW | GENERAL_MED | ASK | CAUTION | **V (Vote-3)** | 全部三模型 | 模糊用药咨询，多模型投票提高覆盖度 |
| REPORT_INTERPRETATION | LAB_RESULT | ANALYZE | DIAGNOSE | **V (Vote-3)** | 全部三模型 | 检验单解读需多视角；三模型独立解读后投票 |
| GENERAL_CONSULTATION | TREATMENT | SEEK_HELP | CAUTION | **V (Vote-3)** | 全部三模型 | 治疗建议涉及风险，投票提高安全性 |
| GENERAL_CONSULTATION | GENERAL | ASK | BASIC | **S (Single)** | DeepSeek | 健康教育科普，单模型足够 |
| CHITCHAT_OR_REJECT | GREETING | — | — | **S (Single)** | DeepSeek | 非医疗，不需要多Agent |

### 3.2 模式选择决策树

```
用户Query
    │
    ▼
TriageAgent → Domain × Act × Attr
    │
    ├─ Act == DEBUNK？
    │   YES → D (Debate, 对抗式)  ← 辟谣永远走辩论
    │
    ├─ Act == SEEK_HELP && Attr == DIAGNOSE？
    │   YES → D (Debate, 收敛式)  ← 诊断求助走辩论
    │
    ├─ Act == CONFIRM && Attr == CAUTION？
    │   YES → S + KG               ← 用药确认走KG验证
    │
    ├─ Act == ANALYZE？
    │   YES → V (Vote-3)            ← 分析类走投票
    │
    ├─ Act == ASK && Domain != MEDICATION？
    │   YES → S (Single)            ← 常识科普走单模型
    │
    └─ 默认 → V (Vote-3)            ← 不确定时保守投票
```

---

## 4. 架构实现

### 4.1 代码改动

`graph_engine.py` 的 `entry_router` 中新增协作模式选择：

```python
COLLAB_MODE_MAP = {
    # Act × Attr → (mode, models)
    ("DEBUNK",    None):        ("debate",  ["deepseek", "qwen", "glm"]),
    ("SEEK_HELP", "DIAGNOSE"):  ("debate",  ["deepseek", "glm"]),
    ("CONFIRM",   "CAUTION"):   ("single_kg", ["deepseek"]),
    ("ANALYZE",   None):        ("vote",    ["deepseek", "qwen", "glm"]),
    ("ASK",       "BASIC"):     ("single",  ["deepseek"]),
    ("ASK",       "PREVENT"):   ("single",  ["deepseek"]),
}

def select_collab_mode(act: str, attr: str, uncertainty: float):
    key = (act, attr)
    mode, models = COLLAB_MODE_MAP.get(
        key, COLLAB_MODE_MAP.get((act, None), ("vote", ["deepseek", "qwen", "glm"]))
    )
    if uncertainty > 0.3:
        return "vote", ["deepseek", "qwen", "glm"]  # 高不确定性保守投票
    return mode, models
```

### 4.2 与现有架构的整合

```
TriageAgent            ─ 意图识别（已有）
    │
    ▼
select_collab_mode()   ─ 协作模式选择（新增）
    │
    ├─ mode == "single"     → 直接调对应Agent
    ├─ mode == "single_kg"  → Agent + KG 验证
    ├─ mode == "vote"       → VoteRunner（复用 Phase 2 脚本逻辑）
    └─ mode == "debate"     → DebateRunner（复用 Phase 2 脚本逻辑）
         │
         ▼
    Blackboard + agent_loop ─ 共享基础设施
```

**关键设计原则**：协作模式选择是路由层决策，不侵入单个 Agent 内部逻辑。每个 Agent 不知道自己是被"辩论"还是"投票"——它只是收到一个任务并返回结果。上层 Orchestrator 负责协调。

---

## 5. 参考文献

### 5.1 辩论 vs 投票的任务依赖性（核心支撑）

| # | 论文 | 核心结论 | 链接 |
|---|------|---------|------|
| 1 | **Choi, Zhu, Li (2025). "Debate or Vote: Which Yields Better Decisions in Multi-Agent Large Language Models?"** | 系统比较多智能体辩论与投票的决策质量。核心发现：知识检索型任务投票优于辩论，推理型任务辩论优于投票。这是支撑意图驱动模式选择的最核心文献。 | [arXiv:2504](https://arxiv.org/abs/2504) |
| 2 | **Kaesberg et al. (2025). "Voting or Consensus? Decision-Making in Multi-Agent Debate"** | 比较多智能体辩论中投票与共识两种决策协议在不同任务类型下的性能差异。 | [arXiv:2506](https://arxiv.org/abs/2506) |

### 5.2 任务自适应编排

| 3 | **Zhang et al. (2025). "OSC: Cognitive Orchestration through Dynamic Knowledge Alignment in Multi-Agent LLM Collaboration"** | 提出 OSC 框架，根据任务认知复杂性动态选择多智能体协作策略。为"不同任务选不同模式"提供了方法论框架。 | [arXiv:2505](https://arxiv.org/abs/2505) |
| 4 | **Pan, Wu (2025). "Modular Task Decomposition and Dynamic Collaboration in Multi-Agent Systems Driven by Large Language Models"** | 基于任务分解和复杂性指标动态切换协作模式。将复杂任务分解为模块，各模块独立选择最优策略。 | [arXiv:2507](https://arxiv.org/abs/2507) |

### 5.3 集成学习方法论（投票的理论基础）

| 5 | **Dietterich (2000). "Ensemble Methods in Machine Learning"** | 集成学习的经典综述。证明多个独立分类器的多数投票在弱分类器准确率 > 50% 时总能超越单个最强分类器——这是 Vote-3 理论增益的数学基础。 | [Springer LNCS](https://link.springer.com/chapter/10.1007/3-540-45014-9_1) |
| 6 | **Kuncheva & Whitaker (2003). "Measures of Diversity in Classifier Ensembles"** | 提出集成学习中多样性度量的形式化框架。证明多样性（低错误重叠）是集成增益的关键——正是我们用错误重叠矩阵选辩论组合的方法论依据。 | [Machine Learning](https://link.springer.com/article/10.1023/A:1022859024233) |

### 5.4 医疗多智能体协作

| 7 | **Tang et al. (2024). "MedAgents: Large Language Models as Collaborators for Zero-shot Medical Reasoning"** | 多学科协作框架，5 步流程：专家召集→独立分析→综合报告→迭代讨论→最终决策。在 MedQA/USMLE 上验证了多智能体优于单模型。 | [ACL 2024 Findings](https://aclanthology.org/2024.findings-acl.33/) |
| 8 | **Yang et al. (2024). "MedAide: Information Fusion and Anatomy of Medical Intents via LLM-based Agent Collaboration"** | 将医疗意图分类直接链接到特定 LLM 协作工作流。与本方案最接近的已有工作。 | [arXiv:2411](https://arxiv.org/abs/2411) |
| 9 | **袁嵩等 (2025). "基于LLM与多智能体协同的医疗对话机制研究"** | 行为×属性双层意图划分，三层决策模型（独立→交互→协同）。本系统意图本体和 MADDx 架构的核心参考。 | 计算机技术与发展, 2025.8 |

### 5.5 层级式医疗决策（集中式拓扑支持）

| 10 | **Kim et al. (2025). "TAO: Tiered Agentic Oversight for Healthcare Safety"** | 层级式多智能体架构（护士→医生→专家），吸收 24% 个体错误，在 4/5 医疗安全基准上超越同层 +8.2%。为集中式辩论拓扑提供了直接实验支撑。 | [arXiv:2506](https://arxiv.org/abs/2506) |

---

## 6. 论文中的理论框架声明

> "本研究提出的意图驱动的多智能体协作模式自适应选择机制建立在两个理论基础之上：
>
> （1）**任务-策略匹配假说**（Choi et al., 2025）：多智能体协作的最优形式取决于任务的内在认知结构——知识检索型任务适合投票集成，证据推理型任务适合对抗辩论。本研究将这一假说从实验对比扩展为可运行的工程系统，通过三轴意图模型（Domain × Act × Attr）实现运行时的策略切换。
>
> （2）**集成学习多样性理论**（Kuncheva & Whitaker, 2003）：多个独立分类器的集成增益来源于它们错误模式的多样性。本研究通过错误重叠矩阵量化了候选模型间的认知互补性，为辩论/投票的模型选择提供了形式化依据。
>
> 与已有工作（MedAide, OSC）将意图识别仅用于 Agent 选择不同，本机制首次将意图识别与协作模式选择相统一，填补了面向医疗场景的意图自适应多智能体协作研究空白。"

---
*文档版本: 2026-05-02*
