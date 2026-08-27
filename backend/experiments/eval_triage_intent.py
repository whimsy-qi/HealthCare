import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_ROOT / ".env")
except Exception:
    pass

triage_query = None

def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    return rows


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _safe_div(num: float, den: float) -> float:
    return round(num / den, 4) if den else 0.0


def _predicted_intent_set(result: Dict[str, Any]) -> Set[str]:
    intents = result.get("intents") or []
    values = set()
    if isinstance(intents, list):
        for item in intents:
            if isinstance(item, dict):
                domain = _norm(item.get("domain") or item.get("primary_intent") or item.get("intent"))
                if domain:
                    values.add(domain)
    primary = _norm(result.get("primary_intent"))
    if primary:
        values.add(primary)
    return values


def _case_bucket(case_id: str) -> str:
    parts = case_id.split("_")
    return parts[1] if len(parts) >= 3 else "unknown"


def _macro_f1(expected: List[str], predicted: List[str]) -> float:
    labels = sorted(set(expected) | set(predicted))
    if not labels:
        return 0.0
    f1_values = []
    for label in labels:
        tp = sum(1 for e, p in zip(expected, predicted) if e == label and p == label)
        fp = sum(1 for e, p in zip(expected, predicted) if e != label and p == label)
        fn = sum(1 for e, p in zip(expected, predicted) if e == label and p != label)
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, tp + fn)
        f1_values.append(_safe_div(2 * precision * recall, precision + recall))
    return round(sum(f1_values) / len(f1_values), 4)


def _accuracy(rows: Iterable[Dict[str, Any]], expected_key: str, predicted_key: str) -> float:
    scored = [row for row in rows if row.get(expected_key)]
    if not scored:
        return 0.0
    return _safe_div(
        sum(1 for row in scored if row.get(expected_key) == row.get(predicted_key)),
        len(scored),
    )


async def _evaluate_case(case: Dict[str, Any]) -> Dict[str, Any]:
    result = await triage_query(case["query"], history=[], has_image=bool(case.get("has_image", False)))
    expected_multi = {_norm(item) for item in case.get("expected_multi_intents") or [] if _norm(item)}
    predicted_multi = _predicted_intent_set(result)
    return {
        "case_id": case["case_id"],
        "case_bucket": _case_bucket(case["case_id"]),
        "query": case["query"],
        "has_image": bool(case.get("has_image", False)),
        "expected_primary_intent": _norm(case.get("expected_primary_intent")),
        "predicted_primary_intent": _norm(result.get("primary_intent")),
        "expected_sub_intent": _norm(case.get("expected_sub_intent")),
        "predicted_sub_intent": _norm(result.get("sub_intent")),
        "expected_act": _norm(case.get("expected_act")),
        "predicted_act": _norm(result.get("act_intent")),
        "expected_attr": _norm(case.get("expected_attr")),
        "predicted_attr": _norm(result.get("attr_intent")),
        "expected_multi_intents": sorted(expected_multi),
        "predicted_multi_intents": sorted(predicted_multi),
        "multi_exact_match": expected_multi == predicted_multi,
        "multi_partial_match": bool(expected_multi & predicted_multi),
        "raw_result": result,
    }


