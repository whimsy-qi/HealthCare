# 辟谣实验分析框架：面向过程的决策行为评估

## 1. 核心问题：为什么需要面向过程的评估

在复杂的医疗辟谣场景中，单一的非黑即白的 Ground Truth 往往无法覆盖医学证据的多面性。**例如 WHO-IARC 的致癌物分类本身就是四级（确认/很可能/可能/无法分类），而不是二元对错。** 因此，本实验采用**"弱 GT 结果评估 + 过程行为评估"的双层框架**：结果指标回答系统是否接近 AI 辅助标签（用于内部参考），过程指标回答不同协作模式为何产生差异、分歧来自哪里、系统是否存在过度弃判或裁判偏向。

### 1.1 Self-Preference Bias（自我偏好偏见）及其在本实验中的实际影响

**背景**: 辟谣评测集的 GT 由 Qwen 生成 + GLM-5.1 审查修正。GLM-5.1 又参与 Vote-3（作为投票者之一）和 Debate（作为 Judge）。这在理论上引入了 Self-Preference Bias——LLM 作为裁判时，会本能地给与自身内部权重更一致的答案打高分。

**本实验的实际缓解因素**:

| 角色 | 模型 | 是否参与 GT 构建 |
|------|------|:---:|
| Advocate | **DeepSeek-V4-Pro** | ✗ |
| Skeptic | **Qwen-Max** | ✓（初始 GT 生成） |
| Judge | **GLM-5.1** | ✓（GT 审查修正） |

**模型隔离状态**: Qwen（生成）和 GLM-5.1（审查）均与 GT 构建有耦合。只有 DeepSeek 完全隔离——它未参与任何数据构造环节。这一限制是 AI 辅助数据构建管线的固有问题。在当前设计中，Judge 评判的是 Advocate + Skeptic 的实时论证，而非 GT 标签，但无法完全排除 Judge 在推理中受到自身审查过程中知识的影响。

**论文中的诚实声明**:

> "需要承认，Qwen-Max（初始 GT 生成）和 GLM-5.1（GT 审查修正）均与评测集构建有耦合，只有 DeepSeek 完全隔离。在更完善的实验设计中，数据构造模型与实验评测模型应完全独立。本实验在分析中仅将准确率作为辅助参考，核心结论基于不依赖 GT 的过程指标。"

### 1.2 Vote-3 Tiebreak 机制

当三人投票出现三种不同 verdict 时，Tiebreak 代码：

```python
# rumor_debate_experiment.py line 120-122
from collections import Counter
tally = Counter(preds)
top, cnt = tally.most_common(1)[0]
final = top if cnt >= 2 else preds[0]  # 至少 2 票一致 → 多数；全不同 → DeepSeek
```

**设计逻辑**:
- 2+ 票一致 → 多数投票（标准民主规则）
- 三人全不同 → DeepSeek（Proposer，Phase 1 准确率最高的模型）

**当前实现的诚实边界**: 当前的静态降级（`preds[0] → DeepSeek`）仅为满足离线评测吞吐量的工程简化。在医疗健康领域，这存在显著风险：DeepSeek 整体准确率 82.7% 不意味着它在**每道具体题目**上都是最可靠的——如果它在某道题上产生了幻觉而 Qwen 检索到了明确文献依据，静态降级会强行抹杀正确证据。

**工业级改进方向**: 在真正的生产级 MAS 架构中，遇到 1:1:1 死锁时，系统必须引入**置信度加权聚合**或**证据回溯式 Tiebreak**：
1. 要求每个 Agent 输出 verdict 的同时输出 `confidence_score`（0-1）和 `evidence_sources`（引用来源列表）
2. Tiebreak 基于多维度综合评分:
   ```
   tiebreak_score = calibrated_confidence  # 需在验证集上校准
                  + evidence_support_score  # 引用来源的权威性加权
                  + source_authority_score  # 来源类型（PMID>WHO>教科书）
                  − contradiction_penalty   # 如果引用来源存在内部矛盾
   ```
   **注意**：LLM 自报的置信度通常不校准（可能只是"更会装确定"）。`calibrated_confidence` 需要在独立验证集上做 Platt Scaling 或 Isotonic Regression 校准，不能直接使用模型原始输出。
3. 如果综合得分仍低于阈值，向用户返回"系统无法确定，建议咨询专业医生"
这应当作为系统的下一个重大迭代点。

---

## 2. 理论基础：面向过程的评估

### 2.1 过程监督 > 结果监督

**Lightman et al. (2023). "Let's Verify Step by Step." OpenAI. arXiv:2305.20050**

