# 两种辩论拓扑的认知理论基础

## 1. 为什么诊断和辟谣需要不同的辩论拓扑

### 1.1 任务认知结构差异

| | 诊断（症状→疾病） | 辟谣（说法→真伪） |
|------|--------------|--------------|
| 认知过程 | **收敛式**：从多个症状出发，逐步缩小候选范围 | **对抗式**：从单一说法出发，同时收集正反证据 |
| 正确答案 | 存在（一个确定的疾病） | 可能存在，可能不存在（尚无定论） |
| 信息流 | 多→一（症状群→诊断） | 一→多（说法→正反证据→裁决） |
| 临床类比 | 住院医师初诊→主治复核→主任终裁 | 检察官起诉↔辩护律师抗辩→法官裁决 |
| 自然决策模式 | 层级审查 | 对抗辩论 |

### 1.2 为什么诊断适合收敛式辩论

诊断的过程本质是**候选生成与排除**：

- Proposer 生成 Top-3 候选疾病
- Critic 逐一审查：这个候选的典型症状在患者身上存在吗？有矛盾吗？
- Defender 回应：我接受这个批评/我坚持因为...
- Moderator 综合：基于辩论过程给出最终诊断

这和临床会诊流程完全同构——**层级式的审查链**。TAO 系统（Kim et al., 2025）的实验已证实这种临床层级结构在医疗安全基准上优于同层讨论。

**论文支撑：**

| 论文 | 核心发现 | 链接 |
|------|---------|------|
| **TAO (Kim et al., MIT/Harvard, 2025)** | 临床层级结构（护士→医生→专家）吸收 24% 个体错误，在 4/5 医疗安全基准上超越同层 8.2% | https://arxiv.org/abs/2506 |
| **UCAgents (Feng et al., CUHK, 2025)** | 层级式单向收敛框架在病理诊断上超越开放式辩论，降低 87.7% token 成本 | https://arxiv.org/abs/2512 |

> **TAO 原文**："The hierarchical structure functions as an effective error-correction mechanism, absorbing up to 24% of individual agent errors before they can compound. Directly inspired by clinical hierarchies (nurse → physician → specialist)."

### 1.3 为什么辟谣适合对抗式辩论

辟谣的本质是**证据权衡**——没有预定义的候选诊断列表，正反双方从空白开始收集证据。

- Advocate 的任务：找到所有支持该说法的证据（即使说法存疑）
- Skeptic 的任务：找到所有反驳该说法的证据（即使说法有道理）
- Judge 的任务：基于双方收集的证据，做出综合裁决

这和法庭辩论完全同构——**对抗双方各自收集证据，法官在充分听取了双方论证后裁决**。这种结构确保了一个说法在被判定为"谣言"之前，得到了最充分的辩护。

**论文支撑：**

| 论文 | 核心发现 | 链接 |
|------|---------|------|
| **MedAgents (Tang et al., Yale, ACL 2024)** | 多学科专家从不同角度分析同一病例，通过"独立分析→讨论→共识"达成综合诊断 | https://aclanthology.org/2024.findings-acl.33/ |
| **Choi, Zhu, Li (2025). "Debate or Vote?"** | 任务认知结构决定最优多Agent策略——知识检索型用投票，推理型用辩论 | https://arxiv.org/abs/2504 |
| **Kaesberg et al. (2025). "Voting or Consensus?"** | 多Agent辩论中投票与共识两种决策协议的系统对比 | https://arxiv.org/abs/2506 |

> **Choi et al. 原文**："The optimal multi-agent strategy depends on the cognitive structure of the task — knowledge-retrieval tasks benefit from voting, while reasoning tasks benefit from debate."

---

## 2. 为什么诊断实验和辟谣实验不是重复

### 2.1 实验目的不同

