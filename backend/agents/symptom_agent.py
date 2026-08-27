# agents/symptom_agent.py
import os
import json
import logging
import asyncio
import re
from typing import List, Dict, TypedDict, Any
from dotenv import load_dotenv, find_dotenv
from core.llm_client import shared_client as client, FAST_MODEL
from core.intent_ontology import (
    render_attr_focus as _render_attr_focus,
    describe as _describe_intent,
)

load_dotenv(find_dotenv(usecwd=True))
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("SymptomAgent")

MAX_CLARIFYING_TURNS = 6
MAX_INPUT_LENGTH = 500

class SymptomAnalysisResult(TypedDict):
    status: str
    filled_slots: Dict[str, str]
    missing_slots: List[str]
    doctor_reply: str
    options: List[str]
    extracted_keywords: List[str]

def extract_json_from_text(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
        raise ValueError("无法从模型输出中提取有效的 JSON")


async def analyze_and_clarify_symptom(
        messages_history: List[Dict[str, str]],
        turn_count: int,
        current_slots: Dict[str, str],
        act_intent: str = "",      # 🆕 二维内容轴 — 行为
        attr_intent: str = "",     # 🆕 二维内容轴 — 属性
) -> SymptomAnalysisResult:
    """
    症状分析与澄清智能体 (Slot-Filling Agent)

    新增 act_intent / attr_intent：来自 triage 的"行为×属性"二元组。
    在 system_prompt 末尾追加一段"内容侧重"指令，让槽位填充器根据用户
    诉求类型微调追问策略：
       - attr=VISIT  → 减少澄清轮次，快速给就医建议
       - attr=CHECKUP → 优先问可做的检查（而非细致症状描述）
       - attr=CAUTION → 重点问禁忌/服药史
       - attr=DIAGNOSE（默认） → 走完整 4 槽位填充流程
    """
    if messages_history and messages_history[-1]["role"] == "user":
        original_content = messages_history[-1]["content"]
        if len(original_content) > MAX_INPUT_LENGTH:
            messages_history[-1]["content"] = original_content[:MAX_INPUT_LENGTH] + "...[已截断]"
            logger.warning(f"用户输入超长，已截断至 {MAX_INPUT_LENGTH} 字符")

    logger.info(f"🩺 [Symptom Agent] 启动第 {turn_count}/{MAX_CLARIFYING_TURNS} 轮症状分析")
    logger.debug(f"当前已积累槽位: {current_slots}")

    # 🆕 二维内容轴的 audit 描述
    if act_intent or attr_intent:
        logger.info(f"🎯 [Symptom/Intent] {_describe_intent(act_intent, attr_intent)}")

    force_ready_prompt = ""
    if turn_count >= MAX_CLARIFYING_TURNS:
        force_ready_prompt = "\n⚠️ 【极高优指令】：已达到最大追问轮次上限！本次必须结束追问。请将 status 设置为 'READY'，并在 doctor_reply 中温暖地告知用户——信息已收集足够，接下来会为 Ta 做综合分析，请稍等片刻。"

    # 🆕 attr 偏重：影响澄清策略（非 DIAGNOSE 时减少追问轮次）
    intent_focus_prompt = _render_attr_focus(attr_intent)
    fast_track_prompt = ""
    if attr_intent in ("VISIT", "CHECKUP", "CAUTION"):
        fast_track_prompt = (
            f"\n【🚦 快速通道指令】用户的核心需求是【{attr_intent}】，"
            f"请最多再追问 1 轮即转 READY，避免反复追问基础症状细节。"
            f"在 doctor_reply 里也优先围绕 {attr_intent} 给信息。"
        )

    # 🌟 核心优化 1：引入【逐级下钻法则】，提升位置颗粒度
    system_prompt = f"""
    你是一位严谨的三甲医院全科医生。你的核心任务是通过追问，将用户口语化的症状描述映射到标准的【医学槽位】中。

    【核心症状槽位定义】
    - 必需槽位 (Required)：位置(location)、性质(character)、持续时间(duration)、诱因(trigger)。
    - 可选槽位 (Optional)：放射部位(radiation)、缓解因素(alleviating_factors)、伴随症状(associated_symptoms)。

    【💡 逐级下钻法则 (Progressive Drilling)】
    特别是针对“位置(location)”槽位，如果用户描述过于宽泛（例如只说“肚子痛”、“小腹痛”、“胸痛”），你必须判定为“半填充”状态，并在下一轮主动追问具体方位（例如：“请问小腹痛具体是偏左、偏右、正中还是满腹痛？”）。

    【当前会话状态】
    当前是第 {turn_count} 轮追问。
    已成功收集的槽位：{json.dumps(current_slots, ensure_ascii=False)}。
    {force_ready_prompt}

    【💚 doctor_reply 共情要求】
    每次回复（无论 CLARIFYING 还是 READY），开头必须先用 1 句话共情——承认患者的不适、肯定 Ta 主动求医的行为。例如：
      - “头疼确实很折磨人，我们先来把情况理清楚。”
      - “胸口不舒服确实让人担心，你及时来问是对的。”
      - “能理解你的焦虑，我们一步步来看。”
    共情之后，自然过渡到追问或总结。不要直接抛出问题，也不要过度共情（1 句就够）。

    【执行逻辑】
    1. 评估缺失：对比用户描述与历史信息，判断【必需槽位】是否已达到临床所需的精细度。
    2. 状态判定：
       - 如果必需槽位缺失或不够精细，且未达最大轮次，设定 status 为 “CLARIFYING”，针对最关键的 1 个缺失槽位进行追问。
       - 如果必需槽位已满且足够具体，或用户连续回答”不确定”，设定 status 为 “READY”。
    3. 生成选项：如果是 CLARIFYING，必须提供 4~6 个极其简短、符合医学逻辑的可点击选项，覆盖最常见的可能回答。

    【强制输出 JSON 格式】
    {{
        "status": "CLARIFYING 或 READY",
        "filled_slots": {{"location": "左下腹部", "character": "绞痛"}},
        "missing_slots": ["duration", "trigger"],
        "doctor_reply": "医生口吻的回复或追问话术",
        "options": ["选项1", "选项2", "选项3", "选项4", "选项5", "选项6"], // 🌟 选项扩容
        "extracted_keywords": ["左下腹痛", "绞痛"]
    }}
    {intent_focus_prompt}{fast_track_prompt}
    """

    llm_messages = [{"role": "system", "content": system_prompt}] + messages_history

    max_retries = 2
    active_messages = list(llm_messages)  # 复制一份，重试时可追加错误上下文
    last_raw_reply = ""  # 记录上次原始输出，重试时注入 messages 让模型看到自己错在哪
    for attempt in range(max_retries):
        try:
            response = await client.chat.completions.create(
                model=FAST_MODEL,
                messages=active_messages,
                response_format={"type": "json_object"},
                temperature=0.1
            )

            raw_content = response.choices[0].message.content
            last_raw_reply = raw_content or ""
            result = extract_json_from_text(raw_content)

            # 🌟 核心优化 2：放宽前端渲染的选项上限到 6 个
            if result.get("status") == "CLARIFYING":
                options = result.get("options", [])
                if not isinstance(options, list):
                    options = []
                if len(options) > 6:
                    result["options"] = options[:6]
                elif len(options) > 0 and len(options) < 3:
                    result["options"] = options + ["不确定"]
            else:
                result["options"] = []

            logger.info(f"✅ 状态: [{result.get('status')}] | 缺失槽位: {result.get('missing_slots')}")
            return result

        except Exception as e:
            logger.warning(f"⚠️ 第 {attempt + 1} 次解析失败: {e}")
            if attempt < max_retries - 1:
                # 将上一次的错误原因注入对话，让模型看到报错并修正，而不是盲目重试
                active_messages = list(llm_messages) + [
                    {"role": "assistant", "content": last_raw_reply},
                    {"role": "user", "content": f"你上一次的输出导致 JSON 解析失败，错误信息：{e}。请严格按照系统提示中的 JSON 格式重新输出，不要包含任何多余文字。"}
                ]
            else:
                logger.error("❌ 所有重试均失败，触发异常降级机制")
                return {
                    "status": "ERROR",
                    "filled_slots": current_slots,
                    "missing_slots": [],
                    "doctor_reply": "系统解析出现波动，正在为您生成初步参考方案...",
                    "options": [],
                    "extracted_keywords": list(current_slots.values())
                }

if __name__ == "__main__":
    pass