from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from rag.config import COLLECTIONS, DEFAULT_PDF_ROOT, EMBEDDING_MODEL
from rag.ingest.cli import BLOCKING_QUALITY_FLAGS, _chunk_to_evidence
from rag.ingest.pdf import build_guideline_chunks
from rag.store import reset_collection, upsert_evidence_items, utc_now_iso


CHUNKING_STRATEGY = "structure_aware_semantic_packing_v1"
DEFAULT_STATE_FILE = Path(__file__).resolve().parents[1] / "ingest_state" / "pdf_ingest_state.jsonl"
DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "pdf_ingest_runs"


@dataclass(frozen=True)
class ChunkingConfig:
    target_chars: int = 650
    min_chars: int = 180
    max_chars: int = 1100
    overlap_chars: int = 40
    enable_ocr_fallback: bool = False
    ocr_min_chars: int = 60
    ocr_min_cjk_ratio: float = 0.08
    ocr_max_mojibake_ratio: float = 0.02
    min_ocr_confidence: float = 0.5

    @property
    def version(self) -> str:
        return (
            f"{CHUNKING_STRATEGY}:"
            f"target={self.target_chars}:min={self.min_chars}:"
            f"max={self.max_chars}:overlap={self.overlap_chars}:"
            f"ocr={int(self.enable_ocr_fallback)}:"
            f"ocr_min={self.ocr_min_chars}:ocr_cjk={self.ocr_min_cjk_ratio}:"
            f"ocr_mojibake={self.ocr_max_mojibake_ratio}:ocr_conf={self.min_ocr_confidence}"
        )


