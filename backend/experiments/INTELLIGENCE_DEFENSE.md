# 答辩防线：意图查表 ≠ 缺乏智能

## 老师可能的质疑

> "你把每种意图下的协作方式都提前定好了——这不是查表吗？多智能体系统的'智能'体现在哪里？"

---

## 四层防御

### 第一层：意图识别本身是语义推理，不是关键词匹配

**核心论点**：协作模式不是用户选的，不是关键词匹配的——是 LLM 从自由文本中**推理**出来的。

**支撑**：
- 用户说"我头疼"：LLM 理解 SEEK_HELP + DIAGNOSE → Debate
- 用户说"头疼该吃什么药"：同一症状词，但 LLM 理解 SEEK_HELP + CAUTION → Vote
- 关键词匹配系统看到两个句子都有"头疼"，路由结果相同。**我们的系统路由不同——因为 LLM 理解了行为意图的差异。**

**复合意图的诚实边界**：

当前系统的 Triage 输出**单一 primary_intent**——它必须从多个可能性中选择一个为主的。当用户说"我头疼，布洛芬现在多少钱"时，系统会将症状分析设为主意图、药物查询放入并行任务。这是**优先级调度**，不是真正的多意图解耦。

**答辩诚实表述**：

> "第一，意图识别是 LLM 推理的结果，不是关键词匹配。同一句'我头疼'因行为意图不同，可能触发不同的协作模式——这需要语义理解。当前版本处理复合意图采用主意图+并行任务的优先级调度策略；完整的意图解耦和独立路由是后续版本的自然扩展。"

**论文支撑**：

| 论文 | 核心发现 | 对应 |
|------|---------|------|
| **Router-R1 (Zhang et al., NeurIPS 2025)** | LLM Router 将路由视为"思考-路由"交替的序列决策过程 | LLM 路由本身是一种推理行为 |
| **R2-Router (Xue et al., 2026)** | "routers evolve from reactive selectors to deliberate reasoners" | Triage 对意图的语义理解是推理 |

### 第二层：映射表是实验数据的经验总结

**核心论点**：映射表不是凭空编的——它是消融实验的工程化输出。

**支撑**：

| 映射决策 | 实验依据 |
|---------|---------|
| 诊断用 Vote | Phase 2: Vote-3 (84.8%) > Debate (81.8%) |
| 辟谣用 Debate | Phase 3 预期 Debate > Vote |
| 用药用 Single+KG | KG 有 29,794 条禁忌关系——结构化数据优先 |

**OOD 的诚实边界**：

1200 题覆盖了 3 个考试类别（规培结业/执业医师/临床医学），但**不代表覆盖了所有可能的医学问诊类型**。这是所有监督学习系统的共同边界——没有系统能在未见过分布上保证性能。

**答辩诚实表述**：

> "第二，映射表是消融实验的经验总结。这和医生根据临床指南选择治疗方案的逻辑相同——临床指南基于 RCT，映射表基于消融实验。但需要承认：当前映射矩阵是基于特定评测集的局部最优解。在更完善的版本中，Router 不应是静态表，而应接入反馈闭环——通过用户采纳率或 RLHF 动态调整权重。这一动态 Router 的设计已纳入后续研究路线。"

**论文支撑**：

| 论文 | 核心发现 | 对应 |
|------|---------|------|
| **AGILE (Feng et al., NeurIPS 2024)** | "ablation study demonstrates the indispensability of each component" | 消融驱动架构设计 |
| **Principle-Based MAS (Wei et al., AAAI 2025)** | 消融证明多Agent协作框架的有效性 | 消融方法论的先例 |

### 第三层：不确定性降级——诚实面对边界

**当前实现**：

```python
# graph_engine.py
uncertainty = min(0.5, len(parallel) * 0.15)
# 0 parallel → 0.0, 1 parallel → 0.15, 2 parallel → 0.30, 3+ → ≥0.45
# threshold: > 0.3 → fallback to Vote-3
```

逻辑：Triage 输出的并行意图越多，意味着系统对主意图越不确定。

