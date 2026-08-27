from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

import aiohttp
import yaml

from rag.config import COLLECTIONS, EMBEDDING_MODEL
from rag.external.pubmed import PUBMED_BASE
from rag.schema import EvidenceItem
from rag.store import reset_collection, upsert_evidence_items, utc_now_iso


INGEST_VERSION = "pubmed_abstract_ingest_v1"
DEFAULT_SEED = Path(__file__).resolve().parents[1] / "sources" / "research_seed.yaml"
DEFAULT_STATE_FILE = Path(__file__).resolve().parents[1] / "ingest_state" / "pubmed_ingest_state.jsonl"
DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "pubmed_ingest_runs"


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


def latest_by_pmid(rows: Iterable[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        pmid = row.get("pmid")
        if pmid:
            latest[str(pmid)] = row
    return latest


def load_seed(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("queries") or [])


def record_hash(record: dict) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
    return stable_hash(payload, 20)


def matching_state(state: dict | None, *, current_hash: str, collection: str, retry_failed: bool) -> tuple[bool, str]:
    if not state:
        return False, "not_seen"
    same = (
        state.get("record_hash") == current_hash
        and state.get("ingest_version") == INGEST_VERSION
        and state.get("embedding_model") == EMBEDDING_MODEL
        and state.get("collection") == collection
    )
    status = state.get("status")
    if status == "completed" and same:
        return True, "completed"
    if status == "failed" and same and not retry_failed:
        return True, "failed_previous_run"
    if not same:
        return False, "changed"
    return False, f"status_{status or 'unknown'}"


async def fetch_pubmed_records(query: str, *, top_k: int = 8) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{PUBMED_BASE}/esearch.fcgi",
            params={"db": "pubmed", "term": query, "retmax": str(max(1, min(top_k, 100))), "retmode": "json", "sort": "relevance"},
            timeout=aiohttp.ClientTimeout(total=12),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"PubMed esearch failed: HTTP {resp.status}")
            search_data = await resp.json()
        pmids = search_data.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return []
        async with session.get(
            f"{PUBMED_BASE}/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(pmids), "retmode": "xml", "rettype": "abstract"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"PubMed efetch failed: HTTP {resp.status}")
            xml_text = await resp.text()
    root = ET.fromstring(xml_text)
    records: list[dict] = []
    for article in root.findall(".//PubmedArticle"):
        medline = article.find("MedlineCitation")
        article_node = medline.find("Article") if medline is not None else article.find(".//Article")
        if medline is None or article_node is None:
            continue
        pmid = medline.findtext("PMID") or ""
        title = "".join(article_node.findtext("ArticleTitle") or "").strip()
        abstract = " ".join(" ".join(node.itertext()).strip() for node in article_node.findall(".//AbstractText"))
        journal = article_node.findtext(".//Journal/Title") or article_node.findtext(".//ISOAbbreviation") or ""
        year_text = (
            article_node.findtext(".//JournalIssue/PubDate/Year")
            or article_node.findtext(".//ArticleDate/Year")
            or ""
        )
        pub_types = [node.text.strip() for node in article_node.findall(".//PublicationType") if node.text and node.text.strip()]
        mesh_terms = [node.findtext("DescriptorName") or "" for node in medline.findall(".//MeshHeading")]
        doi = ""
        for node in article_node.findall(".//ArticleId"):
            if node.attrib.get("IdType") == "doi" and node.text:
                doi = node.text.strip()
                break
        if pmid and (title or abstract):
            records.append({
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "journal": journal,
                "year": int(year_text) if year_text.isdigit() else 0,
                "publication_types": pub_types,
                "mesh_terms": [term for term in mesh_terms if term],
                "doi": doi,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            })
    return records


def evidence_level(record: dict) -> str:
    types = " ".join(record.get("publication_types") or []).lower()
    if "meta-analysis" in types:
        return "meta_analysis"
    if "systematic review" in types:
        return "systematic_review"
    if "randomized controlled trial" in types:
        return "randomized_controlled_trial"
    if "clinical trial" in types:
        return "clinical_trial"
    if "guideline" in types:
        return "guideline"
    return "pubmed_abstract"


def record_to_item(record: dict, seed: dict) -> EvidenceItem:
    text = "\n".join(part for part in [
        record.get("title", ""),
        record.get("abstract", ""),
        "Publication types: " + "; ".join(record.get("publication_types") or []),
        "MeSH: " + "; ".join(record.get("mesh_terms") or []),
    ] if part.strip())
    pmid = str(record["pmid"])
    text_hash = stable_hash(text, 20)
    chunk_id = f"pmid_{pmid}_{text_hash[:10]}"[:64]
    return EvidenceItem(
        chunk_id=chunk_id,
        text=text[:5000],
        source_type="pubmed",
        source_tier="T2",
        title=record.get("title", "") or f"PubMed {pmid}",
        organization=record.get("journal", ""),
        year=record.get("year") or None,
        department=str(seed.get("topic") or "research"),
        section_title="abstract",
        doc_id=f"pubmed:{pmid}",
        text_hash=text_hash,
        license="NLM API terms",
        evidence_level=evidence_level(record),
        locator={"pmid": pmid, "url": record.get("url", "")},
        metadata={
            "source_id": "ncbi_pubmed",
            "source_name": "PubMed",
            "source_url": record.get("url", ""),
            "url": record.get("url", ""),
            "pmid": pmid,
            "doi": record.get("doi", ""),
            "journal": record.get("journal", ""),
            "publication_types": ";".join(record.get("publication_types") or []),
            "mesh_terms": ";".join(record.get("mesh_terms") or []),
            "query_id": seed.get("query_id", ""),
            "query": seed.get("query", ""),
            "topic": seed.get("topic", ""),
            "collection_key": "literature",
            "embedding_text": f"{seed.get('topic', '')}\n{record.get('title', '')}\n{text}",
        },
    )


def status_row(*, ingest_run_id: str, collection: str, seed: dict, record: dict, status: str, started_at: str, accepted_chunks: int = 0, inserted_chunks: int = 0, failed_chunks: int = 0, error: str = "") -> dict:
    return {
        "ingest_run_id": ingest_run_id,
        "collection": collection,
        "query_id": seed.get("query_id", ""),
        "query": seed.get("query", ""),
        "pmid": str(record.get("pmid") or ""),
        "record_hash": record_hash(record) if record else "",
        "ingest_version": INGEST_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "status": status,
        "title": record.get("title", ""),
        "accepted_chunks": accepted_chunks,
        "inserted_chunks": inserted_chunks,
        "failed_chunks": failed_chunks,
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "error": error,
    }


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Resume-safe PubMed abstract ingestion for RAG v2.")
    parser.add_argument("--seed", default=str(DEFAULT_SEED))
    parser.add_argument("--collection", default=COLLECTIONS["literature"])
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Validate seed without calling PubMed.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--force-pmid", action="append", default=[])
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--run-report", default="")
    args = parser.parse_args()

    seed_path = Path(args.seed)
    seeds = load_seed(seed_path)
    if args.limit:
        seeds = seeds[: args.limit]
    state_file = Path(args.state_file)
    ingest_run_id = uuid.uuid4().hex[:12]
    run_report = Path(args.run_report) if args.run_report else DEFAULT_REPORT_DIR / f"{ingest_run_id}.jsonl"
    latest = {} if args.rebuild else latest_by_pmid(load_jsonl(state_file))
    force_pmids = {str(value) for value in args.force_pmid if str(value).strip()}
    if args.rebuild and not args.dry_run:
        reset_collection(args.collection)

    summary = {
        "ingest_run_id": ingest_run_id,
        "seed": str(seed_path),
        "collection": args.collection,
        "queries": len(seeds),
        "records": 0,
        "processed": 0,
        "skipped": 0,
        "completed": 0,
        "failed": 0,
        "accepted_chunks": 0,
        "inserted_chunks": 0,
        "failed_chunks": 0,
        "dry_run": bool(args.dry_run),
        "offline": bool(args.offline),
        "state_file": str(state_file),
        "run_report": str(run_report),
    }

    for seed in seeds:
        try:
            if args.offline:
                records: list[dict] = []
            else:
                records = await fetch_pubmed_records(seed.get("query", ""), top_k=args.top_k or int(seed.get("max_results") or 8))
        except Exception as exc:
            row = status_row(ingest_run_id=ingest_run_id, collection=args.collection, seed=seed, record={}, status="failed", started_at=utc_now_iso(), error=f"{type(exc).__name__}: {exc}")
            append_jsonl(run_report, row)
            summary["failed"] += 1
            continue
        if args.offline:
            row = status_row(ingest_run_id=ingest_run_id, collection=args.collection, seed=seed, record={}, status="offline_seed_valid", started_at=utc_now_iso())
            append_jsonl(run_report, row)
            continue
        for record in records:
            started_at = utc_now_iso()
            summary["records"] += 1
            current_hash = record_hash(record)
            skip, reason = matching_state(latest.get(str(record["pmid"])), current_hash=current_hash, collection=args.collection, retry_failed=args.retry_failed)
            if skip and str(record["pmid"]) not in force_pmids and not args.rebuild:
                row = status_row(ingest_run_id=ingest_run_id, collection=args.collection, seed=seed, record=record, status="skipped", started_at=started_at, error=reason)
                append_jsonl(run_report, row)
                summary["skipped"] += 1
                continue
            item = record_to_item(record, seed)
            write = {"inserted": 0, "failed": 0, "collection": args.collection}
            if not args.dry_run:
                write = upsert_evidence_items([item], collection_name=args.collection, batch_size=max(1, args.batch_size))
            failed = int(write.get("failed", 0))
            inserted = int(write.get("inserted", 0))
            status = "dry_run_completed" if args.dry_run else ("completed" if failed == 0 else "failed")
            row = status_row(
                ingest_run_id=ingest_run_id,
                collection=args.collection,
                seed=seed,
                record=record,
                status=status,
                started_at=started_at,
                accepted_chunks=1,
                inserted_chunks=inserted,
                failed_chunks=failed,
            )
            append_jsonl(run_report, row)
            if not args.dry_run:
                append_jsonl(state_file, row)
                latest[str(record["pmid"])] = row
            summary["processed"] += 1
            summary["accepted_chunks"] += 1
            summary["inserted_chunks"] += inserted
            summary["failed_chunks"] += failed
            summary["completed" if status.endswith("completed") else "failed"] += 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed"] else 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
