from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable


TERMINAL_STATUSES = {"completed", "quarantined", "quarantined_only", "manifest_only"}


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                row["_state_file"] = str(path)
                row["_line_no"] = line_no
                rows.append(row)
            except json.JSONDecodeError as exc:
                rows.append({
                    "_state_file": str(path),
                    "_line_no": line_no,
                    "status": "state_parse_error",
                    "error": f"{type(exc).__name__}: {exc}",
                })
    return rows


def _expand_patterns(patterns: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            paths.extend(Path(match) for match in matches)
        else:
            paths.append(Path(pattern))
    return sorted({path.resolve() for path in paths})


def _identity(row: dict) -> str:
    for key in ("pdf_path", "url", "dedupe_key_hash", "row_identity", "doc_id"):
        value = row.get(key)
        if value:
            return f"{key}:{value}"
    return f"line:{row.get('_state_file')}:{row.get('_line_no')}"


def audit_rows(rows: list[dict]) -> dict:
    by_identity: dict[str, list[dict]] = defaultdict(list)
    anomalies: list[dict] = []
    for row in rows:
        by_identity[_identity(row)].append(row)
        if row.get("status") == "state_parse_error":
            anomalies.append({"type": "state_parse_error", "row": row})

    duplicate_checkpoints = []
    failed_then_completed = []
    completed_zero_insert = []
    completed_failed_chunks = []

    for identity, group in by_identity.items():
        if len(group) > 1:
            duplicate_checkpoints.append({
                "identity": identity,
                "count": len(group),
                "latest_status": group[-1].get("status"),
                "state_file": group[-1].get("_state_file"),
            })
        statuses = [row.get("status") for row in group]
        if "failed" in statuses and any(status in TERMINAL_STATUSES for status in statuses[statuses.index("failed") + 1:]):
            failed_then_completed.append({
                "identity": identity,
                "statuses": statuses,
                "latest_status": statuses[-1],
            })
        latest = group[-1]
        if latest.get("status") == "completed" and int(latest.get("accepted_chunks") or 0) > 0:
            inserted = int(latest.get("inserted_chunks") or 0)
            if inserted == 0:
                completed_zero_insert.append({
                    "identity": identity,
                    "accepted_chunks": latest.get("accepted_chunks"),
                    "inserted_chunks": latest.get("inserted_chunks"),
                    "state_file": latest.get("_state_file"),
                    "line_no": latest.get("_line_no"),
                })
        if latest.get("status") == "completed" and int(latest.get("failed_chunks") or 0) > 0:
            completed_failed_chunks.append({
                "identity": identity,
                "failed_chunks": latest.get("failed_chunks"),
                "state_file": latest.get("_state_file"),
                "line_no": latest.get("_line_no"),
            })

    return {
        "files": sorted({row.get("_state_file") for row in rows if row.get("_state_file")}),
        "rows": len(rows),
        "identities": len(by_identity),
        "duplicate_checkpoints": duplicate_checkpoints,
        "failed_then_completed": failed_then_completed,
        "completed_zero_insert": completed_zero_insert,
        "completed_failed_chunks": completed_failed_chunks,
        "parse_errors": [a["row"] for a in anomalies if a["type"] == "state_parse_error"],
        "anomaly_count": (
            len(duplicate_checkpoints)
            + len(failed_then_completed)
            + len(completed_zero_insert)
            + len(completed_failed_chunks)
            + len(anomalies)
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit resumable RAG ingestion checkpoint state files.")
    parser.add_argument("--state", action="append", required=True, help="State JSONL file or glob. Repeatable.")
    parser.add_argument("--out", default="", help="Optional path to write JSON audit report.")
    args = parser.parse_args()

    rows: list[dict] = []
    for path in _expand_patterns(args.state):
        rows.extend(_read_jsonl(path))
    result = audit_rows(rows)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
