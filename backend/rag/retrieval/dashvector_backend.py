from __future__ import annotations

import asyncio
import os
from typing import Dict, Iterable, List, Optional

from dotenv import load_dotenv, find_dotenv

from rag.config import COLLECTIONS, EMBEDDING_DIM, EMBEDDING_MODEL, ENABLE_DASHVECTOR
from rag.schema import EvidenceItem, RagIntent


load_dotenv(find_dotenv(usecwd=True))


INTENT_COLLECTIONS: Dict[RagIntent, List[str]] = {
    "medication_safety": ["drug_label", "drug_safety_signal", "guideline", "literature"],
    "latest_research": ["literature", "cancer_evidence", "clinical_trial", "guideline"],
    "rumor_check": ["guideline", "literature", "cancer_evidence", "clinical_trial", "drug_label", "drug_safety_signal"],
    "symptom_dx": ["guideline", "kg", "literature"],
    "guideline_qa": ["guideline"],
    "report_interpretation": ["guideline"],
    "general": ["patient_education", "guideline", "literature"],
}


def _metadata_to_item(doc, collection_key: str) -> EvidenceItem:
    fields = getattr(doc, "fields", {}) or {}
    doc_id = fields.get("doc_id") or fields.get("source_id") or fields.get("file_path") or collection_key
    chunk_id = fields.get("chunk_id") or getattr(doc, "id", "") or "unknown"
    text = fields.get("text") or fields.get("content") or fields.get("abstract") or ""
    source_type = fields.get("source_type") or fields.get("source") or collection_key
    title = fields.get("title") or fields.get("disease") or fields.get("drug_name") or source_type
    page = fields.get("page_start") or fields.get("page")
    try:
        page = int(page) if page is not None else None
    except Exception:
        page = None
    score = float(getattr(doc, "score", 0.0) or 0.0)
    return EvidenceItem(
        chunk_id=str(chunk_id),
        text=str(text),
        source_type=str(source_type),
        source_tier=str(fields.get("source_tier") or fields.get("authority_tier") or "T3"),
        title=str(title),
        organization=str(fields.get("organization") or ""),
        year=_safe_int(fields.get("year")),
        department=str(fields.get("department") or fields.get("dept") or ""),
        section_title=str(fields.get("section_title") or ""),
        page_start=page,
        page_end=_safe_int(fields.get("page_end")) or page,
        doc_id=str(doc_id),
        text_hash=str(fields.get("text_hash") or ""),
        license=str(fields.get("license") or ""),
        evidence_level=str(fields.get("evidence_level") or ""),
        locator={"doc": str(doc_id), "page": page} if page is not None else {"doc": str(doc_id)},
        scores={"dense": score},
        metadata={
            **{k: v for k, v in fields.items() if k not in {"text", "content"}},
            "collection_key": collection_key,
            "collection_name": COLLECTIONS.get(collection_key, collection_key),
        },
    )


def _safe_int(value) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _embed_query(query: str) -> Optional[List[float]]:
    if not ENABLE_DASHVECTOR:
        return None
    import dashscope

    dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
    resp = dashscope.MultiModalEmbedding.call(model=EMBEDDING_MODEL, input=[{"text": query}])
    if getattr(resp, "status_code", None) != 200:
        return None
    return resp.output["embeddings"][0]["embedding"]


def _query_collection(collection_key: str, vector: List[float], top_k: int, filter_expr: Optional[str]) -> List[EvidenceItem]:
    from dashvector import Client

    client = Client(api_key=os.getenv("DASHVECTOR_API_KEY"), endpoint=os.getenv("DASHVECTOR_ENDPOINT"))
    collection = client.get(COLLECTIONS[collection_key])
    resp = collection.query(
        vector=vector,
        topk=top_k,
        filter=filter_expr,
        include_vector=False,
        output_fields=[
            "doc_id", "chunk_id", "text", "content", "source_type", "source", "source_tier",
            "title", "organization", "year", "department", "dept", "section_title",
            "page_start", "page_end", "text_hash", "license", "evidence_level",
            "drug_name", "brand_name", "generic_name", "approval_no", "producer", "drug_class",
            "related_diseases", "source_name", "source_url", "section_key", "safety_critical",
            "parent_id", "section_path", "block_type", "extraction_method", "layout_type",
            "sibling_prev", "sibling_next",
            "disease", "url", "source_id", "publication_date", "recommendation_grade",
            "population", "service_type", "topic_tags", "causality_not_established",
            "report_count", "serious_count", "limitations",
            "pmid", "doi", "journal", "publication_types", "mesh_terms",
            "nct_id", "trial_status", "phase", "study_type", "conditions", "interventions",
            "rxcui", "drug_id", "drug_display_name", "drug_query",
        ],
    )
    return [_metadata_to_item(doc, collection_key) for doc in (getattr(resp, "output", None) or [])]


async def search_dashvector(
    query: str,
    *,
    intent: RagIntent,
    top_k: int = 8,
    filter_expr: Optional[str] = None,
) -> List[EvidenceItem]:
    if not ENABLE_DASHVECTOR or not os.getenv("DASHVECTOR_API_KEY"):
        return []

    def _run() -> List[EvidenceItem]:
        vector = _embed_query(query)
        if not vector:
            return []
        if len(vector) != EMBEDDING_DIM:
            return []
        items: List[EvidenceItem] = []
        if intent == "medication_safety":
            try:
                drug_items = _query_collection("drug_label", vector, top_k, filter_expr)
                items.extend(drug_items)
                if len(drug_items) >= top_k:
                    return items
            except Exception:
                pass
            for key in ["drug_safety_signal", "guideline", "literature"]:
                try:
                    items.extend(_query_collection(key, vector, max(1, (top_k - len(items)) // 2), filter_expr))
                except Exception:
                    continue
            return items
        for key in INTENT_COLLECTIONS.get(intent, ["guideline"]):
            try:
                items.extend(_query_collection(key, vector, max(1, top_k // 2), filter_expr))
            except Exception:
                continue
        return items

    try:
        return await asyncio.wait_for(asyncio.to_thread(_run), timeout=10)
    except Exception:
        return []
