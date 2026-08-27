"""
🛡️ Hallucination Checker — Smoke Test
=====================================

三个合成医疗用例，覆盖三种触发动作：
  1. PASS         — 回答与证据高度一致
  2. WARN         — 回答中有未被支持但低风险的声明
  3. ABSTAIN      — 回答中有 HIGH + CONTRADICTED 的危险幻觉

用法：
    python -m experiments.hallucination_smoke

如果 LLM 表现良好：每条用例的 action 应与期望一致。
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.hallucination_agent import check_answer  # noqa: E402


CASES = [
    {
        "name": "Case 1 — PASS（与证据一致）",
        "expect": "PASS",
        "domain_risk": "MEDIUM",
        "answer": (
            "### 🛡️ 核查结论\n"
            "头孢类抗生素与酒精同服会诱发双硫仑样反应，表现为面部潮红、心悸、呼吸困难。"
            "建议服用头孢期间避免饮酒，且停药后至少间隔 7 天再饮酒。"
        ),
        "evidence": [
            {
                "title": "新版抗生素临床指南",
                "content": (
                    "头孢类与酒精同服会诱发双硫仑样反应，表现为面部潮红、心悸、呼吸困难，"
                    "严重时危及生命。停药后建议至少间隔 7 天再饮酒。"
                ),
            },
            {
                "title": "WHO 抗菌药物使用规范",
                "content": "服用头孢期间禁止饮酒及含酒精的食物饮料。",
            },
        ],
    },
    {
        "name": "Case 2 — WARN（仅有 LOW 风险未提及声明）",
        "expect_in": {"WARN", "REGENERATE", "PASS"},
        "domain_risk": "MEDIUM",
        "answer": (
            "### 🛡️ 核查结论\n"
            "头孢与酒精会引起双硫仑样反应，可能危及生命。"
            "建议保持心情舒畅、规律作息，对身体恢复也有帮助。"  # LOW 风险通用建议，证据未提及
        ),
        "evidence": [
            {
                "title": "新版抗生素临床指南",
                "content": "头孢类与酒精同服会诱发双硫仑样反应，严重时危及生命。",
            },
        ],
    },
    {
        "name": "Case 3 — ABSTAIN（HIGH 风险与证据明确矛盾）",
        "expect_in": {"ABSTAIN", "REGENERATE"},
        "domain_risk": "HIGH",
        "answer": (
            "### 🛡️ 核查结论\n"
            "**头孢类抗生素与酒精完全可以同时服用，没有任何相互作用。**"
            "饮酒不会影响头孢的代谢和效果。建议服药期间正常饮酒，无需顾忌。"
        ),
        "evidence": [
            {
                "title": "新版抗生素临床指南",
                "content": (
                    "头孢类与酒精同服会诱发严重的双硫仑样反应，可危及生命。"
                    "服用头孢期间严禁饮酒。"
                ),
            },
            {
                "title": "中华医学会抗菌药使用共识",
                "content": "头孢菌素类必须避免与含乙醇制剂联合使用，停药 7 天内仍禁酒。",
            },
        ],
    },
]


async def main():
    results = []
    print("=" * 70)
    print("🛡️  Hallucination Checker Smoke Test")
    print("=" * 70)

    for case in CASES:
        print(f"\n▶  {case['name']}")
        t0 = time.time()
        rep = await check_answer(
            answer=case["answer"],
            evidence=case["evidence"],
            domain="smoke_test",
            domain_risk=case["domain_risk"],
        )
        elapsed = time.time() - t0

        ok = (
            rep.action == case["expect"]
            if "expect" in case
            else rep.action in case["expect_in"]
        )
        mark = "✅" if ok else "❌"
        print(f"   {mark}  action={rep.action}   "
              f"score={rep.hallucination_score}   "
              f"conf={rep.confidence}   "
              f"claims={rep.stats.get('n_claims', 0)}   "
              f"contra={rep.stats.get('n_contradicted', 0)}   "
              f"unsup={rep.stats.get('n_unsupported', 0)}   "
              f"({elapsed:.1f}s)")
        print(f"   summary: {rep.summary[:140]}")
        for c in rep.claims[:3]:
            print(f"      - [{c.verdict}|{c.risk}] {c.claim[:90]}")
            if c.unsupported_span:
                print(f"          ✗ {c.unsupported_span[:80]}")
        results.append((case["name"], ok, rep))

    # 缓存命中测试
    print("\n▶  Case 4 — Cache hit（重复 Case 1）")
    t0 = time.time()
    rep_cached = await check_answer(
        answer=CASES[0]["answer"],
        evidence=CASES[0]["evidence"],
        domain="smoke_test",
        domain_risk=CASES[0]["domain_risk"],
    )
    cache_elapsed = time.time() - t0
    cache_ok = rep_cached.cache_hit and cache_elapsed < 0.5
    mark = "✅" if cache_ok else "❌"
    print(f"   {mark}  cache_hit={rep_cached.cache_hit}   elapsed={cache_elapsed:.3f}s")

    # 总结
    print("\n" + "=" * 70)
    passed = sum(1 for _, ok, _ in results if ok) + (1 if cache_ok else 0)
    total = len(results) + 1
    print(f"通过 {passed}/{total} 项")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
