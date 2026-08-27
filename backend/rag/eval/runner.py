from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from rag.service import retrieve_medical_evidence


TERM_ALIASES = {
    "二甲双胍": ["metformin"],
    "阿司匹林": ["aspirin"],
    "华法林": ["warfarin"],
    "布洛芬": ["ibuprofen"],
    "克拉霉素": ["clarithromycin"],
    "辛伐他汀": ["simvastatin"],
    "阿托伐他汀": ["atorvastatin"],
    "硝苯地平": ["nifedipine"],
    "出血": ["bleeding", "hemorrhage", "haemorrhage"],
    "相互作用": ["interaction", "interactions"],
    "不良反应": ["adverse reaction", "adverse reactions", "adverse event"],
    "禁忌": ["contraindication", "contraindications"],
    "肾功能": ["renal", "kidney"],
    "癌症": ["cancer", "tumor", "tumour", "oncology", "neoplasm"],
    "癌痛": ["cancer pain"],
    "疼痛": ["pain"],
    "证据": ["evidence", "systematic review", "meta-analysis", "trial"],
    "生酮": ["ketogenic", "keto"],
    "保健品": ["supplement", "dietary supplement", "nutraceutical"],
    "糖尿病": ["diabetes"],
    "疫苗": ["vaccine", "vaccination", "immunization"],
    "免疫": ["immune", "immunity"],
    "儿童": ["children", "pediatric", "paediatric"],
    "维生素A": ["vitamin a"],
    "维生素D": ["vitamin d"],
    "咳嗽": ["cough"],
    "胸痛": ["chest pain"],
    "低血糖": ["hypoglycemia", "hypoglycaemia"],
    "胰岛素": ["insulin"],
    "阿尔茨海默": ["alzheimer", "alzheimer's", "lecanemab", "donanemab", "aducanumab"],
    "临床试验": ["clinical trial", "clinical trials", "randomized trial", "randomised trial", "trial"],
    "新药": ["new drug", "novel therapy", "lecanemab", "donanemab", "aducanumab"],
}


