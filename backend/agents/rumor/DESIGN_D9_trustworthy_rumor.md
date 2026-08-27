# D9 — Trustworthy Rumor Verification via Claim-Type-Aware Evidence Weighting

> 把谣言验证从"单 LLM 综合判决"升级为"**分类自适应权重 + 工具增强双方辩论 + 加权审判**"的可信验证框架。
>
> 对齐毕设主标题"**可信**"的论文论点，并复用 MADDx D8 Blackboard/ToolRegistry 基础设施。

---

## 1. 现状问题（D7 rumor_agent 的硬伤）

| 问题 | 现状 | 后果 |
|------|------|------|
| **无真辩论** | Judge 是单 LLM 左右互搏 | 与 MADDx 的 Proposer↔Critic↔Defender 不在一个量级 |
| **源权重硬编码** | prompt 里一句"指南 >>> 新闻" | 成分类谣言 KG 最权威，热点类谣言 web 最新——现状"一刀切"误判 |
| **重审循环从不触发** | `needs_revision=True` 条件是双端均无证据 | `max_turns=2` 纯装饰 |
| **证据无溯源** | verdict 里论点不绑 source_ref | 违反 D8 的 evidence_refs 约定，不可审计 |
| **confidence 缺失** | 只有 4 类定性标签 | 前端和下游无法做"可信度门控" |
| **LLM 清洗过重** | 每条源一次 LLM call | 延迟 15s+，成本翻倍 |

---

## 2. D9 核心设计

### 2.1 整体流水线

```
rumor_query
    │
    ▼
┌────────────────────────────────────────────┐
│ Stage 1: Claim Classification              │
│   8 类谣言分类（CAUSAL / COMPOSITIONAL /   │
│   EFFICACY / DOSAGE / INTERACTION /        │
│   POPULATION / NOVEL_TREND / FOLKLORE）    │
└────────────────────────────────────────────┘
                    │ claim_type + confidence
                    ▼
┌────────────────────────────────────────────┐
│ Stage 2: Weight Policy Lookup              │
│   类型 → (w_kg, w_rag, w_web)              │
│   → 分配 per-source retrieval budget       │
└────────────────────────────────────────────┘
                    │
                    ▼
┌────────────────────────────────────────────┐
│ Stage 3: Tool-Augmented Adversarial Debate │
│   Advocate (支持命题) ↔ Skeptic (反驳命题) │
│   双方都走 ToolRegistry (kg/rag/web)       │
│   预算受 weight budget 约束                │
│   每条论点绑 evidence_refs                 │
│   （复用 MADDx Blackboard 与 agent_loop）  │
└────────────────────────────────────────────┘
                    │ debate history
                    ▼
┌────────────────────────────────────────────┐
│ Stage 4: Weighted Adjudication             │
│   按 claim_type 权重聚合双方证据           │
│   计算 weighted_belief_score               │
│   输出 label + calibrated_confidence       │
└────────────────────────────────────────────┘
                    │
                    ▼
      {label, confidence, claim_type,
       evidence_refs_per_source,
       dissent_score, debate_trace}
```

### 2.2 八类谣言分类

基于真实医疗谣言模式（CCTV辟谣档案 / 丁香医生辟谣榜）归纳：

| ID | 类型 | 定义 | 样例 | 最佳证据源 |
|----|------|------|------|-----------|
| `CAUSAL` | 因果说 | "X 导致 Y"、"吃 X 会得 Y" | "喝可乐骨头会融化" | RAG 文献 + KG 致病路径 |
| `COMPOSITIONAL` | 成分说 | "X 含 Y，所以有 Z 效果" | "苹果核含氰化物会中毒" | KG 成分表 + 药典 |
| `EFFICACY` | 功效说 | "X 能治疗/预防 Y" | "盐水能治新冠" | RAG 临床指南 >> KG >> Web |
| `DOSAGE` | 剂量说 | "吃多少 X 才有效/才有毒" | "一天吃 10 粒维 C 防感冒" | KG 药典 + 指南 |
| `INTERACTION` | 联用说 | "X 和 Y 一起吃会..." | "头孢加酒立即死亡" | KG 禁忌关系 + 权威指南 |
| `POPULATION` | 人群说 | "孕妇/老人/小孩不能 X" | "孕妇不能吃海鲜" | RAG 指南 + KG 禁忌 |
| `NOVEL_TREND` | 热点说 | 新冠偏方、网红食疗 | "某明星抗癌神方" | **Web（时效性）**>> RAG |
| `FOLKLORE` | 民俗说 | "老祖宗说"、地方传统 | "发烧要捂汗" | Web + RAG 均衡 |

