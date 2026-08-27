# 毕设项目总览：现阶段成果 + 改造计划 + 补充实验

---

## Part 1: 现阶段成果汇总

### 1.1 基线模型评测（Phase 1）

| 成果 | 数据 |
|------|------|
| 评测集 | CMB-Exam 1200 题（规培结业+执业医师+临床医学，单选1108+多选92） |
| 候选模型 | DeepSeek-V4-Pro / Qwen-Max / GLM-5.1 / GPT-4o |
| 指标 | 单选准确率 + 多选 P/R/F1 + 错误重叠矩阵 |

**核心表格**：

| 模型 | 单选准确率 | 多选准确率 | 多选 P | 多选 R | 多选 F1 |
|------|----------|----------|--------|--------|---------|
| DeepSeek-V4-Pro | 82.2% | 63.0% | 89.3% | 94.6% | 91.9% |
| GLM-5.1 | 81.2% | 78.3% | 95.9% | 95.3% | 95.6% |
| Qwen-Max | 80.1% | 65.2% | 94.5% | 91.8% | 93.1% |
| GPT-4o | 75.2% | 65.2% | 91.3% | 96.2% | 93.7% |

**错误重叠矩阵**：

| | DeepSeek | Qwen | GLM-5.1 | GPT-4o |
|--|:--:|:--:|:--:|:--:|
| DeepSeek | — | 74.0% | 77.4% | 81.7% |
| Qwen | 74.0% | — | 76.5% | 82.9% |
| GLM-5.1 | 77.4% | 76.5% | — | 82.3% |

### 1.2 跨模型辩论实验（Phase 2）

| 成果 | 数据 |
|------|------|
| 实验组数 | 5 组（Vote-3 + 2 组 × 2 种辩论） |
| 辩论协议 | 盲答 → Critic审查 → Proposer防守 → Moderator终裁 |
| 关键指标 | 准确率、分歧率、修正/误伤数、类别分解 |

**核心表格**：

| 模式 | 准确率 | vs 最佳单模型 | 特征 |
|------|--------|------------|------|
| **Vote-3** | **84.8%** | **+2.1pp** | 三模型多数投票 |
| Debate-B-1r (DS+GLM) | 83.9% | +1.2pp | 最佳辩论组合 |
| Debate-B-2r (DS+GLM) | 83.5% | +0.8pp | 2轮反不如1轮 |
| Debate-A-1r (DS+Qwen) | 81.8% | -0.8pp | Qwen不擅长诊断批评 |
| Debate-A-2r (DS+Qwen) | 81.8% | -0.8pp | 2轮=1轮，零增量 |

**关键发现**：
- Vote-3 在全部类别上优于或持平单模型（规培+3.2pp，临床+3.0pp）
- 辩论修正42-91例但误伤52-76例，净值最高仅+15（GLM-5.1）
- 2轮辩论 ≤ 1轮，轮数无增量
- 模型一致时正确率 88-89%，分歧后仅 35-41%

### 1.3 知识图谱 v2 重建

| 成果 | 数据 |
|------|------|
| 节点类型 | 4类 → 8类（新增 Food/Check/Producer/Cure） |
| 关系类型 | 4种 → 15种（新增 11 种） |
| 数据规模 | 64,548 节点 / 404,615 关系 |
| 基准对齐 | 完整 Disease 8,799 节点，与 RAGQnASystem (8,808) 持平 |
| 向量化 | 全部 64,548 节点 DashScope embedding 完成 |
| 前端 | 8 类节点颜色/图例 + 15 种关系中文标签 + 完整图例面板 |
| 数据质量 | 垃圾 Disease 节点已清理；14,719 Stub 占位节点已标记并过滤 |

### 1.4 关键 Bug 修复

- MADDx Critic 的 `missing_symptom` objection 类型已禁用
- 跨模型辩论协议的三项修正：Vote-3 clean voting / 盲答消除锚定 / Moderator 终裁节点
- 辩论脚本断点续跑机制
- API 重试指数退避

---

## Part 2: 意图驱动的协作模式选择 — 改造计划

### 2.1 改造目标

在 `graph_engine.py` 的路由层新增协作模式选择，使不同意图走不同的多Agent协作方式。

**不侵入现有Agent内部逻辑**。Agent 不知道自己是"被投票"还是"在辩论"——它只收到任务并返回结果。上层 Orchestrator 负责协调。

### 2.2 改动范围

