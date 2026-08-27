from __future__ import annotations

import os
from typing import Dict, List, Optional

from rag.retrieval.query import infer_intent, normalize_query
from rag.retrieval.medical_graphrag_client import search_medical_graphrag
from rag.schema import EvidenceItem, RagIntent, RetrievalResult


def _pack_context(items: List[EvidenceItem], max_chars: int = 4200) -> str:
    lines: List[str] = []
    used = 0
    evidence_items = [item for item in items if item.metadata.get("role", "evidence") == "evidence"]
    background_by_parent: Dict[str, List[EvidenceItem]] = {}
    for item in items:
        if item.metadata.get("role") != "background":
            continue
        parent = str(item.metadata.get("evidence_parent_chunk_id") or "")
        if parent:
            background_by_parent.setdefault(parent, []).append(item)

    for idx, item in enumerate(evidence_items, start=1):
        page = f"P{item.page_start}" if item.page_start is not None else "no-page"
        header = f"[E{idx}] {item.source_tier}/{item.source_type} | {item.title} | {item.section_title} | {page}"
        body = item.text.strip()
        block = f"{header}\n{body}"
        background = background_by_parent.get(item.chunk_id) or []
        if background:
            bg_lines = [
                f"- {bg.section_title}: {bg.text.strip()}"
                for bg in background[:2]
                if bg.text.strip()
            ]
            if bg_lines:
                block += "\n\nBackground only; do not cite as independent evidence:\n" + "\n".join(bg_lines)
        if used + len(block) > max_chars:
            remaining = max_chars - used
            if remaining <= 240:
                break
            block = block[:remaining]
        lines.append(block)
        used += len(block)

    if not lines:
        return "（未检索到可用的本地权威证据）"
    guard = (
        "Citation rules: use only [E#] evidence for medical conclusions. "
        "Background blocks are continuity context and must not be cited independently."
    )
    return guard + "\n\n" + "\n\n".join(lines)


async def retrieve_medical_evidence(
    query: str,
    intent: Optional[RagIntent | str] = None,
    filters: Optional[Dict] = None,
    top_k: int = 8,
) -> RetrievalResult:
    detected_intent = infer_intent(query, str(intent) if intent else None)
    normalized_query = normalize_query(query)
    rag_backend = os.getenv("RAG_BACKEND", "medical_graphrag").strip().lower()
    if rag_backend == "medical_graphrag":
        items, debug = await search_medical_graphrag(
            normalized_query,
            intent=detected_intent,
            top_k=top_k,
            filters=filters,
        )
    else:
        from rag.retrieval import hybrid_retrieve

        items, debug = await hybrid_retrieve(normalized_query, intent=detected_intent, top_k=top_k, filters=filters)
    refs = [
        item.to_ref()
        for item in items
        if item.metadata.get("role", "evidence") == "evidence"
        and (item.locator or item.page_start is not None or item.doc_id)
    ]
    return RetrievalResult(
        query=query,
        intent=detected_intent,
        items=items,
        context_text=_pack_context(items),
        refs=refs,
        debug={**debug, "normalized_query": normalized_query, "rag_backend": rag_backend},
    )


async def get_multimodal_context_v2(user_query: str, top_k: int = 6, intent: Optional[str] = None):
    result = await retrieve_medical_evidence(user_query, intent=intent, top_k=top_k)
    return result.to_legacy_tuple()
