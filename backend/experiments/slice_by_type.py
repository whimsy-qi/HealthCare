"""按 claim_type 切片对比 A vs C 在 strict / loose / overconfident_wrong 上的表现。"""
import json, sys, io
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DETAILS = Path(__file__).resolve().parent / "results" / "rumor_full" / "rumor_ablation_details.jsonl"

# rows[(cond, gt_type)] = list of dicts
bucket = defaultdict(list)
with DETAILS.open(encoding="utf-8") as f:
    for line in f:
        r = json.loads(line)
        bucket[(r["condition"], r["gt_type"])].append(r)

types = sorted({k[1] for k in bucket})
conds = ["A", "C"]

print(f"\n{'='*110}")
print(f"{'claim_type':<15} {'N':>3} │ "
      f"{'A_strict':>9} {'A_loose':>8} {'A_ow':>6} │ "
      f"{'C_strict':>9} {'C_loose':>8} {'C_ow':>6} │ "
      f"{'C-A strict':>10} {'C-A loose':>9} {'C-A ow':>7}")
print("─" * 110)

total = defaultdict(lambda: defaultdict(list))
for t in types:
    row = {}
    for cond in conds:
        rs = bucket.get((cond, t), [])
        if not rs:
            continue
        n = len(rs)
        strict = sum(r["verdict_strict_correct"] for r in rs) / n
        loose = sum(r["verdict_loose_correct"] for r in rs) / n
        wrong_rs = [r for r in rs if not r["verdict_loose_correct"]]
        overconf = (sum(1 for r in wrong_rs if r["pred_confidence"] > 0.5) / len(wrong_rs)) if wrong_rs else 0.0
        row[cond] = (n, strict, loose, overconf)
        total[cond]["strict"].append(strict)
        total[cond]["loose"].append(loose)
    if "A" in row and "C" in row:
        n = row["A"][0]
        a_s, a_l, a_o = row["A"][1], row["A"][2], row["A"][3]
        c_s, c_l, c_o = row["C"][1], row["C"][2], row["C"][3]
        d_s = c_s - a_s
        d_l = c_l - a_l
        d_o = c_o - a_o
        marker_strict = "🟢" if d_s > 0 else ("🔴" if d_s < 0 else "⚪")
        marker_loose = "🟢" if d_l > 0 else ("🔴" if d_l < 0 else "⚪")
        marker_ow = "🟢" if d_o < 0 else ("🔴" if d_o > 0 else "⚪")
        print(f"{t:<15} {n:>3} │ "
              f"{a_s*100:>8.1f}% {a_l*100:>7.1f}% {a_o*100:>5.1f}% │ "
              f"{c_s*100:>8.1f}% {c_l*100:>7.1f}% {c_o*100:>5.1f}% │ "
              f"{d_s*100:>+9.1f}pp{marker_strict} {d_l*100:>+7.1f}pp{marker_loose} {d_o*100:>+5.1f}pp{marker_ow}")

print("─" * 110)

# 局部胜利扫描
print("\n【C 相对 A 的局部胜利（按 loose_acc 排）】")
wins = []
for t in types:
    a = bucket.get(("A", t), [])
    c = bucket.get(("C", t), [])
    if not a or not c:
        continue
    a_loose = sum(r["verdict_loose_correct"] for r in a) / len(a)
    c_loose = sum(r["verdict_loose_correct"] for r in c) / len(c)
    wins.append((t, len(a), c_loose - a_loose, a_loose, c_loose))
wins.sort(key=lambda x: x[2], reverse=True)
for t, n, d, al, cl in wins:
    flag = "✅ C 胜" if d > 0 else ("❌ C 负" if d < 0 else "⚪ 持平")
    print(f"  {flag}  {t:<15} n={n}  A={al*100:>5.1f}%  C={cl*100:>5.1f}%  Δ={d*100:+.1f}pp")

# A 过度自信错判的 case
print("\n【A 的 overconfident_wrong case list（conf > 0.5 但 loose 错）】")
a_ow_cases = [r for r in (bucket.get(("A", t), []) for t in types) for r in r if not r["verdict_loose_correct"] and r["pred_confidence"] > 0.5]
for r in a_ow_cases:
    print(f"  · {r['case_id']} ({r['gt_type']}) conf={r['pred_confidence']}  "
          f"pred={r['pred_verdict']}  gt={r['gt_verdict']}  claim={r['claim'][:28]}…")

# C 弃权但 A 答对的 case
print("\n【C 弃权（尚无定论）但 A 答对的 case】（→ 这类是 C 过度保守的证据）")
for t in types:
    a = {r["case_id"]: r for r in bucket.get(("A", t), [])}
    c = {r["case_id"]: r for r in bucket.get(("C", t), [])}
    for cid, cr in c.items():
        if cr["pred_verdict"] == "尚无定论" and a.get(cid, {}).get("verdict_loose_correct"):
            print(f"  · {cid} ({cr['gt_type']}) A={a[cid]['pred_verdict']}(✓) C=尚无定论  "
                  f"claim={cr['claim'][:28]}…")

# C 答对但 A 答错的 case
print("\n【C 答对（loose）但 A 答错的 case】（→ C 的结构性胜利证据）")
for t in types:
    a = {r["case_id"]: r for r in bucket.get(("A", t), [])}
    c = {r["case_id"]: r for r in bucket.get(("C", t), [])}
    for cid, cr in c.items():
        if cr["verdict_loose_correct"] and not a.get(cid, {}).get("verdict_loose_correct", True):
            print(f"  · {cid} ({cr['gt_type']}) A={a[cid]['pred_verdict']}(✗) C={cr['pred_verdict']}(✓)  "
                  f"claim={cr['claim'][:28]}…")
