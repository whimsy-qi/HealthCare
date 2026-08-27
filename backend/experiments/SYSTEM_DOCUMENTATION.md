# 医疗多智能体协作问答系统 — 技术文档

---

## 1. 智能体总览

### 1.1 智能体清单

| # | 智能体 | 触发意图 | 协作模式 | 核心逻辑 |
|---|--------|---------|---------|---------|
| 1 | **TriageAgent** | 所有 query 入口 | Single (分类器) | LLM 意图分类，输出 Domain×Act×Attr + intents 列表 |
| 2 | **SymptomAgent** | SYMPTOM_ANALYSIS | Debate (MADDx 收敛式) | 多轮槽位填充 → 图谱+向量双路检索 → MADDx 辩论诊断 |
| 3 | **RumorAgent** | RUMOR_VERIFICATION | Debate (CTAEW 对抗式) | Claim 分类→风险评估→Advocate↔Skeptic→Judge |
| 4 | **MedicationAgent** | MEDICATION_REVIEW | Single+KG 流水线 | 提取药名→KG禁忌+说明书双路检索→药师审查 |
| 5 | **GeneralAgent** | GENERAL_CONSULTATION | Single / Vote-3 | ReAct 工具调用循环（本地指南+KG+Web） |
| 6 | **ReportAgent** | REPORT_INTERPRETATION | Single 流水线 | 视觉OCR→指南检索→报告解读 |
| 7 | **HallucinationAgent** | 所有出口 | Single (后处理) | Claim 级证据对齐，输出 hallucination_score + action |
| 8 | **MemoryAgent** | 所有 query | Single (副作用) | 提取健康档案更新→DB 持久化 |
| 9 | **Moderator** | Debate 模式 | Single (裁决者) | 综合辩论历史，输出最终诊断/判定 |
| 10 | **Synthesizer** | 多意图并发 | Single (合成者) | 合并多路 Agent 输出为连贯答复 |

### 1.2 核心提示词示例

**TriageAgent 意图分类**:
```
你是三甲医院高级AI智能分诊大脑。将用户需求映射到预定义意图本体字典。
Domain (6类): SYMPTOM_ANALYSIS / MEDICATION_REVIEW / RUMOR_VERIFICATION /
              REPORT_INTERPRETATION / GENERAL_CONSULTATION / CHITCHAT_OR_REJECT
Act (5类): ASK / CONFIRM / SEEK_HELP / DEBUNK / ANALYZE
Attr (8类): CAUSE / SYMPTOM / BASIC / CHECKUP / VISIT / PREVENT / DIAGNOSE / CAUTION
```

**SymptomAgent 诊断 Prompt**:
```
你是全科主任医师。基于患者症状、图谱推理路径、指南，生成综合会诊报告。
输出 Markdown：证据链汇总 → 临床分析 → 用药建议 → 就诊指导。
严禁提及"知识图谱/向量库/系统设定"。
```

**RumorAgent 对抗 Prompt (Advocate)**:
```
你是医疗事实核查中的"辩护方"。尽最大努力为说法辩护——
强调支持证据、指出反驳证据的局限性。即使说法存疑也要充分辩护。
```

**RumorAgent 对抗 Prompt (Skeptic)**:
```
你是医疗事实核查中的"质疑方"。尽最大努力质疑说法——
强调反驳证据、指出支持证据的缺陷。即使说法有道理也要充分质疑。
```

**GeneralAgent ReAct Prompt**:
```
你是全科数字大夫。你有三个工具：本地指南库、医学图谱、公网搜索引擎。
工作流: 思考→行动→观察→最终答复。严禁在最终回复中输出内部思考过程。
```

---

## 2. 意图设计

### 2.1 三轴正交意图模型

```
Domain (6类) — 决定路由到哪个 Agent
  ×
Act (5类)   — 决定协作模式 + prompt 偏重
  ×
Attr (8类)  — 决定内容侧重

240 种理论组合 → 约 20 种实际映射规则（大量被"默认"和"降级"规则覆盖）
```

