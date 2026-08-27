# 核心技术点工程审查文档

## 1. 审查结论

本项目的核心技术点不应表述为单纯“多智能体协作”。从代码结构和实验结果看，更准确的定位是：

**混合知识源 RAG + 任务分流 MAS + 证据链/幻觉防护。**

其中，**RAG 检索链路目前最有工程和数据支撑**；MAS 的收益有明显任务边界，不能泛化宣称“多智能体提升准确率”。诊断类任务中，投票集成有效，辩论收益不稳定甚至为负；辟谣任务中，辩论主要提升分歧暴露和保守性，不提升准确率。

## 2. 核心技术点定位

### 2.1 混合知识源 RAG

RAG 是当前最实的核心模块。配置中已经定义了多类集合，包括指南、文献、药品说明书、临床试验、KG、患者教育和药品安全信号：`backend/rag/config.py:12`。

检索主入口是 `hybrid_retrieve`：`backend/rag/retrieval/hybrid.py:279`。它串联了：

- 本地指南 BM25 检索：`backend/rag/retrieval/hybrid.py:328`
- DashVector dense retrieval：`backend/rag/retrieval/hybrid.py:338`
- rerank：`backend/rag/retrieval/hybrid.py:356`
- source quota：`backend/rag/retrieval/hybrid.py:357`
- 用药安全的 `unsafe_to_answer` 防线：`backend/rag/retrieval/hybrid.py:384`

这部分不是概念设计，已经落在检索、排序、证据配额和风险标记上。

### 2.2 GraphRAG 的实际定位

GraphRAG 目前不能作为“已验证核心收益”来写。项目文档明确写了 KG 是候选和路径推理层，不是最终权威层：`backend/rag/graph/README.md:5`。

更关键的是，GraphRAG 默认关闭：`backend/rag/config.py:28`，并且图谱 README 明确写着 “off by default until KG v2 is rebuilt and verified”：`backend/rag/graph/README.md:59`。

工程结论：**GraphRAG 可以作为候选扩展和解释路径，但不能作为当前主效果来源。**

### 2.3 多智能体协作

项目使用 LangGraph 做任务分流，主图包含 `triage`、`symptom`、`general`、`report`、`rumor_subgraph`、`medication_subgraph` 等节点：`backend/graph_engine.py:1678`。

MADDx 的主流程是 Proposer -> Critic -> Defender -> Moderator：`backend/agents/maddx/workflow.py:38`。工具调用通过 `ToolRegistry` 统一留痕，写入 `tool_call` 和 `tool_result`：`backend/agents/maddx/tools.py:1193`。

但这里有一个具体问题：MADDx 的 `rag_search` 固定调用 `retrieve_medical_evidence(query, intent="guideline_qa")`：`backend/agents/maddx/tools.py:307`。这会削弱 medication、latest research、rumor 等场景的检索适配，属于需要整改的工程问题。

### 2.4 可信控制层

项目有 append-only Blackboard，用 version 和 parent refs 支持 trace DAG：`backend/core/blackboard.py:30`。

证据链构造集中在 `build_chain`，并会过滤不在 refs 池中的 triple，防止 LLM 捏造引用：`backend/core/evidence.py:85`。

幻觉防护入口是 `guard_answer`：`backend/agents/hallucination_agent.py:573`。无证据时降级为 WARN：`backend/agents/hallucination_agent.py:361`。高风险或矛盾情况会触发 ABSTAIN/WARN 等动作：`backend/agents/hallucination_agent.py:210`。

## 3. 效果评价

### 3.1 RAG 检索效果

`backend/rag/reports/eval_runs/after_four_stage_upgrade.json` 显示，RAG golden queries 评测 n=50：

- Top1 source accuracy：0.86
- Top5 source accuracy：0.96
- preferred source type hit：0.98
- authority tier match：0.94
- citation locator valid rate：1.0
- mojibake rate：0.004

这说明 RAG 的“找对来源类型”和“返回可定位引用”目前是有数据支撑的。限制是：样本只有 50 条，且 `graph_candidate_recall@5` 为 null，不能据此证明 GraphRAG 效果。

### 3.2 诊断 MAS 效果

`backend/experiments/results/phase2/PHASE2_FINAL_REPORT.md` 显示：

- Single-DeepSeek：82.7%
- Vote-3：84.8%
- Debate-A-1r：81.8%
- Debate-B-1r：83.9%

工程结论：**Vote-3 有小幅有效增益；辩论不能作为诊断准确率提升卖点。**

报告中还显示 Debate-A 修正 42 例但误伤 52 例，净收益 -10。这个结果不能包装成“辩论提升诊断能力”。

### 3.3 辟谣 MAS 效果

`backend/experiments/results/phase3/ANALYSIS_REPORT.md` 显示：

- Single：严格准确率 56.0%
- Vote-3：56.8%
- Debate：53.0%

Debate 的准确率低于 Single 和 Vote-3。但它带来了过程行为变化：Advocate-Skeptic 分歧率 51.7%，高于 Vote-3 pairwise 的 20-31%；“尚无定论”比例从 Single 的 10.5% 提升到 Debate 的 31.8%。

工程结论：**辟谣 Debate 的定位应是风险保守和分歧暴露，不是准确率提升。**

### 3.4 MADDx 动态工具版

`backend/experiments/results/ablation_fixed_critic/ablation_summary.json` 中 n=40 的结果显示：

- Single-LLM Top1：0.425，平均延迟 2.72s
- MADDx-static Top1：0.45，平均延迟 11.95s
- MADDx-dynamic Top1：0.425，平均工具调用 4.8，平均证据命中 28.85，平均延迟 40.24s

动态工具版确实引入证据，但没有带来 Top1 增益，且延迟显著增加。它目前更适合做可解释诊断流程展示，不适合作为默认高性能路径。

## 4. 不能作为核心卖点的表述

以下表述不建议写入答辩或项目说明：

- “多智能体显著提升诊断准确率”：实验不支持，Vote-3 支持，Debate 不支持。
- “GraphRAG 是当前核心效果来源”：代码默认关闭，评测指标为 null。
- “动态工具辩论提升诊断效果”：n=40 消融中 Top1 未优于 Single。
- “系统具备临床诊断能力”：应统一表述为健康咨询辅助、风险提示和证据辅助问答。

## 5. 工程整改建议

1. 修正 MADDx `rag_search` 的固定意图问题，让工具按任务传入 `symptom_dx`、`medication_safety`、`latest_research` 或 `rumor_check`。
2. 将 GraphRAG 从宣传核心降级为候选扩展层，等 KG v2 完成验证后再单独评估。
3. 默认路径优先使用低成本 Single / Vote-3；Debate 只在高争议、高风险、需要解释过程的任务中触发。
4. RAG 评测需要扩展到更大 golden set，并单独报告指南、药品、研究、辟谣、症状诊断各子集。
5. 把延迟作为一等指标。当前动态 MADDx 40s 级延迟，不适合普通问答默认开启。

## 6. 本次验证

已使用项目虚拟环境执行核心测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_rag_v2.py tests/test_maddx_d2.py tests/test_maddx_d3.py tests/test_maddx_d4.py tests/test_hallucination_guard.py -q
```

结果：`64 passed`。存在 1 个 pytest cache 写入权限 warning，不影响本轮核心单元测试结论。
