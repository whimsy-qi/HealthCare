# 系统测试

## 1. 测试方案

### 1.1 测试策略

采用三层递进测试：**单元测试**（核心模块）→ **集成测试**（Agent 协作链路）→ **系统测试**（大规模评测实验）。

| 层级 | 范围 | 用例数 | 通过标准 |
|------|------|:---:|---------|
| 单元测试 | 意图分类、协作模式选择、答案提取、KG 检索 | 12 | 全部通过 |
| 集成测试 | Agent 路由、多意图调度、Hallucination 检测 | 8 | 全部通过 |
| 系统测试 | Phase 2 诊断实验 (1200题)、Phase 3 辟谣实验 (400条) | 2 | 结论可复现 |

### 1.2 测试环境

| 项目 | 配置 |
|------|------|
| 后端框架 | FastAPI + LangGraph |
| LLM | DeepSeek-V4-Pro / Qwen-Max / GLM-5.1 |
| 图数据库 | Neo4j 5.x (64,548 节点 / 404,615 关系) |
| 向量数据库 | DashVector |
| 前端 | React + Ant Design |
| 操作系统 | Windows 11 |

---

## 2. 单元测试

### 2.1 意图识别模块

| 序号 | 功能 | 输入 | 预期结果 | 实际结果 |
|:---:|------|------|---------|---------|
| UT-01 | 单一症状意图 | "我头疼" | `primary=SYMPTOM_ANALYSIS, sub=DIAGNOSIS, act=SEEK_HELP, attr=DIAGNOSE` | ✓ 通过 |
| UT-02 | 辟谣意图 | "微波炉加热会致癌吗" | `primary=RUMOR_VERIFICATION, sub=FACT_CHECK, act=DEBUNK` | ✓ 通过 |
| UT-03 | 用药意图 | "高血压能吃布洛芬吗" | `primary=MEDICATION_REVIEW, sub=CONTRAINDICATION, act=CONFIRM, attr=CAUTION` | ✓ 通过 |
| UT-04 | 常识意图 | "维生素D有什么作用" | `primary=GENERAL_CONSULTATION, sub=GENERAL, act=ASK, attr=BASIC` | ✓ 通过 |
| UT-05 | 闲聊意图 | "你好" | `primary=CHITCHAT_OR_REJECT, sub=GREETING` | ✓ 通过 |
| UT-06 | 多意图解耦 | "我头疼，布洛芬多少钱" | `intents.length >= 2, 包含 SYMPTOM_ANALYSIS 和 MEDICATION_REVIEW` | ✓ 通过 |

### 2.2 协作模式选择模块

| 序号 | 功能 | 输入 | 预期结果 | 实际结果 |
|:---:|------|------|---------|---------|
| UT-07 | 诊断→Debate | `act=SEEK_HELP, attr=DIAGNOSE` | `mode=debate` | ✓ 通过 |
| UT-08 | 辟谣→Debate | `act=DEBUNK` | `mode=debate` | ✓ 通过 |
| UT-09 | 用药确认→Single+KG | `act=CONFIRM, attr=CAUTION` | `mode=single_kg` | ✓ 通过 |
| UT-10 | 常识→Single | `act=ASK, attr=BASIC` | `mode=single` | ✓ 通过 |
| UT-11 | 不确定降级 | `uncertainty=0.45` | `mode=vote (fallback)` | ✓ 通过 |

### 2.3 KG 检索模块

| 序号 | 功能 | 输入 | 预期结果 | 实际结果 |
|:---:|------|------|---------|---------|
| UT-12 | 症状→疾病 | `kg_query("symptom_diseases", "头痛")` | 返回 ≥1 条疾病 | ✓ 通过 |
| UT-13 | 疾病→症状 | `kg_query("disease_symptoms", "偏头痛")` | 返回 HAS_SYMPTOM 关系 | ✓ 通过 |
| UT-14 | 药物禁忌 | `search_kg_contraindications(["阿司匹林"])` | 返回 CONTRAINDICATED_FOR 关系 | ✓ 通过 |
| UT-15 | Stub 过滤 | 查询 "尚不明确" | 不返回垃圾节点 | ✓ 通过 |
| UT-16 | 向量检索 | `rag_search("糖尿病 饮食")` | 返回 ≥1 条指南片段 | ✓ 通过 |
| UT-17 | PubMed 检索 | `pubmed_search("metformin aging")` | 返回论文标题+摘要 | ✓ 通过 |

---

## 3. 集成测试

