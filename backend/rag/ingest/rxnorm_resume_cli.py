from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from typing import Iterable

import yaml

from rag.config import EMBEDDING_MODEL
from rag.external.drug import normalize_drug_name
from rag.store import utc_now_iso


INGEST_VERSION = "rxnorm_normalization_v1"
DEFAULT_SEED = Path(__file__).resolve().parents[1] / "sources" / "drug_seed.yaml"
DEFAULT_STATE_FILE = Path(__file__).resolve().parents[1] / "ingest_state" / "rxnorm_ingest_state.jsonl"
DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "rxnorm_ingest_runs"


def stable_hash(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"status": "state_parse_error", "line_no": line_no, "error": line[:200]})
    return rows


def latest_by_drug(rows: Iterable[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        drug_id = row.get("drug_id")
        if drug_id:
            latest[str(drug_id)] = row
    return latest


def load_seed(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("drugs") or [])


def seed_hash(drug: dict) -> str:
    payload = json.dumps(drug, ensure_ascii=False, sort_keys=True)
    return stable_hash(payload, 20)


def matching_state(state: dict | None, *, current_hash: str, retry_failed: bool) -> tuple[bool, str]:
    if not state:
        return False, "not_seen"
    same = (
        state.get("seed_hash") == current_hash
        and state.get("ingest_version") == INGEST_VERSION
        and state.get("embedding_model") == EMBEDDING_MODEL
    )
    status = state.get("status")
    if status == "completed" and same:
        return True, "completed"
    if status == "failed" and same and not retry_failed:
        return True, "failed_previous_run"
    if not same:
        return False, "changed"
    return False, f"status_{status or 'unknown'}"


def status_row(*, ingest_run_id: str, drug: dict, status: str, started_at: str, rxnorm: dict | None = None, error: str = "") -> dict:
    rxnorm = rxnorm or {}
    return {
        "ingest_run_id": ingest_run_id,
        "drug_id": drug.get("drug_id", ""),
        "query": drug.get("query") or drug.get("display_name") or drug.get("drug_id", ""),
        "display_name": drug.get("display_name", ""),
        "aliases": drug.get("aliases") or [],
        "seed_hash": seed_hash(drug),
        "ingest_version": INGEST_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "source_id": "rxnorm_rxnav",
        "source_type": "rxnorm",
        "source_tier": "T1",
        "status": status,
        "rxcui": rxnorm.get("rxcui") or "",
        "rxnorm_query": rxnorm.get("query") or "",
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "error": error,
    }


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Resume-safe RxNorm RxNav drug-name normalization.")
    parser.add_argument("--seed", default=str(DEFAULT_SEED))
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Validate seed without calling RxNav.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force-drug", action="append", default=[])
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--mapping-report", default="")
    parser.add_argument("--run-report", default="")
    args = parser.parse_args()

    seed_path = Path(args.seed)
    drugs = load_seed(seed_path)
    if args.limit:
        drugs = drugs[: args.limit]
    state_file = Path(args.state_file)
    ingest_run_id = uuid.uuid4().hex[:12]
    run_report = Path(args.run_report) if args.run_report else DEFAULT_REPORT_DIR / f"{ingest_run_id}.jsonl"
    mapping_report = Path(args.mapping_report) if args.mapping_report else None
    latest = latest_by_drug(load_jsonl(state_file))
    force = {str(value) for value in args.force_drug if str(value).strip()}
    summary = {
        "ingest_run_id": ingest_run_id,
        "seed": str(seed_path),
        "drugs": len(drugs),
        "processed": 0,
        "skipped": 0,
        "completed": 0,
        "failed": 0,
        "dry_run": bool(args.dry_run),
        "offline": bool(args.offline),
        "state_file": str(state_file),
        "run_report": str(run_report),
        "mapping_report": str(mapping_report) if mapping_report else "",
    }

    for drug in drugs:
        started_at = utc_now_iso()
        drug_id = str(drug.get("drug_id") or "")
        current_hash = seed_hash(drug)
        skip, reason = matching_state(latest.get(drug_id), current_hash=current_hash, retry_failed=args.retry_failed)
        if skip and drug_id not in force:
            row = status_row(ingest_run_id=ingest_run_id, drug=drug, status="skipped", started_at=started_at, error=reason)
            append_jsonl(run_report, row)
            summary["skipped"] += 1
            continue
        try:
            rxnorm = {} if args.offline else await normalize_drug_name(drug.get("query") or drug.get("display_name") or drug_id)
            status = "offline_seed_valid" if args.offline else "dry_run_completed" if args.dry_run else "completed"
            row = status_row(ingest_run_id=ingest_run_id, drug=drug, status=status, started_at=started_at, rxnorm=rxnorm)
            append_jsonl(run_report, row)
            if mapping_report:
                append_jsonl(mapping_report, row)
            if not args.dry_run and not args.offline:
                append_jsonl(state_file, row)
                latest[drug_id] = row
            summary["processed"] += 1
            summary["completed"] += 1
        except Exception as exc:
            row = status_row(ingest_run_id=ingest_run_id, drug=drug, status="failed", started_at=started_at, error=f"{type(exc).__name__}: {exc}")
            append_jsonl(run_report, row)
            if not args.dry_run:
                append_jsonl(state_file, row)
            summary["failed"] += 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed"] else 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