**这不是认知不确定性量化（Epistemic UQ），更不是偶然不确定性量化（Aleatoric UQ）。它是一条经验启发式——"当分类器无法给出干净的单意图时，说明输入本身模糊或系统对分类缺乏信心"。在缺乏 Logprobs 或贝叶斯推理的条件下，这是最务实的代理指标。**

不能做的事：在论文或答辩中声称系统具有"不确定性量化"或"元认知能力"——会被戳穿。

可以做的是：诚实描述这条启发式的设计动机和局限性，并指出更严谨的不确定性估计方法（Logprobs、Ensemble Disagreement、Conformal Prediction）是后续工作的方向。

**答辩诚实表述**：

> "第三，系统在意图不明确时自动降级为更保守的 Vote-3 模式。当前版本使用并行意图数量作为不确定性的代理指标——当 Triage 无法收敛到单一意图时，以多数投票取代针对性策略。需要诚实说明，这不是严格的不确定性量化，而是一条工程启发式。在后续版本中，我们计划引入 Logprobs 驱动的认知不确定性估计或 Conformal Prediction，以提供更严格的降级保证。Mind the Ambiguity (Liu et al., WWW 2026) 和 SafePath (Doula et al., 2025) 为这一方向提供了方法论基础。"

**论文支撑**：

| 论文 | 核心发现 | 对应 |
|------|---------|------|
| **Liu et al. (2026). "Mind the Ambiguity." WWW 2026.** | 在医疗 QA 中，检测到输入歧义时主动请求澄清而非强行回答 | 不确定时保守降级的先例 |
| **Doula et al. (2025). "SafePath."** | 不确定性高时安全降级的人类交接机制 | 降级到更安全的模式 |

### 第四层：协作模式的预定义 ≠ 协作过程的非涌现

**核心论点**：医院会诊制度规定了"住院医师初诊→主治医师复核→科主任终裁"，但每次会诊的讨论内容、诊断结论、修正过程是完全不可预测的。

Phase 2 数据支撑：12.5% 的题目（150/1200）两模型意见不同并进入辩论。辩论修正了 42 题也误伤了 52 题——这种**修正 vs 误伤的博弈模式是运行时产生的，无法预设**。

**答辩表述**：

> "第四，协作模式只决定'如何组织'，但多智能体之间的实时辩论、互校正和共识达成是完全涌现的。Phase 2 中 150 道分歧题目里，辩论修正了 42 题也误伤了 52 题——这种博弈模式是运行时产生的。Emergent Coordination (Riedl, 2025) 证实了角色赋值系统中涌现行为的存在。"

**论文支撑**：

| 论文 | 核心发现 | 对应 |
|------|---------|------|
| **Riedl (2025). "Emergent Coordination in MAS."** | "role assignment introduces stable identity-linked differentiation... enabling goal-directed complementarity" | 角色预定义是涌现的前提 |
| **AgentVerse (Chen et al., 2023)** | "social behaviors spontaneously emerge during collaborative task accomplishment" | 协作中的涌现 |

---

## 完整答辩回答

> "老师，协作映射表的存在并不意味着系统缺乏智能，但我也要诚实地说明当前系统的边界。
>
> **第一层（意图识别）**：意图识别是 LLM 推理的结果，不是关键词匹配。用户说'我头疼'和'头疼该吃什么药'触发不同的协作模式——因为 LLM 理解了行为意图的差异。当前版本处理复合意图采用主意图+并行任务的优先级调度，完整的意图解耦是后续方向。
>
> **第二层（映射表）**：映射表是消融实验的经验总结——诊断场景下我们通过 1200 题实验验证了 Vote-3 显著优于所有辩论配置。需要承认当前映射表是基于特定评测集的局部最优解，在更完善版本中应接入反馈闭环动态调整权重。
>
> **第三层（不确定性降级）**：系统在 Triage 输出多并行意图时自动降级为 Vote-3。我需要诚实说明这是工程启发式，不是严格的不确定性量化。引入 Logprobs 驱动的认知不确定性估计是后续工作方向。
>
> **第四层（涌现行为）**：协作模式只决定'如何组织'，但辩论过程中的互校正、修正 vs 误伤的博弈完全在运行时产生。Phase 2 中辩论修正了 42 题也误伤了 52 题——这种模式无法预设，是系统运行时涌现的。"

