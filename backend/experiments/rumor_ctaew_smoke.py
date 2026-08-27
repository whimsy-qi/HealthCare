"""Rumor CTAEW 端到端冒烟：分类 → 辩论 → 裁决。"""
import sys, asyncio, io
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from agents.rumor.integration import run_rumor_ctaew

CLAIMS = [
    "骨头汤能补钙",                 # FOLKLORE - 典型伪
    "柚子不能和降压药一起吃",       # INTERACTION - 真
]


async def main():
    for claim in CLAIMS:
        print(f"\n{'='*60}\n命题: {claim}\n{'='*60}")
        md, trace, logs = await run_rumor_ctaew(claim)
        ct = trace.get("rumor_ctaew", {})
        print(f"[分类]   {ct.get('claim_type')}")
        print(f"[权重]   {ct.get('weights')}")
        print(f"[裁决]   verdict={ct.get('final_verdict')} "
              f"belief={ct.get('belief_score')} dissent={ct.get('dissent_score')} "
              f"conf={ct.get('confidence')}")
        print(f"[统计]   rounds={ct.get('rounds_completed')} "
              f"tool_calls={ct.get('total_tool_calls')} hits={ct.get('total_evidence_hits')} "
              f"reason={ct.get('termination_reason')}")
        print("---- Audit Logs ----")
        for l in logs:
            print("  " + l)
        print("---- Markdown (前 300 字) ----")
        print(md[:300])


if __name__ == "__main__":
    asyncio.run(main())