> "Process supervision — rewarding each intermediate reasoning step — leads to significantly better performance than outcome supervision for training reliable LLMs on math reasoning."

**对本实验的启示**：Lightman et al. 在数学推理任务上证明了过程监督在训练阶段的优势。本实验借鉴这一思想——不是声称过程指标优于准确率，而是指出**当 GT 存在系统性局限时，过程指标是结果准确率之外的必要补充，用于解释不同协作模式的决策行为差异**。两者回答不同的问题：准确率回答"离标签有多近"，过程指标回答"为什么会这样决策"。

### 2.2 分歧是信息信号，不是错误

**Wang et al. (2022). "Self-Consistency Improves Chain of Thought Reasoning." ICLR 2023.**

> "Disagreement among sampled reasoning paths is informative — the more paths converge on an answer, the more likely it is correct. Consistency across diverse paths is a strong proxy for correctness."

**Pathak et al. (2019). "Self-Supervised Exploration via Disagreement." ICLR 2019.**

> "Disagreement among ensemble predictions can serve as an intrinsic reward signal for exploration — indicating unexplored areas of the solution space."

**对本实验的意义**：辟谣辩论和诊断辩论的分歧率差异表明，在当前模型组合与角色提示下，辟谣任务触发了更强的不一致性。该不一致性可能来自证据空间更开放，也可能来自标签边界模糊、角色提示诱导或模型能力差异。因此，分歧率不直接等同于任务内在不确定性，而是作为识别高争议样本、分析协作机制是否有必要的过程信号。

### 2.3 对抗辩论在无完美 GT 时仍有价值

**Michael et al. (2023). "Debate Helps Supervise Unreliable Experts." NYU/Anthropic.**

> "Debate between two unreliable experts can help a supervisor extract the truth, even when neither expert individually knows the full answer."

**Young (2026). "Knowledge Divergence and the Value of Debate for Scalable Oversight."**

> "When models share information, debate adds no value; when they diverge, debate systematically surfaces correct answers. The value of debate derives from knowledge divergence between models."

**对本实验的意义**：Advocate(DS) 和 Skeptic(QW) 是不同架构的模型（MoE vs Dense），天然产生知识分歧。**即使没有完美 GT，知识分歧本身已经创造了辩论的价值——它确保了一个说法在被判定之前得到了最充分的论证。**

### 2.4 无 GT 时的评估框架

**Rawal et al. (2025). "Evaluating Model Explanations without Ground Truth."**

> "Explanation quality can be assessed through internal consistency and robustness metrics rather than comparison to ground truth."

**Es et al. (2023). "Ragas: Automated Evaluation of Retrieval Augmented Generation."**

> "Reference-free evaluation using metrics like faithfulness, answer relevance, and context precision — all process metrics rather than outcome comparisons."

---

## 3. 过程指标体系

### 3.1 核心过程指标（不依赖 GT）

三种模式（Single / Vote-3 / Debate）使用**统一的可比过程指标**进行横向分析。注意 Debate 的"分歧率"（二人对抗）和 Vote 的"共识率"（三人独立）是结构不同的指标，不能直接比较。本实验统一使用两两分歧率和 verdict 分布熵进行跨模式对比。

| 指标 | 定义 | 适用模式 | 测什么 |
|------|------|---------|--------|
| **verdict 分布熵 (H)** | H = −Σ p(v)×log₂(p(v)) | Single/Vote/Debate | 高熵→分散判断；低熵→集中趋同 |
| **两两分歧率** | 任意两个独立 Agent 给出不同 verdict 的比例 | 全部（统一可比） | 统一衡量认知冲突程度 |
| **完全一致率** | Vote-3: 三模型独立投票完全一致; Debate: Advocate与Skeptic初始判定一致（不含Judge裁决） | Vote-3 / Debate | 聚合趋同度（两种模式的"一致"语义不同，不可直接比较） |
| **多数裕度** | 多数票占比 − 少数票占比 | Vote-3 | 投票决策的清晰度 |
| **Judge 偏向率** | Judge 采纳 Advocate vs Skeptic 的比例 | Debate | **注意**：角色=模型绑定，无法区分偏好来源（模型偏向/角色偏向/Prompt强度/证据质量）。仅为描述性统计 |
| **模式间一致性** | 同一 claim，三种模式给相同 verdict 的比例 | 全部 | 协作模式是否影响决策 |
| **有效决策率** | 1 −（verdict="尚无定论"的比例） | 全部 | 见下方安全锁 |

**有效决策率的安全锁**：在医疗场景下并非越高越好。如果输出"尚无定论"是因为检索到权威依据（如 WHO-IARC 2B 类），这是高质量防守；只有因内部逻辑混乱退缩时才是负面信号。**本实验不将其作为优化目标，仅作为描述性统计。**