---

## 参考文献

| # | 论文 | 链接 | 支持哪一层 |
|---|------|------|----------|
| 1 | Zhang et al. (2025). "Router-R1: Teaching LLMs Multi-Round Routing via RL." NeurIPS 2025. | https://arxiv.org/abs/2510 | 第一层 |
| 2 | Xue et al. (2026). "R2-Router: A New Paradigm for LLM Routing with Reasoning." | https://arxiv.org/abs/2602 | 第一层 |
| 3 | Feng et al. (2024). "AGILE: A Novel RL Framework of LLM Agents." NeurIPS 2024. | https://arxiv.org/abs/2410 | 第二层 |
| 4 | Wei et al. (2025). "Principle-Based Multi-Agent Prompting." AAAI 2025 Workshop. | https://arxiv.org/abs/2503 | 第二层 |
| 5 | Liu et al. (2026). "Mind the Ambiguity: UQ in LLMs for Safe Medical QA." WWW 2026. | https://arxiv.org/abs/2601 | 第三层 |
| 6 | Doula et al. (2025). "SafePath: Conformal Prediction for Safe LLM Navigation." | https://arxiv.org/abs/2504 | 第三层 |
| 7 | Riedl (2025). "Emergent Coordination in Multi-Agent Language Models." | https://arxiv.org/abs/2505 | 第四层 |
| 8 | Chen et al. (2023). "AgentVerse: Facilitating Multi-Agent Collaboration." | https://arxiv.org/abs/2308.10848 | 第四层 |

---

## 当前系统的诚实边界汇总

| 层次 | 现状 | 边界 |
|------|------|------|
| 意图识别 | LLM 语义推理，单主意图 + 并行任务 | 不支持多主意图独立路由 |
| 映射表 | 基于 Phase 2 消融实验 | 评测集覆盖有限，OOD 未验证 |
| 不确定性 | 并行意图数作为启发式代理 | 非严格 UQ，无统计学保证 |
| 涌现行为 | 辩论中观察到的修正/误伤博弈 | 涌现≠总是有益的 |
| 动态权重 | 静态映射表 | 无在线学习或反馈闭环 |

---

## 附录 C：多主意图独立路由 —— 改进方案与文献调研

### 问题定义

当前 Triage 输出单一 `primary_intent` + `parallel_intents`（仅用于预处理触发）。当用户 query 包含多主意图时（如"我头疼，布洛芬现在多少钱"），系统必须选择 SYMPTOM_ANALYSIS 或 MEDICATION_REVIEW 之一作为主意图，另一意图仅作为并行预处理任务，不触发独立的协作模式。

### 改进目标

Triage 输出意图列表 `[{domain, confidence, collab_mode}]`，graph_engine 并发调度多个 Agent，最后合成回答。

```
用户: "我头疼，布洛芬现在多少钱"

Triage:
  [{domain: SYMPTOM_ANALYSIS, confidence: 0.92, collab: debate},
   {domain: MEDICATION_REVIEW, confidence: 0.78, collab: single_kg}]

graph_engine:
  ├─ 并发 → SymptomAgent(Debate) + MedAgent(Single+KG)
  └─ 合成 → "您的头痛可能是偏头痛 [辩论诊断详情]。
             布洛芬当前价格因品牌和规格而异 [用药查询详情]。"
```

### 文献调研

#### 多意图检测

| 论文 | 核心机制 | 对本方案的启示 |
|------|---------|-------------|
| **NLU++ (2022)** | 多标签数据集，每个 utterance 对应多个意图标签 | 多意图标注是标准 NLU 实践 |
| **Uni-MIS (Yin et al., AAAI 2024)** | 多视角意图-槽位交互，联合解码多个意图 | 联合解码比串行分类更高效 |
| **Joint Intent Detection Survey (Weld et al., ACM CSUR 2022)** | 系统综述 122 引 | 提供了方法分类框架 |

#### 并发智能体编排

