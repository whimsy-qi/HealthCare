# MADDx D8 技术方案：工具增强的鉴别诊断辩论（Tool-Augmented Differential Diagnosis Debate, TADD）

> 作者视角：多智能体架构设计师 / 毕设代码负责人
> 目标阶段：D8
> 前置完成：D1–D7（MADDx 辩论骨架 + SSE 流式前端 + 科主任 narrative）

---

## 1. 问题陈述（Why D8）

当前 MADDx 存在一个**体系级缺陷**，必须在答辩前解决：

> 三个辩论 agent（Proposer / Critic / Defender）共享**同一份在辩论启动前一次性预取的证据快照**（见 `integration.py::build_evidence_providers`），Critic 无权在辩论中查新证据。所谓"质疑—反驳"本质是 LLM 在同一条件下的采样分歧，不是 epistemic disagreement。

这导致三个学术问题：

| 问题 | 表现 | 论文风险 |
|---|---|---|
| P1. 辩论无 grounding 闭环 | `kg_neighbors` / `rag_chunks` 在循环里原样传入每一轮 | "辩论收敛"结果无法归因到证据 |
| P2. Critic 无法证伪 | Prompt 明文要求"只基于证据质疑"但证据是别人预取的 | 被问"Critic 怎么发现 Proposer 漏了" 会崩 |
| P3. Blackboard 沦为日志 | 没有 agent 读取别人写入的证据节点 | "黑板架构" 这个术语底气不足 |

D8 的使命：**把 MADDx 从"LLM 3 人小组讨论" 升级为"agent-controlled evidence debate"**。

---

## 2. 论文论点（一句话）

> *We introduce per-agent tool invocation into a differential-diagnosis debate: each debating agent decides when and what to retrieve from a medical knowledge graph and a guideline corpus. Compared with static-evidence debate, tool-augmented debate produces higher Top-1 accuracy, stronger evidence-citation density, and interpretable convergence.*

---

## 3. 改造前 vs 改造后

### 改造前（D7 现状）

```
integration.py (预取一次)
    └─ kg_context (文本) ──┐
    └─ rag_context (文本) ─┤
                            ▼
workflow.py loop：
  Round k:
    Proposer  ◄── 文本证据 (round 0 only)
    Critic    ◄── 文本证据 (每轮相同)
    Defender  ◄── 上一轮 Critic + 文本证据 (每轮相同)
```

证据 = 静态快照，被动注入。

### 改造后（D8 目标）

```
Blackboard
  │   key="evidence"  (由 agent 工具调用产生的新节点)
  │   key="tool_call" (每次工具调用留痕，DAG 上可视)
  ▼
Tool Layer（KGTool / RAGTool / 可扩展）
  ▲
  │  call / result
  ▼
┌─────────────────────────────────────────────┐
│  Agent Inner Loop（Proposer / Critic / Defender）│
│  ┌────────────┐    ┌────────────┐            │
│  │ Reason     │──▶ │ Tool call? │──┐         │
│  └────────────┘    └────────────┘  │         │
│        ▲                │           ▼         │
│        │          yes   │    [KGTool/RAGTool] │
│        └────────────────┴──── result ◀───────┘│
│                                               │
│  (ReAct-style, bounded N_MAX_TOOL_CALLS)      │
└─────────────────────────────────────────────┘
```

每个 agent 运行一个**受限 ReAct 环**，LLM 输出 `"action": "tool_call"` 或 `"action": "finish"`，主循环根据 action 分派。

---

## 4. Schema 扩展（`blackboard_schema.py`）

新增：

```python
# ---------- 工具调用留痕 ----------

ToolName = Literal["kg_query", "rag_search"]

class ToolCall(TypedDict):
    tool: ToolName
    query: dict                # 工具入参（如 {"disease": "偏头痛", "relation": "symptom"}）
    caller_agent: str          # proposer / critic / defender
    caller_round: int

class ToolResult(TypedDict):
    call_ref: int              # Blackboard version of the matching ToolCall entry
    tool: ToolName
    hits: List[dict]           # 工具命中的证据片段
    hit_count: int

# ---------- Objection 扩展 ----------

class Objection(TypedDict):
    target_disease: str
    type: ObjectionType
    detail: str
    evidence_refs: List[int]      # ⚠️ 改：由 str → List[int]，指向 Blackboard ToolResult 的 version
    triggered_by_tool: Optional[int]  # 新增：这条 objection 由哪次工具调用触发

# ---------- Candidate 扩展 ----------

class Candidate(TypedDict):
    disease: str
    icd10: Optional[str]
    reasoning: str
    supporting_symptoms: List[str]
    confidence: float
    evidence_refs: List[int]      # 新增：支持该候选的证据 version 列表

# ---------- 终止理由扩展 ----------

TerminationReason = Literal[
    "MAX_ROUNDS_REACHED",
    "TOP1_STABLE_HIGH_CONF",
    "NO_VALID_OBJECTIONS",
    "NO_NEW_EVIDENCE",       # ★ 新增 Rule 4
]
```

