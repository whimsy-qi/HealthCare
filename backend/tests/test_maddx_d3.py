"""
MADDx D3 完整辩论冒烟测试
---------------------------
跑 2 轮完整辩论（Proposer → Critic → Defender → Critic → Defender → Moderator），
预计消耗 6 次 LLM 调用，约 25-35 秒。

测试目的：
1. 验证 run_maddx() 能跑通 happy path
2. 观察候选列表在两轮之间是否发生变化（证明 Defender 有在"反应"objections）
3. 验证终止原因记录正确
4. 验证 trace DAG 完整（至少含 symptoms/profile/3×candidate_dx/2×objections/final_diagnosis）

跑法：
  cd D:\\Health_system
  python -m backend.tests.test_maddx_d3
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

from backend.core.blackboard import Blackboard
from backend.agents.maddx.workflow import run_maddx


CASE_SYMPTOMS = [
    {"name": "胸骨后压榨性疼痛", "duration_days": 1, "severity": "severe"},
    {"name": "活动后加重", "duration_days": 1, "severity": "moderate"},
    {"name": "出汗", "duration_days": 1, "severity": "moderate"},
    {"name": "左肩放射痛", "duration_days": 1, "severity": "moderate"},
]

CASE_PROFILE = {
    "age": 58,
    "gender": "male",
    "chronic_conditions": ["高血压", "糖尿病"],
    "allergies": [],
}


async def main():
    bb = Blackboard(session_id="test-d3")
    report = await run_maddx(bb, CASE_SYMPTOMS, CASE_PROFILE)

    print("\n" + "=" * 70)
    print("【候选演进】")
    print("=" * 70)
    for i, entry in enumerate(bb.all_by_key("candidate_dx")):
        tag = "Proposer (Round 0)" if i == 0 else f"Defender (Round {i})"
        print(f"\n— {tag} —")
        for j, c in enumerate(entry["value"], 1):
            print(f"  {j}. {c.get('disease')} (conf={c.get('confidence')}) — {c.get('reasoning')[:60]}...")

    print("\n" + "=" * 70)
    print("【Objections 历史】")
    print("=" * 70)
    for i, entry in enumerate(bb.all_by_key("objections"), 1):
        print(f"\n— Critic Round {i} —")
        if not entry["value"]:
            print("  (无反对意见)")
        for o in entry["value"]:
            print(f"  • [{o.get('type')}] 针对「{o.get('target_disease')}」: {o.get('detail')}")

    print("\n" + "=" * 70)
    print("【最终诊断报告】")
    print("=" * 70)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    print("\n" + "=" * 70)
    print(f"【终止原因】{report['termination_reason']} (轮数: {report['rounds_used']})")
    print("=" * 70)

    # 基本断言
    assert "primary_dx" in report and report["primary_dx"]
    assert report["termination_reason"] in (
        "MAX_ROUNDS_REACHED", "TOP1_STABLE_HIGH_CONF", "NO_VALID_OBJECTIONS"
    )
    assert report["rounds_used"] >= 0

    # Trace DAG 完整性
    dag = bb.to_trace_dag()
    agents_seen = {n["agent"] for n in dag["nodes"]}
    assert "proposer" in agents_seen
    assert "critic" in agents_seen
    assert "moderator" in agents_seen

    print(f"\n[OK] MADDx D3 测试通过，trace DAG 共 {len(dag['nodes'])} 节点 / {len(dag['edges'])} 边")


if __name__ == "__main__":
    asyncio.run(main())
