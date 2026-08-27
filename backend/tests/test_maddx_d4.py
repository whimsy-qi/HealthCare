"""
MADDx D4 集成测试：开关 + 适配层 + 端到端
----------------------------------------
覆盖：
1. slots_to_symptoms 转换器正确性（不调 LLM）
2. format_maddx_report_as_markdown 输出包含必要小节（不调 LLM）
3. run_maddx_for_symptom_report 端到端能出 Markdown（测试中使用确定性假 MADDx，不调真实 LLM）
4. 返回值结构含 blackboard_trace，DAG 至少含 symptoms/candidate_dx/final_diagnosis

跑法（启用 MADDx）：
  cd D:\\Health_system
  $env:USE_MADDX="true"
  python -m backend.tests.test_maddx_d4
"""
import asyncio
import sys
import os
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
_ENV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
load_dotenv(_ENV_PATH, override=False)

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from backend.agents.maddx.integration import (
    slots_to_symptoms,
    format_maddx_report_as_markdown,
    run_maddx_for_symptom_report,
)
from backend.agents.maddx import integration


# ==================== 纯函数测试（不调 LLM） ====================

def test_slots_to_symptoms_basic():
    slots = {
        "主诉": "胸痛",
        "伴随症状": "出汗、左肩放射痛",
        "严重程度": "剧烈",
        "持续时间": "1天",
    }
    sym = slots_to_symptoms(slots)
    assert len(sym) >= 2
    assert all(s["severity"] == "severe" for s in sym)
    assert all(s["duration_days"] == 1 for s in sym)
    print("[PASS] test_slots_to_symptoms_basic")


def test_slots_to_symptoms_empty():
    sym = slots_to_symptoms({})
    assert len(sym) == 1  # fallback 至少一条
    print("[PASS] test_slots_to_symptoms_empty")


def test_slots_to_symptoms_duration_parsing():
    assert slots_to_symptoms({"主诉": "咳嗽", "病程": "2周"})[0]["duration_days"] == 14
    assert slots_to_symptoms({"主诉": "咳嗽", "持续时间": "3个月"})[0]["duration_days"] == 90
    print("[PASS] test_slots_to_symptoms_duration_parsing")


def test_format_markdown_contains_sections():
    fake_report = {
        "primary_dx": "急性心肌梗死",
        "differential_dx": ["不稳定型心绞痛", "主动脉夹层"],
        "confidence": 0.87,
        "supporting_evidence": ["压榨性胸痛", "放射痛"],
        "recommended_tests": ["心电图", "心肌酶谱"],
        "termination_reason": "MAX_ROUNDS_REACHED",
        "rounds_used": 2,
    }
    md = format_maddx_report_as_markdown(fake_report)
    assert "全科综合会诊研判报告" in md
    assert "急性心肌梗死" in md
    assert "87%" in md
    assert "心电图" in md
    assert "2 轮内部辩论" in md
    print("[PASS] test_format_markdown_contains_sections")


# ==================== 端到端测试（不调真实 LLM） ====================

async def test_e2e_run_maddx_for_symptom_report(monkeypatch):
    async def fake_run_maddx(bb, symptoms, patient_profile, **kwargs):
        symptoms_v = await bb.append("symptoms", symptoms, "test.symptom_adapter", [])
        candidate_v = await bb.append(
            "candidate_dx",
            [
                {
                    "disease": "急性心肌梗死",
                    "confidence": 0.87,
                    "evidence_refs": [symptoms_v],
                }
            ],
            "proposer",
            [symptoms_v],
        )
        await bb.append(
            "final_diagnosis",
            {
                "primary_dx": "急性心肌梗死",
                "confidence": 0.87,
                "supporting_evidence": ["胸骨后压榨性疼痛", "左肩放射痛"],
            },
            "moderator",
            [candidate_v],
        )
        return {
            "primary_dx": "急性心肌梗死",
            "differential_dx": ["不稳定型心绞痛", "主动脉夹层"],
            "confidence": 0.87,
            "supporting_evidence": ["胸骨后压榨性疼痛", "左肩放射痛"],
            "recommended_tests": ["心电图", "心肌酶谱"],
            "termination_reason": "TEST_FAKE",
            "rounds_used": 1,
        }

    monkeypatch.setattr(integration, "_medrag_ranker_enabled", lambda: False)
    monkeypatch.setattr(integration, "run_maddx", fake_run_maddx)

    slots = {
        "主诉症状": "胸骨后压榨性疼痛",
        "伴随症状": "出汗、左肩放射痛、活动后加重",
        "严重程度": "剧烈",
        "持续时间": "1天",
    }
    profile = {
        "age": 58,
        "gender": "male",
        "chronic_conditions": ["高血压", "糖尿病"],
    }
    # D4 用 symptom_controller 已经检索过的字符串作为证据
    kg_context = (
        "路径1: 胸痛 → 冠状动脉狭窄 → 急性心肌梗死\n"
        "路径2: 压榨性胸痛 + 出汗 → 心肌缺血\n"
        "路径3: 左肩放射痛 → 心源性胸痛"
    )
    local_guide = (
        "2023 中国急性心肌梗死诊治指南：典型表现为胸骨后压榨性疼痛伴放射痛，持续 >20 分钟不缓解。\n\n"
        "急诊处置：首选心电图 + 心肌酶谱双联检测。"
    )

    markdown, bb, events_log = await run_maddx_for_symptom_report(
        slots=slots,
        patient_profile=profile,
        kg_context=kg_context,
        local_guide_context=local_guide,
    )
    assert events_log == []

    print("\n" + "=" * 70)
    print("【生成的 Markdown 报告】")
    print("=" * 70)
    print(markdown)

    print("\n" + "=" * 70)
    print("【Blackboard Trace DAG 摘要】")
    print("=" * 70)
    dag = bb.to_trace_dag()
    for n in dag["nodes"]:
        print(f"  v{n['id']:>2} [{n['agent']:>9}] {n['label']:>16} | {n['preview'][:50]}")

    assert "全科综合会诊研判报告" in markdown
    assert any("candidate_dx" == n["label"] for n in dag["nodes"])
    assert any("final_diagnosis" == n["label"] for n in dag["nodes"])
    print("\n[PASS] test_e2e_run_maddx_for_symptom_report")


class _DirectMonkeyPatch:
    def setattr(self, obj, name, value):
        setattr(obj, name, value)


async def main():
    # 纯函数测试
    test_slots_to_symptoms_basic()
    test_slots_to_symptoms_empty()
    test_slots_to_symptoms_duration_parsing()
    test_format_markdown_contains_sections()
    # 端到端
    await test_e2e_run_maddx_for_symptom_report(_DirectMonkeyPatch())
    print("\n[OK] MADDx D4 集成测试全部通过")


if __name__ == "__main__":
    asyncio.run(main())
