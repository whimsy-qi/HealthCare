from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


def _login(api_base: str, username: str, password: str) -> str:
    response = requests.post(
        f"{api_base.rstrip('/')}/auth/login",
        json={"username": username, "password": password},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _get(api_base: str, token: str, path: str, **params) -> dict[str, Any]:
    response = requests.get(f"{api_base.rstrip('/')}{path}", headers=_headers(token), params=params, timeout=60)
    response.raise_for_status()
    return response.json()


def _run_chunks(run: dict[str, Any]) -> dict[str, int]:
    debug = run.get("debug") or {}
    return {
        "accepted": int(run.get("accepted_chunks") or 0),
        "inserted": int(debug.get("inserted_chunks") or run.get("accepted_chunks") or 0),
        "quarantined": int(run.get("quarantined_chunks") or 0),
        "failed": int(run.get("failed_chunks") or 0),
    }


def run() -> int:
    parser = argparse.ArgumentParser(description="Create a read-only recovery report for medical-graphrag migration state.")
    parser.add_argument("--api-base", default="http://localhost:8026/api/v1")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin123")
    parser.add_argument("--token", default="")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--out", default="rag/reports/medical_graphrag_migration/batch_5000_drug_30_pdf_recovered.json")
    args = parser.parse_args()

    token = args.token or _login(args.api_base, args.username, args.password)
    stats = _get(args.api_base, token, "/rag-admin/stats")
    runs_payload = _get(args.api_base, token, "/rag-admin/ingest-runs", limit=args.limit)
    runs = runs_payload.get("items") or []

    status_counts = Counter(run.get("status") for run in runs)
    by_type: dict[str, dict[str, Any]] = defaultdict(lambda: {"runs": 0, "statuses": Counter(), "accepted": 0, "inserted": 0, "quarantined": 0, "failed": 0})
    stale_runs = []
    for run_item in runs:
        source_type = run_item.get("source_type") or "unknown"
        chunks = _run_chunks(run_item)
        bucket = by_type[source_type]
        bucket["runs"] += 1
        bucket["statuses"][run_item.get("status")] += 1
        for key, value in chunks.items():
            bucket[key] += value
        if run_item.get("stale_processing"):
            stale_runs.append(
                {
                    "ingest_run_id": run_item.get("ingest_run_id"),
                    "source_type": run_item.get("source_type"),
                    "collection": run_item.get("collection"),
                    "status": run_item.get("status"),
                    "current_phase": run_item.get("current_phase"),
                    "processed_rows": run_item.get("processed_rows"),
                    "seconds_since_update": run_item.get("seconds_since_update"),
                    "celery_task_id": run_item.get("celery_task_id"),
                    "error_message": run_item.get("error_message"),
                }
            )

    summary_by_type = {}
    for source_type, values in by_type.items():
        summary_by_type[source_type] = {
            "runs": values["runs"],
            "statuses": dict(values["statuses"]),
            "accepted_chunks": values["accepted"],
            "inserted_chunks": values["inserted"],
            "quarantined_chunks": values["quarantined"],
            "failed_chunks": values["failed"],
        }

    allow_next_batch = not stale_runs and not any(run.get("status") == "processing" for run in runs)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "api_base": args.api_base,
        "stats": stats,
        "run_status_counts": dict(status_counts),
        "summary_by_source_type": summary_by_type,
        "stale_runs": stale_runs,
        "allow_next_batch": allow_next_batch,
        "runs": runs,
    }

    out_path = (Path.cwd() / args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out_path))
    print(
        json.dumps(
            {
                "run_status_counts": report["run_status_counts"],
                "summary_by_source_type": report["summary_by_source_type"],
                "stale_runs": report["stale_runs"],
                "allow_next_batch": report["allow_next_batch"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
