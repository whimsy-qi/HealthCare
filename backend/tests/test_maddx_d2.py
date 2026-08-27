"""
MADDx D2 冒烟测试：Proposer + Critic 一轮交互
--------------------------------------------
会真实调用 DeepSeek（消耗 token，约 2 次调用，成本 < $0.01）。
运行前确保 .env 里 OPENAI_API_KEY / OPENAI_API_BASE 已配置。

跑法：
  cd D:\\Health_system
  python -m backend.tests.test_maddx_d2
"""
import asyncio
import sys
import os
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 显式加载 backend/.env（因为可能从项目根目录运行）
from dotenv import load_dotenv
_ENV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
load_dotenv(_ENV_PATH, override=False)

from backend.core.blackboard import Blackboard
from backend.agents.maddx.proposer import run_proposer
from backend.agents.maddx.critic import run_critic


# 经典鉴别诊断病例：胸痛（心梗 vs 胃食管反流 vs 肋软骨炎 vs 焦虑）
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
    bb = Blackboard(session_id="test-d2")

    # 记录输入（感知层 stub）
    v_sym = await bb.append("symptoms", CASE_SYMPTOMS, agent_id="perception")
    v_prof = await bb.append("patient_profile", CASE_PROFILE, agent_id="perception")

    print("=" * 60)
    print("【Round 0】Proposer 冷启动")
    print("=" * 60)
    candidates_v0 = await run_proposer(
        bb,
        symptoms=CASE_SYMPTOMS,
        patient_profile=CASE_PROFILE,
        parent_refs=[v_sym, v_prof],
    )
    for i, c in enumerate(candidates_v0, 1):
        print(f"  {i}. {c.get('disease')} (conf={c.get('confidence')}) — {c.get('reasoning')}")

    print()
    print("=" * 60)
    print("【Round 1】Critic 审查（D2 阶段证据为空，预期只发 missing_symptom/epi_mismatch）")
    print("=" * 60)
    # D2: evidence 传空数组
    proposer_entry_v = bb.latest("candidate_dx")["v"]
    objections_v1 = await run_critic(
        bb,
        candidates=candidates_v0,
        symptoms=CASE_SYMPTOMS,
        patient_profile=CASE_PROFILE,
        kg_neighbors=[],
        rag_chunks=[],
        parent_refs=[proposer_entry_v],
    )
    for i, o in enumerate(objections_v1, 1):
        print(f"  {i}. [{o.get('type')}] 针对「{o.get('target_disease')}」: {o.get('detail')}")

    print()
    print("=" * 60)
    print("【Trace DAG】")
    print("=" * 60)
    dag = bb.to_trace_dag()
    print(json.dumps(dag, ensure_ascii=False, indent=2))

    # 基本断言
    assert len(candidates_v0) == 3, f"预期 3 个候选，实际 {len(candidates_v0)}"
    assert all("disease" in c for c in candidates_v0)
    # Critic 证据为空时不应出现 kg_refutation 或 contradicting_symptom
    for o in objections_v1:
        assert o.get("type") in ("missing_symptom", "epidemiology_mismatch"), \
            f"D2 阶段空证据不应产生 {o.get('type')}"

    print("\n[OK] MADDx D2 冒烟测试通过")


if __name__ == "__main__":
    asyncio.run(main())