### 3.2 Verdict 分布偏移（不依赖 GT）

| 指标 | 说明 |
|------|------|
| Vote verdict 分布 | 投票模式更倾向哪种判定？ |
| Debate verdict 分布 | 辩论模式更倾向哪种判定？ |
| Single verdict 分布 | 单模型更倾向哪种判定？ |
| 分布偏移 | 辩论是否比投票更保守（更多"误导"/"尚无定论"）？**结合有效决策率一起看** |

### 3.3 延迟与效率（不依赖 GT）

| 指标 | 说明 |
|------|------|
| 单题平均延迟 | Single vs Vote-3 vs Debate |
| 总耗时 | 三种模式的资源消耗对比 |

### 3.4 参考指标（依赖 GT，论文中标注"仅供参考"）

| 指标 | 用途 | 使用边界 |
|------|------|---------|
| 严格准确率 | AI 辅助 GT 下的参考值 | 声明 GT 局限 |
| 宽松准确率 | 同上 | 同上 |

---

## 4. 预期分析结果

### 4.1 如果 Debate 的两两分歧率或 verdict 熵显著高于 Vote-3

→ 高分歧/高熵表明该任务在当前模型组合与提示结构下存在更强的不一致性，提示辩论机制可能具有分析价值。分歧的来源——证据开放、Prompt 强制对抗、标签边界模糊还是模型能力差异——需要通过角色互换、提示消融和人工抽样进一步确认。

### 4.2 如果 Vote 共识率高但有效决策率低

→ 三个模型的知识重叠度高，但它们可能集体回避困难判定（都选"尚无定论"）。此时投票的"共识"不是优势而是缺陷——它可能只是集体退缩。

### 4.3 Judge 偏向率只能做描述性统计

→ 角色与模型绑定意味着无法区分偏好来源。本实验仅报告原始偏向率。分离模型偏向需要补充角色互换对照实验。

### 4.4 如果 Debate 有效决策率 > Vote

→ 辩论模式在保证论证质量的同时给出了更多实质性判断——这是对抗式辩论在辟谣场景中的核心价值。

---

## 5. 论文中的写法

> "在复杂的医疗辟谣场景中，单一的二元 Ground Truth 难以覆盖医学证据的多层级不确定性——例如 WHO-IARC 的致癌物分类本身就包含'确认/很可能/可能/无法分类'等等级，而非简单的真伪二分。因此，本实验采用**'弱 GT 结果评估 + 过程行为评估'的双层框架**：严格准确率和宽松准确率用于衡量系统与 AI 辅助标签的一致性（内部参考），分歧率、verdict 分布熵、模式间一致性和有效决策率用于解释不同协作模式的决策行为差异。
>
> 实验发现，辟谣场景下 Advocate-Skeptic 的 verdict 分歧率达到 [实际值]%，显著高于诊断实验中的 12%。这一现象表明，在当前模型组合和提示结构下，医疗辟谣任务比诊断任务触发了更强的不一致性。结合辩论监督（Michael et al., 2023）和知识分歧（Young, 2026）相关研究，这一结果提示：辟谣任务可能更依赖对立证据的显式展开，而不是简单多数投票。但分歧来源仍可能包括证据开放性、角色提示、标签边界和模型能力差异，因此本实验将其作为支持'任务-策略匹配'假说的过程证据，而非直接等同于任务本体属性。
>
> 同时，Qwen-Max 和 GLM-5.1 均参与了评测集构建，导致准确率指标存在同源偏好风险。因此，本实验不将 AI 辅助 GT 下的准确率作为唯一结论依据，而是结合不依赖 GT 的过程指标进行交叉解释。由于 Debate 中角色与模型绑定，Judge 偏向率仅作为描述性统计；更严格的偏向分析需要在后续实验中引入角色互换和裁判模型隔离。Tiebreak 仅覆盖 [实际值]% 的边缘样本，Vote-3 的主要收益来自 2:1 多数纠错样本——这些样本上的增益才是多智能体集成的核心价值。"

---

## 6. 参考文献