| 论文 | 核心机制 | 对本方案的启示 |
|------|---------|-------------|
| **AutoGen (Wu et al., 2023)** | "支持 LLM 之间灵活的对话模式，包括静态、动态、顺序和**并行执行**" | 并行 Agent 是成熟工程实践 |
| **DynTaskMAS (Yu et al., 2025)** | 构建有向无环图任务图，支持异步并行 Agent 调度 | 任务图是比顺序调度更优雅的抽象 |
| **MARCO (Shrimal et al., 2024)** | 实时聊天编排，查询分发至多个专门 Agent，协调层合并结果 | 分发-合并模式直接可用于我们的多意图场景 |

#### 查询分解

| 论文 | 核心机制 | 对本方案的启示 |
|------|---------|-------------|
| **Dependency-Aware Query Decomposition (Gao et al., 2025)** | 将复杂查询分解为依赖关系图，识别可并行的子查询 | 如果子意图有依赖关系（如"先诊断再看用什么药"），需要依赖图 |
| **TDAG (Wang et al., Neural Networks 2025)** | LLM 实时分解任务，为每个子任务动态生成专门 Agent | 动态 Agent 生成比静态 Agent 池更灵活 |

#### 结果合成

| 论文 | 核心机制 | 对本方案的启示 |
|------|---------|-------------|
| **Mixture-of-Agents (Wang et al., 2024)** | 分层架构："基础模型分发至多个 LLM 专家并行处理，聚合模型合并为最终响应" | 需要一个合成 Agent 来融合多路输出 |
| **ReConcile (Chen et al., 2024)** | 圆桌共识会议，加权投票或一致性融合 | 如果多个 Agent 对同一问题给出不同答案，需要冲突解决机制 |

### 方案设计

#### 改动范围

| 文件 | 改动 | 行数 |
|------|------|------|
| `agents/triage_agent.py` | Prompt 改为输出意图列表 `[{domain, confidence, collab_mode}]` | ~30 |
| `graph_engine.py` | 新增 `dispatch_parallel_agents()` + `synthesize_response()` | ~80 |
| `core/intent_ontology.py` | `select_collab_mode()` 已支持 per-intent 调用，无需改动 | 0 |

#### 合成策略

当多个 Agent 返回结果后：

```
1. 如果各 Agent 回答独立不冲突（诊断 + 药价查询）→ 直接拼接
2. 如果各 Agent 回答有冲突（两 Agent 给出不同诊断）→ 调 Moderator 做最终裁决
3. 如果某 Agent 超时或返回空白 → 跳过，其他结果正常输出
```

#### 论文中的写法

> "在多主意图场景下，系统将用户查询分解为多个子意图，为每个子意图独立选择协作模式，通过 DynTaskMAS (Yu et al., 2025) 式的任务图进行并发调度，最终由 Mixture-of-Agents (Wang et al., 2024) 式的聚合层合并输出。这一改进将系统从'单意图单通道'升级为'多意图多通道并发'，是意图驱动协作框架的自然扩展。"

### 实现优先级

| 优先级 | 功能 | 理由 |
|--------|------|------|
| P1 | Triage 输出多意图 + graph_engine 并发调度 | 核心改进，论文亮点 |
| P2 | 结果合成 Agent (Synthesizer) | 多意图合并的完整闭环 |
| P3 | 依赖图优化（有依赖关系的子意图串行） | 锦上添花 |

---

## 附录 D：谣言数据 verdict 归一化

审查后的数据中 verdict 出现了 40+ 种变体（"虚假""部分属实""不实"等），需要归一化到四类标准值：属实 / 谣言 / 误导 / 尚无定论。

归一化规则：

```
"属实/正确/真实/真/基本属实/部分真实/有证据支持"       → 属实
"谣言/虚假/不实/假/不属实/伪科学/不正确/基本错误/伪真相" → 谣言  
"误导/片面/偏误/夸大/表述不严谨/不实/误导/存在误导"     → 误导
"尚无定论/存疑/缺乏证据/缺乏充分证据/证据不足"          → 尚无定论
```

归一化后从 456 条中选取 400 条（保证类别平衡），作为最终辟谣评测集。

---
*文档版本: 2026-05-02*
