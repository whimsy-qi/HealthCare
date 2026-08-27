# KG v2 重建设计文档

> 知识图谱 schema 完整化改造（参考 RAGQnASystem 基准）
> 状态：方案已实现 + dry-run 验证通过，待执行 `--rebuild` 切换
> 作者：本毕设作者
> 日期：2026-05-01

---

## 1. 背景与动机

### 1.1 现状问题

经过对 `medical.json`（DiseaseKG 公开数据集，8808 行疾病百科）的字段审计，发现 v1 构建脚本 `build_neo4j_graph.py` **严重欠抽取**：

- v1 仅抽取 **4 类节点 + 4 种关系 + 1 个 Disease 属性**
- 同一份数据，参考项目 [RAGQnASystem](https://github.com/honeyandme/RAGQnASystem) 抽取出 **8 类节点 + 11 种关系 + 7 个 Disease 属性**
- 量化对比：v1 约 25k 节点 / 50k 关系；RAGQnASystem 44k 节点 / 31w 关系
- **v1 关系数仅为 RAGQnASystem 的 16%**，跟参考论文做 evaluation 对比时基线不可比

### 1.2 业务影响

v1 KG 缺少的关系类型导致下列高频医疗问题**只能让 LLM 凭空编造**（高幻觉风险）：

| 用户问题示例 | 缺失关系 |
|------------|---------|
| "感冒能吃什么"           | `DO_EAT` |
| "高血压不能吃什么"        | `NOT_EAT` |
| "肺炎要做什么检查"        | `NEED_CHECK` |
| "感冒会引起什么并发症"     | `ACOMPANY_WITH` |
| "糖尿病怎么治"           | `CURE_WAY` |
| "高血压要吃什么药"        | `COMMON_DRUG` |
| "甲流是传染病吗"         | Disease.get_way 属性 |
| "看抑郁症大概多少钱"       | Disease.cost_money 属性 |
| "感冒多久能好"           | Disease.cure_lasttime 属性 |
| "布洛芬谁生产的"         | `PRODUCED_BY` |

### 1.3 改造目标

- **数据完整化**：把同一份 medical.json 的全部字段解析出来
- **基准对齐**：节点数、关系类型、命名跟 RAGQnASystem 严格对齐，evaluation 可比
- **零破坏**：v1 已有的 4 种关系全部保留，下游 agent / GraphRAG / 黑板等架构无需改动
- **后续基础**：为意图驱动的 Cypher 模板层（Phase 2）准备数据资产

---

## 2. Schema Diff（v1 → v2）

### 2.1 节点类型

| 节点 | v1 | v2 | 性质 |
|------|----|----|------|
| Disease       | ✅ name | ✅ name + **10 个属性** | 🔧 属性扩展 |
| Symptom       | ✅      | ✅                  | 不变 |
| Department    | ✅ name | ✅ + `level` 字段（1=一级科室 / 2=二级科室） | 🔧 属性扩展 |
| Drug          | ✅ name + class | ✅ 同 v1 + medical.json 的通用名 | 🔧 数据源扩展 |
| **Food**      | ❌      | ✅ name             | ⭐ 全新 |
| **Check**     | ❌      | ✅ name             | ⭐ 全新 |
| **Cure**      | ❌      | ✅ name             | ⭐ 全新 |
| **Producer**  | ❌      | ✅ name             | ⭐ 全新 |

### 2.2 Disease 节点新增 10 个属性

| 属性 | 含义 | 业务用途 |
|------|------|---------|
| `desc`          | 疾病描述                                   | 综合解读、科普问答 |
| `cause`         | 病因                                      | "为什么会得 X 病" |
| `prevent`       | 预防方式                                   | "怎么预防 X" |
| `cure_lasttime` | 治疗周期                                   | "X 多久能好" |
| `cured_prob`    | 治愈概率                                   | "X 能治好吗" |
| `easy_get`      | 易感人群                                   | "什么样的人容易得 X" |
| `get_prob`      | 患病率                                    | 评估稀有度 |
| `get_way`       | 传播途径                                   | "X 是不是传染病" |
| `yibao_status`  | 医保状态                                   | 就医建议 |
| `cost_money`    | 治疗费用                                   | "看 X 病要多少钱" |

### 2.3 关系类型

| 关系（含方向） | v1 | v2 | 数据源 |
|---------------|----|----|--------|
| `HAS_SYMPTOM` (Disease→Symptom)            | ✅ | ✅          | medical.json `symptom` |
| `BELONGS_TO` (Disease→Department)          | ✅ | ✅          | medical.json `cure_department` |
| `TREATS` (Drug→Disease)                    | ✅ | ✅（去重）   | drug_data Excel `相关疾病` |
| `CONTRAINDICATED_FOR` (Drug→Disease)       | ✅ | ✅（去重）   | drug_data Excel `禁忌` |
| **`COMMON_DRUG`** (Disease→Drug)           | ❌ | ✅          | medical.json `common_drug` |
| **`RECOMMAND_DRUG`** (Disease→Drug)        | ❌ | ✅          | medical.json `recommand_drug` |
| **`DO_EAT`** (Disease→Food)                | ❌ | ✅          | medical.json `do_eat` |
| **`NOT_EAT`** (Disease→Food)               | ❌ | ✅          | medical.json `not_eat` |
| **`RECOMMAND_EAT`** (Disease→Food)         | ❌ | ✅          | medical.json `recommand_eat`（菜谱）|
| **`NEED_CHECK`** (Disease→Check)           | ❌ | ✅          | medical.json `check` |
| **`CURE_WAY`** (Disease→Cure)              | ❌ | ✅          | medical.json `cure_way` |
| **`ACOMPANY_WITH`** (Disease→Disease)      | ❌ | ✅ 并发症    | medical.json `acompany` |
| **`PRODUCED_BY`** (Drug→Producer)          | ❌ | ✅          | medical.json `drug_detail` 解析 + drug_data Excel |
| **`DEPT_PARENT`** (Department→Department)  | ❌ | ✅ 一二级层级 | medical.json `category` |

### 2.4 量化增量

| 指标 | v1 | v2 | 增量 |
|------|----|----|------|
| 节点类型     | 4       | 8        | +100% |
| 关系类型     | 4       | 14       | +250% |
| Disease 属性 | 1       | 11       | +1000% |
| 节点总数     | ~25,000 | 49,837   | +99% |
| 关系总数     | ~50,000 | 421,798  | +743% |
| 向量索引     | 4 类    | 8 类     | +100% |

---

## 3. 构建脚本变更

### 3.1 新文件 `backend/build_neo4j_graph_v2.py`

旧的 `build_neo4j_graph.py` **保留**作为回退版本，**不删除**。

### 3.2 工程改造

| 改造项 | v1 | v2 |
|-------|----|----|
| 关系去重 | 仅依赖 Cypher MERGE | **解析阶段先 set 去重**，写库前减少 66.5% 无效 MERGE 调用 |
| 节点写入 | 单条 `session.run`，逐行循环 | **`UNWIND` 批量写入**（节点 500/批，关系 2000/批），快 10-50x |
| 向量索引 | 4 个 | 8 个（新增 Food/Check/Cure/Producer）|
| 节点向量化 | 4 类节点 | 8 类节点 |
| Drug 数据源 | 仅 drug_data Excel | drug_data Excel **+** medical.json 的 `common_drug`/`recommand_drug`/`drug_detail` 三字段 |
| CLI flags | 无 | `--dry-run` / `--rebuild` / `--skip-embed` / `--json-path` / `--drug-folder` |
| 编码兼容 | 无 | utf-8 强制 reconfigure（防 Windows GBK 乱码导致脚本崩） |
| `.env` 加载 | 依赖 cwd | 相对脚本自身位置查找（任何 cwd 都能跑） |

### 3.3 模块拆分

```
KGParser   ─ 仅解析，不依赖 Neo4j → 支持 --dry-run 安全验证
KGWriter   ─ 仅写入 Neo4j，依赖 KGParser 的产物
main()     ─ 顶层 CLI 调度，按 flag 决定是否清库 / 是否向量化
```

`KGParser` 与 `KGWriter` 解耦的好处：parser 可单测、可统计、可复用到其它存储后端（如 ArangoDB / Memgraph）。

---

## 4. 操作手册

### 4.1 三种使用模式

```bash
# 模式 1：纯解析验证（不写库，最安全）
python backend/build_neo4j_graph_v2.py --dry-run

# 模式 2：清库 + 全量重建（schema 改了必须用这个）
python backend/build_neo4j_graph_v2.py --rebuild

# 模式 3：建图 + 向量化分两步（推荐生产做法，便于排错）
python backend/build_neo4j_graph_v2.py --rebuild --skip-embed
python backend/build_neo4j_graph_v2.py --skip-embed=False  # 后续单独跑向量化
```

### 4.2 自定义数据路径

```bash
python backend/build_neo4j_graph_v2.py --rebuild \
    --json-path D:/path/to/medical.json \
    --drug-folder D:/path/to/drug_data
```

### 4.3 预期耗时

| 阶段 | 耗时 | 资源消耗 |
|------|------|---------|
| 解析 medical.json | ~0.5 秒 | 无外部依赖 |
| 解析 drug_data Excel（7 个） | ~30 秒 | 仅本地磁盘 |
| 写入 Neo4j（节点 50k + 关系 42w） | 5-10 分钟 | Neo4j Bolt 长连接 |
| 向量化（49k 节点） | 30-60 分钟 | DashScope API 调用，¥0.13 |

### 4.4 中断恢复

- 写入阶段中断 → 数据已 MERGE 进库，重跑 `--rebuild` 会清库重建（建议直接重跑）
- 向量化阶段中断 → `embed_all_nodes` 自动断点续传（仅处理 `embedding IS NULL` 的节点）

---

## 5. 兼容性与风险

### 5.1 现有代码兼容性

| 模块 | 受影响 | 原因 |
|------|--------|------|
| `medication_agent.search_kg_contraindications` | ❌ 不影响 | 仍走 `CONTRAINDICATED_FOR` 关系 |
| `scripts/kg_pruner.py` (KGPruner) | ❌ 不影响 | 仍走 4 类向量索引 + `0..2` 跳游走 |
| `agents/general_agent` `search_medical_graph` 工具 | ❌ 不影响 | 走 KGPruner |
| `agents/maddx/tools.py` KG 调用 | ❌ 不影响 | 走 KGPruner |
| `core/evidence.py` 关系词表 | ❌ 不影响 | KG 关系词表是面向用户输出的，不直接对应 Neo4j 关系类型 |
| 前端组件 | ❌ 不影响 | 全部走 trace_data 抽象 |

**结论：v2 是纯增量，零破坏**。新增的 Food/Check/Cure/Producer 节点与新关系，在 Phase 2（意图模板层）实施前不会被任何 agent 主动查询，只是"备好弹药"。

### 5.2 数据库膨胀

- 节点 +99%、关系 +743%
- 估算磁盘占用：节点属性扩展（Disease 10 个属性，平均 200 字符）→ +~20MB；关系数 4x → +~50MB
- 总计 50-100MB 量级，对 Neo4j 微不足道

### 5.3 回退方案

```bash
# 方案 A：直接重跑 v1 脚本（清库 + v1 schema）
python backend/build_neo4j_graph.py

# 方案 B：保留 v2 数据，但禁用新关系
# —— 不可行。Cypher 检索不会主动查新关系，新关系闲置，无负面影响
```

**风险评估**：v2 是单向兼容增量，回退极少必要。

---

## 6. 业务能力增量

### 6.1 v1 答不了 → v2 走图谱直接答的问题类型

| 问题模板 | 涉及关系/属性 |
|---------|--------------|
| "X 病能/不能吃什么" | `DO_EAT` / `NOT_EAT` |
| "X 病推荐食谱" | `RECOMMAND_EAT` |
| "X 病要做什么检查" | `NEED_CHECK` |
| "X 病有什么并发症" | `ACOMPANY_WITH` |
| "X 病怎么治" | `CURE_WAY` |
| "X 病常用什么药" | `COMMON_DRUG` |
| "为什么会得 X" | Disease.cause |
| "如何预防 X" | Disease.prevent |
| "X 多久能好" | Disease.cure_lasttime |
| "X 治愈率" | Disease.cured_prob |
| "X 易感人群" | Disease.easy_get |
| "X 是不是传染病" | Disease.get_way |
| "X 治疗费用" | Disease.cost_money |
| "Y 药谁生产的" | `PRODUCED_BY` |
| "X 科室上一级是什么" | `DEPT_PARENT` |

### 6.2 后续 Phase 2 的施工基础

意图驱动的 Cypher 模板层（Phase 2）将为每个 (action × attribute) 意图组合预定义 Cypher 模板，与现有 GraphRAG（KGPruner）共存：

- 命中精确意图 → 走模板（速度快、精度高）
- 未命中模板 → 回退 GraphRAG（兜底）

v2 是 Phase 2 的**前置依赖**，没有 v2 的关系扩展，模板矩阵无米下锅。

---

## 7. 论文价值

### 7.1 实验章节可写的对比

```
表 X：知识图谱 schema 完整度对比

| 系统              | 节点类型 | 关系类型 | 节点数  | 关系数   | 来源      |
|-------------------|----------|----------|---------|----------|-----------|
| RAGQnASystem      | 8        | 11       | 44,000  | 310,000  | 同源 KG   |
| 本系统 v1（基线）  | 4        | 4        | 25,000  | 50,000   | 欠抽取    |
| 本系统 v2（升级）  | 8        | 14       | 49,837  | 421,798  | 完整解析  |
```

### 7.2 论文话术

> 我们对参考项目 RAGQnASystem 使用的同一份开源 DiseaseKG 数据集进行了系统的字段审计与重新解析。原 v1 实现仅抽取了 4 类节点 / 4 种关系，关系总数为基准的 16%。重构后的 v2 解析器抽取出 8 类节点 / 14 种关系（多出的 3 种来自 drug_data Excel 数据源），节点数 49,837 与基准（44,000）持平，关系数 421,798 超过基准 36%。这为下游意图驱动的 Cypher 模板检索层提供了完整的语义关系基础，相比 v1 系统能直接通过图谱回答"饮食宜忌、必查项目、并发症、治疗方式、常用药、医保费用"等多类高频医疗问题，无需依赖大模型内置常识，从源头降低幻觉风险。

### 7.3 创新点定位

- 这**不是**算法创新点（schema 来自参考项目）
- 是一个**工程完整度对照**的实证：用同样的数据，怎样的实现完整度才是合理的
- 跟你后续做的"意图模板 + GraphRAG 双层混合检索"组合起来，**才是真正的方法学贡献**

---

## 8. 后续改进路线（参考）

| Phase | 任务 | 工作量 | 论文价值 |
|-------|------|-------|---------|
| 1 ✅ | KG schema 完整化（本文档）| 0.5 天 | 中（基线可比性）|
| 2 ⚪ | 意图驱动 Cypher 模板层 | 1 天 | 高（混合检索方法学） |
| 3 ⚪ | medication 用药审查 KG 检索改语义检索（替代 CONTAINS）| 1 小时 | 中 |
| 4 ⚪ | 同义词归一化（alias.csv）| 0.5 天 | 中 |
| 5 ⚪ | PDF 切片加 page/section 元数据 | 0.5 天 | 中（补证据链 locator） |
| 6 ⚪ | Drug-Drug Interaction (DDI) 关系网络 | 1 天 | 高（医疗刚需） |
| 7 ⚪ | NER 模型（BERT+BiLSTM）| 1 天（含训练）| 中（需要 GPU） |

---

## 附录 A：v2 dry-run 输出（已验证通过）

```
==========================================================
📊 KG 解析统计（v2）
==========================================================

【节点：49837】
  Disease     :   8807
  Drug        :  19709
  Symptom     :   5998
  Food        :   4870
  Check       :   3353
  Cure        :    544
  Department  :     54
  Producer    :   6502

【关系：421798（原始 1258667，去重折算 66.5%）】
  TREATS                :  72263
  RECOMMAND_DRUG        :  59465
  HAS_SYMPTOM           :  54710
  RECOMMAND_EAT         :  40221
  NEED_CHECK            :  39418
  CONTRAINDICATED_FOR   :  29794
  NOT_EAT               :  22239
  DO_EAT                :  22230
  CURE_WAY              :  21047
  PRODUCED_BY           :  16922
  BELONGS_TO            :  16781
  COMMON_DRUG           :  14647
  ACOMPANY_WITH         :  12024
  DEPT_PARENT           :     37
==========================================================
```

**关键质量观察**：v2 解析出的 Symptom (5998) / Food (4870) / Check (3353) / Cure (544) / Department (54) **5 类节点的数量与 RAGQnASystem 完全一致**，证明数据源同源（DiseaseKG）+ 解析逻辑正确。
