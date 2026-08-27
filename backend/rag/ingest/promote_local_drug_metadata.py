from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from dashvector import Doc

from rag.config import COLLECTIONS
from rag.store import ensure_collection, utc_now_iso


DEFAULT_STATE_FILE = Path(__file__).resolve().parents[1] / "ingest_state" / "local_drug_ingest_state.jsonl"
DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "metadata_promotion"
DEFAULT_OLD_SOURCE_NAME = "yaozs_xlsx"
DEFAULT_NEW_SOURCE_NAME = "nmpa_cfda_local_snapshot"
DEFAULT_NEW_SOURCE_TIER = "T1"
DEFAULT_NEW_LICENSE = "local_official_snapshot_review_required"
DEFAULT_NEW_EVIDENCE_LEVEL = "official_drug_label_local_snapshot"
UPDATE_REASON = "promote_local_cfda_nmpa_snapshot"


def timestamp_for_path() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_run_report() -> Path:
    return DEFAULT_REPORT_DIR / f"{timestamp_for_path()}.jsonl"


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def iter_completed_chunk_ids(state_file: Path, *, limit: int = 0) -> Iterator[str]:
    seen: set[str] = set()
    yielded = 0
    if not state_file.exists():
        return
    with state_file.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip().lstrip("\ufeff")
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") != "completed":
                continue
            for chunk_id in row.get("chunk_ids") or []:
                chunk_id = str(chunk_id or "").strip()
                if not chunk_id or chunk_id in seen:
                    continue
                seen.add(chunk_id)
                yield chunk_id
                yielded += 1
                if limit and yielded >= limit:
                    return


def batched(values: Iterable[str], batch_size: int) -> Iterator[list[str]]:
    batch: list[str] = []
    for value in values:
        batch.append(value)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def response_ok(resp) -> bool:
    code = getattr(resp, "code", 0)
    if code not in (0, None):
        return False
    try:
        return bool(resp)
    except Exception:
        return True


def response_error(resp) -> str:
    return str(getattr(resp, "message", "") or getattr(resp, "error", "") or resp)


def normalize_docs(output) -> list:
    if output is None:
        return []
    if isinstance(output, list):
        return output
    if isinstance(output, dict):
        docs = []
        for key, value in output.items():
            if isinstance(value, dict):
                docs.append(Doc(id=str(value.get("id") or key), vector=value.get("vector"), fields=value.get("fields") or {}))
            else:
                docs.append(value)
        return docs
    return [output]


def fetch_docs(collection, ids: list[str]) -> tuple[list, str]:
    try:
        resp = collection.fetch(ids=ids)
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    if not response_ok(resp):
        return [], response_error(resp)
    return normalize_docs(getattr(resp, "output", None)), ""


def promote_fields(
    fields: dict,
    *,
    old_source_name: str,
    new_source_name: str,
    new_source_tier: str,
    new_license: str,
    updated_at: str,
) -> tuple[Optional[dict], str]:
    if str(fields.get("source_type") or "") != "drug_label":
        return None, "skip_source_type"
    if str(fields.get("source_name") or "") != old_source_name:
        return None, "skip_source_name"
    promoted = dict(fields)
    promoted.update(
        {
            "source_name": new_source_name,
            "source_tier": new_source_tier,
            "license": new_license,
            "evidence_level": DEFAULT_NEW_EVIDENCE_LEVEL,
            "authority_tier_updated_at": updated_at,
            "authority_tier_update_reason": UPDATE_REASON,
            "official_source_assumption": True,
            "source_verified_online": False,
            "source_provenance": "cfda_nmpa_local_snapshot",
        }
    )
    return promoted, "would_update"


def fetch_doc_with_vector(collection, chunk_id: str):
    try:
        resp = collection.query(id=chunk_id, topk=1, include_vector=True)
    except Exception:
        return None
    if not response_ok(resp):
        return None
    docs = normalize_docs(getattr(resp, "output", None))
    return docs[0] if docs else None


