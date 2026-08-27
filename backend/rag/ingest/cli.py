from __future__ import annotations

import argparse
import json
from pathlib import Path

from rag.config import COLLECTIONS, DEFAULT_PDF_ROOT
from rag.ingest.pdf import build_guideline_chunks
from rag.schema import EvidenceItem
from rag.store import reset_collection, upsert_evidence_items


BLOCKING_QUALITY_FLAGS = {"mojibake", "likely_references", "missing_page", "missing_title", "too_short"}


def _chunk_to_evidence(chunk) -> EvidenceItem:
    metadata = {
        "quality": ",".join(chunk.quality),
        "parent_id": getattr(chunk, "parent_id", ""),
        "section_path": " > ".join(getattr(chunk, "section_path", []) or []),
        "block_type": getattr(chunk, "block_type", "paragraph"),
        "embedding_text": getattr(chunk, "embedding_text", ""),
        "extraction_method": getattr(chunk, "extraction_method", "pymupdf"),
        "ocr_engine": "PaddleOCR" if getattr(chunk, "extraction_method", "") == "paddleocr" else "",
        "ocr_confidence": getattr(chunk, "ocr_confidence", 0.0),
        "layout_type": getattr(chunk, "layout_type", "paragraph"),
        "sibling_prev": getattr(chunk, "sibling_prev", ""),
        "sibling_next": getattr(chunk, "sibling_next", ""),
    }
    return EvidenceItem(
        chunk_id=chunk.chunk_id,
        text=chunk.text,
        source_type=chunk.source_type,
        source_tier=chunk.source_tier,
        title=chunk.title,
        organization=chunk.organization,
        year=chunk.year,
        department=chunk.department,
        section_title=chunk.section_title,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        doc_id=chunk.doc_id,
        text_hash=chunk.text_hash,
        license=chunk.license,
        evidence_level=chunk.evidence_level,
        locator={"doc": chunk.doc_id, "page": chunk.page_start, "title": chunk.title},
        metadata=metadata,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build structured medical RAG chunks from local PDFs.")
    parser.add_argument("--pdf-root", default=str(DEFAULT_PDF_ROOT))
    parser.add_argument("--collection", default=COLLECTIONS["guideline"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--quarantine-report", default="")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--target-chars", type=int, default=650)
    parser.add_argument("--min-chars", type=int, default=180)
    parser.add_argument("--max-chars", type=int, default=1100)
    parser.add_argument("--overlap-chars", type=int, default=40)
    parser.add_argument("--enable-ocr-fallback", action="store_true")
    parser.add_argument("--ocr-min-chars", type=int, default=60)
    parser.add_argument("--ocr-min-cjk-ratio", type=float, default=0.08)
    parser.add_argument("--ocr-max-mojibake-ratio", type=float, default=0.02)
    parser.add_argument("--min-ocr-confidence", type=float, default=0.5)
    args = parser.parse_args()

    paths = sorted(Path(args.pdf_root).rglob("*.pdf"))
    if args.limit:
        paths = paths[: args.limit]

    total_chunks = 0
    quarantined = 0
    evidence_items = []
    quarantine_rows = []
    for path in paths:
        chunks = build_guideline_chunks(
            path,
            department=path.parent.name,
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
        accepted = []
        rejected = []
        block_counts = {}
        for chunk in chunks:
            block_counts[chunk.block_type] = block_counts.get(chunk.block_type, 0) + 1
            if BLOCKING_QUALITY_FLAGS & set(chunk.quality):
                rejected.append(chunk)
                quarantine_rows.append({
                    "pdf": str(path),
                    "chunk_id": chunk.chunk_id,
                    "title": chunk.title,
                    "page_start": chunk.page_start,
                    "quality": chunk.quality,
                    "text_preview": chunk.text[:160],
                })
            else:
                accepted.append(chunk)
                evidence_items.append(_chunk_to_evidence(chunk))
        total_chunks += len(chunks)
        quarantined += len(rejected)
        print(json.dumps({
            "pdf": str(path),
            "chunks": len(chunks),
            "accepted": len(accepted),
            "quarantined": len(rejected),
            "block_counts": block_counts,
        }, ensure_ascii=False))

    write_result = {"inserted": 0, "failed": 0, "collection": args.collection}
    if args.quarantine_report:
        Path(args.quarantine_report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.quarantine_report).write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in quarantine_rows),
            encoding="utf-8",
        )
    if not args.dry_run:
        if args.rebuild:
            reset_collection(args.collection)
        write_result = upsert_evidence_items(
            evidence_items,
            collection_name=args.collection,
            batch_size=max(1, args.batch_size),
        )

    print(json.dumps({
        "pdf_count": len(paths),
        "total_chunks": total_chunks,
        "accepted_chunks": len(evidence_items),
        "quarantined_chunks": quarantined,
        "dry_run": bool(args.dry_run),
        "chunking": {
            "target_chars": args.target_chars,
            "min_chars": args.min_chars,
            "max_chars": args.max_chars,
            "overlap_chars": args.overlap_chars,
            "strategy": "structure_aware_semantic_packing_v1",
        },
        "write": write_result,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
