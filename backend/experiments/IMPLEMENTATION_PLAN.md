# 改造实施计划表

**更新日期**: 2026-05-02

---

## 优先级总览

| 优先级 | 任务 | 预估时间 | 依赖 | 产出 |
|--------|------|---------|------|------|
| **P0** | 辟谣跨模型辩论实验 | 2-3h (含跑实验) | 无 | 辟谣 Debate vs Vote 数据 |
| **P1** | 意图协作模式路由实现 | 1-2h (纯代码) | P0 数据更完整 | graph_engine.py 改造 |
| **P2** | 意图路由准确率验证 | 30min (已有数据) | P1 | 映射表正确性验证 |
| **P3** | 用药 Single+KG 验证 | 1h (需造数据) | 无 | S+KG 模式有效性证明 |

---

## P0: 辟谣跨模型辩论实验

### 目标
证明辟谣场景下 Debate > Vote——与诊断实验形成对比，支撑"任务-策略匹配"核心叙事。

### 为什么是 P0
- 当前所有实验数据都指向"Vote 优于 Debate"，缺少证明辩论价值的场景
- 有了辟谣的 Debate > Vote 数据，论文叙事从"辩论在诊断上不 work"升级为"诊断适合投票，辟谣适合辩论——验证了任务-策略匹配假说"
- 100 条数据 + 脚本已有，改动成本最低

### 实验配置

| 组 | 模式 | 模型 |
|----|------|------|
| Baseline | Single | DeepSeek-V4-Pro |
| Group 1 | Vote-3 | DS + QW + GLM 三模型投票 |
| Group 2 | Debate-Adv | DS(Advocate) + QW(Skeptic) + GLM(Judge) |

### 指标
准确率、修正率、误伤率、分歧率

### 数据
`experiments/data/rumor_eval_n50.jsonl` (50条) + `rumor_eval_seed.jsonl` (25条) + `rumor_eval_paraphrased.jsonl` (25条) = 100条

### 产出
- `experiments/results/rumor_cross_model/summary.json`
- 论文实验 4.5 节数据

---

## P1: 意图协作模式路由实现

### 目标
在 `graph_engine.py` 实现 `select_collab_mode(act, attr, uncertainty)` → 不同意图走不同协作模式。

### 改动清单

| 文件 | 改动 | 行数 |
|------|------|------|
| `core/intent_ontology.py` | 新增 `COLLAB_MODE_MAP` 字典 + `select_collab_mode()` 函数 | +40 |
| `graph_engine.py` | 在 `dynamic_agent_router` 前插入协作模式选择；新增 `run_vote_mode()` `run_debate_mode()` wrapper | +60 |
| `experiments/debate_runner.py` | 抽取 `VoteRunner.run()` `DebateRunner.run()` 为可复用类 | +30 |

### 非侵入设计
```
现有: triage → dynamic_agent_router → agent_node
改造: triage → select_collab_mode → dynamic_agent_router → agent_node
              └─ 只是在外层包了一层，agent 内部零改动
```

### 产出
- 运行时可切换协作模式的 graph_engine
- 论文 4.5 节系统实现描述

---

## P2: 意图路由准确率验证

### 目标
验证 P1 实现的映射表正确性——每个意图在走推荐模式后准确率 ≥ 走 Single。

### 方法
在已有 40 条多意图测试集 + Phase 1/2 诊断数据上做交叉验证：

| 意图 | 已有数据 | 需补数据 |
|------|---------|---------|
| SYMPTOM_ANALYSIS → D/V | Phase 2 1200 题 | 无 |
| RUMOR_VERIFICATION → D | P0 100 题 | 无 |
| MEDICATION_REVIEW → S+KG | 无 | P3 可补 |
| GENERAL_CONSULTATION → V/S | 无 | 可用 40 条里的对症 case |

### 产出
- 意图路由准确率对比表

---

## P3: 用药 Single+KG 验证

### 目标
验证用药确认场景下 S+KG 模式的有效性。

### 方法
从 drug_data Excel 构造 50 条用药查询，对比 Single vs Single+KG。

### 优先级低的原因
- 需要手工造数据（1h）
- 即使不做，S+KG 的合理性也可以通过 KG 数据量论证（29,794 CONTRAINDICATED_FOR + 72,263 TREATS）

---

## 时间线建议

```
Day 1 (今天):
  ├─ 上午: P0 辟谣辩论脚本改造 + 开跑 (~1h 改造 + 1-2h 等结果)
  └─ 下午: P1 一部分（intent_ontology.py 常量 + select_collab_mode 函数）

Day 2:
  ├─ 上午: P1 收尾（graph_engine.py 路由改造 + debate_runner 抽取）
  └─ 下午: P2 验证（用已有数据跑映射表交叉验证）

Day 3 (缓冲):
  └─ P3 (可选) + 论文写作
```

---

## 论文写作顺序建议

1. **实验章节** (已有全部数据) → 先写
2. **系统设计章节** (架构图 + MADDx + 意图本体) → 先写
3. **意图驱动路由章节** (P1 完成后) → Day 2 写
4. **引言 + 相关工作** (最后写，因为现在最清楚贡献是什么)

---

*文档版本: 2026-05-02*
