# 基于见解知识库的自我反思优化方案

**设计日期**: 2026-05-03

---

## 1. 现有基础

见解知识库 (`core/insight_memory.py`, 805 行) 已具备的基本能力：

| 功能 | 实现 | 状态 |
|------|------|:---:|
| 双极性存储 | SUCCESS 正例 + FAILURE 反例 | ✅ |
| 双桶隐私 | user_id 私有桶 + 共享桶 | ✅ |
| 质量评分 | confidence + hallucination_score + evidence_count | ✅ |
| 向量检索 | dashscope text_embedding_v3 + cosine similarity | ✅ |
| Few-shot 注入 | render_insights_as_fewshot() → system prompt | ✅ |
| Hallucination 收割 | harvest_from_hallucination_report() | ✅ |
| 当前使用者 | 仅 GeneralAgent（few-shot 注入） | ⚠️ 利用率极低 |

---

## 2. 文献调研

### 2.1 Self-Refine (Madaan et al., NeurIPS 2023)

**机制**: LLM 生成初始输出 → 自我批评 → 迭代修正。关键是反馈循环不依赖外部信号——LLM 自己产生批评。

> **对本方案的启示**: HallucinationAgent 已经能产出 per-claim 的 hallucination_score——这是天然的自我批评信号。如果某条 claim 的 verdict=CONTRADICTED，可以将该回答标记为 FAILURE insight，下次相似问题直接换策略。

### 2.2 Reflexion (Shinn et al., NeurIPS 2023)

**机制**: Agent 存储完整轨迹，失败时进行口头反思（不更新权重），将反思文本存入长期记忆。后续决策时检索相关反思。

> **对本方案的启示**: 这是最接近我们已有架构的方案。我们的 Blackboard 已经是轨迹存储，Insight Memory 已经做检索。缺失的只是一个"反思生成"步骤——在检测到 FAILURE 时，调一次 LLM 让它分析失败原因并写入 insight。

### 2.3 Generative Agents (Park et al., UIST 2023)

**机制**: Agent 维护经验流 → 周期性反思 → 抽象为高阶洞察。三层记忆：observation → reflection → plan。

> **对本方案的启示**: 当前我们只存了一级 insight（单次问答的成功/失败）。可以加入"跨 case 模式挖掘"——比如定期扫描 insight 库，发现"药物相互作用类问题频繁 FAILURE"→ 自动建议将 MEDICATION_REVIEW 的协作模式从 Single+KG 升级为 Vote-3。

### 2.4 Direct Preference Optimization (Rafailov et al., NeurIPS 2023)

**机制**: 用偏好对（preferred vs dispreferred）直接优化模型，不需要单独的 reward model。

> **对本方案的启示**: 用户采纳的答案 = preferred，用户追问/反驳的答案 = dispreferred。这可以在 product 层面收集，用于离线更新 COLLAB_MODE_MAP 的权重。

### 2.5 多Agent辩论中的自我改进 (Du et al., 2023; Tian et al., 2024)

**机制**: 辩论过程本身产生"哪个论证更强"的信号——被 Judge 采纳的一方 = stronger。

> **对本方案的启示**: Phase 2/3 的辩论数据可以直接作为 insight 来源——Debate 分歧中 Judge 偏向 Skeptic 的 case → Advocate 下次需要更好的论证策略。

---

## 3. 方案设计

### 3.1 三层反思架构

```
Level 1: Instance-Level (每次问答后)
  每次 Agent 回答后:
    ├─ HallucinationAgent  → hallucination_score
    ├─ harvest hook       → 写入 Insight Memory (已有)
    └─ 新增: 反思生成     → 如果是 FAILURE，LLM 分析失败原因 → 写入 insight.failure_analysis

Level 2: Pattern-Level (每 N 次问答后，离线)
  定期扫描 Insight Memory:
    ├─ 统计各 (domain, polarity) 的 failure rate
    ├─ 发现高频 failure pattern → 生成优化建议
    └─ 更新 COLLAB_MODE_MAP 中的静态映射权重

Level 3: Strategy-Level (Phase 完成后)
  离线分析实验数据:
    ├─ 重新计算每 (act, attr) 组合的最优协作模式
    └─ 更新 COLLAB_MODE_MAP
```

### 3.2 Level 1 实现（最小改动，最高收益）

**改动文件**: `core/insight_memory.py` + `graph_engine.py`

```python
# 新增函数：失败反思生成
async def generate_reflection(
    query: str,
    final_answer: str,
    hallucination_report: dict,
    agent_path: str
) -> str:
    """用 LLM 分析失败原因，生成 1-2 句反思文本。"""
    prompt = f"""
    你是多智能体系统的质量分析师。以下回答被标记为 FAILURE。
    
    用户问题: {query[:300]}
    AI回答: {final_answer[:300]}
    幻觉检测: {json.dumps(hallucination_report, ensure_ascii=False)[:300]}
    处理链路: {agent_path}
    
    请分析 1-2 句话: 这个回答为什么失败？下次类似问题应该如何改进策略？
    例如: "该问题涉及药物相互作用，但 Single+KG 模式未能充分检索禁忌信息，建议升级为 Vote-3。"
    """
    reflection = await llm_call(prompt)
    return reflection
```