| 文件 | 改动 | 行数 | 风险 |
|------|------|------|------|
| `graph_engine.py` | 新增 `select_collab_mode()` + 修改 `dynamic_agent_router` | ~50 行 | 低——新增分支，不删旧代码 |
| `core/intent_ontology.py` | 新增 `COLLAB_MODE_MAP` 常量 | ~30 行 | 零——纯常量定义 |
| `experiments/debate_runner.py` | 抽取出 `VoteRunner` 和 `DebateRunner` 类，可供 `graph_engine.py` 调用 | ~40 行 | 低——现有代码抽取 |
| 测试 | 新增 `test_collab_routing.py` | ~30 行 | — |

### 2.3 四种协作模式定义

| 模式 | 缩写 | 含义 | 延迟 | 成本 |
|------|------|------|------|------|
| Single | **S** | 单模型直接调用，无通信 | 1× | 1× |
| Single+KG | **S+KG** | 单模型 + Neo4j 结构化查询验证 | 1.5× | 1× |
| Vote-3 | **V** | 三模型独立盲答 → 多数投票 | 3× (并行) | 3× |
| Debate | **D** | Proposer → Critic审查 → Defender回应 → Moderator终裁 | 3-5× | 3-5× |

### 2.4 完整意图 → 协作模式映射表

#### SYMPTOM_ANALYSIS（症状分析）

| Sub-intent | Act | Attr | 模式 | 模型 | 依据 |
|-----------|-----|------|------|------|------|
| DIAGNOSIS | SEEK_HELP | DIAGNOSE | **D** | DS(P)+GLM(C)+DS(M) | Phase 2 数据：诊断是证据推理型任务，GLM-5.1作为Critic最优(83.9%)；DS Proposer在多选弱但GLM补强 |
| DIAGNOSIS | CONFIRM | DIAGNOSE | **V** | DS+QW+GLM | 用户已有判断仅求确认，不需要辩论的对抗压力，投票更经济 |
| DIAGNOSIS | ANALYZE | DIAGNOSE | **V** | DS+QW+GLM | 用户提供数据求分析（如"尿酸520提示什么"），多视角解读优于单视角 |
| DURATION | ASK | BASIC | **S** | DS | 信息查询型：问病程/严重度，单模型知识覆盖足够 |
| DURATION | ASK | VISIT | **S** | DS | 问就医建议，低复杂度，单模型够 |

#### RUMOR_VERIFICATION（辟谣求证）

| Sub-intent | Act | Attr | 模式 | 模型 | 依据 |
|-----------|-----|------|------|------|------|
| FACT_CHECK | DEBUNK | CAUSE | **D** | DS(A)+QW(S)+GLM(J) | 辟谣是教科书级对抗式辩论场景——Advocate为说法辩护，Skeptic找证据反驳。已有CTAEW架构验证。Choi et al. (2025)确认推理型任务辩论优于投票 |
| FACT_CHECK | DEBUNK | BASIC | **D** | DS(A)+QW(S)+GLM(J) | 同上 |
| FACT_CHECK | CONFIRM | CAUSE | **D** | DS(A)+QW(S)+GLM(J) | 用户对某说法求证真假，即使Act=CONFIRM也走辩论（需要正反证据对抗） |

#### MEDICATION_REVIEW（用药审查）

| Sub-intent | Act | Attr | 模式 | 模型 | 依据 |
|-----------|-----|------|------|------|------|
| CONTRAINDICATION | CONFIRM | CAUTION | **S+KG** | DS + Neo4j | KG有29,794条禁忌关系 + 72,263条治疗关系——结构化数据直接查，LLM做解读。确定性查询不需要多模型集成 |
| DOSAGE | ASK | CAUTION | **S+KG** | DS + Neo4j | 同上，药品说明书结构化查询优先 |
| SIDE_EFFECT | CONFIRM | CAUTION | **S+KG** | DS + Neo4j | 同上 |
| INTERACTION | CONFIRM | CAUTION | **S+KG** | DS + Neo4j | 同上 |
| GENERAL_MED | ASK | CAUTION | **V** | DS+QW+GLM | 模糊用药咨询（如"我该吃什么药"），无具体药名可查KG，需多模型投票提高安全覆盖 |

#### REPORT_INTERPRETATION（报告解读）

| Sub-intent | Act | Attr | 模式 | 模型 | 依据 |
|-----------|-----|------|------|------|------|
| LAB_RESULT | ANALYZE | DIAGNOSE | **V** | DS+QW+GLM | 检验单解读需多视角降低单一模型解读偏差；三模型独立解读后投票选出最合理结果 |
| LAB_RESULT | ANALYZE | CHECKUP | **V** | DS+QW+GLM | 同上 |
| LAB_RESULT | CONFIRM | DIAGNOSE | **V** | DS+QW+GLM | 用户已有判断来确认报告，三模型投票降低单一确认偏差 |

