from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pandas as pd

from rag.config import BACKEND_ROOT, COLLECTIONS
from rag.schema import EvidenceItem
from rag.store import reset_collection, upsert_evidence_items


DEFAULT_DRUG_ROOT = BACKEND_ROOT / "drug_data"
SOURCE_NAME = "nmpa_cfda_local_snapshot"
SOURCE_TIER = "T1"
LICENSE = "local_official_snapshot_review_required"
EVIDENCE_LEVEL = "official_drug_label_local_snapshot"

DEDUP_COLUMNS = ["通用名称", "批准文号", "生产企业"]
IDENTITY_COLUMNS = {
    "drug_name": "通用名称",
    "brand_name": "商品名称",
    "approval_no": "批准文号",
    "drug_class": "药品分类",
    "producer": "生产企业",
    "related_diseases": "相关疾病",
    "source_url": "标题链接",
}
SECTION_COLUMNS = [
    ("indications", "适应症", False),
    ("contraindications", "禁忌", True),
    ("adverse_reactions", "不良反应", True),
    ("dosage", "用法用量", False),
    ("precautions", "注意事项", True),
    ("pregnancy_lactation", "孕妇及哺乳期妇女用药", True),
    ("pediatric_use", "儿童用药", True),
    ("geriatric_use", "老人用药", True),
    ("drug_interactions", "药物相互作用", True),
    ("pharmacology_toxicology", "药理毒理", False),
    ("pharmacokinetics", "药代动力学", False),
]
BODY_COLUMNS = [label for _, label, _ in SECTION_COLUMNS]
MOJIBAKE_MARKERS = ("锟", "ï¼", "Ã", "\ufffd")


