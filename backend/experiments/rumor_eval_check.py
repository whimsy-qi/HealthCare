"""检查 rumor_eval_seed.jsonl 的 schema 合法性与分布均衡性。"""
import sys, io, json
from pathlib import Path
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from agents.rumor.weight_policy import ALL_CLAIM_TYPES

DATA = Path(__file__).resolve().parent / "data" / "rumor_eval_seed.jsonl"
VALID_VERDICTS = {"属实", "谣言", "误导", "尚无定论"}
REQUIRED_KEYS = {"case_id", "claim", "ground_truth_type", "ground_truth_verdict",
                 "rationale", "source", "difficulty"}


def main():
    cases = []
    errors = []
    with DATA.open(encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"L{i}: JSON 解析失败 - {e}")
                continue
            missing = REQUIRED_KEYS - c.keys()
            if missing:
                errors.append(f"{c.get('case_id','?')}: 缺字段 {missing}")
            if c.get("ground_truth_type") not in ALL_CLAIM_TYPES:
                errors.append(f"{c.get('case_id')}: 非法 claim_type = {c.get('ground_truth_type')}")
            if c.get("ground_truth_verdict") not in VALID_VERDICTS:
                errors.append(f"{c.get('case_id')}: 非法 verdict = {c.get('ground_truth_verdict')}")
            for alt in (c.get("acceptable_alt_verdicts") or []):
                if alt not in VALID_VERDICTS:
                    errors.append(f"{c.get('case_id')}: 非法 alt_verdict = {alt}")
                if alt == c.get("ground_truth_verdict"):
                    errors.append(f"{c.get('case_id')}: alt_verdict 与 primary 重复 = {alt}")
            cases.append(c)

    print(f"载入 {len(cases)} 条样本\n")
    if errors:
        print("❌ 发现错误：")
        for e in errors:
            print(f"  - {e}")
        return

    # 分布统计
    type_dist = Counter(c["ground_truth_type"] for c in cases)
    verd_dist = Counter(c["ground_truth_verdict"] for c in cases)
    diff_dist = Counter(c["difficulty"] for c in cases)

    print("【claim_type 分布】")
    for t in ALL_CLAIM_TYPES:
        if t == "UNKNOWN":
            continue
        n = type_dist.get(t, 0)
        bar = "█" * n
        print(f"  {t:<14} {n:>2}  {bar}")

    print("\n【verdict 分布】")
    for v, n in verd_dist.most_common():
        bar = "█" * n
        print(f"  {v:<6} {n:>2}  {bar}")

    print("\n【difficulty 分布】")
    for d, n in diff_dist.most_common():
        print(f"  {d:<8} {n:>2}")

    # ID 唯一性
    ids = [c["case_id"] for c in cases]
    if len(set(ids)) != len(ids):
        print("\n⚠️ case_id 存在重复！")
    else:
        print(f"\n✅ case_id 全部唯一（{len(ids)} 条）")


if __name__ == "__main__":
    main()
