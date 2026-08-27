import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_ROOT / ".env")
except Exception:
    pass


ACTION_ORDER = {"PASS": 0, "WARN": 1, "REGENERATE": 2, "ABSTAIN": 3}
check_answer = None


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


def _safe_div(num: float, den: float) -> float:
    return round(num / den, 4) if den else 0.0


def _binary_label(value: str) -> str:
    normalized = (value or "").strip().upper()
    if normalized in {"SAFE", "PASS", "NEGATIVE"}:
        return "SAFE"
    if normalized in {"RISK", "UNSAFE", "POSITIVE", "WARN"}:
        return "RISK"
    raise ValueError(f"unsupported expected_binary={value!r}")


def _is_guard_fallback(report: Dict[str, Any]) -> bool:
    stats = report.get("stats") or {}
    return bool(stats.get("error") or stats.get("timeout"))


async def _evaluate_case(case: Dict[str, Any]) -> Dict[str, Any]:
    report = await check_answer(
        answer=case["answer"],
        evidence=case.get("evidence"),
        domain=case.get("domain", "eval"),
        domain_risk=case.get("risk_tier", "MEDIUM"),
    )
    report_dict = report.as_dict() if hasattr(report, "as_dict") else dict(report)
    action = str(report_dict.get("action", "")).upper()
    if action not in ACTION_ORDER:
        raise RuntimeError(f"{case['case_id']} returned unsupported action={action!r}")

    expected_binary = _binary_label(case["expected_binary"])
    predicted_binary = "SAFE" if action == "PASS" else "RISK"
    expected_min_action = str(case.get("expected_min_action") or "PASS").upper()
    min_action_met = ACTION_ORDER[action] >= ACTION_ORDER.get(expected_min_action, 0)

    return {
        "case_id": case["case_id"],
        "domain": case.get("domain"),
        "risk_tier": case.get("risk_tier"),
        "fault_type": case.get("fault_type"),
        "expected_binary": expected_binary,
        "predicted_binary": predicted_binary,
        "expected_min_action": expected_min_action,
        "action": action,
        "min_action_met": min_action_met,
        "hallucination_score": report_dict.get("hallucination_score"),
        "confidence": report_dict.get("confidence"),
        "summary": report_dict.get("summary"),
        "stats": report_dict.get("stats") or {},
    }


def _summarize(details: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(details)
    cm = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for row in rows:
        exp_risk = row["expected_binary"] == "RISK"
        pred_risk = row["predicted_binary"] == "RISK"
        if exp_risk and pred_risk:
            cm["tp"] += 1
        elif not exp_risk and pred_risk:
            cm["fp"] += 1
        elif not exp_risk and not pred_risk:
            cm["tn"] += 1
        else:
            cm["fn"] += 1

    precision = _safe_div(cm["tp"], cm["tp"] + cm["fp"])
    recall = _safe_div(cm["tp"], cm["tp"] + cm["fn"])
    f1 = _safe_div(2 * precision * recall, precision + recall)
    high_contradictions = [
        row
        for row in rows
        if row.get("risk_tier") == "HIGH" and row.get("fault_type") == "contradicted"
    ]
    high_min_met = sum(1 for row in high_contradictions if row["min_action_met"])

    return {
        "n_cases": len(rows),
        "domain_distribution": dict(Counter(row.get("domain") for row in rows)),
        "risk_tier_distribution": dict(Counter(row.get("risk_tier") for row in rows)),
        "fault_type_distribution": dict(Counter(row.get("fault_type") for row in rows)),
        "expected_binary_distribution": dict(Counter(row.get("expected_binary") for row in rows)),
        "action_distribution": dict(Counter(row["action"] for row in rows)),
        "confusion_matrix": cm,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "risk_trigger_rate": _safe_div(cm["tp"] + cm["fp"], len(rows)),
        "false_negatives": [
            {
                "case_id": row["case_id"],
                "domain": row.get("domain"),
                "risk_tier": row.get("risk_tier"),
                "fault_type": row.get("fault_type"),
                "action": row.get("action"),
                "summary": row.get("summary"),
            }
            for row in rows
            if row["expected_binary"] == "RISK" and row["predicted_binary"] == "SAFE"
        ],
        "false_positives": [
            {
                "case_id": row["case_id"],
                "domain": row.get("domain"),
                "risk_tier": row.get("risk_tier"),
                "fault_type": row.get("fault_type"),
                "action": row.get("action"),
                "summary": row.get("summary"),
            }
            for row in rows
            if row["expected_binary"] == "SAFE" and row["predicted_binary"] == "RISK"
        ],
        "high_risk_contradiction_min_action_rate": _safe_div(high_min_met, len(high_contradictions)),
        "high_risk_contradiction_min_action_met": high_min_met,
        "high_risk_contradiction_total": len(high_contradictions),
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

    cm = summary["confusion_matrix"]
    report = f"""# HallucinationGuard Evaluation

Cases: {summary["n_cases"]}

| Metric | Value |
| --- | ---: |
| Precision | {summary["precision"]:.4f} |
| Recall | {summary["recall"]:.4f} |
| F1 | {summary["f1"]:.4f} |
| Risk trigger rate | {summary["risk_trigger_rate"]:.4f} |
| High-risk contradiction min-action rate | {summary["high_risk_contradiction_min_action_rate"]:.4f} |

| Confusion Matrix | Predicted Risk | Predicted Safe |
| --- | ---: | ---: |
| Expected Risk | {cm["tp"]} | {cm["fn"]} |
| Expected Safe | {cm["fp"]} | {cm["tn"]} |

Action distribution: `{json.dumps(summary["action_distribution"], ensure_ascii=False)}`

Fault type distribution: `{json.dumps(summary["fault_type_distribution"], ensure_ascii=False)}`

False negatives: `{json.dumps(summary["false_negatives"], ensure_ascii=False)}`

False positives: `{json.dumps(summary["false_positives"], ensure_ascii=False)}`
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
            if case_id and not _is_guard_fallback(row):
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
        raise SystemExit("OPENAI_API_KEY is required for HallucinationGuard evaluation.")

    global check_answer
    from agents.hallucination_agent import check_answer as _check_answer

    check_answer = _check_answer
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
            print(f"[guard] resume skip {index}/{total} {case_id}", flush=True)
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
        if _is_guard_fallback(row) and not args.allow_guard_fallback:
            _write_run_error(
                output_dir,
                {
                    "case_id": case_id,
                    "index": index,
                    "error_type": "guard_fallback",
                    "error": "HallucinationGuard returned timeout/error fallback",
                    "row": row,
                },
            )
            raise SystemExit(
                "HallucinationGuard returned timeout/error fallback for case "
                f"{case_id}; evaluation metrics were not written."
            )
        details_by_id[case_id] = row
        _append_partial(partial_path, row)
        print(f"[guard] completed {index}/{total} {case_id}", flush=True)

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
    fallback_cases = [row["case_id"] for row in details if _is_guard_fallback(row)]

    if fallback_cases and not args.allow_guard_fallback:
        raise SystemExit(
            "HallucinationGuard returned timeout/error fallback for cases "
            f"{fallback_cases}; evaluation metrics were not written."
        )

    summary = _summarize(details)
    _write_outputs(output_dir, details, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate HallucinationGuard on a labeled JSONL set.")
    parser.add_argument("--dataset", required=True, help="Path to hallucination_guard_eval_120.jsonl")
    parser.add_argument("--output", required=True, help="Directory for summary.json, details.jsonl and report.md")
    parser.add_argument(
        "--allow-guard-fallback",
        action="store_true",
        help="Write metrics even if check_answer returns timeout/error fallback.",
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