`evidence_refs` 从字符串改成 int version 是**破坏性改动**，但必要——否则 Blackboard 的 DAG 无法真正闭环。前端 trace DAG 渲染也因此获益（点一条 objection 可直接高亮触发它的 ToolResult 节点）。

---

## 5. 工具层（新文件：`agents/maddx/tools.py`）

```python
class KGTool:
    """
    作用：给定疾病名或症状名，返回其在 Neo4j 里的邻居关系。
    用法（agent 调用时）：
      kg_query(mode="disease_symptoms", disease="偏头痛", top_k=8)
      kg_query(mode="symptom_diseases", symptom="发热", top_k=8)
      kg_query(mode="disease_contraindications", disease="哮喘")
    返回：List[{"ref": "kg:node_id", "subject": ..., "predicate": ..., "object": ...}]
    """

class RAGTool:
    """
    作用：对本地指南 / 互联网医学文献做向量检索。
    用法：rag_search(query="中年女性剧烈头痛伴呕吐鉴别", top_k=5)
    返回：List[{"ref": "rag:chunk_id", "source": ..., "text": ...}]
    """
```

**实现策略**：
- 复用现有 `medication_agent.py` 的 Neo4j client 和 `symptom_controller.py` 的 DashVector client
- 加**会话级缓存**：`(tool, hashed_args) → result`，避免 agent 在同一 session 反复查一样的东西（成本 + 时延）
- 每次调用先 `bb.append("tool_call", ...)`，结果 `bb.append("tool_result", ..., parent_refs=[tool_call_v])`。DAG 自动成型

---

## 6. Agent 内部 ReAct 循环（关键改动）

### 6.1 新增控制器：`agents/maddx/agent_loop.py`

```python
MAX_TOOL_CALLS_PER_TURN = 3       # 每个 agent 一轮最多查 3 次
MAX_TOOL_CALL_ROUNDS  = 4         # ReAct 最多循环 4 次（硬上限，防发散）

async def run_agent_with_tools(
    bb, agent_name: str, round_idx: int,
    system_prompt: str, user_prompt: str,
    allowed_tools: set[str],
    final_schema: dict,            # 期望的最终 JSON 结构（candidates / objections / rebuttal）
) -> dict:
    """
    ReAct 受限循环：
      while not finish and steps < MAX:
          LLM 输出: {"action": "tool_call"|"finish", ...}
          if tool_call: 执行工具, 追加结果到消息历史
          else: 校验 final_schema, 返回
      超限强制 finish: 用 degrade prompt ("不再查证据，直接给结论") 收尾
    """
```

### 6.2 Prompt 改写要点

**Proposer**（原本不访问证据，D8 改为可选）：
- 初版加 1 句：`你可以通过 kg_query(mode="symptom_diseases", symptom=...) 对最主要症状反查候选，但最多调用 2 次。`
- 保持**每个候选必须带 `evidence_refs`** 硬约束；空 refs 视为未经证据支撑，Moderator 会惩罚 confidence

**Critic**（D8 的主战场）：
```
你必须通过工具主动取证后才能提出 objection。
推荐流程：
  1. 对每个可疑候选，先用 kg_query(mode="disease_symptoms", disease=X) 
     取 X 的典型症状集；
  2. 对照患者 symptoms 列表，寻找 missing_symptom / contradicting_symptom；
  3. 若 KG 为空，fallback 用 rag_search(query=...) 检索最新指南；
  4. 每条 objection 的 evidence_refs 必须非空，指向步骤 1/3 的 tool_result。
禁止无工具 objection（evidence_refs=[] 的 objection 会被系统丢弃）。
```

**Defender**：
```
面对每条 objection，先判断是否需要新证据反驳：
  - 若 Critic 声称"X 应该有症状 Y 但患者没有"，用 rag_search 查 X 的非典型表现；
  - 若 Critic 声称"KG 无该关系"，换关系方向 kg_query 再验证一次；
完成后输出 rebuttal + 修正的 candidates。若确实无法反驳，action=drop 降权/剔除该候选。
```

