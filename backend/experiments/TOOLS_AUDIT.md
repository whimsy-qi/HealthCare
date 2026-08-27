# 子智能体工具现状审计与优化方案

**日期**: 2026-05-04

---

## 1. 工具全景

| # | 工具 | 所属 Agent | 外部服务 | 超时 | 用途 |
|---|------|-----------|---------|:---:|------|
| 1 | `kg_query` | MADDx (P/C/D), Rumor(A/S) | Neo4j | 8s | KG 5 种 Cypher 模式查询 |
| 2 | `rag_search` | MADDx, Rumor(A/S) | DashVector + DashScope | 8s | 本地指南向量检索 |
| 3 | `web_search` | MADDx, Rumor(A/S) | Tavily + DashScope | 12s | 权威医学站群搜索+重排序 |
| 4 | `search_local_guidelines` | GeneralAgent | DashVector + DeepSeek | ReAct 内 | 同 #2，但走 General 的 ReAct 循环 |
| 5 | `search_medical_graph` | GeneralAgent | Neo4j + DashScope | ReAct 内 | KGPruner: 语义锚定+0-2跳游走 |
| 6 | `search_public_internet` | GeneralAgent | Tavily + DashScope | ReAct 内 | 同 #3 |
| 7 | `search_kg_contraindications` | MedicationAgent | Neo4j | — | 精确 CONTRAINDICATED_FOR 查询 |
| 8 | `search_drug_manual` | MedicationAgent | DashVector + Tavily | — | 药品说明书检索 |
| 9 | `analyze_image_with_vision` | General, Report, Pre-flight | VL 模型 (qwen-vl-plus) | — | 医学图像/药盒 OCR |
| 10 | `check_answer` | HallucinationAgent | LLM (DeepSeek) | 12s | Claim 级幻觉检测 |
| 11 | `analyze_and_clarify_symptom` | SymptomAgent | LLM (DeepSeek) | — | 症状槽位填充（纯 LLM） |
| 12 | `KnowledgeGraphPruner` | Symptom, General | Neo4j + DashScope | — | Vector-GraphRAG 语义检索+游走 |

### 外部依赖

| 服务 | 用途 | 使用次数/次请求 |
|------|------|:---:|
| Neo4j | 知识图谱查询 | 1-3 |
| DashVector | 向量检索 | 1-3 |
| DashScope | 嵌入/重排序/VL | 2-5 |
| Tavily | Web 搜索 | 0-2 |
| DeepSeek | LLM 推理 | 3-15 |

---

## 2. 现状问题

### 2.1 工具重复实现

`ToolRegistry.kg_query` (MADDx) 和 `search_medical_graph` (GeneralAgent) 都查 Neo4j，但走不同代码路径。`ToolRegistry` 有缓存+追踪，GeneralAgent 的没有。

### 2.2 无文献检索能力

所有"医学证据"来自三个来源：
- 本地 PDF 指南 (DashVector)
- Neo4j KG (结构化关系)
- Web 搜索 (Tavily)

**没有 PubMed/学术文献搜索**。当用户问"二甲双胍抗衰老的最新研究"时，Tavily 返回的是科普文章，不是原始文献。

### 2.3 无药物相互作用数据库

`search_kg_contraindications` 只查 KG 中的 `CONTRAINDICATED_FOR` 边。没有 DrugBank 级别的 DDI（drug-drug interaction）数据。两个药物之间的相互作用完全依赖 LLM 自身知识。

### 2.4 SymptomAgent 纯 LLM 推理

症状澄清（11）完全由 LLM 驱动，不查 KG、不查指南。LLM 可能在罕见病症状上产生幻觉。

### 2.5 工具质量参差

| 工具 | 缓存 | 追踪 | 降级 | 评估者 |
|------|:---:|:---:|:---:|------|
| ToolRegistry | ✓ | ✓ | ✓ | MADDx/Rumor |
| GeneralAgent ReAct | ✗ | ✗ | ✗ | GeneralAgent |
| MedicationAgent | ✗ | ✗ | ✓ | MedicationAgent |

---

## 3. 优化建议

### 3.1 短期（现在做）

| 优先级 | 改动 | 效果 |
|--------|------|------|
| P0 | GeneralAgent 接入 ToolRegistry 替代自己的三个工具 | 统一缓存/追踪/降级，删除重复代码 |
| P1 | SymptomAgent 的槽位填充加入 KG 验证 | LLM 追问时参考 KG 已有症状数据 |

### 3.2 中期（论文亮点）

| 优先级 | 改动 | 效果 |
|--------|------|------|
| P1 | **加 PubMed 文献检索工具** | 辟谣/NOVEL_TREND 类查询直接检索原始文献 |
| P2 | 工具调用的前端实时展示 | 右侧面板显示当前 Agent 正在调用哪个工具 |

### 3.3 长期

| 工具 | 方案 | 依赖 |
|------|------|------|
| DrugBank DDI | OpenFDA API (免费) | HTTP |
| 医学计算器 | 纯 Python 函数，不需要外部 API | 无 |
| 症状检查器 | 基于 KG HAS_SYMPTOM 的结构化决策树 | Neo4j |

---

## 4. PubMed 工具详细设计

### 为什么需要

当前辟谣/Novel Trend 类查询的"最新研究证据"来自 Tavily Web 搜索，返回科普文章。用户问"二甲双胍抗衰老有研究吗"，系统应能检索到真实论文。

### 实现方案

使用 NCBI Entrez API（免费，无需 API Key）：

```
输入: query string
输出: 最多 5 篇论文的 title + abstract + PMID

流程:
  query → Entrez.esearch(db="pubmed", retmax=5) → PMID 列表
       → Entrez.efetch(id=pmids) → title + abstract
       → LLM 总结 → 返回给 Agent
```

### 集成位置

- 加入 MADDx `ToolRegistry` 作为第 4 个工具
- Rumor Advocate/Skeptic 可以在辩论中检索 PubMed
- GeneralAgent 的 ReAct 循环中可用

### 论文价值

> "本研究在通用 Web 搜索和本地指南检索之外，引入了 PubMed 文献检索工具，使 Agent 能够在辩论中引用原始学术文献。这一工具的加入使辟谣验证从'查科普文章'升级为'查原始研究'，显著提升了系统在高风险/新观点类辟谣场景中的证据质量。"

---

## 5. 结论

**当前状态**: 12 个工具，覆盖 KG/RAG/Web/Vision/LLM 五个维度。功能完整但因历史原因存在重复实现。

**核心缺口**: 学术文献检索、药物相互作用数据库、医学计算器。

**优先行动**: GeneralAgent 接入 ToolRegistry（去重）+ 加 PubMed 工具（论文亮点）。
