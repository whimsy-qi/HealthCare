"""
D9 Rumor 消融实验主脚本
======================
4 组对照 × N 例谣言，产出 CSV + JSON + Reliability Diagram 数据。

对照设计：
  A. Single-LLM     裸 LLM 直接判（无工具 无辩论 无加权）
  B. Static-weight  完整辩论 + 均衡权重（uniform_weights=True）
  C. CTAEW (Ours)   完整流程：claim_type 自适应权重 + 辩论
  D. No-debate      走分类器 + 自适应权重，但 max_rounds=0，直接 Judge 空证据兜底

指标：
  - verdict_strict_acc  : verdict 精确匹配（4 类）
  - verdict_loose_acc   : "属实 vs 非属实" 的 2 分类准确率
  - belief_sign_acc     : belief 正负符号是否与 ground truth 一致
  - classify_type_acc   : R1 分类器命中 ground_truth_type 的比例
  - avg_confidence_correct / wrong : 置信度校准（Reliability Diagram 原料）
  - avg_rounds / avg_tool_calls / avg_evidence_hits / avg_latency_sec
  - termination_dist    : Rule A/B/C/D 触发占比

用法：
  python -m experiments.run_rumor_ablation \\
      --dataset experiments/data/rumor_eval_seed.jsonl \\
      --out experiments/results/rumor \\
      --conditions A,B,C,D \\
      --limit 0
"""
import os
import sys
import json
import csv
import time
import asyncio
import argparse
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from collections import Counter, defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.blackboard import Blackboard
from core.llm_client import shared_client as client
from agents.maddx.tools import ToolRegistry
from agents.rumor.workflow import run_rumor
from agents.rumor.claim_classifier import classify_claim
from agents.rumor.weight_policy import (
    resolve_weights, BELIEF_THRESHOLD_TRUE, BELIEF_THRESHOLD_FALSE,
)

logger = logging.getLogger("D9.RumorAblation")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

# ---- 4 组条件 ----

CONDITIONS = {
    "A": {"name": "Single-LLM",      "mode": "single_llm"},
    "B": {"name": "Static-weight",   "mode": "rumor", "uniform_weights": True,  "max_rounds": 2, "enabled_tools": None},
    "C": {"name": "CTAEW (Ours)",    "mode": "rumor", "uniform_weights": False, "max_rounds": 2, "enabled_tools": None},
    "D": {"name": "No-debate",       "mode": "rumor", "uniform_weights": False, "max_rounds": 0, "enabled_tools": None},
    "E": {"name": "R10 Risk-Routing","mode": "risk_routing"},  # R10：低风险走 Fast-Path，高风险走 CTAEW
}

VALID_VERDICTS = {"属实", "谣言", "误导", "尚无定论"}

# ---------------------------------------------------------------------
# Baseline A：单次 LLM 直接判（无工具、无辩论）
# ---------------------------------------------------------------------

SINGLE_LLM_SYSTEM = """你是医疗谣言审核员。请对给定"命题"给出判定。

【严格输出 JSON】
{
  "verdict": "属实|谣言|误导|尚无定论",
  "confidence": 0.0~1.0,
  "reasoning": "<40 字内说明>"
}

【verdict 定义】
- 属实：命题完全正确，有权威证据支持
- 谣言：命题完全错误，或属常见伪科学
- 误导：命题有一定事实基础但被夸大/简化，容易误解
- 尚无定论：当前证据不足以裁决
"""


async def run_single_llm_verdict(claim: str) -> Dict[str, Any]:
    t0 = time.time()
    try:
        resp = await client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": SINGLE_LLM_SYSTEM},
                {"role": "user", "content": f"命题：{claim}"},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
    except Exception as e:
        logger.warning(f"[SingleLLM] 异常: {e}")
        data = {}
    verdict = data.get("verdict") or "尚无定论"
    if verdict not in VALID_VERDICTS:
        verdict = "尚无定论"
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    # 用符号表达 belief（供统一计算 belief_sign_acc）
    belief = {"属实": +0.6, "谣言": -0.6, "误导": -0.3, "尚无定论": 0.0}.get(verdict, 0.0)
    return {
        "final_verdict": verdict,
        "confidence": conf,
        "belief_score": belief,
        "dissent_score": 0.0,
        "rounds_completed": 0,
        "total_tool_calls": 0,
        "total_evidence_hits": 0,
        "termination_reason": "SINGLE_SHOT",
        "classification": {"primary": None},  # A 组不分类
        "latency_sec": round(time.time() - t0, 2),
    }