### 6.3 `run_critic` / `run_proposer` 改造

原来是**单次 LLM 调用**：

```python
resp = await client.chat.completions.create(...)   # 一次性拿 JSON
```

改为**走 agent_loop**：

```python
result = await run_agent_with_tools(
    bb, "critic", round_idx,
    CRITIC_SYSTEM, user_prompt,
    allowed_tools={"kg_query", "rag_search"},
    final_schema=OBJECTIONS_SCHEMA,
)
```

---

## 7. workflow.py 改动

```python
# 删除：kg_neighbors_provider / rag_chunks_provider 参数（D8 后不再预取）
# 改为：传入 tools=KGTool(), RAGTool() 给 agent_loop

async def run_maddx(bb, symptoms, patient_profile, tools):
    ...
    # Round 0: Proposer (可选少量工具调用)
    candidates = await run_proposer(bb, ..., tools=tools)

    for round_idx in range(1, MAX_ROUNDS + 1):
        # Critic 自主取证
        objections = await run_critic(bb, ..., round_idx=round_idx, tools=tools)

        # ★ Rule 4: NO_NEW_EVIDENCE
        new_tool_results_this_round = bb.count_since(
            key="tool_result", 
            since=round_start_version
        )
        if new_tool_results_this_round == 0 and round_idx >= 2:
            termination_reason = "NO_NEW_EVIDENCE"
            break

        # Defender
        if not objections:
            termination_reason = "NO_VALID_OBJECTIONS"
            break
        candidates = await run_defender(bb, ..., tools=tools)
        # 收敛判断同前
```

`Blackboard.count_since(key, since)` 是新增小工具方法，实现 3 行。

---

## 8. 终止规则升级

| Rule | 触发条件 | 语义 |
|---|---|---|
| 1. MAX_ROUNDS_REACHED | 现状不变 | 预算硬停 |
| 2. TOP1_STABLE_HIGH_CONF | 现状不变 | 顶部稳定 + conf≥0.7 |
| 3. NO_VALID_OBJECTIONS | 现状不变 | Critic 举白旗 |
| **4. NO_NEW_EVIDENCE** | 本轮 Critic + Defender 累计 0 次 tool 命中 | **证据空间已榨干** |

Rule 4 是 D8 的新论点：**证据驱动的收敛判据**。论文里会做一节对比——纯对话收敛（Rule 2）vs 证据收敛（Rule 4）。

---

## 9. 前端改动（`MADDxLiveDebate.jsx`）

需要新增**两种事件类型**的渲染：

```jsx
// 现有：proposer_done / critic_done / defender_done / moderator_done ...

// 新增：
phase: "tool_call"     data: {agent, tool, query, call_ref}
phase: "tool_result"   data: {call_ref, hit_count, preview}
```

UI 呈现：
- Critic 卡片内部嵌套小型灰色徽章 `🔎 kg_query × 2 | 📚 rag_search × 1`
- 点徽章展开工具调用明细（query + 前 3 条命中）
- 给辩论过程增加 evidence density 可视——前端汇总 `总工具调用次数` / `证据引用总数` 显示在气泡顶部

后端 `sse_emit` 对应加 2 个点位：`run_agent_with_tools` 内部每次 tool_call / tool_result 各发一条。

---

## 10. 实验设计（论文实验章底稿）

### 10.1 数据集

自建测试集，40–60 例（量力而行）：
- 每例：症状描述 + profile + **金标诊断**（可用公开 case report 或 MIMIC-IV 匿名病例）
- 按科室分层（消化 / 呼吸 / 神经 / 心血管各 10 例）

### 10.2 对照组

| 系统 | 说明 |
|---|---|
| **A. Single-LLM** | 直接让 deepseek-chat 给 top-3 诊断，无 MADDx | 最弱 baseline |
| **B. MADDx-static** (D7 现状) | 预取一次证据，辩论不动态查 | 中间 baseline |
| **C. MADDx-dynamic** (D8 目标) | 完整工具增强 | 本文方法 |
| （可选）D. MADDx-dynamic-critic-only | 只给 Critic 工具权限 | 消融：Critic 工具是否是主贡献者 |

### 10.3 指标