**在 harvest hook 中集成**:
```python
if polarity == "FAILURE":
    reflection = await generate_reflection(query, final_answer, halluc_report, agent_path)
    await add_insight(..., failure_analysis=reflection)
```

**检索时利用反思**:
```python
# 当前: retrieve_insights() 只返回 few-shot 示例
# 改为: 同时返回相关失败反思，作为 "anti-pattern 警告"
def retrieve_with_reflections(query, user_id, domain):
    insights = await retrieve_insights(query, user_id, domain)
    # 分离正例和反例
    success = [i for i in insights if i.polarity == "SUCCESS"]
    failures = [i for i in insights if i.polarity == "FAILURE"]
    # 正例作为 few-shot，反例的 failure_analysis 作为 "避免以下错误" 的提示
    return success, failures
```

### 3.3 Level 2 实现（中期）

**定期离线脚本**: `experiments/analyze_insight_patterns.py`

```python
# 1. 按 (domain, polarity) 聚合
SELECT domain, polarity, COUNT(*), AVG(quality_score)
FROM insights
GROUP BY domain, polarity

# 2. 发现 failure 集中的 (act, attr) 组合
#    如果某个组合的 failure_rate > 阈值 → 建议调整协作模式

# 3. 输出优化建议
#    "MEDICATION_REVIEW 在 CONTRAINDICATION 子意图下 failure_rate=35%，
#     建议将协作模式从 Single+KG 升级为 Vote-3"
```

### 3.4 Level 3 实现（Phase 实验后）

每次 Phase 实验完成后，将结果反向写入 COLLAB_MODE_MAP：

```python
def update_collab_map_from_experiment(act, attr, best_mode, accuracy_delta):
    current = COLLAB_MODE_MAP.get((act, attr))
    if accuracy_delta > 0.02:  # 超过 2pp 的显著差异
        COLLAB_MODE_MAP[(act, attr)] = {"mode": best_mode, ...}
```

---

## 4. 参考文献

| # | 论文 | 链接 | 采用的设计 |
|---|------|------|-----------|
| 1 | **Self-Refine (Madaan et al., NeurIPS 2023)** | https://arxiv.org/abs/2303.17651 | 自我批评→迭代修正 |
| 2 | **Reflexion (Shinn et al., NeurIPS 2023)** | https://arxiv.org/abs/2303.11366 | 轨迹存储+口头反思+长期记忆 |
| 3 | **Generative Agents (Park et al., UIST 2023)** | https://arxiv.org/abs/2304.03442 | 经验流→反思→高阶洞察 |
| 4 | **Voyager (Wang et al., 2023)** | https://arxiv.org/abs/2305.16291 | 成功技能库的复用 |
| 5 | **DPO (Rafailov et al., NeurIPS 2023)** | https://arxiv.org/abs/2305.18290 | 偏好对驱动的策略更新 |
| 6 | **Multiagent Debate (Du et al., 2023)** | https://arxiv.org/abs/2305.14325 | 辩论胜者信号→策略改进 |
| 7 | **Constitutional AI (Bai et al., 2022)** | https://arxiv.org/abs/2212.08073 | 自我批评+宪法原则 |
| 8 | **STaR (Zelikman et al., ICLR 2022)** | https://arxiv.org/abs/2203.14465 | 正确推理链的筛选复用 |

---

## 5. 论文贡献声明

> "本研究在见解知识库的基础上，提出了三层自我反思优化架构。Level 1 在每次问答后由 HallucinationAgent 驱动的反思生成和 insight 存储，实现了 Reflexion (Shinn et al., 2023) 式的口头强化学习。Level 2 通过周期性扫描 insight 库中的跨 case 失败模式，自动生成协作模式调整建议，对应 Generative Agents (Park et al., 2023) 的反思→计划管道。Level 3 将消融实验结果反向写入协作模式映射表，完成离线优化→在线部署的闭环。这一架构使系统具备了从自身经验中持续改进的能力，填补了当前静态映射表缺乏自适应能力的空白。"

---

## 6. 实现优先级

| 优先级 | 功能 | 预估工作量 | 论文价值 |
|--------|------|---------|---------|
| P0 | Level 1: 失败反思生成 + insight 检索增强 | 2-3h | 核心亮点 |
| P1 | Level 3: 实验结论→映射表更新 | 1h | 闭环验证 |
| P2 | Level 2: 跨 case 模式挖掘 | 3-4h | 锦上添花 |

---
*文档版本: 2026-05-03*
