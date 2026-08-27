from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _audit_pages_by_pdf(audit_report: dict) -> dict[str, list[int]]:
    return {
        str(row.get("pdf_path")): [int(page) for page in (row.get("ocr_candidate_pages") or [])]
        for row in (audit_report.get("pdfs") or [])
        if row.get("pdf_path")
    }


def build_incremental_plan(ocr_dryrun: dict, audit_report: dict) -> dict:
    accepted: list[dict] = []
    manual: list[dict] = []
    rejected: list[dict] = []
    failed: list[dict] = []
    for row in ocr_dryrun.get("pages") or []:
        decision = row.get("decision")
        if decision == "accept_ocr":
            accepted.append(row)
        elif decision == "manual_review":
            manual.append(row)
        elif decision == "ocr_failed":
            failed.append(row)
        else:
            rejected.append(row)

    accepted_pdfs = sorted({str(row["pdf_path"]) for row in accepted if row.get("pdf_path")})
    audit_pages = _audit_pages_by_pdf(audit_report)
    estimated_pages = sum(len(audit_pages.get(pdf, [])) for pdf in accepted_pdfs)
    summary = ocr_dryrun.get("summary") or {}
    accept_rate = float(summary.get("accept_ocr_rate") or 0.0)
    failed_rate = float(summary.get("ocr_failed_rate") or 0.0)
    if not accepted_pdfs:
        reason = "no accepted OCR pages; do not run incremental ingestion"
    elif accept_rate < 0.3:
        reason = "accepted OCR rate below 0.3; require manual review before ingestion"
    elif failed_rate > 0.1:
        reason = "OCR failure rate above 0.1; fix OCR runtime before ingestion"
    else:
        reason = "accepted OCR sample meets thresholds; run incremental ingestion for recommended_force_docs"

    return {
        "recommended_force_docs": accepted_pdfs,
        "accepted_ocr_pages": [
            {"pdf_path": row.get("pdf_path"), "page": row.get("page"), "reason": row.get("reason")}
            for row in accepted
        ],
        "manual_review_pages": [
            {"pdf_path": row.get("pdf_path"), "page": row.get("page"), "reason": row.get("reason")}
            for row in manual
        ],
        "rejected_ocr_pages": [
            {"pdf_path": row.get("pdf_path"), "page": row.get("page"), "reason": row.get("reason")}
            for row in [*rejected, *failed]
        ],
        "estimated_pdf_count": len(accepted_pdfs),
        "estimated_page_count": estimated_pages,
        "reason": reason,
        "source_summary": summary,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an incremental PDF OCR ingestion plan from OCR dry-run output.")
    parser.add_argument("--ocr-dryrun", required=True)
    parser.add_argument("--audit-report", required=True)
    parser.add_argument("--out", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = build_incremental_plan(load_json(args.ocr_dryrun), load_json(args.audit_report))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "recommended_force_docs": plan["recommended_force_docs"],
        "estimated_pdf_count": plan["estimated_pdf_count"],
        "estimated_page_count": plan["estimated_page_count"],
        "reason": plan["reason"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
