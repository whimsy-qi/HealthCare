# 模型 API 使用优化方案

**日期**: 2026-05-04

---

## 1. 现状问题

### 1.1 三个核心问题

| 问题 | 表现 | 影响 |
|------|------|------|
| **意图分类用 FAST_MODEL** | Triage 用 `FAST_MODEL`（deepseek-chat），是整个系统最重要的路由决策，却用了最轻量的模型 | 意图分错→后续全错 |
| **线上路由和离线实验模型不一致** | 实验用 `deepseek-v4-pro` 硬编码，线上路由走 `DEFAULT_MODEL`（deepseek-chat） | 实验结论不能直接对应线上行为 |
| **Qwen/GLM 在线上一场未用** | 投票和辩论模式在 graph_engine 中只调 DeepSeek，collab_models 字段被忽略 | 三模型投票退化为三路 DeepSeek 盲答 |

### 1.2 当前模型分配（线上）

```
TriageAgent:         FAST_MODEL (deepseek-chat)
GeneralAgent ReAct:  DEFAULT_MODEL (deepseek-chat)
SymptomAgent:        DEFAULT_MODEL → MADDx agent_loop 用 REASONING_MODEL
MADDx Debate:        REASONING_MODEL (deepseek-reasoner)
Rumor CTAEW:         REASONING_MODEL via agent_loop
MedicationAgent:     DEFAULT_MODEL + FAST_MODEL
ReportAgent:         DEFAULT_MODEL
HallucinationAgent:  REASONING_MODEL
Insight Reflection:  FAST_MODEL
```

---

## 2. 优化方案

### 2.1 模型分级策略

| 任务等级 | 模型 | 理由 |
|---------|------|------|
| **S 级（路由决策）** | `deepseek-v4-pro` | 意图分类是全系统的入口，分类错了后续全错。路由决策需要最强的推理能力 |
| **A 级（推理+辩论）** | `deepseek-v4-pro` + `qwen-max` + `glm-5.1` | 诊断辩论、辟谣对抗、用药审查——核心业务逻辑，需要跨模型多样性 |
| **B 级（内容生成）** | `deepseek-chat` (DEFAULT_MODEL) | 常识科普、报告解读、急救回答——单模型足够，成本优先 |
| **C 级（辅助任务）** | `deepseek-chat` (FAST_MODEL) | 标题生成、实体提取、图谱归一化——轻量任务，速度优先 |

### 2.2 具体改动

#### 改动 1: TriageAgent 升级到 V4-Pro [P0]

```python
# agents/triage_agent.py
# 当前: model=FAST_MODEL
# 改为: model="deepseek-v4-pro"
```

**理由**: 意图分类是全系统的 bottleneck——它是唯一的路由入口，且只调用一次。用 V4-Pro 的额外推理能力换取分类准确率，成本增加可忽略（每次 query 只多 1 次 V4-Pro 调用）。

#### 改动 2: 统一线上和离线模型 ID [P0]

```python
# core/llm_client.py
DEFAULT_MODEL = "deepseek-v4-pro"      # 从 deepseek-chat 改为 v4-pro
FAST_MODEL = "deepseek-chat"           # 轻量任务仍用 chat
REASONING_MODEL = "deepseek-v4-pro"    # 推理任务用 v4-pro
```

#### 改动 3: VoteRunner/DebateRunner 支持跨模型 [P1]

当前 graph_engine 调用 VoteRunner 时只传 `query`，不传模型配置。改为：

```python
# graph_engine.py general_node
if collab_mode == "vote":
    models = state.get("collab_models", ["deepseek", "qwen", "glm"])
    ans = await VoteRunner.run(query=query, models=models)
```

这个改动已经在实验脚本里实现了（VoteRunner 支持 `models` 参数），只需在 graph_engine 中正确传参。

#### 改动 4: HallucinationAgent 保持 REASONING_MODEL

HallucinationAgent 是安全底线，保持用最强推理模型。不降级。

---

## 3. 优化后的模型分配

```
                    ┌─────────────────────────────┐
                    │       TriageAgent            │
                    │   deepseek-v4-pro (S级)      │  每个 query 入口
                    └─────────────┬───────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
        ▼                         ▼                         ▼
┌───────────────┐     ┌──────────────────┐     ┌──────────────────┐
│ GeneralAgent  │     │  MADDx Debate     │     │  Rumor CTAEW     │
│ v4-pro (A级)  │     │ v4-pro + qwen-max │     │ v4-pro + qwen-max│
│ ReAct循环     │     │ + glm-5.1 (A级)   │     │ + glm-5.1 (A级)  │
└───────────────┘     └──────────────────┘     └──────────────────┘
        │                         │                         │
        ▼                         ▼                         ▼
┌───────────────┐     ┌──────────────────┐     ┌──────────────────┐
│MedicationAgent│     │  ReportAgent     │     │HallucinationAgent│
│ v4-pro (B级)  │     │ chat (B级)       │     │ v4-pro (A级)     │
└───────────────┘     └──────────────────┘     └──────────────────┘
        │                         │                         │
        ▼                         ▼                         ▼
┌───────────────┐     ┌──────────────────┐     ┌──────────────────┐
│ 辅助任务       │     │  Insight反思     │     │  嵌入/重排序     │
│ chat (C级)    │     │ chat (C级)       │     │ qwen3 (专用)    │
└───────────────┘     └──────────────────┘     └──────────────────┘
```

---

## 4. 预期效果

| 改动 | 预期效果 |
|------|---------|
| Triage → V4-Pro | 意图分类准确率提升（Phase 1 数据显示 V4-Pro 单选 82.2% vs chat 80.2%） |
| 三模型跨模型投票 | 线上 Vote-3 不再退化为三路 DeepSeek，真正产生模型多样性 |
| 统一模型 ID | 实验结论可直接对应线上行为，论文可复现 |

---

## 5. 实施优先级

| 优先级 | 改动 | 文件 | 预估 |
|--------|------|------|:---:|
| **P0** | TriageAgent → V4-Pro | `triage_agent.py` | 5 min |
| **P0** | DEFAULT_MODEL → V4-Pro | `llm_client.py` | 5 min |
| **P1** | VoteRunner 传 models | `graph_engine.py` | 10 min |
| **P1** | DebateRunner 跨模型 | `graph_engine.py` | 10 min |

---
*文档版本: 2026-05-04*