| 序号 | 功能 | 输入 | 预期行为 | 实际结果 |
|:---:|------|------|---------|---------|
| IT-01 | Single 路由 | "维生素D有什么作用" | Triage→collab_mode=single→GeneralAgent→回答 | ✓ 通过 |
| IT-02 | Vote 路由 | "高血压怎么治疗" | Triage→collab_mode=vote→VoteRunner→三模型投票 | ✓ 通过 |
| IT-03 | 诊断 Debate | "我头疼两周了" | Triage→Debate→SymptomAgent→MADDx→诊断报告 | ✓ 通过 |
| IT-04 | 辟谣对抗辩论 | "微波炉加热致癌吗" | Triage→Debate→RumorAgent→Adv↔Skp→Jdg→判定 | ✓ 通过 |
| IT-05 | 用药 Single+KG | "高血压能吃布洛芬吗" | Triage→Single+KG→MedAgent→KG禁忌查询→回答 | ✓ 通过 |
| IT-06 | 多意图并发 | "我头疼，布洛芬多少钱" | Triage→intents=2→并发Symptom+Med→Synthesizer | ✓ 通过 |
| IT-07 | Hallucination Guard | 任意医疗回答 | 出口触发→Claim分解→证据对齐→action (不再输出ABSTAIN) | ✓ 通过 |
| IT-08 | 辟谣快速通道 | "吃木瓜能丰胸吗" | 低风险→RumorFastPath→3秒内响应→自然语言回答 | ✓ 通过 |
| IT-09 | Insight 记忆注入 | 连续两次相似 query | 第二次回答 prompt 中含历史案例参考 | ✓ 通过 |

---

## 4. 系统测试

### 4.1 Phase 2: 诊断协作模式评测

**目的**: 在标准医学评测基准上，比较单模型、投票集成、辩论三种协作模式的诊断准确率。

**数据**: CMB-Exam 1200 题（规培结业 400 + 执业医师 400 + 临床医学 400）

**结果**:

| 模式 | 准确率 | 正确/总数 | vs 最佳单模型 |
|------|:---:|:---:|:---:|
| Single-DeepSeek (基线) | 82.7% | 992/1200 | — |
| Single-Qwen | 81.9% | 983/1200 | -0.8pp |
| Single-GLM-5.1 | 81.7% | 980/1200 | -1.0pp |
| **Vote-3** | **84.8%** | **1017/1200** | **+2.1pp** |
| Debate (DS+Qwen, 1轮) | 81.8% | 982/1200 | -0.8pp |
| Debate (DS+Qwen, 2轮) | 81.8% | 982/1200 | -0.8pp |
| Debate (DS+GLM-5.1, 1轮) | 83.9% | 1007/1200 | +1.2pp |
| Debate (DS+GLM-5.1, 2轮) | 83.5% | 1002/1200 | +0.8pp |

**结论**: Vote-3 在诊断选择题上显著优于单模型和辩论。2 轮辩论无增量。GLM-5.1 作为 Critic 优于 Qwen。

### 4.2 Phase 3: 辟谣协作模式评测

**目的**: 在自建辟谣评测集上，分析三种协作模式的决策过程行为差异。

**数据**: 自建 400 条中文医疗辟谣条目（属实/谣言/误导/尚无定论 各 100 条）

**结果**:

| 指标 | Single | Vote-3 | Debate |
|------|:---:|:---:|:---:|
| 参考准确率 (弱GT) | 56.0% | 56.8% | 53.0% |
| "尚无定论"输出率 | 10.5% | 12.5% | **31.8%** |
| 有效决策率 | 89.5% | 87.5% | 68.2% |
| 两两分歧率 | — | 20-31% | **51.7%** |
| 耗时 | 384s | 548s | 3627s |

**结论**: 对抗式辩论未带来准确率提升，但显著改变了决策行为——分歧率提升 2.5 倍，保守倾向提升 3 倍。Debate 不应作为默认策略，而应定位为高争议样本的风险保守机制。

---

## 5. 测试结论

### 5.1 通过项

- 意图识别在三轴模型下准确覆盖 6 类 Domain、5 类 Act、8 类 Attr，多意图解耦功能正常
- 协作模式选择在 17 条映射规则下正确路由，不确定降级机制有效
- KG 检索覆盖 15 种关系类型，向量检索延迟稳定在 8s 以内，PubMed 工具正常返回学术文献
- 7 个 Agent 全部通过集成测试，多意图并发调度和 Synthesizer 合成功能正常
- Hallucination 检测正常运行，ABSTAIN 不再导致弃答（修改后）
- Insight 记忆的反思生成、存储、检索、注入全链路在 3 个 Agent 上验证通过
- Phase 2 实验证实 Vote-3 在诊断场景下显著优于单模型（+2.1pp）
- Phase 3 实验以过程指标量化了 Debate 在辟谣场景下的决策行为特征

### 5.2 限制与后续工作

- 辟谣评测集 GT 由 AI 辅助构建，未经多标注者独立审核，准确率为内部参考值
- SymptomAgent 和 ReportAgent 的 Vote-3 分派尚未实现运行时调用（collab_mode 已注入，Agent 端待接入）
- Judge 偏向率无法分解为模型-角色独立效应（需角色互换实验）
- LLM 自报置信度的校准未经验证（Tiebreak 置信度加权依赖校准后的置信度）

### 5.3 综合评价

系统核心功能可用，7 个 Agent 均通过集成测试。意图驱动的协作模式选择在 general_node 完成端到端验证。Phase 2/3 实验为诊断和辟谣场景的协作模式选择提供了量化依据，证实了"任务-策略匹配"假说。系统满足本科毕业设计的完整性和创新性要求。

---
*文档版本: 2026-05-04*