def stable_hash(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def pdf_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def make_doc_id(path: Path, doc_hash: str) -> str:
    return f"guideline:{stable_hash(str(path.resolve()), 10)}:{doc_hash}"


def normalize_pdf_path(path: Path, pdf_root: Path) -> str:
    try:
        return path.resolve().relative_to(pdf_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


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


def latest_state_by_pdf(rows: Iterable[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        pdf_path = row.get("pdf_path")
        if not pdf_path:
            continue
        latest[pdf_path] = row
    return latest


def should_force(path: Path, pdf_rel_path: str, doc_id: str, force_docs: set[str]) -> bool:
    if not force_docs:
        return False
    candidates = {
        pdf_rel_path,
        path.name,
        path.stem,
        str(path),
        str(path.resolve()),
        doc_id,
    }
    return bool(candidates & force_docs)


def matching_completed_state(
    state: Optional[dict],
    *,
    doc_hash: str,
    chunking_version: str,
    collection: str,
    retry_failed: bool,
) -> tuple[bool, str]:
    if not state:
        return False, "not_seen"
    status = state.get("status")
    same_version = (
        state.get("doc_hash") == doc_hash
        and state.get("chunking_version") == chunking_version
        and state.get("embedding_model") == EMBEDDING_MODEL
        and state.get("collection") == collection
    )
    if status in {"completed", "quarantined_only"} and same_version:
        return True, status
    if status == "failed" and not retry_failed and same_version:
        return True, "failed_previous_run"
    if not same_version:
        return False, "changed"
    return False, f"status_{status or 'unknown'}"


def quarantine_row(pdf_path: str, chunk) -> dict:
    return {
        "pdf": pdf_path,
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "title": chunk.title,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "section_title": chunk.section_title,
        "block_type": chunk.block_type,
        "quality": chunk.quality,
        "text_hash": chunk.text_hash,
        "text_preview": chunk.text[:180],
    }


def build_status_row(
    *,
    ingest_run_id: str,
    collection: str,
    pdf_rel_path: str,
    pdf_abs_path: Path,
    doc_hash: str,
    doc_id: str,
    chunking_version: str,
    status: str,
    started_at: str,
    accepted_chunks: int = 0,
    quarantined_chunks: int = 0,
    inserted_chunks: int = 0,
    failed_chunks: int = 0,
    chunk_ids: Optional[list[str]] = None,
    text_hashes: Optional[list[str]] = None,
    error: str = "",
) -> dict:
    return {
        "ingest_run_id": ingest_run_id,
        "collection": collection,
        "pdf_path": pdf_rel_path,
        "pdf_abs_path": str(pdf_abs_path.resolve()),
        "doc_hash": doc_hash,
        "doc_id": doc_id,
        "chunking_version": chunking_version,
        "embedding_model": EMBEDDING_MODEL,
        "status": status,
        "accepted_chunks": accepted_chunks,
        "quarantined_chunks": quarantined_chunks,
        "inserted_chunks": inserted_chunks,
        "failed_chunks": failed_chunks,
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "error": error,
        "chunk_ids": chunk_ids or [],
        "text_hashes": text_hashes or [],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resume-safe structured PDF ingestion for medical RAG.")
    parser.add_argument("--pdf-root", default=str(DEFAULT_PDF_ROOT))
    parser.add_argument("--collection", default=COLLECTIONS["guideline"])
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--force-doc", action="append", default=[])
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--quarantine-report", default="")
    parser.add_argument("--run-report", default="")
    parser.add_argument("--target-chars", type=int, default=650)
    parser.add_argument("--min-chars", type=int, default=180)
    parser.add_argument("--max-chars", type=int, default=1100)
    parser.add_argument("--overlap-chars", type=int, default=40)
    parser.add_argument("--enable-ocr-fallback", action="store_true")
    parser.add_argument("--ocr-min-chars", type=int, default=60)
    parser.add_argument("--ocr-min-cjk-ratio", type=float, default=0.08)
    parser.add_argument("--ocr-max-mojibake-ratio", type=float, default=0.02)
    parser.add_argument("--min-ocr-confidence", type=float, default=0.5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pdf_root = Path(args.pdf_root)
    state_file = Path(args.state_file)
    ingest_run_id = uuid.uuid4().hex[:12]
    run_report = Path(args.run_report) if args.run_report else DEFAULT_REPORT_DIR / f"{ingest_run_id}.jsonl"
    quarantine_report = Path(args.quarantine_report) if args.quarantine_report else None
    chunking = ChunkingConfig(
        target_chars=args.target_chars,
        min_chars=args.min_chars,
        max_chars=args.max_chars,
        overlap_chars=args.overlap_chars,
        enable_ocr_fallback=bool(args.enable_ocr_fallback),
        ocr_min_chars=args.ocr_min_chars,
        ocr_min_cjk_ratio=args.ocr_min_cjk_ratio,
        ocr_max_mojibake_ratio=args.ocr_max_mojibake_ratio,
        min_ocr_confidence=args.min_ocr_confidence,
    )
    force_docs = {str(value) for value in args.force_doc if str(value).strip()}

    paths = sorted(pdf_root.rglob("*.pdf"))
    if args.limit:
        paths = paths[: args.limit]

    if args.rebuild and not args.dry_run:
        reset_collection(args.collection)

    latest_state = latest_state_by_pdf(load_state_rows(state_file))
    summary = {
        "ingest_run_id": ingest_run_id,
        "collection": args.collection,
        "pdf_count": len(paths),
        "processed": 0,
        "skipped": 0,
        "completed": 0,
        "failed": 0,
        "quarantined_only": 0,
        "accepted_chunks": 0,
        "quarantined_chunks": 0,
        "inserted_chunks": 0,
        "failed_chunks": 0,
        "dry_run": bool(args.dry_run),
        "rebuild": bool(args.rebuild),
        "state_file": str(state_file),
        "run_report": str(run_report),
        "quarantine_report": str(quarantine_report) if quarantine_report else "",
        "chunking_version": chunking.version,
        "embedding_model": EMBEDDING_MODEL,
    }

    for path in paths:
        started_at = utc_now_iso()
        pdf_rel_path = normalize_pdf_path(path, pdf_root)
        doc_hash = pdf_hash(path)
        doc_id = make_doc_id(path, doc_hash)
        forced = should_force(path, pdf_rel_path, doc_id, force_docs)

        skip, skip_reason = matching_completed_state(
            latest_state.get(pdf_rel_path),
            doc_hash=doc_hash,
            chunking_version=chunking.version,
            collection=args.collection,
            retry_failed=bool(args.retry_failed),
        )
        if skip and not forced and not args.rebuild:
            summary["skipped"] += 1
            row = build_status_row(
                ingest_run_id=ingest_run_id,
                collection=args.collection,
                pdf_rel_path=pdf_rel_path,
                pdf_abs_path=path,
                doc_hash=doc_hash,
                doc_id=doc_id,
                chunking_version=chunking.version,
                status="skipped",
                started_at=started_at,
                error=skip_reason,
            )
            append_jsonl(run_report, row)
            print(json.dumps(row, ensure_ascii=False))
            continue

        try:
            chunks = build_guideline_chunks(
                path,
                department=path.parent.name,
                target_chars=chunking.target_chars,
                min_chars=chunking.min_chars,
                max_chars=chunking.max_chars,
                overlap_chars=chunking.overlap_chars,
                enable_ocr_fallback=chunking.enable_ocr_fallback,
                ocr_min_chars=chunking.ocr_min_chars,
                ocr_min_cjk_ratio=chunking.ocr_min_cjk_ratio,
                ocr_max_mojibake_ratio=chunking.ocr_max_mojibake_ratio,
                min_ocr_confidence=chunking.min_ocr_confidence,
            )
            accepted = []
            rejected = []
            block_counts: dict[str, int] = {}
            for chunk in chunks:
                block_counts[chunk.block_type] = block_counts.get(chunk.block_type, 0) + 1
                if BLOCKING_QUALITY_FLAGS & set(chunk.quality):
                    rejected.append(chunk)
                    if quarantine_report:
                        append_jsonl(quarantine_report, quarantine_row(pdf_rel_path, chunk))
                else:
                    accepted.append(chunk)

            if not accepted:
                row = build_status_row(
                    ingest_run_id=ingest_run_id,
                    collection=args.collection,
                    pdf_rel_path=pdf_rel_path,
                    pdf_abs_path=path,
                    doc_hash=doc_hash,
                    doc_id=doc_id,
                    chunking_version=chunking.version,
                    status="dry_run_quarantined_only" if args.dry_run else "quarantined_only",
                    started_at=started_at,
                    quarantined_chunks=len(rejected),
                    text_hashes=[chunk.text_hash for chunk in rejected],
                )
            else:
                write_result = {"inserted": 0, "failed": 0, "collection": args.collection}
                if not args.dry_run:
                    write_result = upsert_evidence_items(
                        [_chunk_to_evidence(chunk) for chunk in accepted],
                        collection_name=args.collection,
                        batch_size=max(1, args.batch_size),
                    )
                inserted = int(write_result.get("inserted", 0))
                failed = int(write_result.get("failed", 0))
                row = build_status_row(
                    ingest_run_id=ingest_run_id,
                    collection=args.collection,
                    pdf_rel_path=pdf_rel_path,
                    pdf_abs_path=path,
                    doc_hash=doc_hash,
                    doc_id=doc_id,
                    chunking_version=chunking.version,
                    status="dry_run_completed" if args.dry_run else ("completed" if failed == 0 else "failed"),
                    started_at=started_at,
                    accepted_chunks=len(accepted),
                    quarantined_chunks=len(rejected),
                    inserted_chunks=inserted,
                    failed_chunks=failed,
                    chunk_ids=[chunk.chunk_id for chunk in accepted],
                    text_hashes=[chunk.text_hash for chunk in accepted],
                )
            row["block_counts"] = block_counts
        except Exception as exc:
            row = build_status_row(
                ingest_run_id=ingest_run_id,
                collection=args.collection,
                pdf_rel_path=pdf_rel_path,
                pdf_abs_path=path,
                doc_hash=doc_hash,
                doc_id=doc_id,
                chunking_version=chunking.version,
                status="failed",
                started_at=started_at,
                error=f"{type(exc).__name__}: {exc}",
            )

        summary["processed"] += 1
        summary[row["status"]] = summary.get(row["status"], 0) + 1
        summary["accepted_chunks"] += int(row.get("accepted_chunks") or 0)
        summary["quarantined_chunks"] += int(row.get("quarantined_chunks") or 0)
        summary["inserted_chunks"] += int(row.get("inserted_chunks") or 0)
        summary["failed_chunks"] += int(row.get("failed_chunks") or 0)
        append_jsonl(run_report, row)
        if not args.dry_run and row["status"] != "skipped":
            append_jsonl(state_file, row)
            latest_state[pdf_rel_path] = row
        print(json.dumps(row, ensure_ascii=False))

    print(json.dumps(summary, ensure_ascii=False))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
