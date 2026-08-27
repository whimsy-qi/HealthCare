from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from typing import Iterable

import yaml

from rag.config import COLLECTIONS, EMBEDDING_MODEL
from rag.external.clinical_trials import search_clinical_trials
from rag.schema import EvidenceItem
from rag.store import reset_collection, upsert_evidence_items, utc_now_iso


INGEST_VERSION = "clinicaltrials_gov_ingest_v1"
DEFAULT_SEED = Path(__file__).resolve().parents[1] / "sources" / "trial_seed.yaml"
DEFAULT_STATE_FILE = Path(__file__).resolve().parents[1] / "ingest_state" / "clinical_trials_ingest_state.jsonl"
DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "clinical_trials_ingest_runs"


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


def latest_by_nct(rows: Iterable[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        nct_id = row.get("nct_id")
        if nct_id:
            latest[str(nct_id)] = row
    return latest


def load_seed(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("queries") or [])


def _module(study: dict, name: str) -> dict:
    return ((study.get("protocolSection") or {}).get(name) or {})


def trial_record(study: dict) -> dict:
    ident = _module(study, "identificationModule")
    status = _module(study, "statusModule")
    design = _module(study, "designModule")
    conditions = _module(study, "conditionsModule")
    arms = _module(study, "armsInterventionsModule")
    desc = _module(study, "descriptionModule")
    outcomes = _module(study, "outcomesModule")
    eligibility = _module(study, "eligibilityModule")
    nct_id = ident.get("nctId") or ""
    interventions = [i.get("name", "") for i in arms.get("interventions", []) if i.get("name")]
    return {
        "nct_id": nct_id,
        "title": ident.get("briefTitle") or ident.get("officialTitle") or nct_id,
        "official_title": ident.get("officialTitle") or "",
        "status": status.get("overallStatus") or "",
        "phase": ";".join(design.get("phases") or []),
        "study_type": design.get("studyType") or "",
        "conditions": conditions.get("conditions") or [],
        "interventions": interventions,
        "brief_summary": desc.get("briefSummary") or "",
        "outcomes": [o.get("measure", "") for o in outcomes.get("primaryOutcomes", []) if o.get("measure")],
        "eligibility": eligibility.get("eligibilityCriteria") or "",
        "start_date": (status.get("startDateStruct") or {}).get("date", ""),
        "completion_date": (status.get("completionDateStruct") or {}).get("date", ""),
        "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else "",
    }


def record_hash(record: dict) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
    return stable_hash(payload, 20)


def matching_state(state: dict | None, *, current_hash: str, collection: str, retry_failed: bool) -> tuple[bool, str]:
    if not state:
        return False, "not_seen"
    same = (
        state.get("record_hash") == current_hash
        and state.get("ingest_version") == INGEST_VERSION
        and state.get("embedding_model") == EMBEDDING_MODEL
        and state.get("collection") == collection
    )
    status = state.get("status")
    if status == "completed" and same:
        return True, "completed"
    if status == "failed" and same and not retry_failed:
        return True, "failed_previous_run"
    if not same:
        return False, "changed"
    return False, f"status_{status or 'unknown'}"


def record_to_item(record: dict, seed: dict) -> EvidenceItem:
    text = "\n".join(part for part in [
        record.get("title", ""),
        "Status: " + record.get("status", ""),
        "Phase: " + record.get("phase", ""),
        "Conditions: " + "; ".join(record.get("conditions") or []),
        "Interventions: " + "; ".join(record.get("interventions") or []),
        record.get("brief_summary", ""),
        "Primary outcomes: " + "; ".join(record.get("outcomes") or []),
        "Eligibility: " + record.get("eligibility", ""),
    ] if part.strip() and part.strip() != "Status:" and part.strip() != "Phase:")
    nct_id = record["nct_id"]
    text_hash = stable_hash(text, 20)
    return EvidenceItem(
        chunk_id=f"nct_{nct_id}_{text_hash[:8]}"[:64],
        text=text[:5000],
        source_type="clinical_trial",
        source_tier="T2",
        title=record.get("title", "") or nct_id,
        organization="ClinicalTrials.gov",
        year=int(record.get("start_date", "")[:4]) if str(record.get("start_date", ""))[:4].isdigit() else None,
        department=str(seed.get("condition") or "clinical_trial"),
        section_title="study_summary",
        doc_id=f"clinical_trial:{nct_id}",
        text_hash=text_hash,
        license="ClinicalTrials.gov public API",
        evidence_level="clinical_trial_registry",
        locator={"nct_id": nct_id, "url": record.get("url", "")},
        metadata={
            "source_id": "clinicaltrials_gov",
            "source_name": "ClinicalTrials.gov",
            "source_url": record.get("url", ""),
            "url": record.get("url", ""),
            "nct_id": nct_id,
            "trial_status": record.get("status", ""),
            "phase": record.get("phase", ""),
            "study_type": record.get("study_type", ""),
            "conditions": ";".join(record.get("conditions") or []),
            "interventions": ";".join(record.get("interventions") or []),
            "query_id": seed.get("query_id", ""),
            "query": seed.get("query", ""),
            "collection_key": "clinical_trial",
            "embedding_text": f"{seed.get('query', '')}\n{record.get('title', '')}\n{text}",
        },
    )


def status_row(*, ingest_run_id: str, collection: str, seed: dict, record: dict, status: str, started_at: str, accepted_chunks: int = 0, inserted_chunks: int = 0, failed_chunks: int = 0, error: str = "") -> dict:
    return {
        "ingest_run_id": ingest_run_id,
        "collection": collection,
        "query_id": seed.get("query_id", ""),
        "query": seed.get("query", ""),
        "nct_id": str(record.get("nct_id") or ""),
        "record_hash": record_hash(record) if record else "",
        "ingest_version": INGEST_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "status": status,
        "title": record.get("title", ""),
        "accepted_chunks": accepted_chunks,
        "inserted_chunks": inserted_chunks,
        "failed_chunks": failed_chunks,
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "error": error,
    }


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Resume-safe ClinicalTrials.gov ingestion for RAG v2.")
    parser.add_argument("--seed", default=str(DEFAULT_SEED))
    parser.add_argument("--collection", default=COLLECTIONS["clinical_trial"])
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Validate seed without calling ClinicalTrials.gov.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--force-nct", action="append", default=[])
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--run-report", default="")
    args = parser.parse_args()

    seed_path = Path(args.seed)
    seeds = load_seed(seed_path)
    if args.limit:
        seeds = seeds[: args.limit]
    state_file = Path(args.state_file)
    ingest_run_id = uuid.uuid4().hex[:12]
    run_report = Path(args.run_report) if args.run_report else DEFAULT_REPORT_DIR / f"{ingest_run_id}.jsonl"
    latest = {} if args.rebuild else latest_by_nct(load_jsonl(state_file))
    force_ncts = {str(value) for value in args.force_nct if str(value).strip()}
    if args.rebuild and not args.dry_run:
        reset_collection(args.collection)

    summary = {
        "ingest_run_id": ingest_run_id,
        "seed": str(seed_path),
        "collection": args.collection,
        "queries": len(seeds),
        "records": 0,
        "processed": 0,
        "skipped": 0,
        "completed": 0,
        "failed": 0,
        "accepted_chunks": 0,
        "inserted_chunks": 0,
        "failed_chunks": 0,
        "dry_run": bool(args.dry_run),
        "offline": bool(args.offline),
        "state_file": str(state_file),
        "run_report": str(run_report),
    }

    for seed in seeds:
        try:
            studies = [] if args.offline else await search_clinical_trials(seed.get("query", ""), top_k=args.top_k or int(seed.get("max_results") or 8))
        except Exception as exc:
            row = status_row(ingest_run_id=ingest_run_id, collection=args.collection, seed=seed, record={}, status="failed", started_at=utc_now_iso(), error=f"{type(exc).__name__}: {exc}")
            append_jsonl(run_report, row)
            summary["failed"] += 1
            continue
        if args.offline:
            row = status_row(ingest_run_id=ingest_run_id, collection=args.collection, seed=seed, record={}, status="offline_seed_valid", started_at=utc_now_iso())
            append_jsonl(run_report, row)
            continue
        for study in studies:
            record = trial_record(study)
            if not record.get("nct_id"):
                continue
            started_at = utc_now_iso()
            summary["records"] += 1
            current_hash = record_hash(record)
            skip, reason = matching_state(latest.get(str(record["nct_id"])), current_hash=current_hash, collection=args.collection, retry_failed=args.retry_failed)
            if skip and str(record["nct_id"]) not in force_ncts and not args.rebuild:
                row = status_row(ingest_run_id=ingest_run_id, collection=args.collection, seed=seed, record=record, status="skipped", started_at=started_at, error=reason)
                append_jsonl(run_report, row)
                summary["skipped"] += 1
                continue
            item = record_to_item(record, seed)
            write = {"inserted": 0, "failed": 0, "collection": args.collection}
            if not args.dry_run:
                write = upsert_evidence_items([item], collection_name=args.collection, batch_size=max(1, args.batch_size))
            failed = int(write.get("failed", 0))
            inserted = int(write.get("inserted", 0))
            status = "dry_run_completed" if args.dry_run else ("completed" if failed == 0 else "failed")
            row = status_row(ingest_run_id=ingest_run_id, collection=args.collection, seed=seed, record=record, status=status, started_at=started_at, accepted_chunks=1, inserted_chunks=inserted, failed_chunks=failed)
            append_jsonl(run_report, row)
            if not args.dry_run:
                append_jsonl(state_file, row)
                latest[str(record["nct_id"])] = row
            summary["processed"] += 1
            summary["accepted_chunks"] += 1
            summary["inserted_chunks"] += inserted
            summary["failed_chunks"] += failed
            summary["completed" if status.endswith("completed") else "failed"] += 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed"] else 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
