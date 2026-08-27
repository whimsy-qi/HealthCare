"""
MADDx Agent 受限 ReAct 循环（D8）
===============================
统一封装"LLM 推理 ↔ 工具调用"的循环，供 Proposer / Critic / Defender 共用。

关键约束（论文 4.4 节 "Bounded Tool-Augmented Debate"）：
  - MAX_TOOL_CALLS_PER_TURN：单次 agent 调用最多取证次数
  - MAX_REACT_STEPS：ReAct 步数硬上限，防止循环发散
  - 预算耗尽强制降级：插入 degrade_prompt 要求 agent"用已有证据直接给结论"
  - JSON schema 失败带错误重试一次（顺带修了旧版 symptom_agent 那个"无反馈重试"问题）

输出契约：
  每一步 LLM 必须输出如下 JSON 之一：
    { "action": "tool_call", "tool": "<name>", "args": {...}, "thought": "<reason>" }
    { "action": "finish", "result": { ...期望 final_schema... }, "thought": "<reason>" }

  发散场景保护：
    - action 字段缺失 → 视为 finish + result={}（降级给上层处理）
    - tool 名不在 registry → 回复 error message 并计 1 步
"""
import json, os, logging
from typing import Optional, List, Dict, Any

from core.llm_client import shared_client as client, REASONING_MODEL
from core.blackboard import Blackboard

from .tools import ToolRegistry

# 🆕 跨模型客户端：根据 model_id 前缀路由到正确的 API
_GLM_CLIENT = None
_QW_CLIENT = None