def stable_hash(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def clean_cell(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.strip(" \t,，;；") for line in text.splitlines() if line.strip(" \t,，;；"))
    return text.strip()


def row_key(row: Dict[str, str]) -> Tuple[str, str, str]:
    return tuple(clean_cell(row.get(col)) for col in DEDUP_COLUMNS)


def row_quality_flags(row: Dict[str, str]) -> List[str]:
    flags: List[str] = []
    drug_name = clean_cell(row.get("通用名称"))
    approval_no = clean_cell(row.get("批准文号"))
    body = "\n".join(clean_cell(row.get(col)) for col in BODY_COLUMNS)
    if not drug_name:
        flags.append("missing_drug_name")
    if not approval_no:
        flags.append("missing_approval_no")
    if len(body.strip()) < 20:
        flags.append("body_too_short")
    if sum(body.count(marker) for marker in MOJIBAKE_MARKERS) / max(len(body), 1) > 0.01:
        flags.append("mojibake")
    return flags


def _row_identity(row: Dict[str, str]) -> Dict[str, str]:
    return {name: clean_cell(row.get(col)) for name, col in IDENTITY_COLUMNS.items()}


def row_to_items(row: Dict[str, str]) -> List[EvidenceItem]:
    identity = _row_identity(row)
    approval_no = identity["approval_no"]
    approval_hash = stable_hash(approval_no, 10)
    row_hash = stable_hash("|".join(row_key(row)), 10)
    doc_id = f"drug_label:{approval_no}:{row_hash}"
    title = identity["drug_name"]
    if identity["brand_name"]:
        title = f"{title}（{identity['brand_name']}）"

    items: List[EvidenceItem] = []
    for section_key, section_title, safety_critical in SECTION_COLUMNS:
        text = clean_cell(row.get(section_title))
        if not text:
            continue
        text_hash = stable_hash(text, 20)
        chunk_id = f"drug_{approval_hash}_{section_key}_{text_hash[:8]}"
        items.append(
            EvidenceItem(
                chunk_id=chunk_id,
                text=text,
                source_type="drug_label",
                source_tier=SOURCE_TIER,
                title=title,
                organization=identity["producer"],
                department="pharmacy",
                section_title=section_title,
                doc_id=doc_id,
                text_hash=text_hash,
                license=LICENSE,
                evidence_level=EVIDENCE_LEVEL,
                locator={
                    "doc": doc_id,
                    "approval_no": approval_no,
                    "section": section_title,
                    "source_url": identity["source_url"],
                },
                metadata={
                    **identity,
                    "section_key": section_key,
                    "source_name": SOURCE_NAME,
                    "safety_critical": bool(safety_critical),
                    "collection_key": COLLECTIONS["drug_label"],
                    "official_source_assumption": True,
                    "source_provenance": "cfda_nmpa_local_snapshot",
                    "source_verified_online": False,
                },
            )
        )
    return items


def iter_excel_rows(drug_root: Path, *, limit: int = 0) -> Iterable[Tuple[Path, int, Dict[str, str]]]:
    yielded = 0
    for path in sorted(drug_root.glob("*.xlsx")):
        df = pd.read_excel(path, dtype=str)
        for idx, raw in df.iterrows():
            yield path, int(idx) + 2, raw.to_dict()
            yielded += 1
            if limit and yielded >= limit:
                return


def build_local_drug_items(drug_root: Path, *, limit: int = 0) -> tuple[List[EvidenceItem], Dict, List[Dict], List[Dict]]:
    items: List[EvidenceItem] = []
    quarantine_rows: List[Dict] = []
    dedupe_rows: List[Dict] = []
    seen = set()
    stats = {
        "files": 0,
        "rows": 0,
        "accepted_rows": 0,
        "duplicate_rows": 0,
        "quarantined_rows": 0,
        "items": 0,
    }
    files_seen = set()

    for path, row_no, row in iter_excel_rows(drug_root, limit=limit):
        files_seen.add(str(path))
        stats["rows"] += 1
        key = row_key(row)
        flags = row_quality_flags(row)
        if flags:
            stats["quarantined_rows"] += 1
            quarantine_rows.append({
                "file": str(path),
                "row": row_no,
                "quality": flags,
                "drug_name": clean_cell(row.get("通用名称")),
                "approval_no": clean_cell(row.get("批准文号")),
            })
            continue
        if key in seen:
            stats["duplicate_rows"] += 1
            dedupe_rows.append({
                "file": str(path),
                "row": row_no,
                "dedupe_key": list(key),
            })
            continue
        seen.add(key)
        row_items = row_to_items(row)
        if not row_items:
            stats["quarantined_rows"] += 1
            quarantine_rows.append({
                "file": str(path),
                "row": row_no,
                "quality": ["no_section_chunks"],
                "drug_name": clean_cell(row.get("通用名称")),
                "approval_no": clean_cell(row.get("批准文号")),
            })
            continue
        stats["accepted_rows"] += 1
        items.extend(row_items)

    stats["files"] = len(files_seen)
    stats["items"] = len(items)
    return items, stats, quarantine_rows, dedupe_rows


def _write_jsonl(path: str, rows: List[Dict]) -> None:
    if not path:
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")


def resolve_drug_root(raw: str) -> Path:
    root = Path(raw)
    if root.exists():
        return root
    project_relative = BACKEND_ROOT.parent / root
    if project_relative.exists():
        return project_relative
    if root.parts and root.parts[0].lower() == "backend":
        backend_relative = BACKEND_ROOT.joinpath(*root.parts[1:])
        if backend_relative.exists():
            return backend_relative
    return root


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Clean local Chinese drug-label Excel files and ingest into RAG v2.")
    parser.add_argument("--drug-root", default=str(DEFAULT_DRUG_ROOT))
    parser.add_argument("--collection", default=COLLECTIONS["drug_label"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--quarantine-report", default="")
    parser.add_argument("--dedupe-report", default="")
    args = parser.parse_args()

    drug_root = resolve_drug_root(args.drug_root)
    items, stats, quarantine_rows, dedupe_rows = build_local_drug_items(drug_root, limit=args.limit)
    _write_jsonl(args.quarantine_report, quarantine_rows)
    _write_jsonl(args.dedupe_report, dedupe_rows)

    write_result = {"inserted": 0, "failed": 0, "collection": args.collection}
    if not args.dry_run:
        if args.rebuild:
            reset_collection(args.collection)
        write_result = upsert_evidence_items(items, collection_name=args.collection, batch_size=max(1, args.batch_size))

    print(json.dumps({
        "drug_root": str(drug_root),
        "collection": args.collection,
        "dry_run": bool(args.dry_run),
        "rebuild": bool(args.rebuild),
        "stats": stats,
        "write": write_result,
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
