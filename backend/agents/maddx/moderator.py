"""
Moderator 节点 + 收敛判断
========================
两部分：
1. check_convergence(): 纯规则函数，不调 LLM，在每轮 Defender 结束后判断是否终止
2. run_moderator(): 辩论结束后做一次总结性 LLM 调用，产出最终诊断报告

论文 4.3.3 节的三条终止规则：
  - Rule 1 (MAX_ROUNDS_REACHED): 达到预算上限
  - Rule 2 (TOP1_STABLE_HIGH_CONF): Top-1 诊断连续两轮未变且置信度 ≥ 0.7
  - Rule 3 (NO_VALID_OBJECTIONS): Critic 无有效反驳
"""
import json
import logging
from typing import List, Tuple, Optional

from core.llm_client import shared_client as client, REASONING_MODEL
from core.blackboard import Blackboard
from core.blackboard_schema import Candidate, Objection, FinalDiagnosis, TerminationReason

logger = logging.getLogger("MADDx.Moderator")

MAX_ROUNDS = 2            # 辩论轮数上限（不含 Proposer 冷启动 & Moderator 收尾）
CONF_THRESHOLD = 0.7      # TOP1_STABLE 触发的置信度下限

MODEL = REASONING_MODEL
TEMPERATURE = 0.2


# ==================== 收敛判断（纯规则） ====================

def check_convergence(
    candidate_history: List[List[Candidate]],
    latest_objections: List[Objection],
    round_idx: int,
) -> Tuple[bool, Optional[TerminationReason]]:
    """
    Args:
        candidate_history: 按时间顺序的候选列表，index 0 是 Proposer 冷启动产出
        latest_objections: 最近一次 Critic 的 objections
        round_idx: 已完成的辩论轮数（Proposer 之后完成 1 轮 Critic+Defender 则为 1）
    Returns:
        (是否终止, 终止原因)
    """
    # Rule 3: Critic 无反驳 → 立即收敛（此时不需要 Defender）
    if len(latest_objections) == 0:
        return True, "NO_VALID_OBJECTIONS"

    # Rule 1: 达到预算上限
    if round_idx >= MAX_ROUNDS:
        return True, "MAX_ROUNDS_REACHED"

    # Rule 2: Top-1 稳定高置信
    # ⚠️ 关键：必须比较两次 Defender 输出（candidate_history 至少 3 条：v0 Proposer + v1/v2 Defender）
    # 若只比 Proposer(v0) 与 Defender(v1)，"稳定"可能只是 LLM 锚定偏差，非真实共识
    if len(candidate_history) >= 3:
        top1_curr = candidate_history[-1][0] if candidate_history[-1] else None
        top1_prev = candidate_history[-2][0] if candidate_history[-2] else None
        if (top1_curr and top1_prev
                and top1_curr["disease"] == top1_prev["disease"]
                and top1_curr.get("confidence", 0) >= CONF_THRESHOLD):
            return True, "TOP1_STABLE_HIGH_CONF"

    return False, None


# ==================== Moderator LLM 调用 ====================