#### GENERAL_CONSULTATION（综合问答）

| Sub-intent | Act | Attr | 模式 | 模型 | 依据 |
|-----------|-----|------|------|------|------|
| TREATMENT | SEEK_HELP | CAUTION | **V** | DS+QW+GLM | 求治疗建议涉及医疗风险，Vote-3提高安全性；Phase 2确认Vote-3优于单模型+2.1pp |
| TREATMENT | SEEK_HELP | VISIT | **V** | DS+QW+GLM | 同上 |
| GENERAL | ASK | BASIC | **S** | DS | 常识科普（如"维生素D有什么作用"），低风险低复杂度，多Agent浪费成本 |
| GENERAL | ASK | PREVENT | **S** | DS | 预防知识科普，同上 |
| GENERAL | ASK | CAUSE | **S** | DS | 病因机制询问，单模型医学知识充足 |
| GENERAL | ASK | SYMPTOM | **S** | DS | 症状知识询问，同上 |
| GENERAL | ASK | CHECKUP | **S** | DS | 检查项目科普，同上 |
| GENERAL | ASK | VISIT | **S** | DS | 就医建议，同上 |

#### CHITCHAT_OR_REJECT（闲聊与拒绝）

| Sub-intent | Act | Attr | 模式 | 模型 | 依据 |
|-----------|-----|------|------|------|------|
| GREETING | — | — | **S** | DS | 非医疗对话，不需要任何多Agent机制 |

#### EMERGENCY（紧急）

| Sub-intent | Act | Attr | 模式 | 模型 | 依据 |
|-----------|-----|------|------|------|------|
| 任意 | 任意 | 任意 | **S** | DS | 急诊场景延迟优先——直接走急诊哨兵，跳过所有多Agent环节 |

### 2.5 不确定性降级规则

当 Triage 输出满足以下任一条件时，任何模式强制降级为 Vote-3（保守优先）：

1. Triage `thinking` 字段包含"不确定/可能/疑似/模糊"等词
2. 并行意图数组 `parallel_intents` 长度 ≥ 3（系统对意图判断分散）
3. `extracted_entities` 为空但 Domain 是 MEDICATION_REVIEW（未提取到药名时用药审查无意义）

**降级的理论依据**：集成学习中，当基分类器置信度低时，多数投票的鲁棒性优势凸显。Dietterich (2000) 证明当弱分类器准确率 > 50% 时，多数投票总能超越最佳单个分类器——在不确定时选择投票是理论上最优的保守策略。

### 2.6 实验依据汇总

| 设计决策 | 实验依据 |
|---------|---------|
| 诊断选择题用 Vote-3 | Phase 2: Vote-3 84.8% > 最佳单模型 82.7%，+2.1pp |
| 诊断辩论不如投票 | Phase 2: 最佳 Debate 83.9% < Vote-3 84.8%，-0.9pp |
| GLM-5.1 是最佳 Critic | Phase 2: DS+GLM 83.9% > DS+Qwen 81.8%，+2.1pp |
| 2轮辩论无效 | Phase 2: Debate-B-2r (83.5%) ≤ Debate-B-1r (83.9%) |
| Qwen 不适合做诊断 Critic | Phase 2: Debate-A 81.8% < Single-DS 82.7%，净负收益 |
| 辟谣用 Debate | 已有 CTAEW 架构；任务特征是证据对抗非知识检索 |
| 用药确认用 Single+KG | KG 有 29,794 CONTRAINDICATED_FOR + 72,263 TREATS，结构化数据优先于 LLM 猜测 |
| 常识用 Single | Phase 1：执业医师 92% 准确率——简单题多Agent无增益 |
| 不确定时降级 Vote-3 | Phase 2: 27 例三模型全错但投票正确——降级有实证基础 |

### 2.7 参考文献