def _load_jsonl(path: Path) -> List[Dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _contains_any(text: str, needles: List[str]) -> bool:
    lowered = (text or "").lower()
    for needle in needles:
        if not needle:
            continue
        candidates = [needle, *TERM_ALIASES.get(needle, [])]
        if any(candidate and candidate.lower() in lowered for candidate in candidates):
            return True
    return False


def _must_match_recall(text: str, needles: List[str]) -> float:
    if not needles:
        return 1.0
    lowered = (text or "").lower()
    hits = 0
    for needle in needles:
        candidates = [needle, *TERM_ALIASES.get(needle, [])]
        if any(candidate and candidate.lower() in lowered for candidate in candidates):
            hits += 1
    return hits / len(needles)


def _item_text(item) -> str:
    return f"{item.title}\n{item.text}"


def _item_relevant(item, case: Dict) -> bool:
    expected_sources = case.get("expected_source_id")
    if expected_sources:
        expected = set(expected_sources if isinstance(expected_sources, list) else [expected_sources])
        source_id = item.metadata.get("source_id")
        if source_id in expected:
            return True
    return _contains_any(_item_text(item), case.get("must_match", []))


def _mrr_at_k(result, case: Dict, k: int = 5) -> float:
    for idx, item in enumerate(result.items[:k], start=1):
        if _item_relevant(item, case):
            return 1.0 / idx
    return 0.0


def _ndcg_at_k(result, case: Dict, k: int = 5) -> float:
    gains = [1.0 if _item_relevant(item, case) else 0.0 for item in result.items[:k]]
    dcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal_hits = min(sum(1 for gain in gains if gain > 0), k)
    if ideal_hits == 0:
        return 0.0
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def _unanswerable_abstain(result, case: Dict) -> bool | None:
    if case.get("answerability") != "unanswerable":
        return None
    if not result.items:
        return True
    if result.debug.get("research_source_missing"):
        return True
    joined = "\n".join(_item_text(item) for item in result.items[:5])
    return not _contains_any(joined, case.get("must_match", []))


def _preferred_source_hit(result, preferred) -> bool:
    if result.intent == "latest_research":
        return not bool(result.debug.get("research_source_missing"))
    if not preferred:
        return True
    preferred_set = set(preferred if isinstance(preferred, list) else [preferred])
    return any(item.source_type in preferred_set for item in result.items[:5])


def _source_policy_hit(result, case: Dict) -> bool:
    expected_sources = case.get("expected_source_id")
    if expected_sources:
        expected = set(expected_sources if isinstance(expected_sources, list) else [expected_sources])
        return any(item.metadata.get("source_id") in expected for item in result.items[:5])
    return _preferred_source_hit(result, case.get("preferred_source_type"))


def _authority_tier_match(result, case: Dict) -> bool:
    expected_tiers = case.get("expected_authority_tier")
    if expected_tiers:
        expected = set(expected_tiers if isinstance(expected_tiers, list) else [expected_tiers])
        return any(item.source_tier in expected for item in result.items[:5])
    if result.intent in {"guideline_qa", "medication_safety", "latest_research", "rumor_check", "symptom_dx"}:
        return any(item.source_tier in {"T1", "T2"} for item in result.items[:5])
    return True


def _license_allowed_rate(result) -> float:
    if not result.items:
        return 0.0
    allowed = 0
    blocked_markers = {"unknown", "forbidden", "commercial_unlicensed"}
    for item in result.items:
        license_text = (item.license or item.metadata.get("license") or "").lower()
        if license_text and not any(marker in license_text for marker in blocked_markers):
            allowed += 1
    return allowed / len(result.items)


def _low_tier_override_error(result) -> bool:
    if not result.items:
        return False
    top = result.items[0]
    has_high_tier = any(item.source_tier in {"T1", "T2"} for item in result.items[:5])
    return has_high_tier and top.source_tier in {"T3", "T4"} and top.source_type not in {"drug_label"}


def _faers_causality_warning_rate(result) -> float:
    faers_items = [
        item for item in result.items
        if item.source_type == "drug_safety_signal" or item.metadata.get("source_id") == "fda_faers_signal"
    ]
    if not faers_items:
        return 1.0
    warned = sum(
        1 for item in faers_items
        if item.metadata.get("causality_not_established")
        or "causality" in item.text.lower()
        or "does not establish" in item.text.lower()
    )
    return warned / len(faers_items)


def _mojibake_rate(result) -> float:
    if not result.items:
        return 0.0
    bad_markers = ("å", "�", "\\x", "锟")
    return sum(1 for item in result.items if any(m in item.text for m in bad_markers)) / len(result.items)


async def _run_case(case: Dict) -> Dict:
    result = await retrieve_medical_evidence(
        case["query"],
        intent=case.get("intent"),
        filters=case.get("filters"),
        top_k=5,
    )
    joined = "\n".join(_item_text(i) for i in result.items)
    top1_text = _item_text(result.items[0]) if result.items else ""
    preferred_hit = _preferred_source_hit(result, case.get("preferred_source_type"))
    kg_items = [item for item in result.items[:5] if item.source_type == "kg"]
    graph_refs = [ref for ref in result.refs if ref.get("type") == "kg"]
    expect_graph = bool(case.get("expect_graph"))
    graph_path_valid = not expect_graph or bool(result.debug.get("graph_path_count", 0) > 0)
    graph_locator_valid = all(ref.get("locator", {}).get("neo4j_element_id") for ref in graph_refs)
    return {
        "id": case["id"],
        "query": case["query"],
        "intent": result.intent,
        "scenario": case.get("scenario", "unknown"),
        "risk_level": case.get("risk_level"),
        "answerability": case.get("answerability", "answerable"),
        "top1_source_accuracy": _contains_any(top1_text, case.get("must_match", [])),
        "top5_source_accuracy": _contains_any(joined, case.get("must_match", [])),
        "mrr@5": _mrr_at_k(result, case),
        "ndcg@5": _ndcg_at_k(result, case),
        "must_match_recall": _must_match_recall(joined, case.get("must_match", [])),
        "preferred_source_type_hit": preferred_hit,
        "source_policy_hit": _source_policy_hit(result, case),
        "authority_tier_match": _authority_tier_match(result, case),
        "license_allowed_rate": _license_allowed_rate(result),
        "low_tier_override_error": _low_tier_override_error(result),
        "faers_causality_warning_rate": _faers_causality_warning_rate(result),
        "citation_locator_valid_rate": sum(1 for r in result.refs if r.get("locator")) / max(len(result.refs), 1),
        "mojibake_rate": _mojibake_rate(result),
        "graph_candidate_recall@5": bool(kg_items) if expect_graph else None,
        "graph_path_valid_rate": graph_path_valid,
        "graph_locator_valid_rate": graph_locator_valid,
        "unanswerable_abstain": _unanswerable_abstain(result, case),
        "authority_evidence_closure_rate": bool(result.debug.get("authority_evidence_closure")) if kg_items else None,
        "kg_only_high_confidence_error_rate": bool(result.debug.get("kg_only_result")),
        "unsafe_kg_only_answer_rate": bool(result.debug.get("kg_only_result") and result.intent == "medication_safety"),
        "research_source_missing": bool(result.debug.get("research_source_missing")),
        "returned": len(result.items),
        "debug": result.debug,
    }


async def run_eval(path: Path) -> Dict:
    cases = _load_jsonl(path)
    rows = [await _run_case(case) for case in cases]
    graph_rows = [r for r in rows if r["graph_candidate_recall@5"] is not None]
    closure_rows = [r for r in rows if r["authority_evidence_closure_rate"] is not None]
    unanswerable_rows = [r for r in rows if r["unanswerable_abstain"] is not None]
    by_scenario = _group_metrics(rows, "scenario")
    return {
        "n": len(rows),
        "top1_source_accuracy": sum(r["top1_source_accuracy"] for r in rows) / max(len(rows), 1),
        "top5_source_accuracy": sum(r["top5_source_accuracy"] for r in rows) / max(len(rows), 1),
        "mrr@5": sum(r["mrr@5"] for r in rows) / max(len(rows), 1),
        "ndcg@5": sum(r["ndcg@5"] for r in rows) / max(len(rows), 1),
        "must_match_recall": sum(r["must_match_recall"] for r in rows) / max(len(rows), 1),
        "preferred_source_type_hit": sum(r["preferred_source_type_hit"] for r in rows) / max(len(rows), 1),
        "source_policy_hit": sum(r["source_policy_hit"] for r in rows) / max(len(rows), 1),
        "authority_tier_match": sum(r["authority_tier_match"] for r in rows) / max(len(rows), 1),
        "license_allowed_rate": sum(r["license_allowed_rate"] for r in rows) / max(len(rows), 1),
        "low_tier_override_error": sum(r["low_tier_override_error"] for r in rows) / max(len(rows), 1),
        "faers_causality_warning_rate": sum(r["faers_causality_warning_rate"] for r in rows) / max(len(rows), 1),
        "citation_locator_valid_rate": sum(r["citation_locator_valid_rate"] for r in rows) / max(len(rows), 1),
        "mojibake_rate": sum(r["mojibake_rate"] for r in rows) / max(len(rows), 1),
        "graph_candidate_recall@5": (
            sum(bool(r["graph_candidate_recall@5"]) for r in graph_rows) / max(len(graph_rows), 1)
            if graph_rows else None
        ),
        "graph_path_valid_rate": sum(r["graph_path_valid_rate"] for r in rows) / max(len(rows), 1),
        "graph_locator_valid_rate": sum(r["graph_locator_valid_rate"] for r in rows) / max(len(rows), 1),
        "unanswerable_abstain_rate": (
            sum(bool(r["unanswerable_abstain"]) for r in unanswerable_rows) / max(len(unanswerable_rows), 1)
            if unanswerable_rows else None
        ),
        "authority_evidence_closure_rate": (
            sum(bool(r["authority_evidence_closure_rate"]) for r in closure_rows) / max(len(closure_rows), 1)
            if closure_rows else None
        ),
        "kg_only_high_confidence_error_rate": sum(r["kg_only_high_confidence_error_rate"] for r in rows) / max(len(rows), 1),
        "unsafe_kg_only_answer_rate": sum(r["unsafe_kg_only_answer_rate"] for r in rows) / max(len(rows), 1),
        "by_scenario": by_scenario,
        "failures": _failure_examples(rows),
        "rows": rows,
    }


def _avg(rows: List[Dict], key: str) -> float:
    return sum(float(r.get(key) or 0.0) for r in rows) / max(len(rows), 1)


def _group_metrics(rows: List[Dict], key: str) -> Dict[str, Dict]:
    groups: Dict[str, List[Dict]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key) or "unknown"), []).append(row)
    summary = {}
    for group, items in sorted(groups.items()):
        graph_items = [r for r in items if r["graph_candidate_recall@5"] is not None]
        unanswerable_items = [r for r in items if r["unanswerable_abstain"] is not None]
        summary[group] = {
            "n": len(items),
            "top1_source_accuracy": _avg(items, "top1_source_accuracy"),
            "top5_source_accuracy": _avg(items, "top5_source_accuracy"),
            "mrr@5": _avg(items, "mrr@5"),
            "ndcg@5": _avg(items, "ndcg@5"),
            "must_match_recall": _avg(items, "must_match_recall"),
            "authority_tier_match": _avg(items, "authority_tier_match"),
            "citation_locator_valid_rate": _avg(items, "citation_locator_valid_rate"),
            "graph_candidate_recall@5": (
                sum(bool(r["graph_candidate_recall@5"]) for r in graph_items) / max(len(graph_items), 1)
                if graph_items else None
            ),
            "unanswerable_abstain_rate": (
                sum(bool(r["unanswerable_abstain"]) for r in unanswerable_items) / max(len(unanswerable_items), 1)
                if unanswerable_items else None
            ),
        }
    return summary