MODERATOR_SYSTEM = """你是三甲医院内科主治医师，正在面诊一位患者。刚才你和同事就 Ta 的症状做了鉴别诊断辩论，现在要给出结论并当面告诉患者。

【严格输出 JSON】
{
  "primary_dx": "主诊断疾病名",
  "differential_dx": ["次要鉴别1", "次要鉴别2"],
  "confidence": 0.85,
  "supporting_evidence": ["证据1", "证据2"],
  "recommended_tests": ["检查1", "检查2"],
  "termination_reason": "MAX_ROUNDS_REACHED / TOP1_STABLE_HIGH_CONF / NO_VALID_OBJECTIONS",
  "rounds_used": 2,
  "narrative": "markdown 格式的会诊意见正文"
}

【narrative 写作要求 —— 重点】

**身份**：经验丰富的内科主治医师。
**语气**：温和、专业、像在诊室里对患者说话。开头用自然的方式共情（一两句即可），解释病情时像讲故事而不是背教科书，结尾给人踏实的感觉而不是扔下结论就走。
**用词尽量让非医学背景的人能懂。比喻和例子比术语堆砌更能让患者安心。

**必要内容**（自然融入 3-5 段）：
1. 开头共情（简单一两句，真诚不油腻）
2. 基于患者症状给出主诊断，解释医学推理——为什么是它
3. 对 1-2 个次要可能性的排除逻辑——为什么不太像
4. 建议进行哪些检查，每项简短说明目的（可用列表）
5. 生活建议（可操作的、具体的）
6. 危险信号清单（出现这些请立即就医）
7. 温暖的收尾

**字数**：350-500 字。
**格式**：Markdown 自由组织，可以有列表。**不要**出现"### 开篇""### 结语"标签。

【🎨 排版美化】
- 各小节标题可适度使用 emoji 点缀（如 🩺 会诊意见、🔬 建议检查、💡 生活建议、⚠️ 危险信号），但每段最多 1 个 emoji，不要滥用
- 关键医学术语用 **加粗** 突出，方便患者抓住重点
- 列表项保持简洁，每项 1-2 行为宜

【🚫 禁止】
- ❌ 使用"作为 AI""作为模型"等自我指涉
- ❌ 编造具体发病率、生存率等无来源数字
- ❌ 出现"知识图谱"、"系统设定"、"MADDx"、"辩论轮次"、"证据命中"等内部术语
- ❌ 出现"以上结论由多智能体鉴别诊断系统得出"等水印文字

【硬约束】
- primary_dx / differential_dx / recommended_tests 的疾病和检查名必须和结构化字段严格一致
- termination_reason 和 rounds_used 原样使用传入值
- 不要出现任何"AI/模型/系统"字样

**参考风格**（示意，不要照抄用词）：
> ### 🩺 会诊意见
>
> 从你描述的搏动性头痛、在压力大和睡眠不足背景下持续数小时，目前倾向考虑**偏头痛**。这一类头痛典型发作常由情绪紧张和睡眠紊乱诱发，搏动性疼痛也是其特征之一，和你的情况吻合。
>
> 紧张型头痛虽然也与压力相关，但多表现为双侧"紧箍"样的钝痛而非搏动性，和你目前的疼痛性质不太符合。心绞痛在你这个年龄段、且无心血管危险因素的情况下可能性较低，不作为优先方向。
>
> 建议做以下检查以进一步明确：
> - **头颅 MRI**：排除颅内结构性病变
> - **颈椎正侧位片**：评估颈源性诱因
>
> 日常上，规律作息、每天保证 7 小时以上睡眠、避免情绪剧烈波动对偏头痛控制很有帮助。发作时在安静、光线较弱的环境里休息通常能缓解。
>
> 若出现以下情况，**应立即就医**：
> - 突发的"雷击样"剧烈头痛
> - 伴随发热、呕吐或颈部僵硬
> - 视物模糊、一侧肢体无力或意识改变
"""

MODERATOR_USER_TEMPLATE = """【最终候选诊断】
{final_candidates_json}

【完整辩论历史】
{debate_history_json}

【终止信息】
termination_reason: {termination_reason}
rounds_used: {rounds_used}
"""


async def run_moderator(
    bb: Blackboard,
    final_candidates: List[Candidate],
    debate_history: dict,
    termination_reason: TerminationReason,
    rounds_used: int,
    parent_refs: Optional[List[int]] = None,
    model_id: str = None,  # 🆕
) -> FinalDiagnosis:
    """
    执行 Moderator LLM 调用，产出最终诊断报告并写入黑板。
    """
    user_prompt = MODERATOR_USER_TEMPLATE.format(
        final_candidates_json=json.dumps(final_candidates, ensure_ascii=False, indent=2),
        debate_history_json=json.dumps(debate_history, ensure_ascii=False, indent=2),
        termination_reason=termination_reason,
        rounds_used=rounds_used,
    )

    logger.info(f"👨‍⚕️ [Moderator] 收敛: {termination_reason}, 轮数: {rounds_used}")

    resp = await client.chat.completions.create(
        model=model_id or MODEL,
        messages=[
            {"role": "system", "content": MODERATOR_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        temperature=TEMPERATURE,
        response_format={"type": "json_object"},
    )

    raw = resp.choices[0].message.content
    try:
        report: FinalDiagnosis = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"[Moderator] JSON 解析失败: {e}; 原始: {raw[:300]}")
        # Fallback：直接用最终候选构造一份
        primary = final_candidates[0]["disease"] if final_candidates else "未能确诊"
        report = {
            "primary_dx": primary,
            "differential_dx": [c["disease"] for c in final_candidates[1:3]],
            "confidence": final_candidates[0].get("confidence", 0.5) if final_candidates else 0.0,
            "supporting_evidence": [],
            "recommended_tests": [],
            "termination_reason": termination_reason,
            "rounds_used": rounds_used,
            "narrative": f"### 🩺 科主任会诊意见\n\n根据您的症状描述和我们的鉴别讨论，目前倾向考虑 **{primary}**。建议结合门诊进一步确认，完善相关检查。\n\n**💡 日常建议：** 保持规律作息，注意观察症状变化。\n\n**⚠️ 如出现以下情况请及时就医：** 症状持续加重、新发剧烈疼痛、或出现发热/呕吐等伴随症状。",
        }

    # 强制校正：termination_reason / rounds_used 不允许 LLM 篡改
    report["termination_reason"] = termination_reason
    report["rounds_used"] = rounds_used

    await bb.append(
        key="final_diagnosis",
        value=report,
        agent_id="moderator",
        parent_refs=parent_refs or [],
    )
    logger.info(f"👨‍⚕️ [Moderator] 最终诊断: {report['primary_dx']} (conf={report.get('confidence')})")
    return report