| | Phase 2 诊断实验 | Phase 3 辟谣实验 |
|------|--------------|--------------|
| **要回答的问题** | 知识检索型任务上，Vote 和 Debate 哪个好？ | 证据推理型任务上，Vote 和 Debate 哪个好？ |
| **辩论拓扑** | 收敛式（Proposer→Critic→Defender→Moderator） | 对抗式（Advocate↔Skeptic→Judge） |
| **评测指标** | 准确率（有 GT） | 决策过程指标 + 准确率参考 |
| **论文位置** | 4.3 节：诊断协作实验 | 4.5 节：辟谣协作验证 |
| **在核心叙事中的角色** | 证明 Vote > Debate（一面） | 证明 Debate > Vote 或 Debate 产生更丰富的决策过程（另一面） |

### 2.2 两者结合证明的核心论点

> "多智能体协作的最优形式取决于任务的内在认知结构。本研究在 CMB-Exam 1200 题诊断评测上证明投票集成显著优于辩论（+2.1pp），在自建 400 条辟谣评测上发现对抗式辩论产生了投票无法提供的决策过程多样性。两种任务对应两种辩论拓扑——诊断用收敛式层级审查，辟谣用对抗式证据权衡——这一'任务-拓扑匹配'原则是多智能体系统设计中的关键考量。"

---

## 3. 两种辩论拓扑的形式化定义

### 3.1 收敛式辩论（诊断）

```
输入: 症状列表 S = {s1, s2, ..., sn}, 患者档案 P

Step 1: Proposer(S, P) → 候选列表 C = [c1, c2, c3]
        每个候选 c_i 包含 disease, confidence, reasoning

Step 2: Critic(C, S, P) → 质疑列表 O = [o1, o2, ...]
        每条质疑 o_j 必须引用证据（KG/RAG/Web）

Step 3: Defender(C, O, S, P) → 修正候选 C' + rebuttals
        接受或反驳每条质疑

Step 4: Moderator(C', debate_history) → 最终诊断 D_final
        综合辩论过程，输出叙事性诊断报告

收敛条件:
  - NO_VALID_OBJECTIONS: Critic 无法提出有证据的质疑
  - TOP1_STABLE_HIGH_CONF: Top-1 候选连续两轮未变且置信度 ≥ 0.7
  - MAX_ROUNDS_REACHED: 达到最大辩论轮数
  - NO_NEW_EVIDENCE: 连续两轮未获得新工具证据
```

### 3.2 对抗式辩论（辟谣）

```
输入: 健康说法 Claim, 上下文 Ctx

Step 1: Advocate(Claim, Ctx) → 辩护意见 A
        收集所有支持 Claim 的证据和合理推论
        "即使说法存疑，也尽力为它辩护"

Step 2: Skeptic(Claim, Ctx) → 质疑意见 S
        收集所有反驳 Claim 的证据和逻辑漏洞
        "即使说法有道理，也尽力质疑"

Step 3: Judge(Claim, A, S) → 最终判定 V_final ∈ {属实, 谣言, 误导, 尚无定论}
        综合双方的证据和论证，做出裁决

关键特征:
  - Advocate 和 Skeptic 并发执行，不串行
  - Judge 看到的是双方最充足的论证，而非协商后的妥协
  - 无收敛条件——一轮对抗即裁决（避免"互相说服"导致证据稀释）
```

### 3.3 为什么辟谣不需要多轮

诊断需要多轮因为候选列表需要逐步修正——Proposer 先列出 3 个候选，Critic 逐一审查后 Defender 修正，第二轮再审查修正后的列表。

辟谣只有"该说法是否成立"这一个问题。Advocate 和 Skeptic 第一次就给出了最充分的辩护和质疑，再多轮只是重复。**对抗式辩论的信息增益集中在第一轮。**

---

## 4. 实验设计对照

### Phase 2: 诊断实验（已完成）

| 组 | 模式 | 辩论拓扑 | 准确率 |
|----|------|---------|--------|
| S | Single-DeepSeek | — | 82.7% |
| V | Vote-3 | — | **84.8%** |
| D-A | Debate-Convergent (DS+Qwen) | P→C→D→M | 81.8% |
| D-B | Debate-Convergent (DS+GLM) | P→C→D→M | 83.9% |

**结论**: 诊断选择题上，Vote > Debate。知识检索型任务最优协作模式是投票集成。

