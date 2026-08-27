# backend/agents/general_fusion.py
import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv, find_dotenv
from openai import AsyncOpenAI

from core.intent_ontology import describe as _describe_intent
from core.llm_client import DEFAULT_MODEL, shared_client

load_dotenv(find_dotenv(usecwd=True))
logger = logging.getLogger("GeneralFusion")


FUSION_MODEL_KEYS = ("deepseek", "qwen", "glm")


MODEL_OUTPUT_SYSTEM = """你是一名严谨的临床医学顾问。请针对患者问题独立给出医学意见。
严格输出 JSON，不要输出 Markdown，不要输出选项字母。

JSON 字段：
{
  "answer_summary": "给患者的核心判断，80-160字",
  "evidence_points": ["支持判断的医学依据，每条不超过80字"],
  "risk_warnings": ["需要提醒的禁忌、红旗症状或就医风险"],
  "next_steps": ["患者下一步可执行建议"],
  "confidence": 0.0,
  "uncertainties": ["信息不足或不同可能性的分歧点"]
}

约束：
- 不要直接开处方，不要给出确定诊断。
- 如果证据不足，请明确写入 uncertainties。
- 如果问题属于急症风险，请把就医/急诊建议放在 risk_warnings 和 next_steps。"""


SYNTHESIS_SYSTEM = """你是全科主任医师，正在整合多名 AI 医学顾问的独立意见。
你的任务是生成面向患者的最终 Markdown 答复。

要求：
- 合并各模型共同结论，删除无证据或越权诊疗内容。
- 对明确分歧只保留临床相关部分，并用“目前信息不足以确定...”表达。
- 优先输出红旗症状、就医建议、风险边界。
- 不要出现 H1/H2 标题，只使用 ### 小节。
- 不要提模型名称、投票、内部协作过程。
- 不要输出 A/B/C/D 这类选择题答案。"""


def _json_from_text(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}


def _normalize_output(model_key: str, data: Dict[str, Any], raw: str = "", error: str = "") -> Dict[str, Any]:
    def _list_value(name: str) -> List[str]:
        value = data.get(name, [])
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()][:6]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    confidence = data.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return {
        "model": model_key,
        "answer_summary": str(data.get("answer_summary") or raw[:300] or "").strip(),
        "evidence_points": _list_value("evidence_points"),
        "risk_warnings": _list_value("risk_warnings"),
        "next_steps": _list_value("next_steps"),
        "confidence": confidence,
        "uncertainties": _list_value("uncertainties"),
        "error": error,
    }


def _client_for_model(model_key: str) -> Optional[Tuple[AsyncOpenAI, str, Dict[str, Any]]]:
    if model_key == "deepseek":
        return shared_client, DEFAULT_MODEL, {}

    if model_key == "qwen":
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            return None
        return (
            AsyncOpenAI(api_key=api_key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"),
            os.getenv("LLM_MODEL_QWEN", "qwen-max"),
            {},
        )

    if model_key == "glm":
        api_key = os.getenv("GLM_API_KEY")
        base_url = os.getenv("GLM_API_BASE")
        if not api_key or not base_url:
            return None
        return (
            AsyncOpenAI(api_key=api_key, base_url=base_url),
            os.getenv("LLM_MODEL_GLM", "glm-5.1"),
            {"thinking": {"type": "disabled"}},
        )

    return None


async def _call_model(
    model_key: str,
    query: str,
    patient_profile: dict,
    evidence_text: str,
    act_intent: str,
    attr_intent: str,
) -> Dict[str, Any]:
    cfg = _client_for_model(model_key)
    if not cfg:
        return _normalize_output(model_key, {}, error="model_config_missing")

    client, model_id, extra_body = cfg
    intent_desc = _describe_intent(act_intent, attr_intent)
    user_prompt = f"""
【患者问题】
{query}

【意图侧重】
{intent_desc}

【患者健康档案】
{json.dumps(patient_profile or {}, ensure_ascii=False)[:1200]}

【已检索证据】
{evidence_text[:2500] if evidence_text else "暂无外部证据。"}
"""
    kwargs = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": MODEL_OUTPUT_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
    }
    if extra_body:
        kwargs["extra_body"] = extra_body

    try:
        resp = await client.chat.completions.create(**kwargs)
    except Exception as first_error:
        kwargs.pop("response_format", None)
        try:
            resp = await client.chat.completions.create(**kwargs)
        except Exception as second_error:
            logger.warning(f"[Fusion] {model_key} 调用失败: {second_error}")
            return _normalize_output(model_key, {}, error=type(second_error).__name__ or str(first_error)[:80])

    raw = (resp.choices[0].message.content or "").strip()
    data = _json_from_text(raw)
    if not data:
        return _normalize_output(model_key, {}, raw=raw, error="json_parse_failed")
    return _normalize_output(model_key, data, raw=raw)