### 2.2 意图→协作模式映射表（核心）

| Domain | Sub-intent | Act | Attr | 模式 | 模型 | 实验依据 |
|--------|-----------|-----|------|------|------|---------|
| SYMPTOM | DIAGNOSIS | SEEK_HELP | DIAGNOSE | Debate | DS(P)+GLM(C)+DS(M) | Phase 2 诊断实验 |
| RUMOR | FACT_CHECK | DEBUNK | any | Debate | DS(A)+QW(S)+GLM(J) | Phase 3 辟谣实验 |
| MEDICATION | CONTRA/ DOSAGE/ SIDE/ INTER | CONFIRM | CAUTION | Single+KG | DS + Neo4j | KG 29K禁忌关系 |
| MEDICATION | GENERAL_MED | ASK | CAUTION | Vote | DS+QW+GLM | Phase 2 投票增益 |
| REPORT | LAB_RESULT | ANALYZE | DIAGNOSE | Vote | DS+QW+GLM | 多视角降低偏差 |
| GENERAL | TREATMENT | SEEK_HELP | CAUTION | Vote | DS+QW+GLM | 治疗高风险 |
| GENERAL | GENERAL | ASK | BASIC/ PREVENT/ CAUSE | Single | DS | 科普低风险 |
| CHITCHAT | GREETING | — | — | Single | DS | 非医疗 |

### 2.3 意图识别的工程实现

- TriageAgent 使用 `FAST_MODEL`，`temperature=0.0`，`response_format={"type":"json_object"}`
- LLM 输出 JSON → 标准化（normalize_act/normalize_attr）→ 构建 intents 列表
- 向后兼容：旧格式自动构造单条目 intents
- 不确定性：`uncertainty = min(0.5, len(intents) * 0.2)`，`>0.3` 时强制降级为 Vote-3

---

## 3. 多智能体协作元素

### 3.1 协作模式

| 模式 | 描述 | 参与模型数 | 延迟 |
|------|------|:---:|------|
| Single | 单模型直接响应 | 1 | 1× |
| Single+KG | 单模型 + Neo4j 查询 | 1 | 1.5× |
| Vote-3 | 三模型独立盲答→多数投票 | 3 (并行) | 3× |
| Debate (收敛式) | Proposer→Critic→Defender→Moderator | 2-3 | 3-5× |
| Debate (对抗式) | Advocate↔Skeptic→Judge | 3 | 3-5× |

### 3.2 通信协议

- **Blackboard** (`core/blackboard.py`): append-only 版本化 DAG。每条 entry 含 `{v, key, value, agent, ts, parents}`，支持 snapshot isolation 和 `based_on_version` 追溯
- **AgentState** (LangGraph TypedDict): 共享内存总线，所有 Agent 节点在同一字典上读写
- **SSE Emitter**: ContextVar 驱动，将 Agent 步骤实时推送到前端

### 3.3 协作中的涌现行为

- **辩论修正/误伤博弈** (Phase 2): 辩论修正 42-91 例但误伤 52-76 例，修正vs误伤的博弈是运行时产生的
- **对抗分歧** (Phase 3): Advocate-Skeptic 分歧率 51.7%
- **保守效应** (Phase 3): Debate 的"尚无定论"率是 Single 的 3 倍
- **Tiebreak** (Phase 3): 仅 0.8% 触发——多数投票在 99.2% 的 case 中有效运作

### 3.4 多意图并发调度

当 Triage 检测到 >1 独立子意图时，系统并发调用对应 Agent，然后由 Synthesizer 合并输出。

---

## 4. 完整架构图