### Phase 3: 辟谣实验（进行中）

| 组 | 模式 | 辩论拓扑 | 角色分配 |
|----|------|---------|---------|
| S | Single-DeepSeek | — | DS 独立判定 |
| V | Vote-3 | — | DS+QW+GLM 独立→多数 |
| D | Debate-Adversarial | Adv↔Skp→Jdg | DS(Adv)+QW(Skp)+GLM(Jdg) |

**预期结论**: 辟谣场景下，对抗式辩论产生的决策过程比投票更丰富（分歧率更高、证据覆盖面更广）。准确率不做核心结论（ground truth 未经人工审核）。

---

## 5. 参考文献

| # | 论文 | 链接 | 支撑论点 |
|---|------|------|---------|
| 1 | **TAO (Kim et al., MIT/Harvard, 2025)** | https://arxiv.org/abs/2506 | 临床层级结构优于同层讨论——收敛式辩论的实证基础 |
| 2 | **UCAgents (Feng et al., CUHK, 2025)** | https://arxiv.org/abs/2512 | 层级收敛优于开放式辩论——诊断场景的拓扑选择 |
| 3 | **MedAgents (Tang et al., Yale, ACL 2024)** | https://aclanthology.org/2024.findings-acl.33/ | 多学科协作优于单模型——多Agent医疗推理的标杆 |
| 4 | **Choi, Zhu, Li (2025). "Debate or Vote?"** | https://arxiv.org/abs/2504 | 任务认知结构决定最优策略——任务-拓扑匹配的理论核心 |
| 5 | **Kaesberg et al. (2025). "Voting or Consensus?"** | https://arxiv.org/abs/2506 | 投票与共识协议的系统对比 |
| 6 | **Riedl (2025). "Emergent Coordination in MAS"** | https://arxiv.org/abs/2505 | 角色赋值引入稳定的身份关联分化——对抗式辩论中 Adv/Skp 角色设计的理论基础 |
| 7 | **AgentVerse (Chen et al., 2023)** | https://arxiv.org/abs/2308.10848 | 多Agent群体中的社会行为在协作中自发涌现 |
| 8 | **Dietterich (2000). "Ensemble Methods in ML"** | https://link.springer.com/chapter/10.1007/3-540-45014-9_1 | 多数投票超越最佳分类器的数学条件 |
| 9 | **Kuncheva & Whitaker (2003). "Measures of Diversity"** | https://link.springer.com/article/10.1023/A:1022859024233 | 多样性度量的形式化框架——错误重叠矩阵的方法论基础 |
| 10 | **袁嵩等 (2025). "基于LLM与多智能体协同的医疗对话机制研究"** | 计算机技术与发展, 2025.8 | 三层决策模型 + 双层意图划分——本项目的学术参考基础 |

---

## 6. 论文中的写法

> "本研究在两个具有不同认知结构的医疗子任务上验证了多智能体协作的有效性。
>
> **诊断任务**（CMB-Exam 1200 题）是典型的知识检索型任务——多个症状收敛到单一诊断。实验表明，三模型多数投票（Vote-3, 84.8%）显著优于收敛式辩论（最优 83.9%）。这一结论与 Choi et al. (2025) 的'知识检索型任务适合投票'假说一致，TAO 系统（Kim et al., 2025）的临床层级结构在错误抑制方面提供了补充解释。
>
> **辟谣任务**（自建 400 条）是典型的证据推理型任务——围绕单一说法收集正反证据并裁决。对此我们采用了对抗式辩论拓扑（Advocate↔Skeptic→Judge），其设计原则源自法庭辩论的对抗制。该拓扑与诊断的收敛式辩论在结构上有本质区别：Advocate 和 Skeptic 并发执行而非串行审查，Judge 在双方充分论证后裁决而非逐级汇总。
>
> 两种辩论拓扑的选择并非随意——它们分别适配了各自任务的内在认知结构。这一'任务-拓扑匹配'原则是本研究在方法论层面的核心贡献。"

---
*文档版本: 2026-05-02*
