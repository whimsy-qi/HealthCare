from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from rag.config import DEFAULT_PDF_ROOT, LOCAL_INDEX_MAX_PDFS
from rag.ingest.pdf import GuidelineChunk, build_guideline_chunks
from rag.schema import EvidenceItem, RagIntent
from rag.retrieval.query import tokenize


INTENT_DEPARTMENT_HINTS = {
    "symptom_dx": ["心血管病学", "呼吸病学", "神经病学", "胃肠病学", "内分泌学", "骨外科学"],
    "guideline_qa": [],
    "report_interpretation": ["心血管病学", "内分泌学", "肾内科", "呼吸病学"],
}


def _chunk_to_item(chunk: GuidelineChunk, score: float, bm25: float, matched_terms: List[str]) -> EvidenceItem:
    return EvidenceItem(
        chunk_id=chunk.chunk_id,
        text=chunk.text,
        source_type=chunk.source_type,
        source_tier=chunk.source_tier,
        title=chunk.title,
        organization=chunk.organization,
        year=chunk.year,
        department=chunk.department,
        section_title=chunk.section_title,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        doc_id=chunk.doc_id,
        text_hash=chunk.text_hash,
        license=chunk.license,
        evidence_level=chunk.evidence_level,
        locator={"doc": chunk.doc_id, "page": chunk.page_start, "title": chunk.title},
        scores={"local_bm25": bm25, "local_score": score},
        metadata={"quality": chunk.quality, "matched_terms": matched_terms, "collection_key": "local_guideline_bm25"},
    )


@lru_cache(maxsize=1)
def load_local_guideline_chunks(pdf_root: str = str(DEFAULT_PDF_ROOT)) -> List[GuidelineChunk]:
    root = Path(pdf_root)
    if not root.exists():
        return []
    chunks: List[GuidelineChunk] = []
    for path in sorted(root.rglob("*.pdf"))[:LOCAL_INDEX_MAX_PDFS]:
        try:
            chunks.extend(build_guideline_chunks(path, department=path.parent.name))
        except Exception:
            continue
    return chunks


def _idf(query_terms: Iterable[str], docs_terms: List[List[str]]) -> Dict[str, float]:
    n_docs = max(len(docs_terms), 1)
    out: Dict[str, float] = {}
    for term in set(query_terms):
        df = sum(1 for terms in docs_terms if term in set(terms))
        out[term] = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
    return out


def search_local_guidelines(
    query: str,
    *,
    intent: RagIntent = "general",
    top_k: int = 8,
    pdf_root: str = str(DEFAULT_PDF_ROOT),
    department_filter: Optional[List[str]] = None,
) -> List[EvidenceItem]:
    chunks = [
        c for c in load_local_guideline_chunks(pdf_root)
        if not {"mojibake", "likely_references", "missing_page"} & set(c.quality)
    ]
    if not chunks:
        return []

    if department_filter:
        allowed = set(department_filter)
        chunks = [c for c in chunks if c.department in allowed]

    q_terms = tokenize(query)
    if not q_terms:
        return []

    doc_terms = [tokenize(f"{c.title} {c.department} {c.section_title} {c.text}") for c in chunks]
    idf = _idf(q_terms, doc_terms)
    avg_len = sum(len(t) for t in doc_terms) / max(len(doc_terms), 1)
    k1 = 1.4
    b = 0.65

    scored: List[tuple[float, float, GuidelineChunk, List[str]]] = []
    for chunk, terms in zip(chunks, doc_terms):
        tf: Dict[str, int] = {}
        for term in terms:
            tf[term] = tf.get(term, 0) + 1
        bm25 = 0.0
        matched: List[str] = []
        for term in q_terms:
            freq = tf.get(term, 0)
            if not freq:
                continue
            matched.append(term)
            denom = freq + k1 * (1 - b + b * (len(terms) / max(avg_len, 1.0)))
            bm25 += idf.get(term, 0.0) * (freq * (k1 + 1)) / max(denom, 1e-6)
        if bm25 <= 0:
            continue
        title_boost = 0.35 if any(term in tokenize(chunk.title) for term in q_terms) else 0.0
        section_boost = 0.15 if chunk.section_title in {"推荐意见", "诊断与评估", "治疗与管理", "用药安全"} else 0.0
        tier_boost = 0.15 if chunk.source_tier == "T1" else 0.0
        dept_hints = INTENT_DEPARTMENT_HINTS.get(intent, [])
        dept_boost = 0.1 if dept_hints and chunk.department in dept_hints else 0.0
        score = bm25 + title_boost + section_boost + tier_boost + dept_boost
        scored.append((score, bm25, chunk, matched[:12]))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [_chunk_to_item(chunk, score, bm25, matched) for score, bm25, chunk, matched in scored[:top_k]]


def local_guideline_quality_stats(pdf_root: str = str(DEFAULT_PDF_ROOT)) -> Dict[str, int]:
    chunks = load_local_guideline_chunks(pdf_root)
    blocked = sum(1 for c in chunks if {"mojibake", "likely_references", "missing_page"} & set(c.quality))
    return {"total": len(chunks), "quarantine_filtered": blocked}