def _confusion_matrix(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    matrix: Dict[str, Dict[str, int]] = defaultdict(dict)
    for row in rows:
        exp = row["expected_primary_intent"]
        pred = row["predicted_primary_intent"]
        matrix[exp][pred] = matrix[exp].get(pred, 0) + 1
    return {label: dict(sorted(preds.items())) for label, preds in sorted(matrix.items())}


def _high_risk_errors(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    risky_pairs = {
        ("MEDICATION_REVIEW", "GENERAL_CONSULTATION"): "medication_to_general",
        ("SYMPTOM_ANALYSIS", "CHITCHAT_OR_REJECT"): "symptom_to_chitchat",
        ("SYMPTOM_ANALYSIS", "GENERAL_CONSULTATION"): "symptom_to_general",
        ("REPORT_INTERPRETATION", "GENERAL_CONSULTATION"): "report_to_general",
    }
    errors = []
    for row in rows:
        expected = row["expected_primary_intent"]
        predicted = row["predicted_primary_intent"]
        reason = risky_pairs.get((expected, predicted))
        if len(row["expected_multi_intents"]) > 1 and not row["multi_partial_match"]:
            reason = "multi_intent_missing"
        if row["has_image"] and "VISION_ANALYSIS" not in str(row.get("raw_result", {})):
            reason = reason or "image_context_not_marked"
        if reason:
            errors.append(
                {
                    "case_id": row["case_id"],
                    "reason": reason,
                    "query": row["query"],
                    "expected": expected,
                    "predicted": predicted,
                    "expected_multi": row["expected_multi_intents"],
                    "predicted_multi": row["predicted_multi_intents"],
                }
            )
    return errors


def _is_triage_fallback(row: Dict[str, Any]) -> bool:
    thinking = str((row.get("raw_result") or {}).get("thinking") or "")
    return "解析失败" in thinking


def _summarize(details: List[Dict[str, Any]]) -> Dict[str, Any]:
    expected_primary = [row["expected_primary_intent"] for row in details]
    predicted_primary = [row["predicted_primary_intent"] for row in details]
    multi_rows = [row for row in details if len(row["expected_multi_intents"]) > 1]
    return {
        "n_cases": len(details),
        "primary_intent_accuracy": _accuracy(details, "expected_primary_intent", "predicted_primary_intent"),
        "sub_intent_accuracy": _accuracy(details, "expected_sub_intent", "predicted_sub_intent"),
        "act_accuracy": _accuracy(details, "expected_act", "predicted_act"),
        "attr_accuracy": _accuracy(details, "expected_attr", "predicted_attr"),
        "primary_macro_f1": _macro_f1(expected_primary, predicted_primary),
        "primary_expected_distribution": dict(Counter(expected_primary)),
        "primary_predicted_distribution": dict(Counter(predicted_primary)),
        "case_bucket_distribution": dict(Counter(row["case_bucket"] for row in details)),
        "primary_confusion_matrix": _confusion_matrix(details),
        "multi_intent_cases": len(multi_rows),
        "multi_intent_exact_match_rate": _safe_div(sum(1 for row in multi_rows if row["multi_exact_match"]), len(multi_rows)),
        "multi_intent_partial_match_rate": _safe_div(sum(1 for row in multi_rows if row["multi_partial_match"]), len(multi_rows)),
        "high_risk_errors": _high_risk_errors(details),
        "primary_mismatches": [
            {
                "case_id": row["case_id"],
                "expected": row["expected_primary_intent"],
                "predicted": row["predicted_primary_intent"],
                "query": row["query"],
            }
            for row in details
            if row["expected_primary_intent"] != row["predicted_primary_intent"]
        ],
    }


def _write_outputs(output_dir: Path, details: List[Dict[str, Any]], summary: Dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (output_dir / "details.jsonl").open("w", encoding="utf-8") as f:
        for row in details:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = f"""# Triage Intent Evaluation

Cases: {summary["n_cases"]}

| Metric | Value |
| --- | ---: |
| Primary intent accuracy | {summary["primary_intent_accuracy"]:.4f} |
| Sub intent accuracy | {summary["sub_intent_accuracy"]:.4f} |
| Act accuracy | {summary["act_accuracy"]:.4f} |
| Attr accuracy | {summary["attr_accuracy"]:.4f} |
| Primary macro-F1 | {summary["primary_macro_f1"]:.4f} |
| Multi-intent exact match | {summary["multi_intent_exact_match_rate"]:.4f} |
| Multi-intent partial match | {summary["multi_intent_partial_match_rate"]:.4f} |

Expected primary distribution: `{json.dumps(summary["primary_expected_distribution"], ensure_ascii=False)}`

Predicted primary distribution: `{json.dumps(summary["primary_predicted_distribution"], ensure_ascii=False)}`

Case bucket distribution: `{json.dumps(summary["case_bucket_distribution"], ensure_ascii=False)}`

Primary confusion matrix: `{json.dumps(summary["primary_confusion_matrix"], ensure_ascii=False)}`

High-risk errors: `{json.dumps(summary["high_risk_errors"], ensure_ascii=False)}`

Primary mismatches: `{json.dumps(summary["primary_mismatches"], ensure_ascii=False)}`
"""
    (output_dir / "report.md").write_text(report, encoding="utf-8")


def _read_details(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_resume_details(output_dir: Path) -> Dict[str, Dict[str, Any]]:
    resumed: Dict[str, Dict[str, Any]] = {}
    for name in ("details.jsonl", "details.partial.jsonl"):
        for row in _read_details(output_dir / name):
            case_id = str(row.get("case_id") or "")
            if case_id and not _is_triage_fallback(row):
                resumed[case_id] = row
    return resumed


def _append_partial(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_run_error(output_dir: Path, payload: Dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "run_error.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def _run(args: argparse.Namespace) -> int:
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required for triage intent evaluation.")

    global triage_query
    from agents.triage_agent import triage_query as _triage_query

    triage_query = _triage_query
    output_dir = Path(args.output)
    partial_path = output_dir / "details.partial.jsonl"
    dataset = _load_jsonl(Path(args.dataset))
    details_by_id = _load_resume_details(output_dir) if args.resume else {}
    if not args.resume:
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path.write_text("", encoding="utf-8")

    total = len(dataset)
    for index, case in enumerate(dataset, start=1):
        case_id = str(case["case_id"])
        if case_id in details_by_id:
            print(f"[triage] resume skip {index}/{total} {case_id}", flush=True)
            continue
        try:
            row = await _evaluate_case(case)
        except Exception as exc:
            _write_run_error(
                output_dir,
                {
                    "case_id": case_id,
                    "index": index,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise
        if _is_triage_fallback(row) and not args.allow_triage_fallback:
            _write_run_error(
                output_dir,
                {
                    "case_id": case_id,
                    "index": index,
                    "error_type": "triage_fallback",
                    "error": "triage_query returned fallback output",
                    "row": row,
                },
            )
            raise SystemExit(
                "triage_query returned fallback output for case "
                f"{case_id}; evaluation metrics were not written."
            )
        details_by_id[case_id] = row
        _append_partial(partial_path, row)
        print(f"[triage] completed {index}/{total} {case_id}", flush=True)

    missing = [case["case_id"] for case in dataset if case["case_id"] not in details_by_id]
    if missing:
        _write_run_error(
            output_dir,
            {
                "error_type": "incomplete_evaluation",
                "missing_cases": missing,
            },
        )
        raise SystemExit(f"evaluation incomplete; missing cases: {missing}")

    details = [details_by_id[case["case_id"]] for case in dataset]
    fallback_cases = [row["case_id"] for row in details if _is_triage_fallback(row)]

    if fallback_cases and not args.allow_triage_fallback:
        raise SystemExit(
            "triage_query returned fallback output for cases "
            f"{fallback_cases}; evaluation metrics were not written."
        )

    summary = _summarize(details)
    _write_outputs(output_dir, details, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate triage_query on a labeled JSONL set.")
    parser.add_argument("--dataset", required=True, help="Path to triage_intent_eval_300.jsonl")
    parser.add_argument("--output", required=True, help="Directory for summary.json, details.jsonl and report.md")
    parser.add_argument(
        "--allow-triage-fallback",
        action="store_true",
        help="Write metrics even if triage_query returns its exception fallback.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse valid rows from details.jsonl/details.partial.jsonl and continue unfinished cases.",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
