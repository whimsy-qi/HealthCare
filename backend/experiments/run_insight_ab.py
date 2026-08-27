"""
🧠 Insight Memory A/B Experiment（方案 C）
==========================================

实验设计（cross-validation 风格）：

  1. 清空见解知识库
  2. **Phase POPULATE（养库）**：跑 25 例【原版】rumor seed
       → 触发 fire-and-forget harvest，自然积累 25 条 insights
  3. 等收割完成
  4. **Phase A — Cold（基线）**：清空库 → 跑 25 例【改写版】
       记录每例的 [latency, halluc_action, loose_correct, conf, hit_count]
  5. **Phase B — Warm（实验）**：用 25 原版重新养库 → 跑同一批 25 改写版
       记录同样指标。改写版与库里原版语义高度相似，应触发 insight 注入

  对比 A vs B：
    - latency 下降幅度（hit 后 judge 不必反复犹豫）
    - loose_acc 提升（参考相似先验，避免重蹈覆辙）
    - hit_rate（命中相似 insight 的比例）

  N=50 综合（25 原 + 25 改）也作为 R10 ablation 的扩样数据，统计显著性 ↑

输出：experiments/results/insight_ab/
  - cold.jsonl / warm.jsonl
  - summary.json
  - 控制台对比表
"""
import asyncio
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.rumor.integration import run_rumor_ctaew  # noqa: E402
from agents.rumor.weight_policy import (
    BELIEF_THRESHOLD_TRUE, BELIEF_THRESHOLD_FALSE,
)  # noqa: E402
from core.insight_memory import DB_PATH  # noqa: E402


VALID_VERDICTS = {"属实", "谣言", "误导", "尚无定论"}


def _load_cases(path: Path) -> List[dict]:
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            cases.append(json.loads(line))
    return cases


def _clear_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM insights")
    conn.commit()
    conn.close()


