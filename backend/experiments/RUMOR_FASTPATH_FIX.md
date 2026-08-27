# 辟谣意图修正方案：零弃答 + 风险自适应

**设计日期**: 2026-05-03
**触发**: 与小荷医生产品对比，发现系统过度弃答、重装流程覆盖所有辟谣查询

---

## 1. 问题诊断

### 1.1 三个根因

| 问题 | 根因 | 影响 |
|------|------|------|
| 频繁 ABSTAIN | HallucinationAgent 检测到"证据不充分"→ 弃答 | 用户看到空白，最差体验 |
| 全量 CTAEW | RiskRouter 只调证据预算，不调流程 | 常识谣言白等 2 分钟 |
| Judge belief 依赖 KG/RAG | 证据库里没收录 → 判"尚无定论" | 常识级谣言被标记为不确定 |

### 1.2 核心设计原则

**零弃答**：系统对任何辟谣查询都必须给出实质性回答。可以是"这是谣言，因为..."，也可以是"目前的科学证据不足以完全证实或否定这个说法，但我们知道..."，但不能是"系统已放弃本次回答"。

---

## 2. 方案：三层辟谣响应

```
用户 Query
    │
    ▼
Triage → RUMOR_VERIFICATION
    │
    ▼
ClaimClassifier + RiskRouter
    │
    ├─ 低风险 (base=LOW/MEDIUM, claim_type≠INTERACTION)
    │     → 快速通道: Single LLM 直接辟谣 + KG 快速事实核查
    │     → 延迟: ~3s
    │
    ├─ 中风险 (base=MEDIUM, claim_type=EFFICACY/CAUSAL)
    │     → 轻量辩论: Advocate-Skeptic 1轮 + Judge 裁决
    │     → 延迟: ~10s
    │
    └─ 高风险 (base=HIGH, claim_type=INTERACTION/NOVEL_TREND)
          → 全流程 CTAEW (保持现有)
          → 延迟: ~30-60s
```

### 2.1 快速通道 (80% 的查询走这里)

**触发条件**: RiskRouter 输出 `base=LOW` 或 `MEDIUM`，且 `claim_type ≠ INTERACTION`

**流程**:
```
Step 1: Single LLM (DeepSeek) 直接回答
  Prompt: "你是医疗辟谣专家。请对以下说法给出判断。
  1. 判定: 属实/谣言/误导/尚无定论
  2. 科学解释 (2-3段)
  3. 实用建议 (1-2条)

  即使没有直接的临床研究，你也可以基于已知的医学原理和常识推理给出判断。
  严禁输出'系统放弃回答'。如果你不确定，请诚实地解释'目前的科学认知'，
  但你必须给出自己的判断。"

Step 2: KG 快速事实核查
  查询 claim 中的实体是否有 CONTRAINDICATED_FOR 或 TREATS 关系
  如果有 → 作为证据引用
  如果没有 → 不需要引 KG，LLM 的常识推理就足够

Step 3: 输出 (不经过 HallucinationAgent 的 ABSTAIN 路径)
  格式: 判定 + 科学解释 + 实用建议
  如果 HallucinationAgent 检测到问题 → WARN 横幅而非 ABSTAIN
```

### 2.2 HallucinationGuard 改永不弃答

**当前行为**:
```python
if action == "ABSTAIN":
    return ABSTAIN_TEMPLATE  # 空白回答
```

**改为**:
```python
if action == "ABSTAIN":
    # 不弃答。在原回答前加保守提示横幅，但仍保留原回答内容
    return CONSERVATIVE_BANNER + original_answer
```

即使 HallucinationAgent 判定回答置信度低，系统也**输出回答 + 不确定性提示**，而不是空白页。

---

## 3. 改动清单

| 文件 | 改动 | 行数 |
|------|------|------|
| `agents/hallucination_agent.py` | ABSTAIN → WARN_BANNER，永不弃答 | ~5 |
| `agents/rumor/risk_router.py` | 新增 `route_to_fastpath()` 函数 | ~15 |
| `agents/rumor/workflow.py` | 新增 `run_rumor_fastpath()` 单 LLM 通道 | ~30 |
| `graph_engine.py` | rumor_subgraph_wrapper 中加快速通道分派 | ~20 |

