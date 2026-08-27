from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path
from typing import Iterable

from rag.config import DEFAULT_PDF_ROOT
from rag.ingest.pdf import clean_pdf_text, ocr_pdf_page, page_quality_stats


CORE_TOPIC_TERMS = [
    "高血压",
    "糖尿病",
    "心力衰竭",
    "心衰",
    "肺癌",
    "哮喘",
    "维生素A",
    "维生素D",
]
REQUIRED_MODULES = {
    "paddleocr": "paddleocr",
    "paddle": "paddlepaddle",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "numpy": "numpy",
}
REFERENCE_RE = re.compile(r"(参考文献|References?)", re.I)


def missing_dependencies() -> list[str]:
    missing = []
    for module_name, package_name in REQUIRED_MODULES.items():
        if importlib.util.find_spec(module_name) is None:
            missing.append(package_name)
    return sorted(set(missing))


def load_audit_report(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _is_core_pdf(pdf_path: str) -> bool:
    return any(term.lower() in pdf_path.lower() for term in CORE_TOPIC_TERMS)


def select_candidate_pages(
    audit_report: dict,
    *,
    limit_pages: int = 40,
    max_pages_per_pdf: int = 5,
) -> list[dict]:
    candidates = []
    for pdf in audit_report.get("pdfs") or []:
        pdf_path = str(pdf.get("pdf_path") or "")
        pages = list(pdf.get("ocr_candidate_pages") or [])
        if not pdf_path or not pages:
            continue
        rate = float(pdf.get("ocr_candidate_rate") or 0.0)
        priority = 0
        if rate >= 1.0:
            priority += 100
        if _is_core_pdf(pdf_path):
            priority += 50
        priority += min(int(rate * 30), 30)
        for page in pages[: max(1, max_pages_per_pdf)]:
            candidates.append({
                "pdf_path": pdf_path,
                "page": int(page),
                "priority": priority,
                "ocr_candidate_rate": rate,
            })
    candidates.sort(key=lambda row: (-row["priority"], row["pdf_path"], row["page"]))
    return candidates[: max(0, limit_pages)]


def _read_pymupdf_page(pdf_path: Path, page_no: int) -> str:
    import fitz

    with fitz.open(pdf_path) as doc:
        if page_no < 1 or page_no > len(doc):
            raise ValueError(f"page out of range: {page_no}")
        return clean_pdf_text(doc[page_no - 1].get_text("text"))


def _quality_row(prefix: str, text: str) -> dict:
    stats = page_quality_stats(text)
    return {
        f"{prefix}_chars": int(stats["chars"]),
        f"{prefix}_cjk_ratio": round(float(stats["cjk_ratio"]), 6),
        f"{prefix}_mojibake_ratio": round(float(stats["mojibake_ratio"]), 6),
    }


def decide_ocr(
    *,
    pymupdf_text: str,
    ocr_text: str,
    ocr_confidence: float,
    min_confidence: float = 0.75,
    min_cjk_ratio: float = 0.25,
) -> tuple[str, str]:
    py_stats = page_quality_stats(pymupdf_text)
    ocr_stats = page_quality_stats(ocr_text)
    if not ocr_text:
        return "ocr_failed", "empty_ocr_text"
    if ocr_confidence < min_confidence:
        return "reject_ocr", "low_confidence"
    if REFERENCE_RE.search(ocr_text[:300]):
        return "manual_review", "possible_references"
    if ocr_stats["chars"] < 80:
        return "reject_ocr", "ocr_too_short"
    if ocr_stats["cjk_ratio"] < min_cjk_ratio:
        return "manual_review", "low_ocr_cjk_ratio"
    if ocr_stats["mojibake_ratio"] > py_stats["mojibake_ratio"]:
        return "reject_ocr", "ocr_mojibake_worse"
    length_gain = ocr_stats["chars"] >= py_stats["chars"] * 1.2
    rescue_short_page = py_stats["chars"] < 60 and ocr_stats["chars"] > 180
    if length_gain or rescue_short_page:
        return "accept_ocr", "ocr_quality_improved"
    return "manual_review", "ocr_not_clearly_better"


def run_ocr_for_candidate(
    candidate: dict,
    *,
    pdf_root: Path,
    min_confidence: float = 0.75,
    min_cjk_ratio: float = 0.25,
) -> dict:
    pdf_rel = Path(candidate["pdf_path"])
    pdf_path = pdf_rel if pdf_rel.is_absolute() else pdf_root / pdf_rel
    page_no = int(candidate["page"])
    pymupdf_text = _read_pymupdf_page(pdf_path, page_no)
    try:
        import fitz

        with fitz.open(pdf_path) as doc:
            ocr_text, ocr_confidence = ocr_pdf_page(doc[page_no - 1])
        decision, reason = decide_ocr(
            pymupdf_text=pymupdf_text,
            ocr_text=ocr_text,
            ocr_confidence=float(ocr_confidence),
            min_confidence=min_confidence,
            min_cjk_ratio=min_cjk_ratio,
        )
    except Exception as exc:
        ocr_text = ""
        ocr_confidence = 0.0
        decision = "ocr_failed"
        reason = f"{type(exc).__name__}: {exc}"

    py_stats = page_quality_stats(pymupdf_text)
    ocr_stats = page_quality_stats(ocr_text)
    return {
        "pdf_path": str(candidate["pdf_path"]),
        "page": page_no,
        **_quality_row("pymupdf", pymupdf_text),
        **_quality_row("ocr", ocr_text),
        "ocr_confidence": round(float(ocr_confidence), 6),
        "quality_delta": {
            "chars": int(ocr_stats["chars"]) - int(py_stats["chars"]),
            "cjk_ratio": round(float(ocr_stats["cjk_ratio"] - py_stats["cjk_ratio"]), 6),
            "mojibake_ratio": round(float(ocr_stats["mojibake_ratio"] - py_stats["mojibake_ratio"]), 6),
        },
        "decision": decision,
        "reason": reason,
        "pymupdf_preview": pymupdf_text[:180],
        "ocr_preview": ocr_text[:180],
    }


def summarize(rows: Iterable[dict], *, selected_pages: int) -> dict:
    rows = list(rows)
    total = len(rows)
    counts: dict[str, int] = {}
    for row in rows:
        decision = str(row.get("decision") or "unknown")
        counts[decision] = counts.get(decision, 0) + 1
    return {
        "selected_pages": selected_pages,
        "processed_pages": total,
        "accept_ocr": counts.get("accept_ocr", 0),
        "reject_ocr": counts.get("reject_ocr", 0),
        "manual_review": counts.get("manual_review", 0),
        "ocr_failed": counts.get("ocr_failed", 0),
        "accept_ocr_rate": round(counts.get("accept_ocr", 0) / max(total, 1), 6),
        "ocr_failed_rate": round(counts.get("ocr_failed", 0) / max(total, 1), 6),
        "decision_counts": counts,
    }


def build_report(
    *,
    audit_report: dict,
    pdf_root: Path,
    limit_pages: int = 40,
    max_pages_per_pdf: int = 5,
    min_confidence: float = 0.75,
    min_cjk_ratio: float = 0.25,
) -> dict:
    selected = select_candidate_pages(
        audit_report,
        limit_pages=limit_pages,
        max_pages_per_pdf=max_pages_per_pdf,
    )
    rows = [
        run_ocr_for_candidate(
            candidate,
            pdf_root=pdf_root,
            min_confidence=min_confidence,
            min_cjk_ratio=min_cjk_ratio,
        )
        for candidate in selected
    ]
    return {
        "summary": summarize(rows, selected_pages=len(selected)),
        "pages": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run OCR only for pages selected by pdf_quality_audit.")
    parser.add_argument("--audit-report", required=True)
    parser.add_argument("--pdf-root", default=str(DEFAULT_PDF_ROOT))
    parser.add_argument("--limit-pages", type=int, default=40)
    parser.add_argument("--max-pages-per-pdf", type=int, default=5)
    parser.add_argument("--min-confidence", type=float, default=0.75)
    parser.add_argument("--min-cjk-ratio", type=float, default=0.25)
    parser.add_argument("--out", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    missing = missing_dependencies()
    if missing:
        print(json.dumps({
            "status": "missing_dependencies",
            "missing": missing,
            "install": f"pip install {' '.join(missing)}",
        }, ensure_ascii=False, indent=2))
        return 2
    report = build_report(
        audit_report=load_audit_report(args.audit_report),
        pdf_root=Path(args.pdf_root),
        limit_pages=args.limit_pages,
        max_pages_per_pdf=args.max_pages_per_pdf,
        min_confidence=args.min_confidence,
        min_cjk_ratio=args.min_cjk_ratio,
    )
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
