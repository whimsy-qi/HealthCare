"""
🎬 双层意图 e2e Demo
====================
跑 3 个相同领域但不同 (act, attr) 的 query，验证下游 agent 是否真的
看到 attr 偏重指令并产出差异化回答。

用例：
  「我血压 160 怎么办」 → triage 应给 (SEEK_HELP, VISIT)
  「高血压的病因是什么」 → triage 应给 (ASK, CAUSE)
  「怎么预防高血压」    → triage 应给 (ASK, PREVENT)

同走 GENERAL_CONSULTATION 路径（domain 一致），但 attr 不同 → 三段答案
应分别强调【就医建议】/【病因机制】/【预防干预】。

注意：本 demo 只验证 audit_logs 与回答 markdown 中是否出现 attr 关键词，
不做完整 LLM 回答正确性核验。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.triage_agent import triage_query  # noqa
from agents.general_agent import run_general_agent  # noqa


CASES = [
    ("我血压 160 怎么办",     ["就医", "急诊", "挂"]),  # attr=VISIT 期望关键词
    ("高血压的病因是什么",     ["机制", "因素", "原理"]),  # attr=CAUSE 期望关键词
    ("怎么预防高血压",         ["生活", "饮食", "运动"]),  # attr=PREVENT 期望关键词
]


async def run_one(query, expected_keywords):
    print(f"\n{'='*78}")
    print(f"🔍 Query: {query}")
    print(f"   期望 attr 反映关键词: {expected_keywords}")
    print('=' * 78)

    # Step 1: triage
    triage_res = await triage_query(query)
    act = triage_res.get("act_intent", "")
    attr = triage_res.get("attr_intent", "")
    print(f"  triage → act={act}, attr={attr}, primary={triage_res.get('primary_intent')}")

    # Step 2: 直接调用 general_agent，传入 act/attr
    answer, sources, images, audit_logs, _evidence_chain = await run_general_agent(
        query=query,
        entities=[],
        messages_history=[],
        patient_profile={},
        internal_scratchpad=[],
        act_intent=act,
        attr_intent=attr,
    )

    # Step 3: 审计 audit_logs 中是否出现 Intent 标记
    intent_logged = any("[General/Intent]" in line for line in audit_logs)
    # Step 4: 在最终 markdown 答案中检查期望关键词
    answer_text = answer or ""
    matched = [kw for kw in expected_keywords if kw in answer_text]
    miss = [kw for kw in expected_keywords if kw not in answer_text]

    print(f"\n  audit_logs 含 Intent 标记: {'✓' if intent_logged else '✗'}")
    print(f"  答案命中期望关键词: {matched}")
    if miss:
        print(f"  未命中: {miss}")
    print(f"\n  --- 答案前 500 字 ---")
    print(answer_text[:500])
    print(f"  --- (共 {len(answer_text)} 字) ---")

    return {
        "query": query,
        "act": act, "attr": attr,
        "intent_logged": intent_logged,
        "matched_keywords": matched,
        "all_matched": len(matched) >= 1,  # 至少命中 1 个就算通过
        "answer_excerpt": answer_text[:300],
    }


async def main():
    results = []
    for q, kws in CASES:
        try:
            r = await run_one(q, kws)
            results.append(r)
        except Exception as e:
            print(f"\n  ❌ 异常: {type(e).__name__}: {e}")
            results.append({"query": q, "error": str(e)})

    print("\n" + "=" * 78)
    print("📊 汇总")
    print("=" * 78)
    n_logged = sum(1 for r in results if r.get("intent_logged"))
    n_matched = sum(1 for r in results if r.get("all_matched"))
    n_total = len([r for r in results if "error" not in r])
    print(f"  intent_logged: {n_logged}/{n_total}")
    print(f"  关键词命中:   {n_matched}/{n_total}")
    if n_total > 0:
        print(f"  attr 偏重传导成功率: {n_matched/n_total*100:.0f}%")

    out = Path(__file__).resolve().parent / "results" / "triage_dual" / "e2e_demo.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n✅ 落盘: {out}")


if __name__ == "__main__":
    asyncio.run(main())
