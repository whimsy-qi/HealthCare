from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Dict, List

import yaml

from rag.config import COLLECTIONS
from rag.external.drug import normalize_drug_name, openfda_label_sections, search_dailymed_spls, search_openfda_drug_label
from rag.schema import EvidenceItem
from rag.store import reset_collection, upsert_evidence_items


DEFAULT_SEED = Path(__file__).resolve().parents[1] / "sources" / "drug_seed.yaml"


def _hash(text: str, length: int = 20) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _load_seed(path: Path) -> List[Dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("drugs") or [])


def _section_to_item(drug: Dict, section: Dict, rxnorm: Dict) -> EvidenceItem:
    text = section["text"].strip()
    text_hash = _hash(text)
    drug_id = drug["drug_id"]
    section_key = _hash(section.get("section_title", ""), 8)
    chunk_id = f"drug_{drug_id}_{section_key}_{text_hash[:8]}"
    doc_id = f"drug_label:{drug_id}:{section.get('set_id') or rxnorm.get('rxcui') or 'openfda'}"
    return EvidenceItem(
        chunk_id=chunk_id,
        text=text[:5000],
        source_type="drug_label",
        source_tier="T1",
        title=f"{drug.get('display_name') or drug.get('query')}｜{section.get('title', 'Drug label')}",
        organization="openFDA/DailyMed/RxNorm",
        year=0,
        department="pharmacy",
        section_title=section.get("section_title", "药品标签"),
        doc_id=doc_id,
        text_hash=text_hash,
        license="openFDA/DailyMed public API terms",
        evidence_level="official_drug_label",
        locator={
            "doc": doc_id,
            "drug_id": drug_id,
            "rxcui": rxnorm.get("rxcui"),
            "source_url": section.get("source_url"),
        },
        metadata={
            "drug_id": drug_id,
            "drug_display_name": drug.get("display_name", ""),
            "drug_query": drug.get("query", ""),
            "rxcui": rxnorm.get("rxcui") or "",
            "generic_name": section.get("generic_name", ""),
            "brand_name": section.get("brand_name", ""),
        },
    )


async def build_drug_items(seed_path: Path, *, top_k: int = 2, offline: bool = False) -> tuple[List[EvidenceItem], List[Dict]]:
    items: List[EvidenceItem] = []
    diagnostics: List[Dict] = []
    for drug in _load_seed(seed_path):
        query = drug.get("query") or drug.get("display_name") or drug.get("drug_id")
        if offline:
            diagnostics.append({"drug_id": drug.get("drug_id"), "query": query, "offline": True, "sections": 0})
            continue
        rxnorm = await normalize_drug_name(query)
        labels = await search_openfda_drug_label(query, top_k=top_k)
        dailymed_hits = await search_dailymed_spls(query, top_k=top_k)
        sections: List[Dict] = []
        for label in labels:
            sections.extend(openfda_label_sections(label))
        for spl in dailymed_hits:
            if spl.get("title"):
                sections.append({
                    "title": spl.get("title", query),
                    "section_title": "DailyMed SPL",
                    "text": f"DailyMed SPL record for {query}: {spl.get('title')} setid={spl.get('setid') or spl.get('set_id')}",
                    "set_id": spl.get("setid") or spl.get("set_id") or "",
                    "source_url": "https://dailymed.nlm.nih.gov/dailymed/",
                    "generic_name": query,
                    "brand_name": spl.get("title", ""),
                })
        for section in sections:
            items.append(_section_to_item(drug, section, rxnorm))
        diagnostics.append({
            "drug_id": drug.get("drug_id"),
            "query": query,
            "rxcui": rxnorm.get("rxcui"),
            "openfda_labels": len(labels),
            "dailymed_hits": len(dailymed_hits),
            "sections": len(sections),
        })
    return items, diagnostics


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Ingest authoritative drug labels into RAG v2.")
    parser.add_argument("--seed", default=str(DEFAULT_SEED))
    parser.add_argument("--collection", default=COLLECTIONS["drug_label"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Validate seed without calling external APIs.")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--top-k", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()

    items, diagnostics = await build_drug_items(Path(args.seed), top_k=args.top_k, offline=args.offline)
    write_result = {"inserted": 0, "failed": 0, "collection": args.collection}
    if not args.dry_run and not args.offline:
        if args.rebuild:
            reset_collection(args.collection)
        write_result = upsert_evidence_items(items, collection_name=args.collection, batch_size=max(1, args.batch_size))

    print(json.dumps({
        "seed": args.seed,
        "collection": args.collection,
        "dry_run": bool(args.dry_run),
        "offline": bool(args.offline),
        "drugs": diagnostics,
        "items": len(items),
        "write": write_result,
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