| 指标 | 定义 |
|---|---|
| Top-1 Accuracy | 主诊断 == 金标 |
| Top-3 Accuracy | 金标在鉴别诊断列表里 |
| Evidence Citation Density | 平均每个最终候选关联的 evidence_refs 数量 |
| Avg. Rounds | 平均辩论轮数 |
| Avg. Tool Calls | 平均工具调用次数（仅 C） |
| Termination Distribution | 4 条 Rule 各自触发占比 |

### 10.4 论文可画的图

1. **主实验表**：A / B / C 的 Top-1 / Top-3 / Citation Density
2. **收敛模式分布柱状图**：B vs C 的 TerminationReason 分布差异
3. **案例研究图**：挑一个 C 做对 B 做错的病例，画 Blackboard DAG（已经现成）
4. **工具调用热力图**（加分项）：哪些症状最常触发 Critic 发起 KG 查询

---

## 11. 分阶段实施路线

| 阶段 | 工时 | 可交付 | 验收 |
|---|---|---|---|
| **D8-1** 工具层 + Schema 扩展 | 0.5 天 | `tools.py` + schema 升级 + 单元测试 | pytest 通过，能跑 `KGTool.call()` |
| **D8-2** agent_loop 控制器 | 1 天 | `agent_loop.py` + Proposer 先接入 | 跑通一个真实病例，Proposer 能自主查 KG |
| **D8-3** Critic 工具化 | 0.5 天 | `critic.py` 改写 + prompt | objection.evidence_refs 全部非空 |
| **D8-4** Defender 工具化 + workflow 改造 | 0.5 天 | `workflow.py` + `defender`（现在 Defender 是复用 Proposer 的，要拆出独立文件） | 全链路跑通 |
| **D8-5** Rule 4 + 前端工具事件可视化 | 0.5 天 | Live card 显示 tool_call 徽章 | 视觉 OK |
| **D8-6** 实验脚本 + 40 例数据集 | 1–2 天 | `experiments/run_ablation.py` + 表格 | 产出论文主表 |
| **合计** | **~4–5 天** | | |

---

## 12. 风险 & 回退策略

| 风险 | 概率 | 缓解 |
|---|---|---|
| ReAct 循环发散（agent 陷入反复查同一条） | 中 | 硬限 `MAX_TOOL_CALLS_PER_TURN=3` + session 级缓存 + 结果去重 |
| Neo4j / DashVector 压力 | 低 | 工具层缓存 + 单次病例预计 ≤15 次调用 |
| JSON schema 不稳定（LLM 忘了 action 字段） | 中 | 继续用 `response_format=json_object` + 每步消息里塞一遍期望 schema + 失败重试带 error message（顺便把 P1 清单 #6 一起修了） |
| 延迟显著增加 | 高 | 预计从 ~70s → ~110s。在思考气泡里靠 tool_call 事件实时反馈，用户感知不会比 D7 差，反而更有"智能体在干活"的说服力 |
| 改不完 | 中 | **最小可答辩版本** = D8-1 + D8-3（只改 Critic） + 跑 20 例对比。其他阶段可选 |

---

## 13. 对论文第 4 章（系统设计）的影响

D8 完成后，论文可以新增两节：

- **4.4.X** 工具增强的辩论协议（Tool-Augmented Debate Protocol）
  - 形式化：`debate_step: (State, ToolSet) → (State', {finish | tool_call})`
  - Blackboard 作为共享证据底座的论证
- **4.4.Y** 基于证据空间耗尽的收敛判据（Evidence-Space Exhaustion Termination）
  - 相比纯"输出稳定"的收敛，NO_NEW_EVIDENCE 的 epistemic 意义
  - 配数据：B vs C 的收敛分布对照

这两节是答辩老师问"你的多智能体比别人多什么"的直接答案。

---

## 14. 我需要你确认的 3 个决策点

1. **Defender 要不要独立出文件？** 目前 `workflow.py` 第 120 行 Defender 是复用 `run_proposer` 的。改造后两者行为差别变大（Defender 要响应具体 objection），建议拆成 `defender.py`——你同意吗？

2. **实验数据集规模**：40 例 / 60 例 / 100 例？毕设答辩 40 例够，冲优秀推荐 60 例。你按哪个规模备数据？

3. **工具集是否纳入 Web 搜索（Tavily）？** 现有 Tavily 在 `general_agent` 里。把它也暴露给 Critic 会让辩论能处理"太新没进 KG 的病"，但会加 latency 和费用。建议 **D8 先不做，留作 D9 扩展点**，你认可吗？

---

确认以上 3 点后，我从 **D8-1（Schema + tools.py）** 开始动工。
