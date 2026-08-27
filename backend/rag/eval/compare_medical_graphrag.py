from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from rag.service import retrieve_medical_evidence


CORE_QUERY_LIMITS = {
    "medication_safety": 8,
    "guideline_qa": 6,
    "general": 3,
    "symptom_dx": 3,
}

HIGH_RISK_INTENTS = {"medication_safety", "symptom_dx", "latest_research", "rumor_check"}
AUTHORITATIVE_TIERS = {"T1", "T2"}


def _load_queries(path: Path, limit: int | None) -> list[dict[str, Any]]:
    quotas = CORE_QUERY_LIMITS.copy()
    selected: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            item = json.loads(line)
            intent = str(item.get("intent") or "general")
            if limit is not None and len(selected) >= limit:
                break
            if quotas:
                if quotas.get(intent, 0) <= 0:
                    continue
                quotas[intent] -= 1
            selected.append(item)
            if quotas and all(value <= 0 for value in quotas.values()):
                break
    return selected


def _source_matches(source_type: str, preferred: Any) -> bool:
    aliases = {
        "guideline_pdf": "guideline",
        "drug_excel": "drug_label",
        "openfda_label": "drug_label",
        "daily_med": "drug_label",
    }
    normalized = aliases.get(source_type, source_type)
    if isinstance(preferred, list):
        return normalized in set(str(item) for item in preferred)
    if not preferred:
        return True
    return normalized == str(preferred)


def _item_dict(item) -> dict[str, Any]:
    return {
        "chunk_id": item.chunk_id,
        "doc_id": item.doc_id,
        "source_type": item.source_type,
        "source_tier": item.source_tier,
        "title": item.title,
        "section_title": item.section_title,
        "page_start": item.page_start,
        "page_end": item.page_end,
        "locator": item.locator,
        "scores": item.scores,
        "metadata": item.metadata,
        "text_preview": (item.text or "")[:240],
    }


def _eval_backend(query: dict[str, Any], result) -> dict[str, Any]:
    items = result.items
    intent = str(query.get("intent") or "general")
    preferred = query.get("preferred_source_type")
    top1_hit = bool(items) and _source_matches(items[0].source_type, preferred)
    top5_hit = any(_source_matches(item.source_type, preferred) for item in items[:5])
    locator_valid = all(bool(item.locator or item.page_start is not None or item.doc_id) for item in items[:5]) if items else False
    preferred_items = [item for item in items[:5] if _source_matches(item.source_type, preferred)]
    authority_tier_match = any(item.source_tier in AUTHORITATIVE_TIERS for item in preferred_items)
    low_tier_override = False
    if items:
        top_tier = items[0].source_tier
        better_preferred = any(
            _source_matches(item.source_type, preferred) and item.source_tier in {"T1", "T2"}
            for item in items[1:5]
        )
        low_tier_override = top_tier in {"T3", "T4"} and better_preferred
    policy_flags = result.debug.get("policy_flags", {})
    is_negative = bool(query.get("negative") or query.get("adversarial"))
    negative_rejected = bool(
        is_negative
        and (
            not items
            or policy_flags.get("unsafe_to_answer")
            or policy_flags.get("research_source_missing")
            or result.debug.get("unsafe_to_answer")
        )
    )
    unsafe_no_evidence = (
        intent in HIGH_RISK_INTENTS
        and not top5_hit
        and not bool(policy_flags.get("unsafe_to_answer"))
        and not bool(policy_flags.get("research_source_missing"))
    )
    failure_type = "ok"
    if not items:
        failure_type = "no_results"
    elif not top5_hit:
        if intent == "medication_safety" and not bool(policy_flags.get("unsafe_to_answer")):
            failure_type = "missing_drug_label_without_unsafe_flag"
        elif intent == "latest_research" and not bool(policy_flags.get("research_source_missing")):
            failure_type = "missing_research_without_missing_flag"
        else:
            failure_type = "preferred_source_not_in_top5"
    elif not locator_valid:
        failure_type = "locator_missing"
    elif low_tier_override:
        failure_type = "low_tier_override"
    elif not authority_tier_match:
        failure_type = "authority_tier_mismatch"
    return {
        "top1_source_hit": top1_hit,
        "top5_source_hit": top5_hit,
        "preferred_source_type_hit": top5_hit,
        "authority_tier_match": authority_tier_match,
        "locator_valid": locator_valid,
        "low_tier_override": low_tier_override,
        "unsafe_no_evidence_answer": unsafe_no_evidence,
        "negative_rejected": negative_rejected,
        "failure_type": failure_type,
        "policy_flags": policy_flags,
        "items": [_item_dict(item) for item in items[:8]],
        "debug": result.debug,
    }


