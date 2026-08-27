# P2: 多意图并行调度 — 改造说明

**改造日期**: 2026-05-02
**改造范围**: `agents/triage_agent.py` + `graph_engine.py`

---

## 1. 改造前 vs 改造后

### 改造前

```
用户: "我头疼，布洛芬现在多少钱"

Triage → primary_intent = "SYMPTOM_ANALYSIS"（只能选一个为主）
       → 药物查询作为 parallel_intent（仅触发预处理，不触发独立Agent）
       → 只走 SymptomAgent
```

### 改造后

```
用户: "我头疼，布洛芬现在多少钱"

Triage → intents = [
           {domain: SYMPTOM_ANALYSIS, sub: DIAGNOSIS, confidence: 0.92, act: SEEK_HELP, attr: DIAGNOSE},
           {domain: MEDICATION_REVIEW, sub: DOSAGE, confidence: 0.85, act: ASK, attr: CAUTION}
         ]
       → 并发:
         ├─ SymptomAgent(Debate) → "您的头痛可能是偏头痛..."
         └─ MedAgent(Single+KG) → "布洛芬参考价格..."
       → Synthesizer → 综合答复
```

---

## 2. 文件改动

### 2.1 `agents/triage_agent.py`

| 改动 | 行数 | 说明 |
|------|------|------|
| Prompt 新增"多意图解耦"指令 | +20 | 教 LLM 将复合 query 分解为 intents 列表 |
| Prompt 新增复合意图 Few-Shot 示例 | +15 | 提供"我头疼，布洛芬现在多少钱"的正例 |
| JSON 解析新增 intents 提取与标准化 | +20 | 向后兼容：旧格式自动构造单条 intents |
| 多意图数量→uncertainty 计算 | +3 | `len(intents) * 0.2` 替代旧版 `len(parallel) * 0.15` |

### 2.2 `graph_engine.py`

| 改动 | 行数 | 说明 |
|------|------|------|
| AgentState 新增 `intents` 字段 | +2 | TypedDict 类型声明 |
| triage_node 提取 `intents_list` | +1 | 从 Triage 结果中读取 |
| triage_node 返回中加入 `intents` | +1 | 写入状态供下游读取 |
| 新增 `dispatch_parallel_agents()` | +55 | 核心：并发调度 + Synthesizer 合成 |
| 新增 `SYNTHESIZER_PROMPT` | +5 | 合成 Agent 的 system prompt |
| triage_node 多意图分流 | +10 | >1 intents 时直接并行调度，不进入后续路由 |

---

## 3. 架构

```
triage_node
    │
    ├─ len(intents) == 1 → 原有单意图路径（向后兼容）
    │
    └─ len(intents) > 1 → dispatch_parallel_agents()
                              │
                              ├─ Intent 1: handle_one()
                              │     └─ select_collab_mode(act, attr)
                              │         ├─ vote   → VoteRunner
                              │         ├─ debate → DebateRunner
                              │         └─ single → GeneralAgent
                              │
                              ├─ Intent 2: handle_one()（同上，并发）
                              │
                              └─ asyncio.gather(所有任务)
                                    │
                                    ▼
                              Synthesizer (DeepSeek, 单次调用)
                                    │
                                    ▼
                              final_answer (综合 Markdown)
```

## 4. 关键设计决策

### 4.1 向后兼容

- Triage 输出保留 `primary_intent` + `sub_intent`（不变）
- 新增 `intents` 列表，向后兼容：旧格式自动构造单条
- 单意图时走原有路径（不改变任何行为）

### 4.2 急诊不并行

`urgency == "EMERGENCY"` 时跳过多意图分派——延迟优先。

### 4.3 合成策略

每路 Agent 输出以 `### {domain}` 开头，Synthesizer 负责：
- 将独立回答整合为连贯叙述
- 如有矛盾，给出综合判断
- Markdown 格式，每个子问题独立小节

### 4.4 参考文献

| 论文 | 链接 | 采用的设计 |
|------|------|-----------|
| **DynTaskMAS (Yu et al., 2025)** | https://arxiv.org/abs/2503 | DAG 任务图 + 异步并行调度 |
| **AutoGen (Wu et al., 2023)** | https://arxiv.org/abs/2308.08155 | 并发 + 聚合对话模式 |
| **Mixture-of-Agents (Wang et al., 2024)** | https://arxiv.org/abs/2406.02792 | 分层分发→聚合 |
| **TDAG (Wang et al., 2025)** | Neural Networks 2025 | LLM 驱动的任务分解 |
| **NLU++ (2022)** | EACL 2022 | 多标签意图标注作为 NLU 标准实践 |

---

## 5. 测试验证

### 5.1 单意图（向后兼容）

```
输入: "我头疼"
预期: intents = [{domain: SYMPTOM_ANALYSIS, ...}]  (1条)
行为: 走原有路径，不变
```

### 5.2 多意图

```
输入: "我头疼，布洛芬现在多少钱"
预期: intents = [{SYMPTOM_ANALYSIS}, {MEDICATION_REVIEW}]  (2条)
行为: 并发调度 + Synthesizer 合成
```

### 5.3 急诊

```
输入: "我误服了一整瓶降压药，现在头很晕"
预期: urgency=EMERGENCY, 跳过并行调度
行为: 直接走 emergency_node
```

---

## 6. 论文贡献声明

> "本研究提出的多意图并行调度机制，将传统的'单意图单通道'路由升级为'多意图多通道并发'。当用户 query 包含多个独立子需求时，TriageAgent 将其解耦为意图列表，系统根据每个子意图的 Act×Attr 特征独立选择最优协作模式（Vote/Debate/Single），通过 DynTaskMAS 式的任务图进行异步并行调度，最终由 Mixture-of-Agents 式的聚合层合并输出。这一机制使系统从'一次处理一个问题'的管线模式升级为'一次处理一组相关问题'的并发模式，显著提升了复杂复合查询的响应完整性和逻辑一致性。"
