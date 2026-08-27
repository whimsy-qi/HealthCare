"""R10 Risk-Routing 冒烟：覆盖 Fast-Path（LOW）+ Full CTAEW（HIGH）双路径。"""
import sys, asyncio, io, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from agents.rumor.integration import run_rumor_ctaew

# 设计意图：
#   LOW-risk  : CAUSAL/COMPOSITIONAL/FOLKLORE 短句 → 应走 FAST_PATH
#   HIGH-risk : INTERACTION/DOSAGE/POPULATION → 必须走 FULL_CTAEW
CLAIMS = [
    ("微波炉加热食物会致癌",          "期望 LOW → FAST_PATH"),
    ("头孢配酒会立即死亡",            "期望 HIGH → FULL_CTAEW（INTERACTION）"),
]


async def main():
    for claim, note in CLAIMS:
        print(f"\n{'='*70}\n命题: {claim}\n备注: {note}\n{'='*70}")
        t0 = time.time()
        md, trace, logs = await run_rumor_ctaew(claim)
        elapsed = time.time() - t0
        ct = trace.get("rumor_ctaew", {})
        risk = ct.get("risk_assessment", {})

        print(f"[耗时]   {elapsed:.1f}s")
        print(f"[分类]   {ct.get('claim_type')}")
        print(f"[风险]   base={risk.get('base_risk')} final={risk.get('final_risk')} "
              f"route={risk.get('route')} upgrade={risk.get('upgrade_reasons')}")
        print(f"[裁决]   verdict={ct.get('final_verdict')} "
              f"belief={ct.get('belief_score')} conf={ct.get('confidence')} "
              f"reason={ct.get('termination_reason')}")
        # 验证 SSE 事件流里有 risk_routed
        evts = trace.get("rumor_events", [])
        risk_evts = [e for e in evts if e.get("phase") == "risk_routed"]
        print(f"[SSE]    rumor_events={len(evts)} risk_routed_emitted={len(risk_evts)}")
        if risk_evts:
            r = risk_evts[0]
            print(f"         → base={r.get('base_risk')} final={r.get('final_risk')} route={r.get('route')}")


if __name__ == "__main__":
    asyncio.run(main())
