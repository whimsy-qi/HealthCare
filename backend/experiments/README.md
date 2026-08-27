# MADDx D8 消融实验工具链

本目录收纳论文第 4.4 节「Tool-Augmented Debate 消融实验」所需的全部代码与数据。

---

## 目录结构

```
experiments/
├── __init__.py
├── dataset_schema.py          # TestCase TypedDict 定义（数据契约）
├── gen_cases.py               # LLM 辅助合成新病例（候选 → 人工过审）
├── run_ablation.py            # 主实验脚本（A/B/C/D 四组对照）
├── data/
│   ├── maddx_eval_seed.jsonl          # 手工精写的种子集（真实病例改编）
│   ├── maddx_eval_generated.jsonl     # 由 gen_cases.py 产出（待过审）
│   └── maddx_eval_100.jsonl           # 最终 100 例评测集（人工合并后产出）
└── results/                   # 运行产物（ablation_details.jsonl / csv / summary.json）
```

---

## 一、数据集构建工作流

目标：得到 100 例跨 6 科室、与 Neo4j KG 词表对齐的评测病例。

### 1.1 种子集（已完成）

`data/maddx_eval_seed.jsonl` — 12 例手工精写，每科 2 例，覆盖高频主诊断。

### 1.2 扩充到 100 例

```bash
# 1) 批量生成候选（6 科 × 15 = 90 例）
python -m experiments.gen_cases --per-dept 15 --start-idx 3 \
    --out experiments/data/maddx_eval_generated.jsonl

# 2) 人工过审（必做）：
#    - 检查 primary 是否在 Neo4j KG 里真实存在
#    - 删除症状重复/临床不合理的 case
#    - 修正 severity / duration_days 与临床常识不符的字段

# 3) 合并为最终数据集（种子 12 + 审过 ~88 = 100）
cat experiments/data/maddx_eval_seed.jsonl \
    experiments/data/maddx_eval_generated.jsonl \
    > experiments/data/maddx_eval_100.jsonl
```

### 1.3 数据质量检查清单

- [ ] 每条 `ground_truth.primary` 都能在 Neo4j 里 `MATCH (d:Disease {name: "..."})` 命中
- [ ] 症状名与 KG 的 `:Symptom` 节点贴近（不强制完全一致，允许同义变体）
- [ ] 6 科室分布基本均衡（每科 16-17 例）
- [ ] `acceptable_tier2` 至少 2 个，来自同科室邻近疾病
- [ ] 无 case_id 重复

快速校验脚本：

```bash
python -c "
import json
from collections import Counter
cases = [json.loads(l) for l in open('experiments/data/maddx_eval_100.jsonl', encoding='utf-8') if l.strip()]
print('总数:', len(cases))
print('科室分布:', Counter(c['department'] for c in cases))
print('疾病分布:', Counter(c['ground_truth']['primary'] for c in cases).most_common(10))
assert len(cases) == len({c['case_id'] for c in cases}), 'case_id 重复'
"
```

---

## 二、运行消融实验

### 2.1 四组对照定义

| ID | 名称 | 辩论 | 工具 | 用途 |
|----|------|------|------|------|
| **A** | Single-LLM | ❌ | ❌ | 最弱基线（单次 LLM 直出 Top-3） |
| **B** | MADDx-static | ✅ | ❌ | D7 架构：辩论有但证据静态（空） |
| **C** | MADDx-dynamic | ✅ | ✅ | D8 完整：每 agent 自主取证 |
| **D** | MADDx-critic-only | ✅ | Critic only | 消融：只给 Critic 工具 |

### 2.2 小样本烟测（推荐先跑）

```bash
python -m experiments.run_ablation \
    --dataset experiments/data/maddx_eval_seed.jsonl \
    --conditions A,B,C,D \
    --limit 6 \
    --parallel 2 \
    --out experiments/results/smoke
```

预期耗时：约 10-20 分钟（取决于 LLM RPM 和 Neo4j/DashVector 响应）。

### 2.3 全量运行（论文主表）

```bash
python -m experiments.run_ablation \
    --dataset experiments/data/maddx_eval_100.jsonl \
    --conditions A,B,C,D \
    --parallel 4 \
    --out experiments/results/full
```

注意：
- `--parallel` 默认 4。LLM 供应商 RPM 有限时调小到 2。
- 总任务数 = 100 × 4 = 400 次 `run_maddx`/`single_llm`，预计 2-4 小时。

### 2.4 输出产物

- `ablation_details.jsonl` — 每 (case, condition) 一行，含 Top-1/Top-3、工具调用数、终止原因、耗时
- `ablation_details.csv` — Excel 可打开的扁平表
- `ablation_summary.json` — 按条件聚合的指标
- 终端输出格式化主表（可直接贴论文）

---

## 三、论文主表预期格式

```
Cond Name                   N   Top1   Top3  Tier2 EvDen  Rnds  Tools Lat(s)
------------------------------------------------------------------------------
A    Single-LLM            100  52.0%  78.0%  83.0%  0.00  0.00   0.00    2.3
B    MADDx-static          100  58.0%  82.0%  88.0%  0.00  2.10   0.00   18.5
C    MADDx-dynamic         100  71.0%  91.0%  95.0%  2.35  2.65   5.80   42.1
D    MADDx-critic-only     100  66.0%  87.0%  92.0%  1.20  2.40   2.10   28.7
```

（数字为假设值，以实际实验为准）

论文论点：
- **C > B**: 动态取证显著提升准确率 + 证据密度（evidence citation density > 0）
- **C > D**: Proposer/Defender 也取证比只 Critic 取证更优，验证"每个 agent 独立证据空间"设计
- **D > B**: 即便只给 Critic 工具也比静态强，Critic 否证最依赖证据

---

## 四、复现论文的最小化命令集

```bash
# 准备
export NEO4J_URI=bolt://localhost:7687
export DASHSCOPE_API_KEY=<your-key>
export TAVILY_API_KEY=<your-key>
cd backend

# 数据
python -m experiments.gen_cases --per-dept 15
# ... 人工过审 ...
cat experiments/data/maddx_eval_seed.jsonl experiments/data/maddx_eval_generated.jsonl > experiments/data/maddx_eval_100.jsonl

# 实验
python -m experiments.run_ablation \
    --dataset experiments/data/maddx_eval_100.jsonl \
    --out experiments/results/final
```

---

## 五、常见问题

**Q: gen_cases.py 产出的 primary 不在 KG 里怎么办？**
A: 先看 `whitelist` 是否与 `scripts/setup_neo4j.py` 里实际导入的疾病一致。不一致就改 `DEPARTMENT_DISEASES` 字典。

**Q: 运行中出现 `openai.RateLimitError` 怎么办？**
A: 把 `--parallel` 调到 2 或 1，并在 `core/llm_client.py` 里加指数退避。

**Q: D 组（critic-only）结果接近 B 组？**
A: 正常。Critic 提的 objection 本身会触发下一轮 Proposer/Defender 重新评估，但 Proposer/Defender 本身没有工具，只能基于 Critic 留下的 `tool_result` 做被动推理。这正是该实验要验证的点。