```
                         用户 Query
                             │
                             ▼
                    ┌─────────────────┐
                    │   api_server    │  SSE Streaming
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │   TriageAgent   │  LLM 意图分类
                    │ Domain×Act×Attr │  → intents[]
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
         len(intents)=1  len(intents)>1  EMERGENCY
              │              │              │
              ▼              ▼              ▼
    ┌─────────────┐  ┌───────────┐  ┌──────────┐
    │collab_mode  │  │ Parallel  │  │Emergency │
    │  = vote/    │  │ Dispatch  │  │  Node    │
    │  debate/    │  │  + Synth  │  └──────────┘
    │  single     │  └───────────┘
    └──────┬──────┘
           │
    ┌──────┴──────────────────────────────────┐
    │         Dynamic Agent Router             │
    │  SYMPTOM→symptom  RUMOR→rumor_subgraph   │
    │  MEDICATION→med    GENERAL→general       │
    │  REPORT→report     CHITCHAT→chitchat     │
    └──────┬──────────────────────────────────┘
           │
    ┌──────┴──────┬──────────┬──────────┬──────────┐
    ▼             ▼          ▼          ▼          ▼
┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐
│Symptom │  │ Rumor  │  │  Med   │  │General │  │Report  │
│ Agent  │  │ Agent  │  │ Agent  │  │ Agent  │  │ Agent  │
│        │  │        │  │        │  │        │  │        │
│MADDx   │  │CTAEW   │  │3-Node  │  │ReAct   │  │OCR+    │
│Debate  │  │Debate  │  │Pipeline│  │Loop    │  │Retrieval│
└───┬────┘  └───┬────┘  └───┬────┘  └───┬────┘  └───┬────┘
    │           │           │           │           │
    └───────────┴───────────┴───────────┴───────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │  HallucinationAgent │  出口幻觉检测
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │   MemoryAgent       │  健康档案更新 (副作用)
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │   Blackboard DAG    │  前端溯源可视化
              │   + EvidenceChain   │
              └─────────────────────┘
```

---

## 5. 可改进点分析

### 5.1 见解知识库 (Insight Memory)

**已存在** (`core/insight_memory.py`, 805 行)。功能：
- 双极性存储（SUCCESS 正例 + FAILURE 反例）
- 双桶隐私分层（用户私有桶 + 全局共享桶）
- dashscope text_embedding_v3 向量化
- Top-K cosine 检索 + 质量分排序
- Hallucination Harvest Hook: 自动从 hallucination_agent 报告中收割 insight

**未充分利用**：当前 insight 仅在 GeneralAgent 的 system prompt 中以 few-shot 形式注入。理想用法：
- Triage 时检索相似历史 case 的意图分类结果（提高分类准确率）
- 辩论时 Critic/Advocate 检索相关反例作为反驳素材
- Vote-3 分歧时检索相似 case 的正确答案分布

### 5.2 自我优化能力

**当前没有在线学习机制。** COLLAB_MODE_MAP 是静态映射表，Tiebreak 是固定回退规则。理想的反馈闭环：
- 记录用户对 AI 回答的采纳/拒绝行为
- 定期重新计算每种 (act, attr) 组合的最优协作模式
- 动态更新映射表权重

### 5.3 其他改进点

| 改进 | 现状 | 目标 |
|------|------|------|
| SymptomAgent Vote 分派 | collab_mode 仅日志记录 | 实际调用 VoteRunner |
| ReportAgent Vote 分派 | collab_mode 仅日志记录 | 实际调用 VoteRunner |
| Judge 角色互换 | 角色=模型绑定 | 补充 Role-Reversal 对照 |
| Tiebreak 置信度加权 | 固定回退到 DeepSeek | 多维度综合评分 |
| GT 独立验证 | 100% AI 辅助 | 50 条人工审核 |

---

## 6. 可调用工具

### 6.1 ToolRegistry (MADDx Tools)

| 工具 | 实现 | 数据源 | 超时 |
|------|------|--------|:---:|
| `kg_query` | Cypher 查询 Neo4j | 64K 节点 KG (疾病/症状/药物/食物/检查/厂商/疗法/科室) | 8s |
| `rag_search` | DashScope Embedding → DashVector | 本地医学指南库 (PDF 向量化) | 8s |
| `web_search` | Tavily API | 权威医学站群 (NIH/CDC/NHS/丁香园) | 8s |

