from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from rag.config import DEFAULT_PDF_ROOT
from rag.ingest.pdf import MOJIBAKE_RE, clean_pdf_text


CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def text_quality(text: str) -> dict:
    text = text or ""
    chars = [ch for ch in text if not ch.isspace()]
    total = len(chars)
    cjk = len(CJK_RE.findall(text))
    mojibake = len(MOJIBAKE_RE.findall(text))
    cjk_ratio = cjk / max(total, 1)
    mojibake_ratio = mojibake / max(total, 1)
    flags = []
    if total < 120:
        flags.append("too_short")
    if cjk_ratio < 0.15 and total >= 120:
        flags.append("low_cjk_ratio")
    if mojibake_ratio > 0.01:
        flags.append("mojibake")
    if total < 60 or mojibake_ratio > 0.02 or (total >= 120 and cjk_ratio < 0.08):
        flags.append("ocr_candidate")
    return {
        "chars": total,
        "cjk_ratio": round(cjk_ratio, 6),
        "mojibake_ratio": round(mojibake_ratio, 6),
        "flags": flags,
    }


def audit_pdf(path: Path) -> dict:
    import fitz

    pages = []
    try:
        with fitz.open(path) as doc:
            for idx, page in enumerate(doc, start=1):
                raw = page.get_text("text")
                cleaned = clean_pdf_text(raw)
                quality = text_quality(cleaned)
                pages.append({
                    "page": idx,
                    **quality,
                    "text_preview": cleaned[:160],
                })
    except Exception as exc:
        return {
            "pdf_path": str(path),
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "pages": [],
        }

    ocr_candidates = [row["page"] for row in pages if "ocr_candidate" in row["flags"]]
    mojibake_pages = [row["page"] for row in pages if "mojibake" in row["flags"]]
    too_short_pages = [row["page"] for row in pages if "too_short" in row["flags"]]
    return {
        "pdf_path": str(path),
        "status": "completed",
        "page_count": len(pages),
        "ocr_candidate_pages": ocr_candidates,
        "mojibake_pages": mojibake_pages,
        "too_short_pages": too_short_pages,
        "ocr_candidate_rate": round(len(ocr_candidates) / max(len(pages), 1), 6),
        "mojibake_page_rate": round(len(mojibake_pages) / max(len(pages), 1), 6),
        "avg_cjk_ratio": round(sum(row["cjk_ratio"] for row in pages) / max(len(pages), 1), 6),
        "pages": pages,
    }


def summarize(rows: list[dict]) -> dict:
    completed = [row for row in rows if row.get("status") == "completed"]
    failed = [row for row in rows if row.get("status") == "failed"]
    total_pages = sum(int(row.get("page_count") or 0) for row in completed)
    ocr_pages = sum(len(row.get("ocr_candidate_pages") or []) for row in completed)
    mojibake_pages = sum(len(row.get("mojibake_pages") or []) for row in completed)
    return {
        "pdf_count": len(rows),
        "completed": len(completed),
        "failed": len(failed),
        "total_pages": total_pages,
        "ocr_candidate_pages": ocr_pages,
        "ocr_candidate_rate": round(ocr_pages / max(total_pages, 1), 6),
        "mojibake_pages": mojibake_pages,
        "mojibake_page_rate": round(mojibake_pages / max(total_pages, 1), 6),
        "failed_pdfs": [{"pdf_path": row.get("pdf_path"), "error": row.get("error")} for row in failed],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only PDF extraction quality audit for RAG ingestion.")
    parser.add_argument("--pdf-root", default=str(DEFAULT_PDF_ROOT))
    parser.add_argument("--out", default="")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    pdf_root = Path(args.pdf_root)
    paths = sorted(pdf_root.rglob("*.pdf"))
    if args.limit:
        paths = paths[: args.limit]
    rows = [audit_pdf(path) for path in paths]
    report = {"summary": summarize(rows), "pdfs": rows}
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 1 if report["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
