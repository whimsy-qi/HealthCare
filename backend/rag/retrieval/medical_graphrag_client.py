from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import aiohttp

from rag.schema import EvidenceItem, RagIntent


class MedicalGraphRagClientError(RuntimeError):
    pass


def _api_base() -> str:
    return os.getenv("MEDICAL_GRAPHRAG_API_BASE", "http://localhost:8026/api/v1").rstrip("/")


def _api_token() -> str:
    return os.getenv("MEDICAL_GRAPHRAG_API_TOKEN", "").strip()


def _collection_to_source_type(collection: str, explicit: str | None) -> str:
    explicit_mapping = {
        "guideline_pdf": "guideline",
        "drug_excel": "drug_label",
        "patient_education": "patient_education",
        "openfda_label": "drug_label",
        "daily_med": "drug_label",
    }
    if explicit:
        return explicit_mapping.get(str(explicit), str(explicit))
    mapping = {
        "medical_guideline_v2": "guideline",
        "drug_label_v2": "drug_label",
        "medical_literature_v2": "literature",
        "clinical_trial_v2": "clinical_trial",
        "patient_education_v2": "patient_education",
        "cancer_evidence_v2": "literature",
        "medical_kg_v2": "kg",
    }
    return mapping.get(collection, collection or "unknown")


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _locator(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        return {"raw": raw}
    return {}


def _row_to_item(row: Dict[str, Any]) -> EvidenceItem:
    collection = str(row.get("collection") or "")
    source_type = _collection_to_source_type(collection, row.get("source_type"))
    locator = _locator(row.get("locator"))
    display_text = str(row.get("display_text") or row.get("text") or "")
    snippet = str(row.get("snippet") or row.get("text") or "")[:600]
    scores = {
        "medical_graphrag": float(row.get("score") or 0.0),
        "rrf": float(row.get("rrf_score") or 0.0),
        "reranker": float(row.get("reranker_raw_score") or row.get("rerank_score") or 0.0),
        "reranker_prob": float(row.get("reranker_prob") or 0.0),
    }
    return EvidenceItem(
        chunk_id=str(row.get("chunk_id") or ""),
        doc_id=str(row.get("doc_id") or ""),
        text=str(row.get("text") or ""),
        text_hash=str(row.get("text_hash") or ""),
        source_type=source_type,
        source_tier=str(row.get("source_tier") or "T3"),
        title=str(row.get("title") or ""),
        section_title=str(row.get("section_title") or ""),
        page_start=_as_int(row.get("page_start")),
        page_end=_as_int(row.get("page_end")),
        license=str(row.get("license") or ""),
        locator=locator,
        scores=scores,
        metadata={
            "collection_name": collection,
            "collection_key": collection,
            "source_name": row.get("source_name") or "",
            "embedding_model": row.get("embedding_model") or "",
            "indexed_at": row.get("indexed_at") or "",
            "source_backend": "medical_graphrag",
            "role": row.get("role") or "evidence",
            "evidence_index": row.get("evidence_index"),
            "evidence_parent_chunk_id": row.get("evidence_parent_chunk_id"),
            "dense_rank": row.get("dense_rank"),
            "sparse_rank": row.get("sparse_rank"),
            "rrf_score": row.get("rrf_score"),
            "reranker_raw_score": row.get("reranker_raw_score"),
            "reranker_prob": row.get("reranker_prob"),
            "section_path": row.get("section_path") or [],
            "parent_section_id": row.get("parent_section_id") or "",
            "field_type": row.get("field_type") or "",
            "chunk_index": row.get("chunk_index"),
            "display_text": display_text,
            "snippet": snippet,
            "knowledge_card": row.get("knowledge_card") or {},
            "locator": locator,
        },
    )


async def search_medical_graphrag(
    query: str,
    *,
    intent: RagIntent,
    top_k: int = 8,
    filters: Optional[Dict[str, Any]] = None,
    collection: Optional[str] = None,
) -> tuple[List[EvidenceItem], Dict[str, Any]]:
    token = _api_token()
    if not token:
        raise MedicalGraphRagClientError("MEDICAL_GRAPHRAG_API_TOKEN is required when RAG_BACKEND=medical_graphrag")

    payload: Dict[str, Any] = {
        "query": query,
        "intent": intent,
        "top_k": top_k,
        "filters": filters or {},
        "debug": False,
    }
    if collection:
        payload["collection"] = collection

    timeout = aiohttp.ClientTimeout(total=float(os.getenv("MEDICAL_GRAPHRAG_TIMEOUT_SECONDS", "12")))
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{_api_base()}/medical-rag/retrieve"

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.post(url, json=payload) as response:
            if response.status >= 400:
                body = await response.text()
                raise MedicalGraphRagClientError(f"medical-graphrag retrieval failed: HTTP {response.status}: {body[:500]}")
            data = await response.json()

    rows = data.get("items") or data.get("results") or []
    trace = data.get("trace") or data.get("debug") or {}
    reranker_trace = trace.get("reranker") or {}
    hybrid_trace = trace.get("hybrid") or {}
    rag_trace = {
        "backend": "medical_graphrag",
        "api_base": _api_base(),
        "collections": trace.get("collections") or [],
        "collection_traces": trace.get("collection_traces") or {},
        "dense_top_k": hybrid_trace.get("dense_top_k"),
        "sparse_top_k": hybrid_trace.get("sparse_top_k"),
        "rrf_k": hybrid_trace.get("rrf_k"),
        "candidate_count": hybrid_trace.get("candidate_count"),
        "reranker_model": reranker_trace.get("reranker_model") or reranker_trace.get("model"),
        "reranker_provider": reranker_trace.get("reranker_provider") or reranker_trace.get("provider"),
        "reranker_attempted": reranker_trace.get("reranker_attempted"),
        "rerank_degraded": reranker_trace.get("rerank_degraded") or reranker_trace.get("degraded"),
        "reranker_status": reranker_trace.get("reranker_status") or reranker_trace.get("status"),
        "noise_gate_dropped_count": trace.get("noise_gate_dropped_count", 0),
        "execution_time_ms": trace.get("execution_time_ms"),
    }
    items = [_row_to_item(row) for row in rows if row.get("chunk_id") and row.get("text")]
    for item in items:
        item.metadata["rag_trace"] = rag_trace
    policy_flags = data.get("policy_flags") or {}
    debug = {
        "backend": "medical_graphrag",
        "api_base": _api_base(),
        "policy_flags": policy_flags,
        "unsafe_to_answer": bool(policy_flags.get("unsafe_to_answer")),
        "research_source_missing": bool(policy_flags.get("research_source_missing")),
        "preferred_collection_hit": bool(policy_flags.get("preferred_collection_hit")),
        "trace": trace,
        "rag": rag_trace,
        "execution_time_ms": trace.get("execution_time_ms") or data.get("execution_time_ms"),
    }
    return items, debug