### 6.2 KGPruner (GraphRAG)

独立于 ToolRegistry，为 SymptomAgent 和 GeneralAgent 提供 Vector-GraphRAG：
- 阶段一：DashScope 1024 维向量语义锚定 (Semantic Anchoring)
- 阶段二：Neo4j 0-2 跳扩散 + 指数衰减游走 (Exponential Decay Traversal)
- 阶段三：置信度排序 + XAI 可解释路径生成

---

## 7. 知识库在多智能体协作中的角色

### 7.1 向量知识库 (DashVector)

**存储**: 本地医学指南 PDF → PyMuPDF 文本提取 → DashScope embedding → DashVector
**检索**: MultiModalEmbedding → vector query (topk, source_filter)
**用途**: 为 Agent 提供权威指南作为证据支撑（RAG）
**优化空间**: 当前检索 query 由 Agent 自行构造，质量参差。可引入 Query Rewriting 层

### 7.2 图数据库 (Neo4j KG)

**规模**: 64,548 节点 / 404,615 关系 / 8 类节点 / 15 种关系
**检索**: 向量索引 (symptom/disease/drug/department) + Cypher 0-2 跳路径
**用途**:
- SymptomAgent: KGPruner 做症状→疾病推理
- MedicationAgent: KG 禁忌查询 (29,794 CONTRAINDICATED_FOR) + 治疗查询 (72,263 TREATS)
- HallucinationAgent: 实体接地——验证 LLM 输出的疾病名/药名是否在 KG 中存在
- 前端知识图谱展示 (GraphExplore)
**优化空间**: 向量索引与 Cypher 路径查询的联合优化 (hybrid search)；Cypher 查询模板化以防御注入

### 7.3 检索流程

```
Agent 发起查询
    │
    ├─ Symptom/General: KGPruner.execute_pruning(keywords)
    │     └─ embedding → db.index.vector.queryNodes() → 0-2 hop Cypher traversal
    │
    ├─ Medication: search_kg_contraindications(drugs)
    │     └─ MATCH (m:Drug)-[:CONTRAINDICATED_FOR]->(c) WHERE m.name CONTAINS $name
    │
    ├─ RAG: DashVector.query(text_embedding)
    │
    └─ Web: Tavily API search
```

---

## 8. 系统测试方案

### 8.1 测试策略

采用三层测试：单元测试（核心模块）→ 集成测试（Agent 协作链路）→ 端到端测试（真实 query 场景）

### 8.2 单元测试用例

| # | 功能 | 输入 | 预期结果 |
|---|------|------|---------|
| UT-01 | Triage 意图分类 | "我头疼" | `primary_intent=SYMPTOM_ANALYSIS, act=SEEK_HELP, attr=DIAGNOSE` |
| UT-02 | Triage 多意图 | "我头疼，布洛芬多少钱" | `intents.length >= 2` |
| UT-03 | collab_mode 选择 | act=DEBUNK, attr=CAUSE | `mode=debate` |
| UT-04 | collab_mode 选择 | act=ASK, attr=BASIC | `mode=single` |
| UT-05 | 不确定降级 | uncertainty=0.45 | `mode=vote (fallback)` |
| UT-06 | KG 检索 | keyword="偏头痛" | 返回 HAS_SYMPTOM 关系，≥1 条 |
| UT-07 | KG Stub 过滤 | 查询 Disease "尚不明确" | 不返回垃圾节点 |
| UT-08 | 答案提取 | "【最终答案】A" | 提取 "A" |
| UT-09 | 答案提取 Fallback | "答案是C" | 提取 "C" |
| UT-10 | Vote-3 Tiebreak | ["A","B","C"] | 返回 "A" (DeepSeek) 或触发 1:1:1 处理 |

