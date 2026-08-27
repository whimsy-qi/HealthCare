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
from rag.external.drug import normalize_drug_name, openfda_label_sections, search_dailymed_spls, search_openfda_drug_label
from rag.schema import EvidenceItem
from rag.store import reset_collection, upsert_evidence_items, utc_now_iso


INGEST_VERSION = "openfda_dailymed_label_ingest_v1"
DEFAULT_SEED = Path(__file__).resolve().parents[1] / "sources" / "drug_seed.yaml"
DEFAULT_STATE_FILE = Path(__file__).resolve().parents[1] / "ingest_state" / "openfda_label_ingest_state.jsonl"
DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "openfda_label_ingest_runs"


SAFETY_SECTIONS = {"contraindications", "warnings", "adverse_reactions", "drug_interactions", "use_in_specific_populations"}


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


def matching_state(state: dict | None, *, current_hash: str, collection: str, retry_failed: bool) -> tuple[bool, str]:
    if not state:
        return False, "not_seen"
    same = (
        state.get("seed_hash") == current_hash
        and state.get("ingest_version") == INGEST_VERSION
        and state.get("embedding_model") == EMBEDDING_MODEL
        and state.get("collection") == collection
    )
    status = state.get("status")
    if status in {"completed", "no_label_found"} and same:
        return True, status
    if status == "failed" and same and not retry_failed:
        return True, "failed_previous_run"
    if not same:
        return False, "changed"
    return False, f"status_{status or 'unknown'}"


def section_to_item(drug: dict, section: dict, rxnorm: dict) -> EvidenceItem:
    text = str(section["text"]).strip()
    text_hash = stable_hash(text, 20)
    drug_id = drug["drug_id"]
    section_title = section.get("section_title", "drug_label")
    set_id = section.get("set_id") or rxnorm.get("rxcui") or "openfda"
    chunk_id = f"fda_{drug_id}_{stable_hash(section_title, 8)}_{text_hash[:8]}"[:64]
    doc_id = f"drug_label:official:{drug_id}:{stable_hash(str(set_id), 10)}"
    return EvidenceItem(
        chunk_id=chunk_id,
        text=text[:5000],
        source_type="drug_label",
        source_tier="T1",
        title=f"{drug.get('display_name') or drug.get('query')} - {section.get('title', 'official drug label')}",
        organization="openFDA/DailyMed/RxNorm",
        department="pharmacy",
        section_title=section_title,
        doc_id=doc_id,
        text_hash=text_hash,
        license="openFDA/DailyMed public API terms",
        evidence_level="official_drug_label",
        locator={
            "doc": doc_id,
            "drug_id": drug_id,
            "rxcui": rxnorm.get("rxcui") or "",
            "set_id": str(set_id),
            "source_url": section.get("source_url", ""),
            "section": section_title,
        },
        metadata={
            "source_id": "openfda_drug_label",
            "source_name": "openFDA/DailyMed",
            "source_url": section.get("source_url", ""),
            "drug_id": drug_id,
            "drug_display_name": drug.get("display_name", ""),
            "drug_query": drug.get("query", ""),
            "rxcui": rxnorm.get("rxcui") or "",
            "generic_name": section.get("generic_name", ""),
            "brand_name": section.get("brand_name", ""),
            "section_key": section_title,
            "safety_critical": section_title in SAFETY_SECTIONS,
            "collection_key": "drug_label",
            "embedding_text": f"{drug.get('query', '')}\n{section_title}\n{text}",
        },
    )


async def build_items_for_drug(drug: dict, *, top_k: int) -> tuple[list[EvidenceItem], dict]:
    query = drug.get("query") or drug.get("display_name") or drug.get("drug_id")
    rxnorm = await normalize_drug_name(query)
    labels = await search_openfda_drug_label(query, top_k=top_k)
    dailymed = await search_dailymed_spls(query, top_k=top_k)
    sections: list[dict] = []
    for label in labels:
        sections.extend(openfda_label_sections(label))
    for spl in dailymed:
        title = spl.get("title") or query
        set_id = spl.get("setid") or spl.get("set_id") or ""
        if title:
            sections.append({
                "title": title,
                "section_title": "dailymed_spl_record",
                "text": f"DailyMed SPL record for {query}: {title}. set_id={set_id}",
                "set_id": set_id,
                "source_url": "https://dailymed.nlm.nih.gov/dailymed/",
                "generic_name": query,
                "brand_name": title,
            })
    items = [section_to_item(drug, section, rxnorm) for section in sections if section.get("text")]
    diagnostics = {
        "drug_id": drug.get("drug_id"),
        "query": query,
        "rxcui": rxnorm.get("rxcui"),
        "openfda_labels": len(labels),
        "dailymed_hits": len(dailymed),
        "sections": len(sections),
        "items": len(items),
    }
    return items, diagnostics