def _failure_examples(rows: List[Dict], limit: int = 30) -> List[Dict]:
    failures = [
        {
            "id": row["id"],
            "scenario": row.get("scenario"),
            "query": row["query"],
            "top1_source_accuracy": row["top1_source_accuracy"],
            "top5_source_accuracy": row["top5_source_accuracy"],
            "must_match_recall": row["must_match_recall"],
            "returned": row["returned"],
        }
        for row in rows
        if not row["top5_source_accuracy"] or row["must_match_recall"] < 1.0
    ]
    return failures[:limit]


def _write_report(path: Path, payload: Dict) -> None:
    lines = [
        "# Paper RAG Retrieval Evaluation",
        "",
        f"Suite: `{payload.get('suite')}`",
        f"Cases: {payload['n']}",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Top1 source accuracy | {payload['top1_source_accuracy']:.4f} |",
        f"| Top5 source accuracy | {payload['top5_source_accuracy']:.4f} |",
        f"| MRR@5 | {payload['mrr@5']:.4f} |",
        f"| NDCG@5 | {payload['ndcg@5']:.4f} |",
        f"| Must-match recall | {payload['must_match_recall']:.4f} |",
        f"| Authority tier match | {payload['authority_tier_match']:.4f} |",
        f"| Citation locator valid rate | {payload['citation_locator_valid_rate']:.4f} |",
        f"| Unanswerable abstain rate | {payload['unanswerable_abstain_rate']} |",
        f"| Graph candidate recall@5 | {payload['graph_candidate_recall@5']} |",
        "",
        "## By Scenario",
        "",
        "| Scenario | N | Top1 | Top5 | MRR@5 | NDCG@5 | Must-match recall | Graph recall@5 | Unanswerable abstain |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scenario, row in payload["by_scenario"].items():
        lines.append(
            f"| {scenario} | {row['n']} | {row['top1_source_accuracy']:.4f} | "
            f"{row['top5_source_accuracy']:.4f} | {row['mrr@5']:.4f} | "
            f"{row['ndcg@5']:.4f} | {row['must_match_recall']:.4f} | "
            f"{row['graph_candidate_recall@5']} | {row['unanswerable_abstain_rate']} |"
        )
    lines.extend(["", "## Failure Examples", ""])
    for failure in payload["failures"][:10]:
        lines.append(
            f"- `{failure['id']}` [{failure['scenario']}]: {failure['query']} "
            f"(top5={failure['top5_source_accuracy']}, recall={failure['must_match_recall']:.2f})"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Evaluate medical RAG retrieval quality.")
    parser.add_argument("--suite", default=str(Path(__file__).with_name("golden_queries.jsonl")))
    parser.add_argument("--out", default="", help="Optional JSON path for saving the full evaluation result.")
    parser.add_argument("--report", default="", help="Optional Markdown summary path.")
    args = parser.parse_args()
    result = asyncio.run(run_eval(Path(args.suite)))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "suite": str(Path(args.suite)),
        **result,
    }
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.report:
        _write_report(Path(args.report), payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