| # | 论文 | 链接 | 支撑论点 |
|---|------|------|---------|
| 1 | **Lightman et al. (2023). "Let's Verify Step by Step."** OpenAI. | https://arxiv.org/abs/2305.20050 | 过程监督训练优于结果监督（数学推理），本实验借鉴此思想但外推有限度 |
| 2 | **Wang et al. (2022). "Self-Consistency Improves Chain of Thought."** ICLR 2023. | https://arxiv.org/abs/2203.11171 | 分歧是信息信号 |
| 3 | **Pathak et al. (2019). "Self-Supervised Exploration via Disagreement."** ICLR 2019. | https://arxiv.org/abs/1906.04110 | 分歧 = 未探索的证据空间 |
| 4 | **Michael et al. (2023). "Debate Helps Supervise Unreliable Experts."** NYU/Anthropic. | https://arxiv.org/abs/2311.08702 | 辩论在不完美参与者中仍有效 |
| 5 | **Young (2026). "Knowledge Divergence and the Value of Debate."** | https://arxiv.org/abs/2603.05293 | 知识分歧是辩论价值的来源 |
| 6 | **Khan et al. (2024). "Debating with More Persuasive LLMs."** UCL/Anthropic. | https://arxiv.org/abs/2402.06782 | 辩论中说服力与真实性的关系 |
| 7 | **Rawal et al. (2025). "Evaluating Model Explanations without Ground Truth."** | https://arxiv.org/abs/2505.10535 | 无 GT 时的内部一致性评估 |
| 8 | **Es et al. (2023). "Ragas: Automated Evaluation of RAG."** | https://arxiv.org/abs/2309.15217 | 无参考评估框架 |
| 9 | **Wu et al. (2025). "Can LLM Agents Really Debate?"** | https://arxiv.org/abs/2511.07784 | 辩论过程指标揭示真实协商 |
| 10 | **Zheng et al. (2025). "A Survey of Process Reward Models."** | https://arxiv.org/abs/2510.07971 | 过程奖励模型的系统综述 |

---

## 附录 A：答辩话术 — "既然 Tiebreak 听 DeepSeek，为什么还要 Vote-3？"

### 评委可能的追问

> "既然 DeepSeek 整体表现最好，三人全不同时还是听它的——为什么不直接用 DeepSeek 做单 Agent？你费这么大劲搞 Vote-3 甚至 Debate，意义何在？"

### 答辩话术

**第一步：纠正问题的前提**

> "这个问题的前提需要被修正——Vote-3 的价值不体现在 one-one-one tiebreak 上，而体现在 two-one majority 上。"

**第二步：用数据说话**

> "Phase 2 诊断实验中，Vote-3 较最佳单模型提升了 2.1pp——从 82.7% 到 84.8%。这 2.1pp 的增益来自那些'两个模型对了、一个模型错了'的题目。在这类 case 中，多数投票纠正了少数错误——这才是多智能体集成的核心价值。
>
> Tiebreak（三人全不同）在实际数据中的触发频率是多少？Phase 2 诊断 1200 题中，三人全不同的题目仅占 2.7%（约 32 题）。在这 32 题上系统回退到 DeepSeek，而在其余 1168 题上——多数投票产生了超越任何单模型的增益。"

**第三步：用类比收尾**

> "一个民主制度不会因为 0.1% 的选举平票需要最高法院裁决，就被质疑'民主的意义何在'。Vote-3 的价值同样不能被 3% 的 edge case 否定。当前系统的 Tiebreak 是工程简化，工业级的升级方向是置信度加权聚合——这是系统的下一个迭代点。"

### 如果评委追问"那为什么不直接用 Vote-3 替代所有模式"

> "这正是本研究通过实验要精确回答的问题。Phase 2 证明了诊断场景下 Vote-3 确实最优（+2.1pp）。但 Phase 3 辟谣场景的数据显示，在当前模型组合和提示结构下，辟谣任务更容易触发模型间不一致。辩论的价值在于把这些不一致显式暴露出来，供 Judge 做证据权衡；而 Vote-3 只输出多数结果，较难呈现分歧背后的理由。
>
> Vote-3 善于'多数共识'——适合知识检索型任务。
> Debate 善于'证据探索'——适合证据推理型任务。
>
> 不同任务需要不同模式——这就是本研究提出的'任务-策略匹配'原则。如果所有场景都用 Vote-3，系统在辟谣上会错过对抗式辩论才能挖掘的证据两面性。"

### 附录 A.1：Tiebreak 触发频率的直觉推导

以下推导基于独立同分布（IID）和错误均匀分布的简化假设，仅用于说明 Tiebreak 是边缘机制——**实际触发率以实验统计为准，IID 假设在 LLM 集成中通常不成立**（模型错误高度相关，错误类别不均匀）。

在 IID 简化假设下，4 分类任务单模型准确率 p=0.80 时，三人全不同的概率约 3-5%。Phase 2 实验中实际触发率为 2.7%（32/1200 题）。这一比例与简化推导量级一致，验证了 Tiebreak 是边缘 case 而非主流机制。

---
*文档版本: 2026-05-02*
