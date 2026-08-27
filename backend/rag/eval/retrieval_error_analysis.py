from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag.eval.runner import _contains_any as _eval_contains_any
from rag.eval.runner import _load_jsonl, _preferred_source_hit
from rag.service import retrieve_medical_evidence


def classify_failure(case: dict[str, Any], result) -> str:
    top_text = "\n".join(f"{item.title}\n{item.section_title}\n{item.text}" for item in result.items[:5])
    preferred_hit = _preferred_source_hit(result, case.get("preferred_source_type"))
    top5_match = _eval_contains_any(top_text, case.get("must_match", []))
    ranked = result.debug.get("ranked_candidates_top20") or []
    selected_ids = {row.get("chunk_id") for row in result.debug.get("selected_top") or []}
    quota_dropped = set(result.debug.get("quota_dropped") or [])

    if preferred_hit and top5_match:
        return "ok"
    if not ranked:
        return "no_recall_or_backend_unavailable"
    if not preferred_hit and not result.debug.get("collection_hits"):
        return "data_not_ingested_or_collection_unavailable"
    if not preferred_hit:
        return "preferred_source_missing"
    if any(row.get("chunk_id") in quota_dropped for row in ranked[:10]):
        return "source_quota_dropped_candidate"
    if any(row.get("chunk_id") not in selected_ids for row in ranked[:10]):
        return "rerank_or_quota_ranked_candidate_not_selected"
    return "query_or_section_mismatch"


async def analyze_suite(path: Path, *, top_k: int = 5) -> dict:
    cases = _load_jsonl(path)
    rows = []
    for case in cases:
        result = await retrieve_medical_evidence(
            case["query"],
            intent=case.get("intent"),
            filters=case.get("filters"),
            top_k=top_k,
        )
        rows.append({
            "id": case["id"],
            "query": case["query"],
            "intent": result.intent,
            "failure_type": classify_failure(case, result),
            "preferred_source_type": case.get("preferred_source_type"),
            "must_match": case.get("must_match", []),
            "returned": len(result.items),
            "final_top": result.debug.get("selected_top", []),
            "raw_top20": result.debug.get("ranked_candidates_top20", []),
            "collection_hits": result.debug.get("collection_hits", {}),
            "source_quota": result.debug.get("source_quota", {}),
            "quota_filled": result.debug.get("quota_filled", {}),
            "quota_dropped": result.debug.get("quota_dropped", []),
            "drug_query_aliases": result.debug.get("drug_query_aliases", []),
            "research_source_missing": result.debug.get("research_source_missing"),
            "unsafe_to_answer": result.debug.get("unsafe_to_answer"),
        })
    summary: dict[str, int] = {}
    for row in rows:
        key = row["failure_type"]
        summary[key] = summary.get(key, 0) + 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "suite": str(path),
        "n": len(rows),
        "summary": summary,
        "rows": rows,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Analyze RAG retrieval failures without writing to vector stores.")
    parser.add_argument("--suite", default=str(Path(__file__).with_name("golden_queries.jsonl")))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "reports" / "retrieval_error_analysis" / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"),
    )
    args = parser.parse_args()
    report = asyncio.run(analyze_suite(Path(args.suite), top_k=args.top_k))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