async def _retrieve_with_backend(query: dict[str, Any], backend: str):
    previous_backend = os.environ.get("RAG_BACKEND")
    os.environ["RAG_BACKEND"] = backend
    try:
        return await retrieve_medical_evidence(
            query["query"],
            intent=query.get("intent"),
            top_k=8,
        )
    finally:
        if previous_backend is None:
            os.environ.pop("RAG_BACKEND", None)
        else:
            os.environ["RAG_BACKEND"] = previous_backend


async def run_async(args: argparse.Namespace) -> int:
    queries = _load_queries(Path(args.suite), args.limit)
    rows = []
    include_dashvector = bool(getattr(args, "include_dashvector", False))
    for query in queries:
        medical_result = await _retrieve_with_backend(query, "medical_graphrag")
        row = {
            "id": query.get("id"),
            "query": query.get("query"),
            "intent": query.get("intent"),
            "preferred_source_type": query.get("preferred_source_type"),
            "negative": bool(query.get("negative")),
            "adversarial": bool(query.get("adversarial")),
            "medical_graphrag": _eval_backend(query, medical_result),
        }
        if include_dashvector:
            dashvector_result = await _retrieve_with_backend(query, "dashvector")
            row["dashvector"] = _eval_backend(query, dashvector_result)
        rows.append(row)

    def avg(key: str, backend: str) -> float:
        if not rows:
            return 0.0
        return sum(1 for row in rows if row[backend][key]) / len(rows)

    def avg_negative(key: str, backend: str) -> float:
        negative_rows = [row for row in rows if row.get("negative") or row.get("adversarial")]
        if not negative_rows:
            return 0.0
        return sum(1 for row in negative_rows if row[backend][key]) / len(negative_rows)

    summary = {
        "n": len(rows),
        "medical_graphrag": {
            "top1_source_accuracy": avg("top1_source_hit", "medical_graphrag"),
            "top5_source_accuracy": avg("top5_source_hit", "medical_graphrag"),
            "preferred_source_type_hit": avg("preferred_source_type_hit", "medical_graphrag"),
            "authority_tier_match": avg("authority_tier_match", "medical_graphrag"),
            "citation_locator_valid_rate": avg("locator_valid", "medical_graphrag"),
            "low_tier_override_error": avg("low_tier_override", "medical_graphrag"),
            "unsafe_no_evidence_answer_rate": avg("unsafe_no_evidence_answer", "medical_graphrag"),
            "negative_rejection_rate": avg_negative("negative_rejected", "medical_graphrag"),
        },
        "failure_counts": {
            "medical_graphrag": {},
        },
    }
    if include_dashvector:
        summary["dashvector"] = {
            "top1_source_accuracy": avg("top1_source_hit", "dashvector"),
            "top5_source_accuracy": avg("top5_source_hit", "dashvector"),
            "preferred_source_type_hit": avg("preferred_source_type_hit", "dashvector"),
            "authority_tier_match": avg("authority_tier_match", "dashvector"),
            "citation_locator_valid_rate": avg("locator_valid", "dashvector"),
            "low_tier_override_error": avg("low_tier_override", "dashvector"),
            "unsafe_no_evidence_answer_rate": avg("unsafe_no_evidence_answer", "dashvector"),
            "negative_rejection_rate": avg_negative("negative_rejected", "dashvector"),
        }
        summary["failure_counts"]["dashvector"] = {}
    for backend in ("medical_graphrag", "dashvector") if include_dashvector else ("medical_graphrag",):
        counts: dict[str, int] = {}
        for row in rows:
            failure_type = row[backend]["failure_type"]
            counts[failure_type] = counts.get(failure_type, 0) + 1
        summary["failure_counts"][backend] = counts
    output = {"summary": summary, "rows": rows}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate medical-graphrag Milvus retrieval.")
    parser.add_argument("--suite", default="rag/eval/golden_queries.jsonl")
    parser.add_argument("--out", default="rag/reports/eval_runs/medical_graphrag_milvus_v4.json")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--include-dashvector", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
