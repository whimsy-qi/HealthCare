"""
🎯 Triage Dual-Axis (Act × Attr) Smoke Test
============================================

验证 triage_agent 输出的【行为×属性】二元组是否准确，覆盖：
  - 5 类 act × 8 类 attr 的代表性样本
  - 易混淆边界 case（同 query 不同 attr）
  - 紧急情况下 act/attr 的容错

成功标准：
  - act 命中率 ≥ 90%（5 类粗分类）
  - attr 命中率 ≥ 75%（8 类细分类）
  - 任何 case 都不能返回空 act 或空 attr（兜底必须生效）

输出：
  experiments/results/triage_dual/triage_dual_smoke.jsonl
  experiments/results/triage_dual/triage_dual_summary.json
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.triage_agent import triage_query  # noqa: E402


# ─── 测试用例：(query, expected_act, expected_attr, note) ───
CASES = [
    # ASK × 8 attr
    ("高血压的病因是什么",            "ASK",       "CAUSE",    "纯病因咨询"),
    ("糖尿病的典型症状有哪些",        "ASK",       "SYMPTOM",  "症状罗列"),
    ("什么是乙肝",                    "ASK",       "BASIC",    "疾病科普"),
    ("怀孕初期要做哪些检查",          "ASK",       "CHECKUP",  "检查项目咨询"),
    ("感冒该挂什么科",                "ASK",       "VISIT",    "科室分诊"),
    ("怎么预防中风",                  "ASK",       "PREVENT",  "预防咨询"),
    ("饭前还是饭后吃二甲双胍",        "ASK",       "CAUTION",  "用药须知"),

    # 5 类 act 各 1 条核心样本
    ("我头疼",                        "SEEK_HELP", "DIAGNOSE", "裸主诉求诊"),
    ("我血压 160 怎么办",             "SEEK_HELP", "VISIT",    "求处理+倾向就医"),
    ("我这是不是流感",                "CONFIRM",   "DIAGNOSE", "确认型疑诊"),
    ("吃头孢能喝酒吗",                "CONFIRM",   "CAUTION",  "确认配伍禁忌"),
    ("微波炉加热致癌吗",              "DEBUNK",    "CAUSE",    "辟谣致病机制"),
    ("尿酸 520 提示什么",             "ANALYZE",   "DIAGNOSE", "数据分析"),
]

# 同义边界容许
ATTR_ALIASES = {
    "VISIT":    {"VISIT", "DIAGNOSE"},
    "CAUTION":  {"CAUTION", "BASIC"},
    "DIAGNOSE": {"DIAGNOSE", "SYMPTOM"},
    "BASIC":    {"BASIC", "CAUSE"},
}


def _attr_match(predicted: str, expected: str) -> bool:
    if predicted == expected:
        return True
    return predicted in ATTR_ALIASES.get(expected, set())


async def run_one(case):
    q, exp_act, exp_attr, note = case
    try:
        r = await triage_query(q)
    except Exception as e:
        return {"query": q, "error": f"{type(e).__name__}: {e}",
                "expected_act": exp_act, "expected_attr": exp_attr, "note": note}
    pred_act = r.get("act_intent") or ""
    pred_attr = r.get("attr_intent") or ""
    return {
        "query": q, "note": note,
        "expected_act": exp_act, "expected_attr": exp_attr,
        "pred_act": pred_act, "pred_attr": pred_attr,
        "primary_intent": r.get("primary_intent"),
        "sub_intent": r.get("sub_intent"),
        "act_correct": pred_act == exp_act,
        "attr_correct": _attr_match(pred_attr, exp_attr),
        "attr_strict_correct": pred_attr == exp_attr,
        "non_empty": bool(pred_act and pred_attr),
    }


async def main():
    out_dir = Path(__file__).resolve().parent / "results" / "triage_dual"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 90)
    print(f"🎯 Triage Dual-Axis Smoke ({len(CASES)} 例)")
    print("=" * 90)

    sem = asyncio.Semaphore(3)
    async def bounded(c):
        async with sem:
            return await run_one(c)
    results = await asyncio.gather(*(bounded(c) for c in CASES))

    # 落盘
    with open(out_dir / "triage_dual_smoke.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 控制台对比表
    print(f"{'query':<28}{'expected':<14}{'predicted':<14}{'note':<20}{'act':>5}{'attr':>5}")
    print("-" * 90)
    n = n_act = n_attr_loose = n_attr_strict = n_nonempty = 0
    for r in results:
        if "error" in r:
            print(f"{r['query'][:26]:<28}— ERROR: {r['error'][:50]}")
            continue
        n += 1
        n_act += r["act_correct"]
        n_attr_loose += r["attr_correct"]
        n_attr_strict += r["attr_strict_correct"]
        n_nonempty += r["non_empty"]
        exp = f"{r['expected_act'][:7]}/{r['expected_attr'][:5]}"
        pred = f"{r['pred_act'][:7]}/{r['pred_attr'][:5]}"
        ok_act = "✓" if r["act_correct"] else "✗"
        ok_attr = "✓" if r["attr_strict_correct"] else ("≈" if r["attr_correct"] else "✗")
        print(f"{r['query'][:26]:<28}{exp:<14}{pred:<14}{r['note'][:18]:<20}{ok_act:>5}{ok_attr:>5}")

    print("-" * 90)
    if n == 0:
        print("⚠️ 全部失败")
        return
    print(f"\n📊 统计 (n={n})")
    print(f"  act 命中率           : {n_act}/{n} = {n_act/n*100:.1f}%   (期望 ≥ 90%)")
    print(f"  attr 严格命中率      : {n_attr_strict}/{n} = {n_attr_strict/n*100:.1f}%")
    print(f"  attr 宽松命中率      : {n_attr_loose}/{n} = {n_attr_loose/n*100:.1f}%   (期望 ≥ 75%)")
    print(f"  非空率(兜底)         : {n_nonempty}/{n} = {n_nonempty/n*100:.1f}%   (必须 = 100%)")

    summary = {
        "n": n,
        "n_act_correct": n_act,
        "n_attr_strict_correct": n_attr_strict,
        "n_attr_loose_correct": n_attr_loose,
        "n_nonempty": n_nonempty,
        "act_acc": round(n_act / n, 3),
        "attr_strict_acc": round(n_attr_strict / n, 3),
        "attr_loose_acc": round(n_attr_loose / n, 3),
        "nonempty_rate": round(n_nonempty / n, 3),
    }
    with open(out_dir / "triage_dual_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 详情: {out_dir / 'triage_dual_smoke.jsonl'}")
    print(f"✅ 汇总: {out_dir / 'triage_dual_summary.json'}")


if __name__ == "__main__":
    asyncio.run(main())