# ---------------------------------------------------------------------
# 跑单例
# ---------------------------------------------------------------------

def _gt_belief_sign(verdict: str) -> int:
    """把 ground truth verdict 映射成 belief 符号（+1 / -1 / 0）。"""
    if verdict == "属实":
        return +1
    if verdict in ("谣言", "误导"):
        return -1
    return 0  # 尚无定论


def _pred_belief_sign(belief: float) -> int:
    if belief >= BELIEF_THRESHOLD_TRUE:
        return +1
    if belief <= BELIEF_THRESHOLD_FALSE:
        return -1
    return 0


async def run_one_case(case: dict, condition_id: str) -> Dict[str, Any]:
    cond = CONDITIONS[condition_id]
    claim = case["claim"]
    start_ts = time.time()

    try:
        if cond["mode"] == "single_llm":
            report = await run_single_llm_verdict(claim)
        elif cond["mode"] == "risk_routing":
            # R10：跑完整 Risk-Routing pipeline（分类 → 风险评估 → Fast-Path 或 CTAEW）
            from agents.rumor.integration import run_rumor_ctaew
            t0 = time.time()
            _md, trace, _logs = await run_rumor_ctaew(claim)
            ct = trace.get("rumor_ctaew", {})
            report = {
                "final_verdict":       ct.get("final_verdict") or "尚无定论",
                "confidence":          float(ct.get("confidence") or 0.0),
                "belief_score":        float(ct.get("belief_score") or 0.0),
                "dissent_score":       float(ct.get("dissent_score") or 0.0),
                "rounds_completed":    ct.get("rounds_completed") or 0,
                "total_tool_calls":    ct.get("total_tool_calls") or 0,
                "total_evidence_hits": ct.get("total_evidence_hits") or 0,
                "termination_reason":  ct.get("termination_reason") or "",
                "classification":      ct.get("classification") or {"primary": ct.get("claim_type")},
                "latency_sec":         round(time.time() - t0, 2),
            }
        else:
            bb = Blackboard(session_id=f"rumor-eval-{case['case_id']}-{condition_id}")
            report = await run_rumor(
                bb=bb,
                claim=claim,
                enabled_tools=cond.get("enabled_tools"),
                uniform_weights=cond.get("uniform_weights", False),
                max_rounds=cond.get("max_rounds", 2),
            )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"[{case['case_id']}|{condition_id}] error: {type(e).__name__}: {e}\n{tb}")
        return {
            "case_id": case["case_id"],
            "condition": condition_id,
            "error": f"{type(e).__name__}: {e}",
            "latency_sec": round(time.time() - start_ts, 2),
        }

    gt_verdict = case["ground_truth_verdict"]
    gt_type    = case["ground_truth_type"]
    gt_alts: list = case.get("acceptable_alt_verdicts") or []
    pred_verdict = report.get("final_verdict") or "尚无定论"
    pred_conf = float(report.get("confidence") or 0.0)
    pred_belief = float(report.get("belief_score") or 0.0)
    classification = report.get("classification") or {}
    pred_type = classification.get("primary")

    # 严格匹配：必须命中 primary ground truth
    verdict_strict = (pred_verdict == gt_verdict)

    # 宽松匹配：primary 或 acceptable_alt_verdicts 任一即算对
    # （数据集已人工标注"评估容忍集"，不再机械合并正负类）
    verdict_loose = (pred_verdict == gt_verdict) or (pred_verdict in gt_alts)

    # 符号一致性
    belief_sign_ok = (_pred_belief_sign(pred_belief) == _gt_belief_sign(gt_verdict))

    # 分类器命中（仅 B/C/D 有效）
    classify_ok = (pred_type == gt_type) if pred_type else None

    return {
        "case_id": case["case_id"],
        "condition": condition_id,
        "claim": claim,
        "gt_type": gt_type,
        "gt_verdict": gt_verdict,
        "pred_type": pred_type,
        "pred_verdict": pred_verdict,
        "pred_belief": round(pred_belief, 3),
        "pred_dissent": round(float(report.get("dissent_score") or 0.0), 3),
        "pred_confidence": round(pred_conf, 3),
        "verdict_strict_correct": verdict_strict,
        "verdict_loose_correct": verdict_loose,
        "belief_sign_correct": belief_sign_ok,
        "classify_type_correct": classify_ok,
        "rounds_used": report.get("rounds_completed", 0),
        "tool_calls": report.get("total_tool_calls", 0),
        "evidence_hits": report.get("total_evidence_hits", 0),
        "termination_reason": report.get("termination_reason", ""),
        "difficulty": case.get("difficulty", ""),
        "latency_sec": round(time.time() - start_ts, 2),
    }