def _get_client_for_model(model_id: str):
    """根据 model_id 返回正确的 AsyncOpenAI 客户端。"""
    global _GLM_CLIENT, _QW_CLIENT
    if not model_id:
        return client
    if model_id.startswith("glm-"):
        if _GLM_CLIENT is None:
            from openai import AsyncOpenAI
            _GLM_CLIENT = AsyncOpenAI(
                api_key=os.getenv("GLM_API_KEY"),
                base_url=os.getenv("GLM_API_BASE"),
            )
        return _GLM_CLIENT
    if model_id.startswith("qwen-"):
        if _QW_CLIENT is None:
            from openai import AsyncOpenAI
            _QW_CLIENT = AsyncOpenAI(
                api_key=os.getenv("DASHSCOPE_API_KEY"),
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
        return _QW_CLIENT
    return client  # DeepSeek 默认

logger = logging.getLogger("MADDx.AgentLoop")

MAX_TOOL_CALLS_PER_TURN = 3
MAX_REACT_STEPS = 5

# ---------- 给 LLM 的 action 契约（拼进 system prompt） ----------

REACT_CONTRACT = """
【输出协议 —— 本轮每一步必须严格按以下 JSON 之一回复，不得夹带 markdown / 自然语言】

步骤 A（取证）：
{
  "action": "tool_call",
  "tool": "<工具名，必须是下方工具表里的>",
  "args": { ... 工具入参 ... },
  "thought": "<你为什么要查这个，一句话>"
}

步骤 B（完成）：
{
  "action": "finish",
  "result": { ...严格匹配本任务要求的最终 JSON 结构... },
  "thought": "<结论依据，一句话>"
}

【工具调用预算】
- 你最多可以发起 {max_tools} 次 tool_call
- 超额或 ReAct 步数超限会被强制进入完成步骤，届时你必须只基于已有证据给结论
- 同一 (tool, args) 组合查过一次就被缓存，重复查只会浪费预算
"""


def _format_hit_for_llm(hit: dict) -> str:
    """把 ToolHit 压成单行喂给 LLM，减少 token 浪费。"""
    if hit.get("subject") and hit.get("predicate") and hit.get("object"):
        return f"[{hit.get('ref','')}] {hit['subject']} —{hit['predicate']}→ {hit['object']}"
    if hit.get("text"):
        src = hit.get("source", "")
        prefix = f"[{hit.get('ref','')}"
        if src:
            prefix += f" | {src}"
        prefix += "]"
        return f"{prefix} {hit['text']}"
    return json.dumps(hit, ensure_ascii=False)


def _build_system_prompt(base_system: str, tool_schema: str) -> str:
    # 注意：REACT_CONTRACT 里嵌了 JSON 字面量（含 `{` `}`），不能用 str.format，
    # 否则 Python 会把 JSON 的大括号当成格式占位符。用 replace 精准替换。
    contract = REACT_CONTRACT.replace("{max_tools}", str(MAX_TOOL_CALLS_PER_TURN))
    return base_system + "\n\n" + tool_schema + "\n\n" + contract


async def run_agent_with_tools(
    bb: Blackboard,
    tools: ToolRegistry,
    agent_name: str,           # "proposer" / "critic" / "defender"
    round_idx: int,
    base_system: str,
    user_prompt: str,
    final_schema_hint: str,
    temperature: float = 0.2,
    max_tool_calls: int = MAX_TOOL_CALLS_PER_TURN,
    max_steps: int = MAX_REACT_STEPS,
    model_id: str = None,      # 🆕 可指定模型，None=用 REASONING_MODEL
) -> Dict[str, Any]:
    """
    执行一个受限 ReAct 循环，返回 `finish.result` 的 dict。

    发散时退化行为：
      - 步数超限 / tool_call 超额 → 下一步强制塞 degrade prompt 要求 finish
      - LLM 持续给不出合法 JSON → 2 次解析失败就放弃，返回 {}
    """
    system_prompt = _build_system_prompt(base_system, tools.schema_for_llm())
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt + "\n\n" + final_schema_hint},
    ]

    tool_calls_used = 0
    parse_failures = 0
    evidence_refs_accum: List[int] = []

    for step in range(max_steps):
        # ---- 预算耗尽：强制进入 finish ----
        if tool_calls_used >= max_tool_calls or step == max_steps - 1:
            if step > 0:  # 首步就满预算的极端情况极罕见；正常只在后续步骤触发
                messages.append({
                    "role": "user",
                    "content": (
                        f"【系统提示】取证预算已耗尽（tool_calls={tool_calls_used}/{max_tool_calls}, "
                        f"step={step}/{max_steps}）。请本步直接输出 action=\"finish\"，"
                        "只依据前文已获得的证据给出最终结论。不再允许 tool_call。"
                    ),
                })

        # ---- LLM 推理 ----
        try:
            use_model = model_id or REASONING_MODEL
            use_client = _get_client_for_model(use_model)
            kwargs = dict(
                model=use_model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            # GLM / DeepSeek-V4-Pro 需关思考模式
            if use_model.startswith("glm-") or use_model == "deepseek-v4-pro":
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            resp = await use_client.chat.completions.create(**kwargs)
            raw = resp.choices[0].message.content
        except Exception as e:
            logger.error(f"[AgentLoop/{agent_name}] LLM 调用异常: {e}")
            return {}

        # ---- 解析 ----
        try:
            step_out = json.loads(raw)
        except json.JSONDecodeError as e:
            parse_failures += 1
            logger.warning(f"[AgentLoop/{agent_name}] JSON 解析失败({parse_failures}): {e}")
            if parse_failures >= 2:
                logger.error(f"[AgentLoop/{agent_name}] 连续解析失败，放弃并返回空结果")
                return {}
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": f"【系统】上一步 JSON 解析失败（{e}）。请重新输出严格符合 action 协议的 JSON。",
            })
            continue

        action = step_out.get("action")
        thought = step_out.get("thought", "")

        # ---- 动作分派：finish ----
        if action == "finish":
            result = step_out.get("result", {}) or {}
            # 把 ReAct 累计的 evidence_refs 回填（若 LLM 自己没给）
            if isinstance(result, dict):
                result.setdefault("_agent_meta", {
                    "tool_calls_used": tool_calls_used,
                    "evidence_refs_accum": evidence_refs_accum,
                    "steps_used": step + 1,
                })
            logger.info(
                f"[AgentLoop/{agent_name}] finish @ step={step+1} "
                f"tool_calls={tool_calls_used} evidences={len(evidence_refs_accum)}"
            )
            return result

        # ---- 动作分派：tool_call ----
        if action == "tool_call":
            if tool_calls_used >= max_tool_calls:
                # 预算已耗尽但 LLM 还想查，回注 degrade 提示再循环
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": "【系统】工具预算已用完，下一步必须 action=\"finish\"。",
                })
                continue

            tool_name = step_out.get("tool") or ""
            args = step_out.get("args") or {}
            tool_calls_used += 1

            logger.info(
                f"[AgentLoop/{agent_name}] step={step+1} → tool={tool_name} "
                f"args={json.dumps(args, ensure_ascii=False)[:80]} thought={thought[:40]}"
            )

            result = await tools.invoke(
                tool=tool_name,
                args=args,
                caller_agent=agent_name,
                caller_round=round_idx,
            )
            evidence_refs_accum.append(result["call_ref"])

            # 回注到对话历史（assistant 的 JSON + user 的 tool_result）
            messages.append({"role": "assistant", "content": raw})
            obs_lines = [
                f"【工具返回】tool={tool_name} call_ref={result['call_ref']} "
                f"hits={result['hit_count']} cached={result.get('cached', False)}"
            ]
            if result["hits"]:
                for h in result["hits"][:6]:
                    obs_lines.append("  · " + _format_hit_for_llm(h))
            else:
                obs_lines.append("  · (无命中)")
            obs_lines.append(
                "请基于此证据决定下一步：继续 tool_call（预算剩余 "
                f"{max_tool_calls - tool_calls_used} 次）或 action=\"finish\" 输出最终结果。"
            )
            messages.append({"role": "user", "content": "\n".join(obs_lines)})
            continue

        # ---- 未知 action ----
        logger.warning(f"[AgentLoop/{agent_name}] 未知 action={action!r}, 强制降级到 finish")
        messages.append({"role": "assistant", "content": raw})
        messages.append({
            "role": "user",
            "content": "【系统】action 字段非法。请立即输出 action=\"finish\" 及 result。",
        })

    # 外层超限兜底
    logger.warning(f"[AgentLoop/{agent_name}] 超过 {max_steps} 步未收敛，返回空")
    return {}