### 8.3 集成测试用例

| # | 功能 | 输入 | 预期行为 |
|---|------|------|---------|
| IT-01 | Single 模式路由 | "维生素D有什么作用" | Triage→collab_mode=single→general_node→ReAct→回答 |
| IT-02 | Vote 模式路由 | "高血压怎么治疗" | Triage→collab_mode=vote→VoteRunner→三模型投票 |
| IT-03 | Debate 诊断 | "我头疼，两周一跳一跳的" | Triage→Debate→SymptomAgent→MADDx→诊断报告 |
| IT-04 | 辟谣对抗辩论 | "微波炉加热致癌吗" | Triage→Debate→RumorAgent→Adv↔Skp→Jdg→判定 |
| IT-05 | Single+KG 用药 | "高血压能吃布洛芬吗" | Triage→Single+KG→MedAgent→KG禁忌查询→回答 |
| IT-06 | 多意图并发 | "我头疼，布洛芬多少钱" | Triage→intents=2→并发Symptom+Med→Synthesizer |
| IT-07 | Hallucination Guard | 任意医疗回答 | 出口触发→Claim分解→证据对齐→action (PASS/WARN/ABSTAIN) |
| IT-08 | Blackboard 溯源 | 完整诊断流程 | 黑板 DAG 包含所有 Agent 步骤，前端可渲染 |

### 8.4 端到端测试用例

| # | 场景 | query | 验证点 |
|---|------|-------|--------|
| E2E-01 | 简单症状 | "我头疼" | SymptomAgent 启动→槽位追问→最终诊断报告 |
| E2E-02 | 用药安全 | "高血压能吃布洛芬吗" | MedAgent→KG查询→禁忌警告→风险评级 |
| E2E-03 | 辟谣 | "微波炉加热会致癌吗" | RumorAgent→Adv↔Skp→Jdg→verdict+依据 |
| E2E-04 | 报告解读 | [上传化验单图片] | ReportAgent→OCR→解读→建议 |
| E2E-05 | 常识科普 | "维生素D有什么作用" | GeneralAgent→Single模式→科普回答 |
| E2E-06 | 多意图 | "我头疼，布洛芬多少钱" | 并发调度→Synthesizer综合回答 |
| E2E-07 | 急诊 | "我误服了一整瓶降压药" | EMERGENCY→emergency_node→立即就医建议 |
| E2E-08 | 闲聊 | "你好" | CHITCHAT→chitchat_node→问候 |

### 8.5 Phase 2/3 实验作为系统测试

| 实验 | 测试目标 | 测试数据 | 关键指标 | 结论 |
|------|---------|---------|---------|------|
| Phase 2 诊断 | Vote vs Debate 准确性 | CMB-Exam 1200 题 | Top1 Acc, 修正率, 误伤率 | Vote-3 (84.8%) 最优 |
| Phase 2 辩论消融 | 辩论轮数/Critic 选择 | 同上 | Acc vs 轮数, Acc vs Critic | 2轮≤1轮; GLM>Qwen |
| Phase 3 辟谣 | 对抗式辩论过程行为 | 自建 400 条 | 分歧率, 有效决策率, 熵 | Debate 显著改变决策行为 |

### 8.6 测试结论

**通过项**: 意图识别准确、协作模式路由正确、KG 检索可用、Blackboard 溯源完整、Hallucination 检测功能正常、断点续跑机制有效。

**需改进项**: SymptomAgent 和 ReportAgent 的 Vote-3 分派尚未实际调用（collab_mode 仅日志记录）；多意图并发的 Synthesizer 输出质量未经系统评测；Tiebreak 置信度加权未实现。

**整体评价**: 系统核心功能可用，7 个 Agent 均通过集成测试。协作模式选择在 general_node 完成端到端验证。Phase 2/3 实验为诊断和辟谣场景的协作模式选择提供了量化依据。

---
*文档版本: 2026-05-03*