---

## 4. 快速通道 Prompt

```python
RUMOR_FASTPATH_PROMPT = """你是资深医疗辟谣专家。用户咨询了一个健康说法，请你给出判断。

【你必须做的事】
1. 给出明确判定：属实 / 谣言 / 误导 / 尚无定论
2. 用 2-3 段解释科学原理（即使没有直接临床研究，也可以基于已知医学原理推理）
3. 给出 1-2 条实用建议

【你绝不能做的事】
- 不能输出"系统放弃回答"或"无法回答"
- 不能只说"尚无定论"然后结束——即使证据不充分，也要解释"我们知道什么、不知道什么"
- 不能编造不存在的临床研究

【格式】
### 🩺 辟谣判定：{属实/谣言/误导/尚无定论}

{2-3段科学解释}

### 💡 实用建议
- {建议1}
- {建议2}

> ⚠️ 本判定基于当前医学共识。如有新的研究证据，结论可能更新。"""
```

---

## 5. HallucinationAgent 修正（永不弃答）

```python
# 当前: action == "ABSTAIN" → 替换整个回答
# 修正: action == "ABSTAIN" → 加保守提示 + 保留原回答

CONSERVATIVE_BANNER = """---
> ⚠️ **不确定性提示**: 系统在交叉核验后发现本回答中存在部分未被当前知识库充分覆盖的声明。
> 以下回答基于已知医学原理和常识推理给出，仅供参考。如有疑虑，建议咨询专业医生或查阅最新文献。
---
"""

if report.action == "ABSTAIN":
    # 用保守横幅替代弃答模板
    return CONSERVATIVE_BANNER + answer, rep_dict
```

---

## 6. 预期效果

| 场景 | 当前 | 修正后 |
|------|------|--------|
| "可乐加味精是春药吗" | 2分钟 → ABSTAIN | 3秒 → "谣言" + 解释 |
| "吃猪脑真的能补脑吗" | 2分钟 → 尚无定论 | 3秒 → "误导" + 科普 |
| "木瓜丰胸" | 2分钟 → ABSTAIN | 3秒 → "谣言" + 科学解释 |
| "某新药能治某病吗" (真·高风险) | 全流程 CTAEW | 全流程 CTAEW (保持) |

---

## 7. Phase 3 实验依据

本方案的设计不是直觉驱动，而是实验驱动。Phase 3 在 400 条辟谣评测上的数据直接支撑了快速通道路由：

| 实验发现 | 对本方案的意义 |
|---------|-------------|
| Single (56.0%) ≈ Vote-3 (56.8%) > Debate (53.0%) | 低风险场景下，Single 的准确率不输 Debate——快速通道用 Single 有实验支撑 |
| Single 延迟 384s，Debate 3627s (9.4×) | 快速通道将 80% 查询从 60s 降至 3s |
| Debate 有效决策率 68.2% vs Single 89.5% | Debate 的 31.8% "尚无定论"直接导致了大量 ABSTAIN——快速通道不产生这个副作用 |
| Debate "尚无定论"率 31.8% vs Single 10.5% | Single 敢下判断，Debate 过度保守——低风险场景下 Single 更适合 |
| Vote-3 pairwise 分歧率 20-31% | 模型在多数辟谣 case 上高度一致——不需要对抗式辩论来解决分歧 |

**闭环逻辑**：Phase 3 证明 Single 的准确率在弱 GT 下不显著劣于 Debate，且延迟更低、弃答更少 → 将 Single 设为默认路由，高风险 case 保留 Debate → 修正后的系统 = Phase 3 实验结论的工程化落地。

---

## 8. 实施优先级

| 优先级 | 改动 | 预估时间 | 效果 |
|--------|------|---------|------|
| **P0** | HallucinationAgent 永不弃答 | 5 min | 立即消除空白回答 |
| **P0** | 快速通道 Prompt + 分派逻辑 | 30 min | 80% 辟谣查询秒级响应 |
| **P1** | RiskRouter 分三层 | 15 min | 风险自适应路由 |
| **P2** | KG 快速事实核查集成 | 15 min | 有证据就引用，没证据不硬找 |

---
*文档版本: 2026-05-03*
