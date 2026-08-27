from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

from rag.config import BACKEND_ROOT, EMBEDDING_MODEL
from rag.ingest import local_drug_cli
from rag.ingest.local_drug_resume_cli import (
    DEFAULT_STATE_FILE,
    SECTION_SCHEMA_VERSION,
    dedupe_key_hash,
    load_state_rows,
    row_hash,
)


SAFETY_SECTION_KEYS = {"contraindications", "adverse_reactions", "precautions", "drug_interactions"}


def _latest_by_dedupe(rows: Iterable[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        key = row.get("dedupe_key_hash")
        if key:
            latest[key] = row
    return latest


def _empty_report(drug_root: Path, state_file: Path) -> dict:
    return {
        "drug_root": str(drug_root),
        "state_file": str(state_file),
        "embedding_model": EMBEDDING_MODEL,
        "section_schema_version": SECTION_SCHEMA_VERSION,
        "files": 0,
        "rows": 0,
        "unique_rows": 0,
        "duplicate_rows_in_excel": 0,
        "completed_unique_rows": 0,
        "remaining_unique_rows": 0,
        "quarantined_rows": 0,
        "failed_rows": 0,
        "stale_or_changed_rows": 0,
        "accepted_chunks": 0,
        "inserted_chunks": 0,
        "state_status_counts": {},
        "quarantine_reasons": {},
        "section_coverage": {},
        "safety_section_coverage": {},
        "failed_examples": [],
        "quarantine_examples": [],
    }


def audit_local_drug_coverage(
    drug_root: Path | str = local_drug_cli.DEFAULT_DRUG_ROOT,
    state_file: Path | str = DEFAULT_STATE_FILE,
    *,
    limit: int = 0,
) -> dict:
    drug_root = local_drug_cli.resolve_drug_root(str(drug_root))
    state_file = Path(state_file)
    report = _empty_report(drug_root, state_file)
    state_rows = load_state_rows(state_file)
    latest_state = _latest_by_dedupe(state_rows)
    status_counts = Counter(row.get("status", "") for row in state_rows)
    quarantine_reasons: Counter[str] = Counter()
    section_present: Counter[str] = Counter()
    safety_present: Counter[str] = Counter()
    seen_keys: set[tuple[str, str, str]] = set()
    files_seen: set[str] = set()

    report["state_status_counts"] = dict(status_counts)
    report["accepted_chunks"] = sum(int(row.get("accepted_chunks") or 0) for row in state_rows)
    report["inserted_chunks"] = sum(int(row.get("inserted_chunks") or 0) for row in state_rows)

    for path, row_no, row in local_drug_cli.iter_excel_rows(drug_root, limit=limit):
        files_seen.add(str(path))
        report["rows"] += 1
        key = local_drug_cli.row_key(row)
        key_hash = dedupe_key_hash(key)
        if key in seen_keys:
            report["duplicate_rows_in_excel"] += 1
            continue
        seen_keys.add(key)
        report["unique_rows"] += 1
        state = latest_state.get(key_hash)
        current_row_hash = row_hash(row)
        state_is_current = bool(
            state
            and state.get("row_hash") == current_row_hash
            and state.get("section_schema_version") == SECTION_SCHEMA_VERSION
            and state.get("embedding_model") == EMBEDDING_MODEL
        )
        if state and not state_is_current:
            report["stale_or_changed_rows"] += 1
        if state_is_current and state.get("status") == "completed":
            report["completed_unique_rows"] += 1
            for item in local_drug_cli.row_to_items(row):
                section_key = str(item.metadata.get("section_key") or item.section_title)
                section_present[section_key] += 1
                if section_key in SAFETY_SECTION_KEYS:
                    safety_present[section_key] += 1
        elif state_is_current and state.get("status") == "quarantined":
            report["quarantined_rows"] += 1
            for reason in str(state.get("error") or "unknown").split(","):
                if reason:
                    quarantine_reasons[reason] += 1
            if len(report["quarantine_examples"]) < 20:
                report["quarantine_examples"].append({
                    "file": str(path),
                    "row_no": row_no,
                    "drug_name": state.get("drug_name", ""),
                    "approval_no": state.get("approval_no", ""),
                    "error": state.get("error", ""),
                })
        elif state_is_current and state.get("status") == "failed":
            report["failed_rows"] += 1
            if len(report["failed_examples"]) < 20:
                report["failed_examples"].append({
                    "file": str(path),
                    "row_no": row_no,
                    "drug_name": state.get("drug_name", ""),
                    "approval_no": state.get("approval_no", ""),
                    "error": state.get("error", ""),
                })

    report["files"] = len(files_seen)
    report["remaining_unique_rows"] = max(
        report["unique_rows"] - report["completed_unique_rows"] - report["quarantined_rows"],
        0,
    )
    completed = max(int(report["completed_unique_rows"]), 1)
    report["section_coverage"] = {
        section_key: {
            "count": int(section_present.get(section_key, 0)),
            "rate": round(section_present.get(section_key, 0) / completed, 6),
        }
        for section_key, _, _ in local_drug_cli.SECTION_COLUMNS
    }
    report["safety_section_coverage"] = {
        section_key: {
            "count": int(safety_present.get(section_key, 0)),
            "rate": round(safety_present.get(section_key, 0) / completed, 6),
        }
        for section_key in sorted(SAFETY_SECTION_KEYS)
    }
    report["quarantine_reasons"] = dict(quarantine_reasons)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit local drug Excel ingestion coverage without writing to DashVector.")
    parser.add_argument("--drug-root", default=str(BACKEND_ROOT / "drug_data"))
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", default=str(BACKEND_ROOT / "rag" / "reports" / "local_drug_coverage_audit.json"))
    args = parser.parse_args()

    report = audit_local_drug_coverage(args.drug_root, args.state_file, limit=args.limit)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
