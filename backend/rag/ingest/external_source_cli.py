from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

import yaml

from rag.config import COLLECTIONS, EMBEDDING_MODEL
from rag.ingest.cli import BLOCKING_QUALITY_FLAGS, _chunk_to_evidence
from rag.ingest.pdf import build_guideline_chunks, stable_hash
from rag.schema import EvidenceItem
from rag.sources import load_default_registry
from rag.store import reset_collection, upsert_evidence_items, utc_now_iso


INGEST_VERSION = "external_seed_ingest_v1"
DEFAULT_SEED = Path(__file__).resolve().parents[1] / "sources" / "external_seed.yaml"
DEFAULT_STATE_FILE = Path(__file__).resolve().parents[1] / "ingest_state" / "external_source_ingest_state.jsonl"
DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "external_source_ingest_runs"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / "cache" / "external_sources"

FETCHABLE_MODES = {
    "official_pdf",
    "official_html",
    "evidence_report_html",
    "structured_recommendation_html",
    "structured_summary_html",
    "topic_xml_summary",
}
MANIFEST_ONLY_MODES = {
    "manifest_only_until_licensed",
    "aggregate_signal",
    "structured_statistics",
    "terminology_api",
    "neo4j_graph",
    "neo4j_graph_ontology",
}


@dataclass(frozen=True)
class ExternalSeedEntry:
    source_id: str
    collection_key: str
    title: str
    url: str
    source_type: str
    source_tier: str
    department: str = ""
    language: str = ""
    license: str = ""
    ingest_mode: str = ""
    topic_tags: str = ""
    priority: int = 100
    organization: str = ""
    year: int = 0

    @classmethod
    def from_dict(cls, raw: dict) -> "ExternalSeedEntry":
        return cls(
            source_id=str(raw.get("source_id") or ""),
            collection_key=str(raw.get("collection_key") or ""),
            title=str(raw.get("title") or ""),
            url=str(raw.get("url") or ""),
            source_type=str(raw.get("source_type") or ""),
            source_tier=str(raw.get("source_tier") or ""),
            department=str(raw.get("department") or ""),
            language=str(raw.get("language") or ""),
            license=str(raw.get("license") or ""),
            ingest_mode=str(raw.get("ingest_mode") or ""),
            topic_tags=str(raw.get("topic_tags") or ""),
            priority=int(raw.get("priority") or 100),
            organization=str(raw.get("organization") or ""),
            year=int(raw.get("year") or 0),
        )

    @property
    def url_hash(self) -> str:
        return hashlib.sha256(self.url.encode("utf-8", errors="ignore")).hexdigest()[:16]

    @property
    def entry_hash(self) -> str:
        payload = json.dumps(self.__dict__, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:16]

    @property
    def collection_name(self) -> str:
        return COLLECTIONS.get(self.collection_key, self.collection_key)


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
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"status": "state_parse_error", "line_no": line_no, "error": line[:200]})
    return rows