async def _gather_evidence(query: str, evidence_context: Any = None) -> Tuple[str, List[dict], List[str]]:
    if evidence_context:
        if isinstance(evidence_context, dict):
            text = evidence_context.get("text") or evidence_context.get("context") or ""
            sources = evidence_context.get("sources") or []
            images = evidence_context.get("images") or []
            return str(text), list(sources or []), list(images or [])
        return str(evidence_context), [], []

    try:
        from scripts.main_agent import get_multimodal_context

        ctx, cards, imgs = await get_multimodal_context(query.strip()[:120] or "综合医学咨询")
        return ctx or "", (cards or [])[:5], imgs or []
    except Exception as e:
        logger.warning(f"[Fusion] 证据预检索失败: {e}")
        return "", [], []


def _build_fusion_report(model_outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    successful = [m for m in model_outputs if not m.get("error")]
    failed = [m for m in model_outputs if m.get("error")]
    uncertainties = []
    for item in successful:
        for point in item.get("uncertainties", []):
            if point and point not in uncertainties:
                uncertainties.append(point)
    warnings = []
    for item in successful:
        for warning in item.get("risk_warnings", []):
            if warning and warning not in warnings:
                warnings.append(warning)
    avg_conf = 0.0
    if successful:
        avg_conf = sum(float(m.get("confidence") or 0.0) for m in successful) / len(successful)
    return {
        "n_models": len(model_outputs),
        "n_successful": len(successful),
        "failed_models": [{"model": m["model"], "error": m.get("error")} for m in failed],
        "avg_confidence": round(avg_conf, 3),
        "disagreement_points": uncertainties[:8],
        "risk_warning_pool": warnings[:8],
    }


def _fallback_answer(model_outputs: List[Dict[str, Any]]) -> str:
    successful = [m for m in model_outputs if not m.get("error") and m.get("answer_summary")]
    if not successful:
        return "### 初步建议\n当前多模型融合暂时不可用。请补充症状持续时间、严重程度、既往病史、正在使用的药物等信息；如有明显加重或急性不适，请优先线下就医。"

    top = max(successful, key=lambda m: float(m.get("confidence") or 0.0))
    lines = ["### 初步建议", top["answer_summary"]]
    if top.get("risk_warnings"):
        lines.append("\n### 需要注意")
        lines.extend(f"- {x}" for x in top["risk_warnings"][:4])
    if top.get("next_steps"):
        lines.append("\n### 下一步")
        lines.extend(f"- {x}" for x in top["next_steps"][:4])
    return "\n".join(lines)


async def run_general_fusion(
    query: str,
    models: Optional[List[str]] = None,
    patient_profile: Optional[dict] = None,
    evidence_context: Any = None,
    act_intent: str = "",
    attr_intent: str = "",
) -> Dict[str, Any]:
    """
    开放式全科问答融合链路：多模型独立结构化输出，再由 synthesizer 生成患者可读答复。
    """
    requested_models = [m for m in (models or list(FUSION_MODEL_KEYS)) if m in FUSION_MODEL_KEYS]
    if not requested_models:
        requested_models = ["deepseek"]

    audit_logs = [
        f"[General/Fusion] 启动开放式多模型融合: {', '.join(requested_models)}",
    ]
    evidence_text, sources, images = await _gather_evidence(query, evidence_context=evidence_context)
    if sources:
        audit_logs.append(f"[General/Fusion] 预检索命中 {len(sources)} 条证据。")
    else:
        audit_logs.append("[General/Fusion] 预检索未命中证据，融合阶段将显式保留不确定性。")

    tasks = [
        _call_model(m, query, patient_profile or {}, evidence_text, act_intent, attr_intent)
        for m in requested_models
    ]
    model_outputs = await asyncio.gather(*tasks)
    fusion_report = _build_fusion_report(model_outputs)
    audit_logs.append(
        f"[General/Fusion] 模型成功 {fusion_report['n_successful']}/{fusion_report['n_models']}，"
        f"分歧点 {len(fusion_report['disagreement_points'])} 个。"
    )

    synthesis_input = {
        "query": query,
        "intent": _describe_intent(act_intent, attr_intent),
        "patient_profile": patient_profile or {},
        "evidence_context": evidence_text[:2500],
        "model_outputs": model_outputs,
        "fusion_report": fusion_report,
    }
    try:
        synth_resp = await shared_client.chat.completions.create(
            model=DEFAULT_MODEL,
            temperature=0.2,
            max_tokens=1000,
            messages=[
                {"role": "system", "content": SYNTHESIS_SYSTEM},
                {"role": "user", "content": json.dumps(synthesis_input, ensure_ascii=False)},
            ],
        )
        answer = (synth_resp.choices[0].message.content or "").strip()
        if not answer:
            answer = _fallback_answer(model_outputs)
    except Exception as e:
        logger.warning(f"[Fusion] synthesizer 失败: {e}")
        audit_logs.append(f"[General/Fusion] synthesizer 失败，使用最高置信候选兜底: {type(e).__name__}")
        answer = _fallback_answer(model_outputs)

    trace_data = {
        "collab_mode": "fusion",
        "collab_models": requested_models,
        "sources": sources[:5],
        "model_outputs": model_outputs,
        "fusion_report": fusion_report,
    }
    return {
        "answer": answer,
        "trace_data": trace_data,
        "model_outputs": model_outputs,
        "fusion_report": fusion_report,
        "sources": sources[:5],
        "response_images": images,
        "audit_logs": audit_logs,
    }