# ---------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------

CONF_BINS = [(0.0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5),
             (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]


def aggregate(results: List[Dict]) -> Dict[str, Dict]:
    by_cond: Dict[str, List[Dict]] = defaultdict(list)
    for r in results:
        if "error" in r:
            continue
        by_cond[r["condition"]].append(r)

    agg: Dict[str, Dict] = {}
    for cid, rows in by_cond.items():
        n = len(rows)
        if n == 0:
            continue

        # 分类器准确率：只算有 pred_type 的
        cls_rows = [r for r in rows if r["classify_type_correct"] is not None]
        cls_acc = (sum(r["classify_type_correct"] for r in cls_rows) / len(cls_rows)) if cls_rows else None

        # Reliability Diagram 数据
        reliability = []
        for lo, hi in CONF_BINS:
            bucket = [r for r in rows if lo <= r["pred_confidence"] < hi]
            if not bucket:
                reliability.append({"range": f"[{lo:.1f},{hi:.2f})", "n": 0, "avg_conf": None, "acc": None})
                continue
            reliability.append({
                "range": f"[{lo:.1f},{hi:.2f})",
                "n": len(bucket),
                "avg_conf": round(sum(r["pred_confidence"] for r in bucket) / len(bucket), 3),
                "acc": round(sum(r["verdict_strict_correct"] for r in bucket) / len(bucket), 3),
            })

        # 置信度分对错均值（ECE 原料）
        corr_rows = [r for r in rows if r["verdict_strict_correct"]]
        wrong_rows = [r for r in rows if not r["verdict_strict_correct"]]
        avg_conf_correct = (sum(r["pred_confidence"] for r in corr_rows) / len(corr_rows)) if corr_rows else None
        avg_conf_wrong   = (sum(r["pred_confidence"] for r in wrong_rows) / len(wrong_rows)) if wrong_rows else None

        term_counter = Counter(r["termination_reason"] for r in rows)

        # Expected Calibration Error：对所有非空 bin 按样本数加权
        ece_num, ece_den = 0.0, 0
        for b in reliability:
            if b["n"] and b["avg_conf"] is not None and b["acc"] is not None:
                ece_num += b["n"] * abs(b["avg_conf"] - b["acc"])
                ece_den += b["n"]
        ece = (ece_num / ece_den) if ece_den else None

        # Wrong-but-confident：错的样本里 conf > 0.5 的比例（过度自信）
        overconfident_wrong = [r for r in wrong_rows if r["pred_confidence"] > 0.5]
        overconfident_rate = (len(overconfident_wrong) / len(wrong_rows)) if wrong_rows else None

        # Hard-subset accuracy：单独看 difficulty=hard 的子集
        hard_rows = [r for r in rows if r.get("difficulty") == "hard"]
        hard_loose_acc = (sum(r["verdict_loose_correct"] for r in hard_rows) / len(hard_rows)) if hard_rows else None

        # Abstention（拒答率）：pred_verdict = "尚无定论" 的比例
        abstain_rows = [r for r in rows if r["pred_verdict"] == "尚无定论"]
        abstain_rate = len(abstain_rows) / n

        agg[cid] = {
            "name": CONDITIONS[cid]["name"],
            "n": n,
            "verdict_strict_acc": round(sum(r["verdict_strict_correct"] for r in rows) / n, 3),
            "verdict_loose_acc":  round(sum(r["verdict_loose_correct"] for r in rows) / n, 3),
            "belief_sign_acc":    round(sum(r["belief_sign_correct"] for r in rows) / n, 3),
            "classify_type_acc":  round(cls_acc, 3) if cls_acc is not None else None,
            # —— 校准指标（答辩主打）——
            "ece":                round(ece, 3) if ece is not None else None,
            "avg_conf_correct":   round(avg_conf_correct, 3) if avg_conf_correct is not None else None,
            "avg_conf_wrong":     round(avg_conf_wrong, 3) if avg_conf_wrong is not None else None,
            "overconfident_wrong_rate": round(overconfident_rate, 3) if overconfident_rate is not None else None,
            # —— 拒答与难题子集 ——
            "abstain_rate":       round(abstain_rate, 3),
            "hard_loose_acc":     round(hard_loose_acc, 3) if hard_loose_acc is not None else None,
            "hard_n":             len(hard_rows),
            # —— 过程指标 ——
            "avg_rounds":         round(sum(r["rounds_used"] for r in rows) / n, 2),
            "avg_tool_calls":     round(sum(r["tool_calls"] for r in rows) / n, 2),
            "avg_evidence_hits":  round(sum(r["evidence_hits"] for r in rows) / n, 2),
            "avg_latency_sec":    round(sum(r["latency_sec"] for r in rows) / n, 2),
            "termination_dist":   {k: round(v / n, 3) for k, v in term_counter.items()},
            "reliability_bins":   reliability,
        }
    return agg


def print_summary_table(agg: Dict[str, Dict]):
    print()
    print("=" * 110)
    print(f"{'Cond':<4} {'Name':<18} {'N':>3} "
          f"{'Strict':>7} {'Loose':>7} {'Sign':>6} {'ClsTyp':>7} "
          f"{'Rnds':>5} {'Tools':>6} {'Hits':>5} {'Lat(s)':>7}")
    print("-" * 110)
    for cid in ("A", "B", "C", "D", "E"):
        if cid not in agg:
            continue
        m = agg[cid]
        cls_txt = f"{m['classify_type_acc']*100:>6.1f}%" if m["classify_type_acc"] is not None else "   --  "
        print(
            f"{cid:<4} {m['name']:<18} {m['n']:>3} "
            f"{m['verdict_strict_acc']*100:>6.1f}% {m['verdict_loose_acc']*100:>6.1f}% "
            f"{m['belief_sign_acc']*100:>5.1f}% {cls_txt} "
            f"{m['avg_rounds']:>5.2f} {m['avg_tool_calls']:>6.2f} "
            f"{m['avg_evidence_hits']:>5.1f} {m['avg_latency_sec']:>7.2f}"
        )
    print("=" * 110)
    print()


# ---------------------------------------------------------------------
# main
# ---------------------------------------------------------------------

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="experiments/data/rumor_eval_seed.jsonl")
    ap.add_argument("--out", default="experiments/results/rumor")
    ap.add_argument("--conditions", default="A,B,C,D")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--parallel", type=int, default=2,
                    help="并发度，默认 2（辩论涉及多轮 LLM+工具，过高会触发 RPM）")
    args = ap.parse_args()

    cases: List[dict] = []
    with open(args.dataset, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            cases.append(json.loads(line))
    if args.limit:
        cases = cases[: args.limit]
    logger.info(f"载入 {len(cases)} 例；条件 = {args.conditions}")

    Path(args.out).mkdir(parents=True, exist_ok=True)
    conds = [c.strip() for c in args.conditions.split(",") if c.strip()]

    sem = asyncio.Semaphore(args.parallel)

    async def worker(case, cond_id):
        async with sem:
            return await run_one_case(case, cond_id)

    tasks = [
        asyncio.create_task(worker(case, cid))
        for case in cases
        for cid in conds
    ]
    total = len(tasks)
    logger.info(f"派发任务 {total}（并发 {args.parallel}）")

    results: List[Dict] = []
    done = 0
    for coro in asyncio.as_completed(tasks):
        r = await coro
        results.append(r)
        done += 1
        if done % 5 == 0 or done == total:
            logger.info(f"进度 {done}/{total}")

    # 落盘明细
    details_jsonl = Path(args.out) / "rumor_ablation_details.jsonl"
    with open(details_jsonl, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # CSV
    csv_path = Path(args.out) / "rumor_ablation_details.csv"
    if results:
        keys = sorted({k for r in results for k in r.keys() if not isinstance(r[k], (list, dict))})
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in results:
                w.writerow({k: r.get(k, "") for k in keys})

    # 聚合
    agg = aggregate(results)
    with open(Path(args.out) / "rumor_ablation_summary.json", "w", encoding="utf-8") as f:
        json.dump(agg, f, ensure_ascii=False, indent=2)

    print_summary_table(agg)
    logger.info(f"详情 → {details_jsonl}")
    logger.info(f"聚合 → {Path(args.out) / 'rumor_ablation_summary.json'}")


if __name__ == "__main__":
    asyncio.run(main())