def latest_state_by_url(rows: Iterable[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        url = row.get("url")
        if url:
            latest[url] = row
    return latest


def matching_completed_state(
    state: Optional[dict],
    *,
    entry_hash: str,
    collection_name: str,
    retry_failed: bool,
) -> tuple[bool, str]:
    if not state:
        return False, "not_seen"
    same = (
        state.get("entry_hash") == entry_hash
        and state.get("ingest_version") == INGEST_VERSION
        and state.get("embedding_model") == EMBEDDING_MODEL
        and state.get("collection") == collection_name
    )
    status = state.get("status")
    if status in {"completed", "manifest_only", "quarantined_only"} and same:
        return True, status
    if status == "failed" and same and not retry_failed:
        return True, "failed_previous_run"
    if not same:
        return False, "changed"
    return False, f"status_{status or 'unknown'}"


def load_seed_entries(path: Path) -> list[ExternalSeedEntry]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = [ExternalSeedEntry.from_dict(row) for row in data.get("entries", [])]
    return sorted(entries, key=lambda e: (e.priority, e.source_id, e.title))


def validate_entry(entry: ExternalSeedEntry) -> list[str]:
    flags: list[str] = []
    registry = load_default_registry()
    record = registry.get(entry.source_id)
    if not record:
        flags.append("source_not_registered")
    if not entry.url:
        flags.append("missing_url")
    if not entry.title:
        flags.append("missing_title")
    if entry.collection_key not in COLLECTIONS:
        flags.append("unknown_collection_key")
    if not entry.license:
        flags.append("missing_license")
    if record and record.source_type != entry.source_type:
        flags.append("source_type_mismatch")
    if record and record.authority_tier != entry.source_tier:
        flags.append("source_tier_mismatch")
    if entry.ingest_mode in MANIFEST_ONLY_MODES:
        flags.append("manifest_only")
    elif entry.ingest_mode not in FETCHABLE_MODES:
        flags.append("unsupported_ingest_mode")
    return flags


def _safe_cache_name(entry: ExternalSeedEntry, suffix: str) -> str:
    parsed = urlparse(entry.url)
    stem = Path(parsed.path).stem or stable_hash(entry.url, 10)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)[:80]
    return f"{entry.source_id}_{entry.url_hash}_{stem}{suffix}"


def fetch_to_cache(entry: ExternalSeedEntry, cache_dir: Path, *, timeout: int = 30) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(entry.url)
    suffix = ".pdf" if parsed.path.lower().endswith(".pdf") or entry.ingest_mode.endswith("_pdf") else ".html"
    target = cache_dir / _safe_cache_name(entry, suffix)
    if target.exists() and target.stat().st_size > 0:
        return target
    if parsed.scheme == "file":
        source = Path(parsed.path)
        target.write_bytes(source.read_bytes())
        return target
    req = urllib.request.Request(
        entry.url,
        headers={"User-Agent": "HealthSystemRAG/0.1 source-validation"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        target.write_bytes(response.read())
    return target


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</(p|div|li|h1|h2|h3|h4|tr)>", "\n", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines()]
    return "\n".join(line for line in lines if line)


def _pack_text(text: str, *, max_chars: int = 900, min_chars: int = 180) -> list[str]:
    paras = [p.strip() for p in text.splitlines() if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if not buf:
            buf = para
            continue
        if len(buf) + len(para) + 1 <= max_chars:
            buf = f"{buf}\n{para}"
        else:
            if len(buf) >= min_chars:
                chunks.append(buf)
            buf = para
    if len(buf) >= min_chars:
        chunks.append(buf)
    return chunks


def _item_from_text(entry: ExternalSeedEntry, text: str, idx: int, *, section_title: str = "external_source") -> EvidenceItem:
    text_hash = stable_hash(text, 20)
    doc_id = f"{entry.source_type}:{entry.source_id}:{entry.url_hash}"
    chunk_id = f"ext_{entry.url_hash}_{idx}_{text_hash[:8]}"
    locator = {"url": entry.url, "source_id": entry.source_id, "section": section_title}
    return EvidenceItem(
        chunk_id=chunk_id,
        text=text,
        source_type=entry.source_type,
        source_tier=entry.source_tier,
        title=entry.title,
        organization=entry.organization or entry.source_id,
        year=entry.year or None,
        department=entry.department,
        section_title=section_title,
        doc_id=doc_id,
        text_hash=text_hash,
        license=entry.license,
        locator=locator,
        metadata={
            "source_id": entry.source_id,
            "source_name": entry.source_id,
            "source_url": entry.url,
            "url": entry.url,
            "language": entry.language,
            "topic_tags": entry.topic_tags,
            "ingest_mode": entry.ingest_mode,
            "embedding_text": f"{entry.title}\n{entry.department}\n{entry.topic_tags}\n{text}",
            "collection_key": entry.collection_key,
            "causality_not_established": entry.source_type == "drug_safety_signal",
            "limitations": "FAERS/openFDA adverse event signals do not establish causality." if entry.source_type == "drug_safety_signal" else "",
        },
    )


def _build_medlineplus_items(entry: ExternalSeedEntry, path: Path, *, max_chunks: int = 300) -> tuple[list[EvidenceItem], list[dict]]:
    try:
        root = ET.parse(path).getroot()
    except Exception as exc:
        return [], [{
            "url": entry.url,
            "source_id": entry.source_id,
            "quality": ["xml_parse_failed"],
            "error": f"{type(exc).__name__}: {exc}",
        }]
    items: list[EvidenceItem] = []
    quarantine: list[dict] = []
    for idx, topic in enumerate(root.findall(".//health-topic")):
        if len(items) >= max_chunks:
            break
        language = topic.attrib.get("language", "")
        if language and language.lower() != "english":
            continue
        title = topic.attrib.get("title") or (topic.text or "").strip()
        topic_url = topic.attrib.get("url") or entry.url
        topic_id = topic.attrib.get("id") or str(idx)
        summary_node = topic.find("full-summary")
        summary = " ".join(summary_node.itertext()).strip() if summary_node is not None else ""
        summary = re.sub(r"\s+", " ", html.unescape(summary)).strip()
        also_called = [node.text.strip() for node in topic.findall("also-called") if node.text and node.text.strip()]
        mesh = [node.text.strip() for node in topic.findall(".//mesh-heading") if node.text and node.text.strip()]
        text_parts = [title]
        if also_called:
            text_parts.append("Also called: " + "; ".join(also_called[:8]))
        if mesh:
            text_parts.append("MeSH: " + "; ".join(mesh[:8]))
        if summary:
            text_parts.append(summary)
        text = "\n".join(text_parts).strip()
        if len(text) < 120:
            quarantine.append({
                "url": topic_url,
                "source_id": entry.source_id,
                "topic_id": topic_id,
                "quality": ["too_short_or_empty_topic"],
                "text_preview": text[:180],
            })
            continue
        text_hash = stable_hash(text, 20)
        chunk_id = f"ext_{entry.url_hash}_{topic_id}_{text_hash[:8]}"[:64]
        items.append(EvidenceItem(
            chunk_id=chunk_id,
            text=text,
            source_type=entry.source_type,
            source_tier=entry.source_tier,
            title=title or entry.title,
            organization="MedlinePlus",
            department=entry.department,
            section_title="health_topic_summary",
            doc_id=f"patient_education:{entry.source_id}:{topic_id}",
            text_hash=text_hash,
            license=entry.license,
            locator={"url": topic_url, "source_id": entry.source_id, "topic_id": topic_id},
            metadata={
                "source_id": entry.source_id,
                "source_name": entry.source_id,
                "source_url": topic_url,
                "url": topic_url,
                "language": language or entry.language,
                "topic_tags": entry.topic_tags,
                "mesh_terms": ";".join(mesh[:16]),
                "also_called": ";".join(also_called[:16]),
                "ingest_mode": entry.ingest_mode,
                "embedding_text": f"{title}\n{'; '.join(also_called[:8])}\n{'; '.join(mesh[:8])}\n{text}",
                "collection_key": entry.collection_key,
            },
        ))
    return items, quarantine


def build_items_from_cached_file(entry: ExternalSeedEntry, path: Path, *, max_chunks: int = 300) -> tuple[list[EvidenceItem], list[dict]]:
    quarantine: list[dict] = []
    if entry.ingest_mode == "topic_xml_summary":
        return _build_medlineplus_items(entry, path, max_chunks=max_chunks)
    if path.suffix.lower() == ".pdf":
        chunks = build_guideline_chunks(
            path,
            department=entry.department,
            target_chars=650,
            min_chars=180,
            max_chars=1100,
            overlap_chars=40,
        )
        items: list[EvidenceItem] = []
        for chunk in chunks:
            if BLOCKING_QUALITY_FLAGS & set(chunk.quality):
                quarantine.append({
                    "url": entry.url,
                    "source_id": entry.source_id,
                    "chunk_id": chunk.chunk_id,
                    "quality": chunk.quality,
                    "text_preview": chunk.text[:180],
                })
                continue
            item = _chunk_to_evidence(chunk)
            item.source_type = entry.source_type
            item.source_tier = entry.source_tier
            item.title = entry.title or item.title
            item.organization = entry.organization or entry.source_id
            item.department = entry.department or item.department
            item.license = entry.license
            item.doc_id = f"{entry.source_type}:{entry.source_id}:{entry.url_hash}"
            item.chunk_id = f"ext_{entry.url_hash}_{stable_hash(item.chunk_id, 24)}"
            item.locator = {"url": entry.url, "page": item.page_start, "source_id": entry.source_id}
            item.metadata.update({
                "source_id": entry.source_id,
                "source_name": entry.source_id,
                "source_url": entry.url,
                "url": entry.url,
                "language": entry.language,
                "topic_tags": entry.topic_tags,
                "ingest_mode": entry.ingest_mode,
                "collection_key": entry.collection_key,
            })
            items.append(item)
        return items[:max_chunks], quarantine

    raw = path.read_text(encoding="utf-8", errors="ignore")
    text = _html_to_text(raw)
    chunks = _pack_text(text)
    items = [_item_from_text(entry, chunk, idx) for idx, chunk in enumerate(chunks[:max_chunks])]
    if not items:
        quarantine.append({
            "url": entry.url,
            "source_id": entry.source_id,
            "quality": ["too_short_or_empty_html"],
            "text_preview": text[:180],
        })
    return items, quarantine


def status_row(
    *,
    ingest_run_id: str,
    entry: ExternalSeedEntry,
    status: str,
    started_at: str,
    accepted_chunks: int = 0,
    quarantined_chunks: int = 0,
    inserted_chunks: int = 0,
    failed_chunks: int = 0,
    error: str = "",
    skip_reason: str = "",
) -> dict:
    return {
        "ingest_run_id": ingest_run_id,
        "source_id": entry.source_id,
        "collection": entry.collection_name,
        "collection_key": entry.collection_key,
        "url": entry.url,
        "url_hash": entry.url_hash,
        "entry_hash": entry.entry_hash,
        "ingest_version": INGEST_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "status": status,
        "skip_reason": skip_reason,
        "title": entry.title,
        "source_type": entry.source_type,
        "source_tier": entry.source_tier,
        "license": entry.license,
        "ingest_mode": entry.ingest_mode,
        "accepted_chunks": accepted_chunks,
        "quarantined_chunks": quarantined_chunks,
        "inserted_chunks": inserted_chunks,
        "failed_chunks": failed_chunks,
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "error": error,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resume-safe seeded ingestion for external authoritative RAG sources.")
    parser.add_argument("--seed", default=str(DEFAULT_SEED))
    parser.add_argument("--source-id", action="append", default=[])
    parser.add_argument("--collection-key", default="")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fetch", action="store_true", help="Also fetch and parse content during dry-run.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-chunks-per-entry", type=int, default=300)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--force-url", action="append", default=[])
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--quarantine-report", default="")
    parser.add_argument("--run-report", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    seed_path = Path(args.seed)
    state_file = Path(args.state_file)
    cache_dir = Path(args.cache_dir)
    ingest_run_id = uuid.uuid4().hex[:12]
    run_report = Path(args.run_report) if args.run_report else DEFAULT_REPORT_DIR / f"{ingest_run_id}.jsonl"
    quarantine_report = Path(args.quarantine_report) if args.quarantine_report else None

    entries = load_seed_entries(seed_path)
    if args.source_id:
        source_ids = set(args.source_id)
        entries = [entry for entry in entries if entry.source_id in source_ids]
    if args.collection_key:
        entries = [entry for entry in entries if entry.collection_key == args.collection_key]
    if args.limit:
        entries = entries[: args.limit]

    latest = latest_state_by_url(load_state_rows(state_file))
    force_urls = set(args.force_url)
    if args.rebuild and not args.dry_run:
        if not args.collection_key:
            raise SystemExit("--rebuild requires --collection-key for external multi-collection ingestion")
        reset_collection(COLLECTIONS[args.collection_key])
        latest = {}

    grouped_items: dict[str, list[EvidenceItem]] = {}
    rows: list[dict] = []
    quarantine_rows: list[dict] = []
    stats = {
        "entries": len(entries),
        "accepted_entries": 0,
        "manifest_only_entries": 0,
        "skipped_entries": 0,
        "failed_entries": 0,
        "accepted_chunks": 0,
        "quarantined_chunks": 0,
        "inserted_chunks": 0,
        "failed_chunks": 0,
    }

    for entry in entries:
        started_at = utc_now_iso()
        flags = validate_entry(entry)
        if "manifest_only" in flags:
            row = status_row(ingest_run_id=ingest_run_id, entry=entry, status="manifest_only", started_at=started_at)
            rows.append(row)
            stats["manifest_only_entries"] += 1
            append_jsonl(run_report, row)
            continue
        blocking_flags = [flag for flag in flags if flag != "manifest_only"]
        if blocking_flags:
            row = status_row(
                ingest_run_id=ingest_run_id,
                entry=entry,
                status="quarantined_only",
                started_at=started_at,
                error=",".join(blocking_flags),
            )
            rows.append(row)
            quarantine_rows.append({**row, "quality": blocking_flags})
            stats["failed_entries"] += 1
            append_jsonl(run_report, row)
            continue

        should_skip, reason = matching_completed_state(
            latest.get(entry.url),
            entry_hash=entry.entry_hash,
            collection_name=entry.collection_name,
            retry_failed=args.retry_failed,
        )
        if should_skip and entry.url not in force_urls:
            row = status_row(
                ingest_run_id=ingest_run_id,
                entry=entry,
                status="skipped",
                started_at=started_at,
                skip_reason=reason,
            )
            rows.append(row)
            stats["skipped_entries"] += 1
            append_jsonl(run_report, row)
            continue

        try:
            if args.dry_run and not args.fetch:
                item = _item_from_text(entry, f"{entry.title}\n{entry.url}\n{entry.topic_tags}", 0, section_title="manifest_preview")
                items, quarantine = [item], []
            else:
                path = fetch_to_cache(entry, cache_dir)
                items, quarantine = build_items_from_cached_file(entry, path, max_chunks=max(1, args.max_chunks_per_entry))
                time.sleep(0.3)
            for q in quarantine:
                quarantine_rows.append(q)
            grouped_items.setdefault(entry.collection_name, []).extend(items)
            stats["accepted_entries"] += 1
            stats["accepted_chunks"] += len(items)
            stats["quarantined_chunks"] += len(quarantine)
            row = status_row(
                ingest_run_id=ingest_run_id,
                entry=entry,
                status="completed" if items else "quarantined_only",
                started_at=started_at,
                accepted_chunks=len(items),
                quarantined_chunks=len(quarantine),
            )
            rows.append(row)
            append_jsonl(run_report, row)
        except Exception as exc:
            stats["failed_entries"] += 1
            row = status_row(
                ingest_run_id=ingest_run_id,
                entry=entry,
                status="failed",
                started_at=started_at,
                error=f"{type(exc).__name__}: {exc}",
            )
            rows.append(row)
            append_jsonl(run_report, row)

    if quarantine_report:
        quarantine_report.parent.mkdir(parents=True, exist_ok=True)
        quarantine_report.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in quarantine_rows),
            encoding="utf-8",
        )

    write_results = []
    if not args.dry_run:
        write_by_collection = {}
        for collection_name, items in grouped_items.items():
            result = upsert_evidence_items(items, collection_name=collection_name, batch_size=max(1, args.batch_size))
            write_results.append(result)
            write_by_collection[collection_name] = result
            stats["inserted_chunks"] += int(result.get("inserted", 0))
            stats["failed_chunks"] += int(result.get("failed", 0))
        for row in rows:
            write_result = write_by_collection.get(row.get("collection"))
            if row["status"] == "completed" and write_result:
                failed = int(write_result.get("failed", 0) or 0)
                inserted = int(write_result.get("inserted", 0) or 0)
                row["inserted_chunks"] = inserted
                row["failed_chunks"] = failed
                if failed:
                    row["status"] = "failed"
                    row["error"] = f"collection_write_failed: inserted={inserted}, failed={failed}"
            if row["status"] in {"completed", "manifest_only", "quarantined_only", "failed"}:
                append_jsonl(state_file, row)

    summary = {
        **stats,
        "dry_run": bool(args.dry_run),
        "fetched_in_dry_run": bool(args.dry_run and args.fetch),
        "seed": str(seed_path),
        "state_file": str(state_file),
        "run_report": str(run_report),
        "quarantine_report": str(quarantine_report) if quarantine_report else "",
        "collections": {name: len(items) for name, items in grouped_items.items()},
        "write": write_results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