def _db_count():
    conn = sqlite3.connect(DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM insights").fetchone()[0]
    conn.close()
    return n


def _gt_belief_sign(verdict: str) -> int:
    if verdict == "属实": return +1
    if verdict in ("谣言", "误导"): return -1
    return 0


def _pred_belief_sign(belief: float) -> int:
    if belief >= BELIEF_THRESHOLD_TRUE: return +1
    if belief <= BELIEF_THRESHOLD_FALSE: return -1
    return 0


async def _run_one(case: dict, user_id: Optional[int] = None) -> Dict[str, Any]:
    """跑单例并提取关键指标。"""
    t0 = time.time()
    try:
        _md, trace, logs = await run_rumor_ctaew(
            query=case["claim"], user_id=user_id,
        )
    except Exception as e:
        return {
            "case_id": case["case_id"], "error": f"{type(e).__name__}: {e}",
            "latency_sec": round(time.time() - t0, 2),
        }
    elapsed = round(time.time() - t0, 2)

    ct = trace.get("rumor_ctaew", {}) or {}
    halluc = trace.get("hallucination_check", {}) or {}

    pred_verdict = ct.get("final_verdict") or "尚无定论"
    if pred_verdict not in VALID_VERDICTS:
        pred_verdict = "尚无定论"
    pred_conf = float(ct.get("confidence") or 0.0)
    pred_belief = float(ct.get("belief_score") or 0.0)

    gt_verdict = case["ground_truth_verdict"]
    gt_alts = case.get("acceptable_alt_verdicts") or []

    strict_correct = (pred_verdict == gt_verdict)
    loose_correct = strict_correct or (pred_verdict in gt_alts)
    sign_correct = (_pred_belief_sign(pred_belief) == _gt_belief_sign(gt_verdict))

    # 判断本次是否命中 insight（看 audit_logs）
    insight_hit = any("[Insight] rumor 检索命中" in ln for ln in logs)
    insight_hit_count = 0
    for ln in logs:
        if "[Insight] rumor 检索命中" in ln:
            # 解析 "命中 N 条"
            try:
                insight_hit_count = int(ln.split("命中")[1].split("条")[0].strip())
            except Exception:
                insight_hit_count = 1

    return {
        "case_id": case["case_id"],
        "claim": case["claim"][:80],
        "gt_verdict": gt_verdict,
        "pred_verdict": pred_verdict,
        "pred_confidence": round(pred_conf, 3),
        "pred_belief": round(pred_belief, 3),
        "verdict_strict_correct": strict_correct,
        "verdict_loose_correct": loose_correct,
        "belief_sign_correct": sign_correct,
        "halluc_action": halluc.get("action", ""),
        "halluc_score": float(halluc.get("hallucination_score", 0.0)),
        "termination_reason": ct.get("termination_reason", ""),
        "rounds": ct.get("rounds_completed", 0),
        "tool_calls": ct.get("total_tool_calls", 0),
        "evidence_hits": ct.get("total_evidence_hits", 0),
        "insight_hit": insight_hit,
        "insight_hit_count": insight_hit_count,
        "latency_sec": elapsed,
    }


async def _populate_with_originals(originals: List[dict]):
    """跑 25 原版，让 fire-and-forget 自然积累。"""
    print("\n▶ Populate phase: 跑 25 原版以积累共享桶 insights ...")
    sem = asyncio.Semaphore(2)
    async def _w(c):
        async with sem:
            return await _run_one(c, user_id=None)
    results = await asyncio.gather(*(_w(c) for c in originals))
    # 等所有 fire-and-forget harvest 完成（每条 ~3-5s 嵌入 + 入库）
    print("   ⏳ 等待 30s 让 harvest 完成 ...")
    await asyncio.sleep(30)
    print(f"   库当前条目数: {_db_count()}")
    return results


async def run_phase(name: str, paraphrased: List[dict],
                    user_id: Optional[int] = None) -> List[dict]:
    print(f"\n▶ Phase {name}: 跑 25 改写版 ...")
    sem = asyncio.Semaphore(2)
    progress = {"done": 0}
    total = len(paraphrased)

    async def _w(c):
        async with sem:
            r = await _run_one(c, user_id=user_id)
            progress["done"] += 1
            print(f"   [{progress['done']:>2}/{total}] {c['case_id']}: "
                  f"verdict={r.get('pred_verdict')} "
                  f"loose={r.get('verdict_loose_correct')} "
                  f"hit={r.get('insight_hit_count', 0)} "
                  f"halluc={r.get('halluc_action')} "
                  f"({r.get('latency_sec')}s)")
            return r
    return await asyncio.gather(*(_w(c) for c in paraphrased))


def aggregate(results: List[Dict]) -> Dict[str, Any]:
    rows = [r for r in results if "error" not in r]
    n = len(rows)
    if n == 0:
        return {"n": 0}
    return {
        "n": n,
        "verdict_strict_acc":  round(sum(r["verdict_strict_correct"] for r in rows) / n, 3),
        "verdict_loose_acc":   round(sum(r["verdict_loose_correct"] for r in rows) / n, 3),
        "belief_sign_acc":     round(sum(r["belief_sign_correct"] for r in rows) / n, 3),
        "avg_latency_sec":     round(sum(r["latency_sec"] for r in rows) / n, 2),
        "avg_rounds":          round(sum(r["rounds"] for r in rows) / n, 2),
        "avg_tool_calls":      round(sum(r["tool_calls"] for r in rows) / n, 2),
        "avg_evidence_hits":   round(sum(r["evidence_hits"] for r in rows) / n, 2),
        "avg_halluc_score":    round(sum(r["halluc_score"] for r in rows) / n, 3),
        "insight_hit_rate":    round(sum(1 for r in rows if r["insight_hit"]) / n, 3),
        "avg_insight_hits":    round(sum(r["insight_hit_count"] for r in rows) / n, 2),
        "abstain_rate":        round(sum(1 for r in rows if r["pred_verdict"] == "尚无定论") / n, 3),
    }


def print_compare_table(cold: dict, warm: dict):
    print("\n" + "=" * 74)
    print("Cold（库空）vs Warm（库有 25 条原版相关 insights）")
    print("=" * 74)
    fields = [
        ("verdict_strict_acc",  "严格准确率",   "pct"),
        ("verdict_loose_acc",   "宽松准确率",   "pct"),
        ("belief_sign_acc",     "belief 符号正确率", "pct"),
        ("abstain_rate",        "弃答率",      "pct"),
        ("avg_halluc_score",    "平均幻觉分 ↓", "f3"),
        ("insight_hit_rate",    "见解命中率 ↑", "pct"),
        ("avg_insight_hits",    "平均命中条数",  "f3"),
        ("avg_latency_sec",     "平均延迟(s) ↓", "f3"),
        ("avg_rounds",          "平均辩论轮次",  "f3"),
        ("avg_tool_calls",      "平均工具调用",  "f3"),
    ]
    print(f"{'指标':<22}{'Cold':>14}{'Warm':>14}{'Δ':>14}")
    print("-" * 74)
    for k, name, fmt in fields:
        cv = cold.get(k); wv = warm.get(k)
        def _fmt(v):
            if v is None: return "  --  "
            return f"{v*100:.1f}%" if fmt == "pct" else f"{v:.3f}"
        if cv is None or wv is None:
            delta = "  --  "
        else:
            d = wv - cv
            delta = f"{d*100:+.1f}pp" if fmt == "pct" else f"{d:+.3f}"
        print(f"{name:<20}{_fmt(cv):>14}{_fmt(wv):>14}{delta:>14}")


async def main():
    out_dir = Path(__file__).resolve().parent / "results" / "insight_ab"
    out_dir.mkdir(parents=True, exist_ok=True)

    seed = _load_cases(Path(__file__).resolve().parent / "data" / "rumor_eval_seed.jsonl")
    para = _load_cases(Path(__file__).resolve().parent / "data" / "rumor_eval_paraphrased.jsonl")
    assert len(seed) == len(para) == 25, f"期望 25/25，实际 {len(seed)}/{len(para)}"

    print("=" * 74)
    print("🧠 Insight Memory A/B Experiment (方案 C)")
    print(f"   seed: {len(seed)} 例 | paraphrased: {len(para)} 例")
    print("=" * 74)

    # ===== COLD：清库 → 跑改写版 =====
    print("\n[1/4] 🧊 Cold 阶段：清库后跑改写版（基线）")
    _clear_db()
    print(f"   清库后 DB 条目数: {_db_count()}")
    cold_results = await run_phase("COLD", para, user_id=None)
    cold_agg = aggregate(cold_results)

    # ===== POPULATE：清库 → 跑原版 =====
    print("\n[2/4] 🔥 Populate 阶段：清库后跑原版以养库")
    _clear_db()
    populate_results = await _populate_with_originals(seed)
    print(f"   养库后 DB 条目数: {_db_count()}")

    # ===== WARM：库已养 → 跑改写版 =====
    print("\n[3/4] 🔥 Warm 阶段：库已有 25 条原版 → 跑改写版")
    warm_results = await run_phase("WARM", para, user_id=None)
    warm_agg = aggregate(warm_results)

    # ===== 落盘 =====
    print("\n[4/4] 💾 落盘")
    with open(out_dir / "cold.jsonl", "w", encoding="utf-8") as f:
        for r in cold_results: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out_dir / "warm.jsonl", "w", encoding="utf-8") as f:
        for r in warm_results: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out_dir / "populate.jsonl", "w", encoding="utf-8") as f:
        for r in populate_results: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {"cold": cold_agg, "warm": warm_agg,
               "diff": {k: round(warm_agg.get(k, 0) - cold_agg.get(k, 0), 4)
                        for k in cold_agg if isinstance(cold_agg.get(k), (int, float))}}
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"   → {out_dir / 'summary.json'}")

    print_compare_table(cold_agg, warm_agg)
    print("\n✅ A/B 实验完成。")


if __name__ == "__main__":
    asyncio.run(main())
