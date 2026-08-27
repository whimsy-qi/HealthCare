# 记忆机制现状与优化方案

**日期**: 2026-05-04

---

## 1. 文献调研

### 1.1 记忆增强的 LLM Agent

| # | 论文 | 核心主张 | 链接 |
|---|------|---------|------|
| 1 | **Park et al. (2023). "Generative Agents: Interactive Simulacra of Human Behavior."** UIST 2023. | 三层记忆架构：observation stream → reflection（周期性反思抽象为高阶洞察）→ plan（基于反思的行为规划）。"Agents retrieve relevant past observations to inform current decisions, and periodically reflect to form higher-level insights." | https://arxiv.org/abs/2304.03442 |
| 2 | **Packer et al. (2023). "MemGPT: Towards LLMs as Operating Systems."** | 分层记忆管理：working memory（当前上下文）+ archival storage（长期存储，自主检索）。"MemGPT manages a hierarchical memory system, autonomously moving data between working and archival memory." | https://arxiv.org/abs/2310.08560 |
| 3 | **Wang et al. (2023). "Voyager: An Open-Ended Embodied Agent with Large Language Models."** | Skill Library 模式：存储成功执行的任务序列作为可复用技能。"Voyager maintains a skill library of executable code, continuously expanding it through self-verification and iterative prompting." | https://arxiv.org/abs/2305.16291 |
| 4 | **Xu et al. (2024). "A Survey on Memory in LLM-based Agents."** | 系统综述：将 Agent 记忆分为 working memory、episodic memory、semantic memory、procedural memory 四类。"Effective memory design is the cornerstone of capable LLM agents." | https://arxiv.org/abs/2404.13501 |
| 5 | **Zhang et al. (2024). "Memory-Augmented Large Language Models: A Survey."** | 记忆增强 LLM 的三阶段：encode（编码）→ retrieve（检索）→ utilize（利用）。"The retrieval module selects the most relevant memories to augment the current context." | https://arxiv.org/abs/2404.12876 |

### 1.2 长期记忆与个性化

| 6 | **Shuster et al. (2022). "BlenderBot 3: A Deployed Conversational Agent that Continually Learns."** | 部署后的持续学习：从用户交互中提取长期记忆，用于个性化。"BlenderBot 3 stores long-term memories extracted from conversations and uses them to personalize future interactions." | https://arxiv.org/abs/2208.03188 |
| 7 | **Xu et al. (2023). "ChatDB: Augmenting LLMs with Databases as Their Symbolic Memory."** | 用数据库（而非向量检索）作为 LLM 的符号化记忆。"SQL databases serve as symbolic memory for LLMs, enabling precise and structured recall of past interactions." | https://arxiv.org/abs/2306.02684 |

### 1.3 Insight/Eureka Memory

| 8 | **Shinn et al. (2023). "Reflexion: Language Agents with Verbal Reinforcement Learning."** NeurIPS 2023. | "The agent stores trajectory experiences in episodic memory, reflects on failures to produce verbal reinforcement signals, and uses these reflections to improve future decisions without weight updates." | https://arxiv.org/abs/2303.11366 |

---

## 2. 现状分析

### 2.1 当前记忆层次

```
会话记忆 (ChatSession+ChatMessage, MySQL)
    ↓ 仅存储，不主动检索
患者档案 (HealthProfile, MySQL)
    ↓ LLM 抽取三类（疾病/过敏/手术），fire-and-forget
见解知识库 (InsightMemory, SQLite)
    ↓ 仅 GeneralAgent 的 few-shot 注入使用，其他 Agent 全未用到
```

### 2.2 四个核心缺口

| 缺口 | 表现 | 用户感知 |
|------|------|---------|
| **无跨会话记忆** | 用户昨天问过"我头疼"，今天再问，系统完全不记得 | "这 AI 好像每次都重新认识我" |
| **档案维度太窄** | 只抽疾病/过敏/手术，不抽用药史、家族史、生活习惯 | "我上次说了在吃降压药，它怎么又问" |
| **Insight 未全局化** | 只有 GeneralAgent 在用 insight few-shot，Symptom/Rumor/Medication 全没用到 | 浪费了反思系统的价值 |
| **Agent 间无共享学习** | 每个 Agent 各自为政，RumorAgent 今天学到"可乐加味精的 case 处理得很好"，SymptomAgent 不知道 | "系统没有在变聪明" |

---

## 3. 优化方案

### 3.1 四层记忆架构

```
L0 — Working Memory (当前会话)
  ChatSession messages + Blackboard DAG
  所有 Agent 共享，会话结束即归档

L1 — Episodic Memory (用户级)
  跨会话的用户健康档案
  提取维度: 疾病 / 过敏 / 手术 / 用药 / 家族史 / 生活习惯
  每次问答后自动更新，下次会话自动注入 Agent prompt

L2 — Semantic Memory (知识级)
  InsightMemory 升级: 从"GeneralAgent only" → 全局 Agent 共享
  检索接口: retrieve_insights(query, domain, user_id) → 注入 Agent prompt
  新增: 跨 case 模式挖掘 (periodic reflection)

L3 — Procedural Memory (策略级)
  存储"哪种协作模式对哪种 query 最有效"
  COLLAB_MODE_MAP 的动态权重 —— 从静态表升级为数据驱动
```