| # | 论文 | 链接 | 用途 |
|---|------|------|------|
| 1 | Choi, Zhu, Li (2025). "Debate or Vote: Which Yields Better Decisions in Multi-Agent LLMs?" | https://arxiv.org/abs/2504 | 任务-策略匹配核心理论支撑 |
| 2 | Wang et al. (2025). "Beyond Frameworks: Unpacking Collaboration Strategies in MAS" | https://arxiv.org/abs/2503 | 多Agent协作策略分类框架 |
| 3 | Zhang et al. (2025). "OSC: Cognitive Orchestration through Dynamic Knowledge Alignment" | https://arxiv.org/abs/2505 | 自适应编排方法论 |
| 4 | Pan, Wu (2025). "Modular Task Decomposition and Dynamic Collaboration in MAS" | https://arxiv.org/abs/2507 | 基于任务复杂度的动态协作切换 |
| 5 | Yang et al. (2024). "MedAide: Information Fusion and Anatomy of Medical Intents" | https://arxiv.org/abs/2411 | 最接近的已有工作——医疗意图链接到协作工作流 |
| 6 | Kim et al. (2025). "TAO: Tiered Agentic Oversight for Healthcare Safety" | https://arxiv.org/abs/2506 | 层级式医疗MAS实验支撑（吸收24%错误） |
| 7 | Feng et al. (2025). "UCAgents: Unidirectional Convergence for Medical Decision-Making" | https://arxiv.org/abs/2512 | 层级收敛优于开放式辩论 |
| 8 | Tang et al. (2024). "MedAgents: LLMs as Collaborators for Zero-shot Medical Reasoning" | https://aclanthology.org/2024.findings-acl.33/ | ACL 2024，多Agent零样本医疗推理标杆 |
| 9 | Wu et al. (2023). "AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation" | https://arxiv.org/abs/2308.08155 | Agent 消息传递抽象 |
| 10 | Hong et al. (2023). "MetaGPT: Meta Programming for Multi-Agent Collaborative Framework" | https://arxiv.org/abs/2308.00352 | SOP驱动的角色分配 |
| 11 | Chen et al. (2023). "AgentVerse: Facilitating Multi-Agent Collaboration" | https://arxiv.org/abs/2308.10848 | 四阶段编排循环 |
| 12 | Dietterich (2000). "Ensemble Methods in Machine Learning" | https://link.springer.com/chapter/10.1007/3-540-45014-9_1 | 多数投票超越最佳分类器的数学条件 |
| 13 | Kuncheva & Whitaker (2003). "Measures of Diversity in Classifier Ensembles" | https://link.springer.com/article/10.1023/A:1022859024233 | 多样性度量的形式化框架 |
| 14 | Guo et al. (2024). "LLM-based Multi-Agents: A Survey of Progress and Challenges" | https://arxiv.org/abs/2402.01680 | MAS 四大挑战综述 |
| 15 | 袁嵩等 (2025). "基于LLM与多智能体协同的医疗对话机制研究" | 计算机技术与发展, 2025.8 | 三轴意图模型的直接来源 |
| 16 | 谌文佳等 (2025). "嵌入意图识别的医疗健康问答文本语义分类模型" | 数据分析与知识发现, 2025.2 | 意图嵌入下游处理的方法论 |

---

## Part 3: 补充实验设计

### 3.1 需要补充的实验

#### 实验 1: 辟谣场景的跨模型辩论验证 [P0]

**目的**：验证辟谣场景下 Debate > Vote——这是诊断实验的对立面，证明"不同任务需要不同协作模式"。

**方法**：在已有 100 条 rumor eval 数据上跑：
- Single-DeepSeek（辟谣基线）
- Vote-3（三模型投票）
- Debate-Advocate+单Skeptic（DeepSeek Advocate + Qwen Skeptic + GLM-5.1 Judge）

**预期**：Debate > Vote（辟谣是证据对抗型，与诊断选择题相反）

**已有基础**：`experiments/run_rumor_ablation.py` + 100 条 rumor eval 数据

**论文价值**：这是唯一能证明"辩论在某场景下优于投票"的证据，对论文核心叙事不可或缺。

#### 实验 2: 意图路由准确率验证 [P1]

**目的**：验证映射表中每个意图在使用了推荐的协作模式后确实优于单模型。

**方法**：在自建 40 条多意图测试集上：
- 每条有 ground truth intent
- 分别用 Single 和推荐模式跑
- 对比每种意图下的准确率

**数据**：已有 `test_cases.json`（40 条，可扩展到不同意图）

#### 实验 3: 用药审查 Single+KG vs Single [P2]

**目的**：验证 S+KG 模式确实优于纯 LLM。

**方法**：从 drug_data Excel 的禁忌列表中抽样 50 条用药安全性查询

---

## Part 4: 论文实验章节结构（建议）

```
第4章 实验设计与结果分析
  4.1 实验环境与数据集
  4.2 单模型基线评测
  4.3 跨模型辩论实验
  4.4 辩论协议消融（轮数/Critic类型/拓扑）
  4.5 意图驱动的协作模式自适应选择
  4.6 分析与讨论
```

---

*文档版本: 2026-05-02*
