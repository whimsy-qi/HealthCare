"""
MADDx D8 消融实验主脚本
======================
跑 4 组对照 × N 例病例，产出 CSV + JSON 用于论文表格：

  A. Single-LLM       直接单次 LLM 给 Top-3，无辩论
  B. MADDx-static     D7 现状：agent 间无工具，辩论走预取证据快照（空）
  C. MADDx-dynamic    D8 完整：agent 自主取证
  D. MADDx-critic-only D8 变体：仅 Critic 拿工具，Proposer/Defender 禁用

指标：
  - top1_correct          : primary == ground_truth.primary
  - top3_correct          : ground_truth.primary in [c.disease for c in Top-3]
  - tier2_top3_correct    : 命中宽松集（primary + acceptable_tier2）
  - evidence_citation_density : avg(len(c.evidence_refs) for c in Top-3)
  - avg_rounds            : 平均辩论轮数
  - avg_tool_calls        : 平均工具调用次数（仅 C/D）
  - termination_dist      : Rule 1/2/3/4 触发占比
  - avg_latency_sec       : 单例端到端耗时

用法：
  python -m experiments.run_ablation \\
      --dataset experiments/data/maddx_eval_100.jsonl \\
      --out experiments/results \\
      --conditions A,B,C,D \\
      --limit 0        # 0 = 全量
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
from collections import Counter

# 允许从 backend 根目录跑
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.blackboard import Blackboard
from core.llm_client import shared_client as client, DEFAULT_MODEL
from agents.maddx.workflow import run_maddx
from agents.maddx.tools import ToolRegistry

logger = logging.getLogger("D8.Ablation")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s | %(message)s',
    datefmt='%H:%M:%S',
)

# ---- 消融条件定义 ----

CONDITIONS = {
    "A": {"name": "Single-LLM",          "mode": "single_llm"},
    "B": {"name": "MADDx-static",        "mode": "maddx", "enabled_tools": []},
    "C": {"name": "MADDx-dynamic",       "mode": "maddx", "enabled_tools": None},   # None = 全开
    "D": {"name": "MADDx-critic-only",   "mode": "maddx",
          "enabled_tools": ["kg_query", "rag_search", "web_search"],
          "critic_only": True},
}


# =============================================================
# 基线：单次 LLM 直接给诊断（对照组 A）
# =============================================================

SINGLE_LLM_SYSTEM = """你是一位内科医师，基于患者信息给出 Top-3 鉴别诊断。
严格输出 JSON：
{
  "candidates": [
    {"disease": "...", "confidence": 0.0~1.0, "reasoning": "..."}
  ]
}
恰好 3 个候选，按 confidence 降序。
"""


async def run_single_llm(symptoms: list, profile: dict) -> Dict[str, Any]:
    user = (
        f"【症状】{json.dumps(symptoms, ensure_ascii=False)}\n"
        f"【档案】{json.dumps(profile, ensure_ascii=False)}\n"
        f"请给出 Top-3 鉴别诊断。"
    )
    resp = await client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": SINGLE_LLM_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    try:
        data = json.loads(resp.choices[0].message.content)
        candidates = data.get("candidates", [])[:3]
    except json.JSONDecodeError:
        candidates = []
    return {
        "primary_dx": candidates[0]["disease"] if candidates else "",
        "candidates": candidates,
        "rounds_used": 0,
        "total_tool_calls": 0,
        "total_evidence_hits": 0,
        "termination_reason": "SINGLE_SHOT",
    }


# =============================================================
# 消融：D（critic-only）需要在运行时 patch 其他 agent 的 ToolRegistry
# 实现方式：ToolRegistry 只对 critic 授权，其他 agent 传同一对象但白名单被收窄
#
# 当前 D8 架构下 ToolRegistry 是会话级单例，不易做 per-agent 权限。
# 简单实现：给 D 模式传一个自定义 registry，invoke 时检查 caller_agent，
# 若非 critic 则直接返回空 hits。
# =============================================================

class CriticOnlyToolRegistry(ToolRegistry):
    """仅 Critic 能真正调工具；其他 agent 的调用立即返回空。用于 D 组消融。"""
    async def invoke(self, tool, args, caller_agent, caller_round):
        if caller_agent != "critic":
            from core.blackboard_schema import ToolResult
            # 留痕但不执行
            call_entry = {"tool": tool, "args": args, "caller_agent": caller_agent, "caller_round": caller_round}
            call_v = await self.bb.append("tool_call_denied", call_entry, agent_id=caller_agent)
            result: ToolResult = {
                "call_ref": call_v, "tool": tool, "hits": [], "hit_count": 0, "cached": False,
            }
            return result
        return await super().invoke(tool, args, caller_agent, caller_round)


# =============================================================
# 评测主循环
# =============================================================

async def run_one_case(case: dict, condition_id: str) -> Dict[str, Any]:
    cond = CONDITIONS[condition_id]
    symptoms = case["symptoms"]
    profile = {
        "age": case.get("age"),
        "gender": case.get("gender"),
        "chronic_conditions": case.get("history", []) or [],
    }

    start_ts = time.time()
    bb = Blackboard(session_id=f"eval-{case['case_id']}-{condition_id}")

    try:
        if cond["mode"] == "single_llm":
            report = await run_single_llm(symptoms, profile)
        else:
            # MADDx
            enabled = cond.get("enabled_tools")
            if cond.get("critic_only"):
                tools = CriticOnlyToolRegistry(bb=bb, enabled=enabled)
            else:
                tools = ToolRegistry(bb=bb, enabled=enabled)
            report = await run_maddx(
                bb=bb, symptoms=symptoms, patient_profile=profile, tools=tools,
            )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"[{case['case_id']}|{condition_id}] error: {type(e).__name__}: {e}\n{tb}")
        return {
            "case_id": case["case_id"], "condition": condition_id,
            "error": f"{type(e).__name__}: {e}", "latency_sec": time.time() - start_ts,
        }

    # ---- 抽取 Top-3 ----
    if cond["mode"] == "single_llm":
        top3 = [c["disease"] for c in report.get("candidates", [])[:3]]
        evidence_density = 0.0
    else:
        # MADDx 的最终候选从 Blackboard 取（最新 candidate_dx）
        last = bb.latest("candidate_dx")
        final_candidates = last["value"] if last else []
        top3 = [c.get("disease", "") for c in final_candidates[:3]]
        refs_per_candidate = [len(c.get("evidence_refs", []) or []) for c in final_candidates[:3]]
        evidence_density = sum(refs_per_candidate) / max(1, len(refs_per_candidate))

    # ---- 命中判定 ----
    gt = case["ground_truth"]
    gt_primary = gt["primary"]
    gt_tier2 = set([gt_primary] + (gt.get("acceptable_tier2") or []))

    top1_correct = (top3[0] == gt_primary) if top3 else False
    top3_correct = gt_primary in top3
    tier2_top3_correct = any(d in gt_tier2 for d in top3)

    return {
        "case_id": case["case_id"],
        "department": case.get("department"),
        "condition": condition_id,
        "top1": top3[0] if top3 else "",
        "top3": top3,
        "gt_primary": gt_primary,
        "top1_correct": top1_correct,
        "top3_correct": top3_correct,
        "tier2_top3_correct": tier2_top3_correct,
        "evidence_density": evidence_density,
        "rounds_used": report.get("rounds_used", 0),
        "tool_calls": report.get("total_tool_calls", 0),
        "evidence_hits": report.get("total_evidence_hits", 0),
        "termination_reason": report.get("termination_reason", ""),
        "latency_sec": round(time.time() - start_ts, 2),
    }


def aggregate(results: List[Dict]) -> Dict[str, Dict]:
    """按 condition 聚合指标，返回 {cond_id: {metric: value}}."""
    agg: Dict[str, Dict] = {}
    by_cond: Dict[str, List[Dict]] = {}
    for r in results:
        if "error" in r:
            continue
        by_cond.setdefault(r["condition"], []).append(r)

    for cond_id, rows in by_cond.items():
        n = len(rows)
        if n == 0:
            continue
        term_counter = Counter(r["termination_reason"] for r in rows)
        agg[cond_id] = {
            "name": CONDITIONS[cond_id]["name"],
            "n": n,
            "top1_acc": sum(r["top1_correct"] for r in rows) / n,
            "top3_acc": sum(r["top3_correct"] for r in rows) / n,
            "tier2_top3_acc": sum(r["tier2_top3_correct"] for r in rows) / n,
            "avg_evidence_density": sum(r["evidence_density"] for r in rows) / n,
            "avg_rounds": sum(r["rounds_used"] for r in rows) / n,
            "avg_tool_calls": sum(r["tool_calls"] for r in rows) / n,
            "avg_evidence_hits": sum(r["evidence_hits"] for r in rows) / n,
            "avg_latency_sec": sum(r["latency_sec"] for r in rows) / n,
            "termination_dist": {k: v / n for k, v in term_counter.items()},
        }
    return agg


def print_summary_table(agg: Dict[str, Dict]):
    """在终端打印论文主表的预览。"""
    print()
    print("=" * 92)
    print(f"{'Cond':<4} {'Name':<20} {'N':>4} {'Top1':>6} {'Top3':>6} {'Tier2':>7} "
          f"{'EvDen':>6} {'Rnds':>5} {'Tools':>6} {'Lat(s)':>7}")
    print("-" * 92)
    for cid in ["A", "B", "C", "D"]:
        if cid not in agg:
            continue
        m = agg[cid]
        print(
            f"{cid:<4} {m['name']:<20} {m['n']:>4} "
            f"{m['top1_acc']*100:>5.1f}% {m['top3_acc']*100:>5.1f}% {m['tier2_top3_acc']*100:>6.1f}% "
            f"{m['avg_evidence_density']:>6.2f} {m['avg_rounds']:>5.2f} "
            f"{m['avg_tool_calls']:>6.2f} {m['avg_latency_sec']:>7.2f}"
        )
    print("=" * 92)
    print()


# =============================================================
# main
# =============================================================

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, help="JSONL 路径")
    ap.add_argument("--out", default="experiments/results", help="输出目录")
    ap.add_argument("--conditions", default="A,B,C,D", help="逗号分隔")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 例（0 = 全量）")
    ap.add_argument("--parallel", type=int, default=4, help="并发度（避免 LLM RPM 超限）")
    args = ap.parse_args()

    # 加载数据集
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

    tasks = []
    for case in cases:
        for cond_id in conds:
            tasks.append(asyncio.create_task(worker(case, cond_id)))
    logger.info(f"派发任务 {len(tasks)}（并发 {args.parallel}）")

    results: List[Dict] = []
    done = 0
    for coro in asyncio.as_completed(tasks):
        r = await coro
        results.append(r)
        done += 1
        if done % 10 == 0:
            logger.info(f"进度 {done}/{len(tasks)}")

    # 落盘明细
    details_path = Path(args.out) / "ablation_details.jsonl"
    with open(details_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # CSV
    csv_path = Path(args.out) / "ablation_details.csv"
    if results:
        keys = sorted({k for r in results for k in r.keys() if not isinstance(r[k], (list, dict))})
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in results:
                w.writerow({k: r.get(k, "") for k in keys})

    # 聚合
    agg = aggregate(results)
    agg_path = Path(args.out) / "ablation_summary.json"
    with open(agg_path, "w", encoding="utf-8") as f:
        json.dump(agg, f, ensure_ascii=False, indent=2)

    print_summary_table(agg)
    logger.info(f"详情 → {details_path}")
    logger.info(f"聚合 → {agg_path}")


if __name__ == "__main__":
    asyncio.run(main())