### 3.2 L1 改造：扩展档案提取维度

**当前**:
```python
提取: diseases, allergies, surgeries (3 类)
```

**改为**:
```python
提取: diseases, allergies, surgeries, medications, family_history, lifestyle (6 类)
```

新增 Prompt 指令：
```
【用药史 (medications)】
用户正在服用的药物: "我在吃降压药" → ["降压药"]
"我每天吃一片阿司匹林" → ["阿司匹林"]

【家族史 (family_history)】
直系亲属的疾病: "我爸有糖尿病" → 存入 family_history
注: 这是 family_history，不是用户本人的 disease

【生活习惯 (lifestyle)】  
"我每天抽一包烟" → smoking
"我一周跑三次步" → exercise
"我睡眠不太好" → sleep_issues
```

**依据**: Shuster et al. (2022) — "long-term memories extracted from conversations personalize future interactions"

### 3.3 L2 改造：InsightMemory 全局化

**改动**:

1. 在 `generate_final_diagnosis` (SymptomAgent)、`run_rumor_controller` (RumorAgent)、`run_med_reviewer` (MedicationAgent) 的 system prompt 注入前，检索 insight：

```python
insights = await retrieve_insights(
    query=query, user_id=user_id, domain=domain,
    top_k=3, min_similarity=0.78, include_shared=True,
)
if insights:
    insight_text = render_insights_as_fewshot(insights, max_chars=800)
    system_prompt += f"\n\n{insight_text}"
```

2. 新增周期性反思（Level 2 Pattern Mining）：
```python
async def periodic_reflection():
    """每 100 次 insight 写入后触发一次"""
    # 按 domain 聚合 failure 模式
    # 发现高频失败 → 自动建议调整协作模式权重
    # 写入 procedural memory
```

**依据**: Park et al. (2023) — "agents periodically reflect to form higher-level insights"; Shinn et al. (2023) — "reflections improve future decisions without weight updates"

### 3.4 L3 改造：动态权重从 Insight 学习

当前 COLLAB_MODE_MAP 是静态表。改为从 Insight 中学习：

```python
# 每 500 次 insight 写入后
def update_collab_weights():
    for (act, attr), stats in aggregate_insight_stats():
        failure_rate = stats.failures / stats.total
        if failure_rate > 0.30:  # 当前模式失败率 >30%
            # 推荐切换协作模式
            suggest_mode_switch(act, attr)
```

**依据**: Zhang et al. (2024) — "the retrieval module selects the most relevant memories to augment the current context"

---

## 4. 实施优先级

| 优先级 | 改动 | 工作量 | 论文价值 | 用户体验影响 |
|--------|------|:---:|:---:|:---:|
| **P0** | L1: 档案维度扩展到 6 类 | 30 min | 中 | 高 — "系统记住我了" |
| **P0** | L2: Insight 注入 Symptom/Rumor/Medication | 30 min | 高 | 中 — "回答越来越准" |
| **P1** | L1: 跨会话记忆注入 Agent prompt | 20 min | 高 | 高 — "它记得上次" |
| **P2** | L3: COLLAB_MODE_MAP 动态权重 | 1h | 高 | 低 — 系统级改进 |
| **P2** | L2: 周期性反思 Pattern Mining | 2h | 高 | 低 — 系统级改进 |

---

## 5. 参考文献

| # | 论文 | 链接 | 采用的设计 |
|---|------|------|-----------|
| 1 | **Park et al. (2023). "Generative Agents."** UIST. | https://arxiv.org/abs/2304.03442 | 周期性反思抽象为高阶洞察 |
| 2 | **Packer et al. (2023). "MemGPT."** | https://arxiv.org/abs/2310.08560 | 分层记忆—working + archival |
| 3 | **Shinn et al. (2023). "Reflexion."** NeurIPS. | https://arxiv.org/abs/2303.11366 | 失败反思存入记忆并影响未来决策 |
| 4 | **Shuster et al. (2022). "BlenderBot 3."** | https://arxiv.org/abs/2208.03188 | 从对话提取长期记忆做个性化 |
| 5 | **Xu et al. (2024). "Survey on Memory in LLM Agents."** | https://arxiv.org/abs/2404.13501 | 四类记忆分类框架 |
| 6 | **Zhang et al. (2024). "Memory-Augmented LLMs Survey."** | https://arxiv.org/abs/2404.12876 | 编码→检索→利用三阶段 |
| 7 | **Wang et al. (2023). "Voyager."** | https://arxiv.org/abs/2305.16291 | 成功技能库的可复用模式 |
| 8 | **Xu et al. (2023). "ChatDB."** | https://arxiv.org/abs/2306.02684 | 数据库作为 LLM 的符号化记忆 |

---
*文档版本: 2026-05-04*