### 2.3 权重食谱（初始策略表）

```python
CLAIM_TYPE_WEIGHTS = {
    #                   KG    RAG   Web    备注
    "CAUSAL":         (0.30, 0.50, 0.20),  # 需要机制和文献
    "COMPOSITIONAL":  (0.60, 0.30, 0.10),  # KG 成分表最权威
    "EFFICACY":       (0.25, 0.55, 0.20),  # 临床指南最重要
    "DOSAGE":         (0.50, 0.45, 0.05),  # 药典硬数据，web 几乎无用
    "INTERACTION":    (0.60, 0.35, 0.05),  # 药物冲突是 KG 的强项
    "POPULATION":     (0.35, 0.55, 0.10),  # 指南分人群规定
    "NOVEL_TREND":    (0.10, 0.30, 0.60),  # 新热点 KG/RAG 来不及收录
    "FOLKLORE":       (0.20, 0.40, 0.40),  # 民间传说多平衡
    "UNKNOWN":        (0.33, 0.34, 0.33),  # 兜底均衡
}

TOTAL_RETRIEVAL_BUDGET = 10  # top_k 总预算
# top_k_source = round(w_source * TOTAL_BUDGET)
```

**消融 baseline**：静态均衡权重 (0.33, 0.34, 0.33) — 即"现有 rumor_agent 硬编码规则"。

### 2.4 Advocate / Skeptic 辩论角色

**复用 MADDx D8 的 `agent_loop.run_agent_with_tools`**，改 prompt 即可：

- **Advocate（支持方）**：站在"命题为真"立场，调用工具找支持证据
  - 系统 prompt 要求明确声明"**即使你主观不信，也要以辩护律师身份找 best-case evidence**"
  - 避免退化为"我找不到证据所以谣言为真"

- **Skeptic（质疑方）**：站在"命题为假/需打假"立场，调用工具找反驳证据或寻找原命题的漏洞
  - 可以像 MADDx Critic 一样提"结构化 objection"：
    - `MISSING_EVIDENCE`（原命题无任何权威来源）
    - `CONTRADICTORY_EVIDENCE`（权威证据直接反驳）
    - `OVERGENERALIZATION`（命题把个案推广成普适）
    - `TEMPORAL_STALE`（命题基于已被修正的旧共识）
    - `DOSAGE_MISREPRESENT`（混淆剂量阈值）

两方各自预算：
- 工具预算按 claim_type 的权重分配：比如 COMPOSITIONAL 类，kg_query 预算 3 次，web 预算 0-1 次
- ReAct 步数共享 `MAX_REACT_STEPS=5`

### 2.5 Rumor Judge（加权审判）

**不是单 LLM 再读一遍**，而是做真正的**加权计算 + 最终解释**：

```python
def adjudicate(bb, claim_type, weights):
    # 1. 从 Blackboard 拉两方的 evidence_refs
    advocate_evidence = bb.filter("evidence_ref", agent_id="advocate")
    skeptic_evidence  = bb.filter("evidence_ref", agent_id="skeptic")

    # 2. 按 source (kg/rag/web) 分桶，算每个桶的"净支持度"
    #    net_i = (advocate_hits_i - skeptic_hits_i) / (advocate_hits_i + skeptic_hits_i + ε)
    #    ∈ [-1, +1]
    net_scores = {src: compute_net(src, adv, skp) for src in ["kg", "rag", "web"]}

    # 3. 加权得到 weighted_belief_score
    belief = sum(weights[src] * net_scores[src] for src in ["kg", "rag", "web"])
    # belief > +τ  → 属实
    # belief < -τ  → 谣言
    # |belief| ≤ τ → 证据不足 / 误导（进一步看 dissent）

    # 4. Calibrated confidence = |belief| × (1 - dissent_score) × evidence_coverage
    # 5. 让 LLM 基于 belief/dissent/weights 做最终自然语言总结（有结构化约束）
```

**关键点**：
- `belief` 是**可审计的数值**，不是 LLM 自说自话
- `dissent_score`（双方证据重叠度的反面）低 → 共识强 → 置信度高
- 论文里可以画 **"belief vs ground_truth"** 校准曲线（Reliability Diagram）