def update_docs(collection, docs: list[Doc]) -> dict[str, tuple[str, str]]:
    if not docs:
        return {}
    try:
        resp = collection.update(docs)
    except Exception as exc:
        resp = None
        first_error = f"{type(exc).__name__}: {exc}"
    else:
        first_error = response_error(resp)
    if response_ok(resp):
        return {str(doc.id): ("updated", "") for doc in docs}

    results: dict[str, tuple[str, str]] = {}
    for doc in docs:
        vector_doc = fetch_doc_with_vector(collection, str(doc.id))
        vector = getattr(vector_doc, "vector", None) if vector_doc is not None else None
        try:
            retry_resp = collection.update(Doc(id=doc.id, vector=vector, fields=doc.fields))
        except Exception as exc:
            results[str(doc.id)] = ("update_failed", f"{type(exc).__name__}: {exc}")
            continue
        if response_ok(retry_resp):
            results[str(doc.id)] = ("updated", "")
        else:
            results[str(doc.id)] = ("update_failed", response_error(retry_resp) or first_error)
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Promote local CFDA/NMPA drug label metadata in DashVector.")
    parser.add_argument("--collection", default=COLLECTIONS["drug_label"])
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--source-name", default=DEFAULT_OLD_SOURCE_NAME)
    parser.add_argument("--new-source-name", default=DEFAULT_NEW_SOURCE_NAME)
    parser.add_argument("--new-source-tier", default=DEFAULT_NEW_SOURCE_TIER)
    parser.add_argument("--new-license", default=DEFAULT_NEW_LICENSE)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-report", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    state_file = Path(args.state_file)
    run_report = Path(args.run_report) if args.run_report else default_run_report()
    batch_size = max(1, int(args.batch_size or 1))
    updated_at = utc_now_iso()
    summary = {
        "collection": args.collection,
        "state_file": str(state_file),
        "run_report": str(run_report),
        "dry_run": bool(args.dry_run),
        "seen": 0,
        "fetched": 0,
        "would_update": 0,
        "updated": 0,
        "skipped": 0,
        "missing": 0,
        "failed": 0,
    }

    collection = None if args.dry_run else ensure_collection(args.collection, create_missing=False)
    if args.dry_run:
        collection = ensure_collection(args.collection, create_missing=False)

    for ids in batched(iter_completed_chunk_ids(state_file, limit=args.limit), batch_size):
        summary["seen"] += len(ids)
        fetched_docs, fetch_error = fetch_docs(collection, ids)
        if fetch_error:
            summary["failed"] += len(ids)
            for chunk_id in ids:
                append_jsonl(run_report, {"chunk_id": chunk_id, "status": "fetch_failed", "error": fetch_error})
            continue
        docs_by_id = {str(getattr(doc, "id", "")): doc for doc in fetched_docs}
        update_batch: list[Doc] = []
        update_report_rows: list[dict] = []
        for chunk_id in ids:
            doc = docs_by_id.get(chunk_id)
            if doc is None:
                summary["missing"] += 1
                append_jsonl(run_report, {"chunk_id": chunk_id, "status": "missing"})
                continue
            summary["fetched"] += 1
            fields = dict(getattr(doc, "fields", {}) or {})
            new_fields, status = promote_fields(
                fields,
                old_source_name=args.source_name,
                new_source_name=args.new_source_name,
                new_source_tier=args.new_source_tier,
                new_license=args.new_license,
                updated_at=updated_at,
            )
            if new_fields is None:
                summary["skipped"] += 1
                append_jsonl(
                    run_report,
                    {
                        "chunk_id": chunk_id,
                        "status": status,
                        "old_source_name": fields.get("source_name"),
                        "old_source_tier": fields.get("source_tier"),
                    },
                )
                continue
            summary["would_update"] += 1
            row = {
                "chunk_id": chunk_id,
                "status": "would_update" if args.dry_run else "pending_update",
                "old_source_name": fields.get("source_name"),
                "new_source_name": args.new_source_name,
                "old_source_tier": fields.get("source_tier"),
                "new_source_tier": args.new_source_tier,
                "old_license": fields.get("license"),
                "new_license": args.new_license,
                "error": "",
            }
            if args.dry_run:
                append_jsonl(run_report, row)
            else:
                update_batch.append(Doc(id=chunk_id, fields=new_fields))
                update_report_rows.append(row)

        if args.dry_run or not update_batch:
            continue
        update_results = update_docs(collection, update_batch)
        for row in update_report_rows:
            status, error = update_results.get(row["chunk_id"], ("update_failed", "missing_update_result"))
            row["status"] = status
            row["error"] = error
            if status == "updated":
                summary["updated"] += 1
            else:
                summary["failed"] += 1
            append_jsonl(run_report, row)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