def status_row(*, ingest_run_id: str, collection: str, drug: dict, status: str, started_at: str, diagnostics: dict | None = None, accepted_chunks: int = 0, inserted_chunks: int = 0, failed_chunks: int = 0, chunk_ids: list[str] | None = None, error: str = "") -> dict:
    diagnostics = diagnostics or {}
    return {
        "ingest_run_id": ingest_run_id,
        "collection": collection,
        "drug_id": drug.get("drug_id", ""),
        "query": drug.get("query") or drug.get("display_name") or drug.get("drug_id", ""),
        "display_name": drug.get("display_name", ""),
        "seed_hash": seed_hash(drug),
        "ingest_version": INGEST_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "source_id": "openfda_drug_label",
        "source_type": "drug_label",
        "source_tier": "T1",
        "status": status,
        "accepted_chunks": accepted_chunks,
        "inserted_chunks": inserted_chunks,
        "failed_chunks": failed_chunks,
        "chunk_ids": chunk_ids or [],
        "diagnostics": diagnostics,
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "error": error,
    }


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Resume-safe openFDA/DailyMed official drug-label ingestion.")
    parser.add_argument("--seed", default=str(DEFAULT_SEED))
    parser.add_argument("--collection", default=COLLECTIONS["drug_label"])
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Validate seed without external API calls.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--force-drug", action="append", default=[])
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--run-report", default="")
    args = parser.parse_args()

    seed_path = Path(args.seed)
    drugs = load_seed(seed_path)
    if args.limit:
        drugs = drugs[: args.limit]
    state_file = Path(args.state_file)
    ingest_run_id = uuid.uuid4().hex[:12]
    run_report = Path(args.run_report) if args.run_report else DEFAULT_REPORT_DIR / f"{ingest_run_id}.jsonl"
    latest = {} if args.rebuild else latest_by_drug(load_jsonl(state_file))
    force = {str(value) for value in args.force_drug if str(value).strip()}
    if args.rebuild and not args.dry_run:
        reset_collection(args.collection)

    summary = {
        "ingest_run_id": ingest_run_id,
        "seed": str(seed_path),
        "collection": args.collection,
        "drugs": len(drugs),
        "processed": 0,
        "skipped": 0,
        "completed": 0,
        "no_label_found": 0,
        "failed": 0,
        "accepted_chunks": 0,
        "inserted_chunks": 0,
        "failed_chunks": 0,
        "dry_run": bool(args.dry_run),
        "offline": bool(args.offline),
        "state_file": str(state_file),
        "run_report": str(run_report),
    }

    for drug in drugs:
        started_at = utc_now_iso()
        drug_id = str(drug.get("drug_id") or "")
        current_hash = seed_hash(drug)
        skip, reason = matching_state(latest.get(drug_id), current_hash=current_hash, collection=args.collection, retry_failed=args.retry_failed)
        if skip and drug_id not in force and not args.rebuild:
            row = status_row(ingest_run_id=ingest_run_id, collection=args.collection, drug=drug, status="skipped", started_at=started_at, error=reason)
            append_jsonl(run_report, row)
            summary["skipped"] += 1
            continue
        try:
            if args.offline:
                items, diagnostics = [], {"drug_id": drug_id, "offline": True, "items": 0}
            else:
                items, diagnostics = await build_items_for_drug(drug, top_k=max(1, args.top_k))
            if not items:
                status = "offline_seed_valid" if args.offline else "no_label_found"
                row = status_row(ingest_run_id=ingest_run_id, collection=args.collection, drug=drug, status=status, started_at=started_at, diagnostics=diagnostics)
            else:
                write = {"inserted": 0, "failed": 0, "collection": args.collection}
                if not args.dry_run:
                    write = upsert_evidence_items(items, collection_name=args.collection, batch_size=max(1, args.batch_size))
                failed = int(write.get("failed", 0))
                inserted = int(write.get("inserted", 0))
                status = "dry_run_completed" if args.dry_run else ("completed" if failed == 0 else "failed")
                row = status_row(
                    ingest_run_id=ingest_run_id,
                    collection=args.collection,
                    drug=drug,
                    status=status,
                    started_at=started_at,
                    diagnostics=diagnostics,
                    accepted_chunks=len(items),
                    inserted_chunks=inserted,
                    failed_chunks=failed,
                    chunk_ids=[item.chunk_id for item in items],
                )
            append_jsonl(run_report, row)
            if not args.dry_run and not args.offline:
                append_jsonl(state_file, row)
                latest[drug_id] = row
            summary["processed"] += 1
            summary[row["status"]] = summary.get(row["status"], 0) + 1
            summary["accepted_chunks"] += int(row.get("accepted_chunks") or 0)
            summary["inserted_chunks"] += int(row.get("inserted_chunks") or 0)
            summary["failed_chunks"] += int(row.get("failed_chunks") or 0)
        except Exception as exc:
            row = status_row(ingest_run_id=ingest_run_id, collection=args.collection, drug=drug, status="failed", started_at=started_at, error=f"{type(exc).__name__}: {exc}")
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