### 2.6 终止条件（复用 MADDx Rule 1-4）

1. `MAX_ROUNDS`（默认 2）
2. `CONSENSUS`（双方最终 label 一致）
3. `NO_VALID_OBJECTIONS`（Skeptic 提不出新质疑）
4. `NO_NEW_EVIDENCE`（整轮没有新 tool_call）

---

## 3. 论文论点（直接写进 4.5 节）

> **Section 4.5 — Trustworthy Rumor Verification via Claim-Type-Aware Evidence Weighting**
>
> 医疗谣言不是同质现象。成分类谣言（"苹果核含氰化物"）可以从 KG 成分表直接判定；而热点类谣言（"某明星抗癌神方"）在 KG/RAG 里根本不存在，必须靠实时 web 检索。现有系统对所有谣言使用固定的"指南 >>> 新闻"权重，对热点类和民俗类谣言存在系统性误判。
>
> 本工作提出 **Claim-Type-Aware Evidence Weighting (CTAEW)**：
> 1. 将医疗谣言归纳为 8 类，基于对 N 条真实谣言的分析；
> 2. 为每类设计证据源权重食谱 (w_kg, w_rag, w_web)，既约束 retrieval 预算，又驱动最终加权审判；
> 3. 集成进 MADDx tool-augmented debate 框架，由 Advocate 和 Skeptic 两方独立取证辩驳；
> 4. 在 30 例标注谣言测试集上做消融：
>    - **A**: Single-LLM  baseline
>    - **B**: Static-weight debate（均衡权重）
>    - **C**: **CTAEW-debate**（本工作）
>    - **D**: CTAEW-no-debate（仅分类+权重，无辩论）
>
> 预期 C > B > D > A，并给出 per-claim-type 准确率细分（COMPOSITIONAL 类在 C 下应显著优于 B）。

**可信（Trustworthy）的三个维度对齐**：
- **证据可审计**：每个结论绑 evidence_refs
- **置信度可校准**：weighted_belief_score 有数学定义，可画 Reliability Diagram
- **类型感知**：不同谣言走不同验证路径，避免一刀切误判

---

## 4. 目录结构

```
backend/agents/rumor/
├── __init__.py
├── DESIGN_D9_trustworthy_rumor.md   # 本文档
├── claim_classifier.py              # R1: 8 类分类
├── weight_policy.py                 # R2: 权重表 + budget 计算
├── advocate.py                      # R3: Advocate agent (复用 agent_loop)
├── skeptic.py                       # R3: Skeptic agent (with objection types)
├── judge.py                         # R4: weighted adjudication
├── workflow.py                      # R5: classify→debate→adjudicate 总控
├── prompts.py                       # 集中所有角色的 system prompts
└── integration.py                   # R6: graph_engine 入口

backend/experiments/data/
└── rumor_eval_seed.jsonl            # R7: 30 例谣言测试集

backend/experiments/
└── run_rumor_ablation.py            # R8: rumor 消融 harness
```

---

## 5. 风险与应对

| 风险 | 应对 |
|------|------|
| **Advocate 退化**（找不到证据就说命题为真） | Prompt 强制"无证据 ≠ 命题为真"；加硬约束：Advocate 无 evidence_refs 时其 belief 贡献被清零 |
| **claim_type 分类错误传导** | 分类器保留 top-2，Judge 发现 belief 接近 τ 时，用 top-2 权重重新打分做二次裁决 |
| **weighted_belief_score 参数 τ 如何选** | 在 30 例测试集上做 ROC 扫 τ，选 F1 最高点，论文里给曲线 |
| **数据清洗延迟问题** | 去掉 LLM 清洗，改用 trafilatura 正则清洗（单独 commit） |
| **MADDx Blackboard 需扩展** | 新增 `claim_class` / `rumor_evidence` / `rumor_objection` entry 类型；无需修改核心 API |

---

## 6. 实施顺序（R1-R9）

详见 TodoWrite。前 4 个任务（R1-R4）是论文核心贡献；R5-R6 是集成；R7-R8 是实验；R9 是演示可视化。

**最小可答辩版本 = R1+R2+R3+R4+R5+R7+R8**（跳过 R6 集成和 R9 前端，只要实验数据出来即可写论文）。
