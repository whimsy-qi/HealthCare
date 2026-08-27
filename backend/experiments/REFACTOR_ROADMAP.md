# 改造路线图

**状态**: 2026-05-02

---

## 总览

```
已完成 ✅
  ├─ Phase 1: 基线模型评测 (1200题, 4模型)
  ├─ Phase 2: 跨模型辩论实验 (5组, Vote vs Debate)
  ├─ KG v2: 重建 + 向量化 + Stub过滤 + 前端8类节点
  ├─ P1-1: intent_ontology.py → COLLAB_MODE_MAP + select_collab_mode()
  └─ P1-2: graph_engine.py → triage_node 设置 collab_mode + collab_models

进行中 🔄
  ├─ Phase 3 数据: 谣言生成+审查 (456条, 补生成中)
  └─ P1-3: 本文档 ─ 规划剩余改造

待做 ⏳
  ├─ P0: Phase 3 辟谣辩论实验
  ├─ P1-4: debate_runner.py → VoteRunner/DebateRunner 可复用类
  ├─ P1-5: graph_engine → agent节点读取 collab_mode 并分派
  ├─ P2: 多主意图并行调度
  └─ P3: 论文写作
```

---

## 当前状态

| 组件 | 状态 | 说明 |
|------|------|------|
| 基线评测 | ✅ | 4模型 × 1200题, 单选+多选, 重叠矩阵 |
| 辩论实验 | ✅ | Vote-3 (84.8%) > Debate-B (83.9%) > Single (82.7%) |
| KG v2 | ✅ | 64K节点, 404K关系, 全向量化 |
| KG前端 | ✅ | 8类节点, 15种关系中文化, Stub过滤 |
| 意图映射表 | ✅ | 17条规则, 已写入 intent_ontology.py |
| 路由注入 | ✅ | triage_node 自动设置 collab_mode + collab_models |
| Agent执行 | ❌ | agent节点还未读取 collab_mode——始终走默认模式 |
| 可复用Runner | ❌ | debate_runner.py 的函数未抽取为类 |
| 辟谣数据 | 🔄 | 456条生成完成, 补生成中 |
| 辟谣实验 | ❌ | 数据就绪后启动 |

---

## P1: 意图驱动路由（核心改造）

### 已完成

```
triage_node → select_collab_mode(act, attr, uncertainty)
           → collab_mode = "vote"|"debate"|"single"|"single_kg"
           → collab_models = ["deepseek","qwen","glm"]
           → 存入 AgentState，下游可读
```

### P1-4: 抽取可复用 Runner 类

**优先级**: 🔴 今天做

**文件**: `experiments/debate_runner.py`

从 Phase 2 脚本中抽取：

```python
class VoteRunner:
    """三模型多数投票，可在 graph_engine 中直接调用"""
    @staticmethod
    async def run(query, options, models=["deepseek","qwen","glm"]):
        ...

class DebateRunner:
    """两模型对抗辩论，Proposer+Critic+Moderator"""
    @staticmethod
    async def run(query, options, proposer="deepseek", critic="glm", rounds=1):
        ...
```

**改动**: ~40 行，从现有函数中提取

### P1-5: Agent 节点读取 collab_mode

**优先级**: 🔴 今天做

**文件**: `graph_engine.py`

每个 agent wrapper node 在入口处检查 `state.get("collab_mode")`：

```python
async def symptom_node(state):
    mode = state.get("collab_mode", "debate")  # 诊断默认辩论
    if mode == "vote":
        return await VoteRunner.run(query, opts, models)
    elif mode == "debate":
        return await DebateRunner.run(query, opts, ...)
    else:
        # 原有单Agent逻辑
```

**改动**: 每个 agent node ~15 行, 6 个 Agent = ~90 行

### P1-6: 集成测试

**优先级**: 🟡 P1-4/5 完成后

- 调 `/api/chat` 发送不同意图的 query
- 验证 collab_mode 被正确设置和使用
- 检查 SSE trace 中是否包含协作模式信息

---

## P2: 多主意图并行调度（论文亮点）

**优先级**: 🟢 明天做（辟谣实验跑完后）

### 改造内容

1. `triage_agent.py`: Prompt 改为输出意图列表
2. `graph_engine.py`: 新增 `dispatch_parallel_agents()` 并发调度
3. `graph_engine.py`: 新增 `synthesize_response()` 结果合成

### 预估工作量: 2-3 小时

---

## Phase 3: 辟谣跨模型辩论实验

**优先级**: 🔴 数据就绪后立即启动

### 依赖
- 辟谣数据 400 条（补生成进行中）
- P1-4 完成的 VoteRunner/DebateRunner（可直接复用）

### 实验矩阵

```
Single-DeepSeek  — 基线
Vote-3           — DS+QW+GLM 投票
Debate-Same      — 全DS (Adv+Ske+Judge)
Debate-Cross     — DS(Adv)+QW(Ske)+GLM(Judge)
```

### 预期: 2-3 小时（含跑实验）

---

## P3: 论文写作

**优先级**: 🟢 实验全部完成后

### 写作顺序

1. **实验章节** (4.2-4.5) — 数据已有，先写
2. **系统设计** (3.1-3.4) — 架构图已清晰
3. **意图路由** (3.5) — P1 完成后写
4. **引言+相关工作** (1-2) — 最后写

---

## 当天执行计划

```
现在 (P1-4): 抽取 VoteRunner/DebateRunner 类  (~30 min)
     (P1-5): Agent 节点读取 collab_mode          (~40 min)
     (P1-6): 集成测试                             (~20 min)

补数据完成后 → Phase 3 辟谣辩论实验               (~2-3 h)

Phase 3 完成后 → P2 多意图并行调度 (可选)           (~2 h)
                → 论文写作
```

---

*文档版本: 2026-05-02*
