from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Iterable, Optional

from rag.config import BACKEND_ROOT, COLLECTIONS, EMBEDDING_MODEL
from rag.ingest import local_drug_cli
from rag.store import reset_collection, upsert_evidence_items, utc_now_iso


DEFAULT_STATE_FILE = Path(__file__).resolve().parents[1] / "ingest_state" / "local_drug_ingest_state.jsonl"
DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "local_drug_ingest_runs"
SECTION_SCHEMA_VERSION = "local_drug_section_schema_v1"


def stable_hash(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_state_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({
                    "status": "state_parse_error",
                    "state_file": str(path),
                    "line_no": line_no,
                    "error": line[:200],
                })
    return rows


def normalize_file_path(path: Path, drug_root: Path) -> str:
    try:
        return path.resolve().relative_to(drug_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def row_hash(row: dict) -> str:
    cleaned = {str(key): local_drug_cli.clean_cell(value) for key, value in row.items()}
    payload = json.dumps(cleaned, ensure_ascii=False, sort_keys=True)
    return stable_hash(payload, 20)


def dedupe_key_hash(dedupe_key: Iterable[str]) -> str:
    return stable_hash("|".join(dedupe_key), 20)


def row_identity(file_rel_path: str, row_no: int) -> str:
    return f"{file_rel_path}:{row_no}"


def row_identity_fields(row: dict) -> dict[str, str]:
    return {
        name: local_drug_cli.clean_cell(row.get(column))
        for name, column in local_drug_cli.IDENTITY_COLUMNS.items()
    }


def latest_state_by_dedupe(rows: Iterable[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        if row.get("status") == "duplicate":
            continue
        key = row.get("dedupe_key_hash")
        if key:
            latest[key] = row
    return latest


def latest_state_by_row_identity(rows: Iterable[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        key = row.get("row_identity")
        if key:
            latest[key] = row
    return latest


def matching_completed_state(
    state: Optional[dict],
    *,
    row_hash_value: str,
    collection: str,
    retry_failed: bool,
) -> tuple[bool, str]:
    if not state:
        return False, "not_seen"
    status = state.get("status")
    same_version = (
        state.get("row_hash") == row_hash_value
        and state.get("section_schema_version") == SECTION_SCHEMA_VERSION
        and state.get("embedding_model") == EMBEDDING_MODEL
        and state.get("collection") == collection
    )
    if status in {"completed", "quarantined"} and same_version:
        return True, status
    if status == "failed" and not retry_failed and same_version:
        return True, "failed_previous_run"
    if not same_version:
        return False, "changed"
    return False, f"status_{status or 'unknown'}"


def matching_duplicate_state(state: Optional[dict], *, row_hash_value: str, collection: str) -> bool:
    return bool(
        state
        and state.get("status") == "duplicate"
        and state.get("row_hash") == row_hash_value
        and state.get("section_schema_version") == SECTION_SCHEMA_VERSION
        and state.get("embedding_model") == EMBEDDING_MODEL
        and state.get("collection") == collection
    )


def should_force(
    *,
    force_rows: set[str],
    file_rel_path: str,
    row_no: int,
    identity: dict,
    doc_id: str,
) -> bool:
    if not force_rows:
        return False
    candidates = {
        row_identity(file_rel_path, row_no),
        f"{Path(file_rel_path).name}:{row_no}",
        identity.get("drug_name", ""),
        identity.get("approval_no", ""),
        doc_id,
    }
    return bool(candidates & force_rows)


def build_status_row(
    *,
    ingest_run_id: str,
    collection: str,
    file_rel_path: str,
    file_abs_path: Path,
    row_no: int,
    dedupe_key: tuple[str, str, str],
    row_hash_value: str,
    status: str,
    started_at: str,
    identity: dict,
    doc_id: str = "",
    accepted_chunks: int = 0,
    inserted_chunks: int = 0,
    failed_chunks: int = 0,
    chunk_ids: Optional[list[str]] = None,
    text_hashes: Optional[list[str]] = None,
    error: str = "",
) -> dict:
    file_row = row_identity(file_rel_path, row_no)
    return {
        "ingest_run_id": ingest_run_id,
        "collection": collection,
        "file": file_rel_path,
        "file_abs_path": str(file_abs_path.resolve()) if hasattr(file_abs_path, "resolve") else str(file_abs_path),
        "row_no": row_no,
        "row_identity": file_row,
        "dedupe_key": list(dedupe_key),
        "dedupe_key_hash": dedupe_key_hash(dedupe_key),
        "row_hash": row_hash_value,
        "section_schema_version": SECTION_SCHEMA_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "status": status,
        "drug_name": identity.get("drug_name", ""),
        "approval_no": identity.get("approval_no", ""),
        "producer": identity.get("producer", ""),
        "doc_id": doc_id,
        "accepted_chunks": accepted_chunks,
        "inserted_chunks": inserted_chunks,
        "failed_chunks": failed_chunks,
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "error": error,
        "chunk_ids": chunk_ids or [],
        "text_hashes": text_hashes or [],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resume-safe local Chinese drug-label Excel ingestion.")
    parser.add_argument("--drug-root", default=str(local_drug_cli.DEFAULT_DRUG_ROOT))
    parser.add_argument("--collection", default=COLLECTIONS["drug_label"])
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--row-batch-size", type=int, default=50)
    parser.add_argument("--progress-every", type=int, default=500)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--force-row", action="append", default=[])
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--failed-only",
        action="store_true",
        help="Only retry rows whose latest matching checkpoint status is failed. Useful for cleaning state without ingesting all unseen rows.",
    )
    parser.add_argument("--quarantine-report", default="")
    parser.add_argument("--dedupe-report", default="")
    parser.add_argument("--run-report", default="")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    drug_root = local_drug_cli.resolve_drug_root(args.drug_root)
    state_file = Path(args.state_file)
    ingest_run_id = uuid.uuid4().hex[:12]
    run_report = Path(args.run_report) if args.run_report else DEFAULT_REPORT_DIR / f"{ingest_run_id}.jsonl"
    quarantine_report = Path(args.quarantine_report) if args.quarantine_report else None
    dedupe_report = Path(args.dedupe_report) if args.dedupe_report else None
    force_rows = {str(value) for value in args.force_row if str(value).strip()}

    if args.rebuild and not args.dry_run:
        reset_collection(args.collection)

    state_rows = [] if args.rebuild else load_state_rows(state_file)
    state_by_dedupe = latest_state_by_dedupe(state_rows)
    state_by_row = latest_state_by_row_identity(state_rows)
    seen_dedupe_keys: set[tuple[str, str, str]] = set()
    files_seen: set[str] = set()
    summary = {
        "ingest_run_id": ingest_run_id,
        "drug_root": str(drug_root),
        "collection": args.collection,
        "rows": 0,
        "files": 0,
        "processed": 0,
        "skipped": 0,
        "completed": 0,
        "failed": 0,
        "quarantined": 0,
        "duplicate": 0,
        "accepted_chunks": 0,
        "inserted_chunks": 0,
        "failed_chunks": 0,
        "dry_run": bool(args.dry_run),
        "rebuild": bool(args.rebuild),
        "state_file": str(state_file),
        "run_report": str(run_report),
        "quarantine_report": str(quarantine_report) if quarantine_report else "",
        "dedupe_report": str(dedupe_report) if dedupe_report else "",
        "section_schema_version": SECTION_SCHEMA_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "row_batch_size": max(1, int(args.row_batch_size or 1)),
        "flush_count": 0,
        "batch_fallback_count": 0,
        "batch_partial_failures": 0,
        "failed_only": bool(args.failed_only),
    }
    pending_rows: list[dict] = []

    def record_row_out(row_out: dict) -> None:
        summary["processed"] += 1
        summary[row_out["status"]] = summary.get(row_out["status"], 0) + 1
        summary["accepted_chunks"] += int(row_out.get("accepted_chunks") or 0)
        summary["inserted_chunks"] += int(row_out.get("inserted_chunks") or 0)
        summary["failed_chunks"] += int(row_out.get("failed_chunks") or 0)
        append_jsonl(run_report, row_out)
        if row_out["status"].endswith("quarantined") and quarantine_report:
            append_jsonl(quarantine_report, row_out)
        if not args.dry_run and row_out["status"] != "skipped":
            append_jsonl(state_file, row_out)
            if row_out["status"] != "duplicate":
                state_by_dedupe[row_out["dedupe_key_hash"]] = row_out
            state_by_row[row_out["row_identity"]] = row_out
        print(json.dumps(row_out, ensure_ascii=False))

    def flush_pending_rows() -> None:
        if not pending_rows:
            return
        summary["flush_count"] += 1
        batch_rows = list(pending_rows)
        pending_rows.clear()
        batch_items = [item for pending in batch_rows for item in pending["row_items"]]
        try:
            write_result = upsert_evidence_items(
                batch_items,
                collection_name=args.collection,
                batch_size=max(1, args.batch_size),
            )
            failed = int(write_result.get("failed", 0))
            inserted = int(write_result.get("inserted", 0))
            batch_error = "" if failed == 0 else f"batch_upsert_failed: inserted={inserted}, failed={failed}"
        except Exception as exc:
            failed = len(batch_items)
            inserted = 0
            batch_error = f"{type(exc).__name__}: {exc}"

        batch_completed = failed == 0
        fallback_results: dict[str, tuple[bool, str]] = {}
        if not batch_completed:
            summary["batch_partial_failures"] += 1
            summary["batch_fallback_count"] += len(batch_rows)
            for pending in batch_rows:
                row_items = pending["row_items"]
                try:
                    row_write = upsert_evidence_items(
                        row_items,
                        collection_name=args.collection,
                        batch_size=max(1, min(args.batch_size, len(row_items) or 1)),
                    )
                    row_failed = int(row_write.get("failed", 0))
                    row_inserted = int(row_write.get("inserted", 0))
                    if row_failed == 0:
                        fallback_results[pending["row_identity"]] = (True, "")
                    else:
                        fallback_results[pending["row_identity"]] = (
                            False,
                            f"row_upsert_failed_after_batch_failure: inserted={row_inserted}, failed={row_failed}; batch_error={batch_error}",
                        )
                except Exception as exc:
                    fallback_results[pending["row_identity"]] = (
                        False,
                        f"{type(exc).__name__}: {exc}; batch_error={batch_error}",
                    )

        for pending in batch_rows:
            row_items = pending["row_items"]
            row_completed = batch_completed
            row_error = "" if batch_completed else batch_error
            if fallback_results:
                row_completed, row_error = fallback_results.get(
                    pending["row_identity"],
                    (False, batch_error),
                )
            row_out = build_status_row(
                ingest_run_id=ingest_run_id,
                collection=args.collection,
                file_rel_path=pending["file_rel_path"],
                file_abs_path=pending["file_abs_path"],
                row_no=pending["row_no"],
                dedupe_key=pending["dedupe_key"],
                row_hash_value=pending["row_hash_value"],
                status="completed" if row_completed else "failed",
                started_at=pending["started_at"],
                identity=pending["identity"],
                doc_id=pending["doc_id"],
                accepted_chunks=len(row_items),
                inserted_chunks=len(row_items) if row_completed else 0,
                failed_chunks=0 if row_completed else len(row_items),
                chunk_ids=[item.chunk_id for item in row_items],
                text_hashes=[item.text_hash for item in row_items],
                error=row_error,
            )
            record_row_out(row_out)

    for path, row_no, row in local_drug_cli.iter_excel_rows(drug_root, limit=args.limit):
        started_at = utc_now_iso()
        path = Path(path)
        file_rel_path = normalize_file_path(path, drug_root)
        files_seen.add(file_rel_path)
        summary["rows"] += 1

        key = local_drug_cli.row_key(row)
        key_hash = dedupe_key_hash(key)
        identity = row_identity_fields(row)
        current_row_hash = row_hash(row)
        row_items = []
        doc_id = f"drug_label:{identity.get('approval_no', '')}:{local_drug_cli.stable_hash('|'.join(key), 10)}"
        forced = should_force(
            force_rows=force_rows,
            file_rel_path=file_rel_path,
            row_no=row_no,
            identity=identity,
            doc_id=doc_id,
        )
        state = state_by_dedupe.get(key_hash)

        if args.failed_only and not forced:
            failed_same_version = bool(
                state
                and state.get("status") == "failed"
                and state.get("row_hash") == current_row_hash
                and state.get("section_schema_version") == SECTION_SCHEMA_VERSION
                and state.get("embedding_model") == EMBEDDING_MODEL
                and state.get("collection") == args.collection
            )
            if not failed_same_version:
                summary["skipped"] += 1
                seen_dedupe_keys.add(key)
                continue

        if key in seen_dedupe_keys and not forced:
            duplicate_state = state_by_row.get(row_identity(file_rel_path, row_no))
            if matching_duplicate_state(duplicate_state, row_hash_value=current_row_hash, collection=args.collection):
                status = "skipped"
                error = "duplicate"
                summary["skipped"] += 1
            else:
                status = "duplicate"
                error = ""
                summary["duplicate"] += 1
            row_out = build_status_row(
                ingest_run_id=ingest_run_id,
                collection=args.collection,
                file_rel_path=file_rel_path,
                file_abs_path=path,
                row_no=row_no,
                dedupe_key=key,
                row_hash_value=current_row_hash,
                status=status,
                started_at=started_at,
                identity=identity,
                doc_id=doc_id,
                error=error,
            )
            append_jsonl(run_report, row_out)
            if status == "duplicate" and dedupe_report:
                append_jsonl(dedupe_report, row_out)
            if not args.dry_run and status == "duplicate":
                append_jsonl(state_file, row_out)
                state_by_row[row_out["row_identity"]] = row_out
            print(json.dumps(row_out, ensure_ascii=False))
            continue

        flags = local_drug_cli.row_quality_flags(row)
        if flags:
            row_out = build_status_row(
                ingest_run_id=ingest_run_id,
                collection=args.collection,
                file_rel_path=file_rel_path,
                file_abs_path=path,
                row_no=row_no,
                dedupe_key=key,
                row_hash_value=current_row_hash,
                status="dry_run_quarantined" if args.dry_run else "quarantined",
                started_at=started_at,
                identity=identity,
                doc_id=doc_id,
                error=",".join(flags),
            )
            summary["processed"] += 1
            summary["quarantined"] += 1
            append_jsonl(run_report, row_out)
            if quarantine_report:
                append_jsonl(quarantine_report, row_out)
            if not args.dry_run:
                append_jsonl(state_file, row_out)
                state_by_dedupe[key_hash] = row_out
            print(json.dumps(row_out, ensure_ascii=False))
            continue

        skip, reason = matching_completed_state(
            state,
            row_hash_value=current_row_hash,
            collection=args.collection,
            retry_failed=bool(args.retry_failed),
        )
        if skip and not forced and not args.rebuild:
            seen_dedupe_keys.add(key)
            summary["skipped"] += 1
            row_out = build_status_row(
                ingest_run_id=ingest_run_id,
                collection=args.collection,
                file_rel_path=file_rel_path,
                file_abs_path=path,
                row_no=row_no,
                dedupe_key=key,
                row_hash_value=current_row_hash,
                status="skipped",
                started_at=started_at,
                identity=identity,
                doc_id=state.get("doc_id") or doc_id,
                error=reason,
            )
            append_jsonl(run_report, row_out)
            print(json.dumps(row_out, ensure_ascii=False))
            continue

        try:
            row_items = local_drug_cli.row_to_items(row)
            if not row_items:
                row_out = build_status_row(
                    ingest_run_id=ingest_run_id,
                    collection=args.collection,
                    file_rel_path=file_rel_path,
                    file_abs_path=path,
                    row_no=row_no,
                    dedupe_key=key,
                    row_hash_value=current_row_hash,
                    status="dry_run_quarantined" if args.dry_run else "quarantined",
                    started_at=started_at,
                    identity=identity,
                    doc_id=doc_id,
                    error="no_section_chunks",
                )
                seen_dedupe_keys.add(key)
                record_row_out(row_out)
                continue
            else:
                doc_id = row_items[0].doc_id
                if args.dry_run:
                    row_out = build_status_row(
                        ingest_run_id=ingest_run_id,
                        collection=args.collection,
                        file_rel_path=file_rel_path,
                        file_abs_path=path,
                        row_no=row_no,
                        dedupe_key=key,
                        row_hash_value=current_row_hash,
                        status="dry_run_completed",
                        started_at=started_at,
                        identity=identity,
                        doc_id=doc_id,
                        accepted_chunks=len(row_items),
                        chunk_ids=[item.chunk_id for item in row_items],
                        text_hashes=[item.text_hash for item in row_items],
                    )
                    seen_dedupe_keys.add(key)
                    record_row_out(row_out)
                    continue
                pending_rows.append({
                    "row_identity": row_identity(file_rel_path, row_no),
                    "file_rel_path": file_rel_path,
                    "file_abs_path": path,
                    "row_no": row_no,
                    "dedupe_key": key,
                    "row_hash_value": current_row_hash,
                    "started_at": started_at,
                    "identity": identity,
                    "doc_id": doc_id,
                    "row_items": row_items,
                })
                seen_dedupe_keys.add(key)
                if len(pending_rows) >= max(1, args.row_batch_size):
                    flush_pending_rows()
                if args.progress_every and summary["rows"] % max(1, args.progress_every) == 0:
                    progress = {
                        "progress": True,
                        "rows": summary["rows"],
                        "processed": summary["processed"],
                        "skipped": summary["skipped"],
                        "completed": summary.get("completed", 0),
                        "failed": summary.get("failed", 0),
                        "quarantined": summary.get("quarantined", 0),
                        "duplicate": summary.get("duplicate", 0),
                        "pending_rows": len(pending_rows),
                        "inserted_chunks": summary["inserted_chunks"],
                    }
                    print(json.dumps(progress, ensure_ascii=False))
                continue
        except Exception as exc:
            row_out = build_status_row(
                ingest_run_id=ingest_run_id,
                collection=args.collection,
                file_rel_path=file_rel_path,
                file_abs_path=path,
                row_no=row_no,
                dedupe_key=key,
                row_hash_value=current_row_hash,
                status="failed",
                started_at=started_at,
                identity=identity,
                doc_id=doc_id,
                accepted_chunks=len(row_items),
                error=f"{type(exc).__name__}: {exc}",
            )

        seen_dedupe_keys.add(key)
        record_row_out(row_out)

    flush_pending_rows()
    summary["files"] = len(files_seen)
    summary["pending_rows"] = len(pending_rows)
    if summary["completed"]:
        summary["avg_chunks_per_completed_row"] = summary["accepted_chunks"] / max(1, summary["completed"])
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
