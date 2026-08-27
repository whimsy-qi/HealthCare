from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence

from dotenv import find_dotenv, load_dotenv

from rag.config import EMBEDDING_DIM, EMBEDDING_MODEL
from rag.schema import EvidenceItem


load_dotenv(find_dotenv(usecwd=True))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_dashvector_client():
    from dashvector import Client

    return Client(api_key=os.getenv("DASHVECTOR_API_KEY"), endpoint=os.getenv("DASHVECTOR_ENDPOINT"))


def ensure_collection(collection_name: str, *, create_missing: bool = True):
    client = get_dashvector_client()
    if create_missing:
        try:
            desc = client.describe(collection_name)
            if getattr(desc, "code", None) != 0 and not bool(desc):
                client.create(collection_name, dimension=EMBEDDING_DIM, metric="cosine")
        except Exception:
            client.create(collection_name, dimension=EMBEDDING_DIM, metric="cosine")
    return client.get(collection_name)


def reset_collection(collection_name: str) -> None:
    collection = ensure_collection(collection_name, create_missing=True)
    resp = collection.delete(delete_all=True)
    if getattr(resp, "code", 0) not in (0, None) and not bool(resp):
        raise RuntimeError(f"DashVector delete_all failed for {collection_name}: {getattr(resp, 'message', resp)}")


def embed_texts(texts: Sequence[str], *, max_retries: int = 4) -> List[Optional[List[float]]]:
    import dashscope

    if not texts:
        return []
    dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
    if not dashscope.api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is required for non-dry-run ingestion")
    inputs = [{"text": str(text)[:6000]} for text in texts]
    for retry in range(max_retries):
        resp = dashscope.MultiModalEmbedding.call(model=EMBEDDING_MODEL, input=inputs)
        if getattr(resp, "status_code", None) == 200:
            embeddings = resp.output["embeddings"]
            if len(embeddings) != len(texts):
                raise RuntimeError(f"embedding count mismatch: got {len(embeddings)}, expected {len(texts)}")
            vectors: List[Optional[List[float]]] = []
            for emb_data in embeddings:
                vector = emb_data["embedding"]
                if len(vector) != EMBEDDING_DIM:
                    raise RuntimeError(f"embedding dimension mismatch: got {len(vector)}, expected {EMBEDDING_DIM}")
                vectors.append(vector)
            return vectors
        time.sleep((2 ** retry) * 0.5)
    return [None for _ in texts]


def embed_text(text: str, *, max_retries: int = 4) -> Optional[List[float]]:
    vectors = embed_texts([text], max_retries=max_retries)
    return vectors[0] if vectors else None
    return None


def evidence_to_fields(item: EvidenceItem, *, indexed_at: Optional[str] = None) -> dict:
    fields = {
        "doc_id": item.doc_id,
        "chunk_id": item.chunk_id,
        "title": item.title,
        "department": item.department,
        "section_title": item.section_title,
        "page_start": item.page_start or 0,
        "page_end": item.page_end or item.page_start or 0,
        "text": item.text,
        "source_type": item.source_type,
        "source_tier": item.source_tier,
        "text_hash": item.text_hash,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dim": EMBEDDING_DIM,
        "indexed_at": indexed_at or utc_now_iso(),
        "organization": item.organization,
        "year": item.year or 0,
        "license": item.license,
        "evidence_level": item.evidence_level,
    }
    for key, value in item.metadata.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            fields[key] = "" if value is None else value
    return fields


def upsert_evidence_items(
    items: Iterable[EvidenceItem],
    *,
    collection_name: str,
    batch_size: int = 32,
) -> dict:
    from dashvector import Doc

    batch_size = max(1, int(batch_size or 1))
    collection = ensure_collection(collection_name, create_missing=True)
    total = 0
    failed = 0
    indexed_at = utc_now_iso()
    item_batch: List[EvidenceItem] = []

    def flush_batch(batch_items: List[EvidenceItem]) -> None:
        nonlocal total, failed
        if not batch_items:
            return
        embedding_texts = [
            str(item.metadata.get("embedding_text") or f"{item.title}\n{item.section_title}\n{item.text}")
            for item in batch_items
        ]
        vectors = embed_texts(embedding_texts)
        docs: List[Doc] = []
        for item, vector in zip(batch_items, vectors):
            if vector is None:
                failed += 1
                continue
            docs.append(Doc(id=item.chunk_id, vector=vector, fields=evidence_to_fields(item, indexed_at=indexed_at)))
        if not docs:
            return
        writer = getattr(collection, "upsert", None) or collection.insert
        resp = writer(docs)
        if getattr(resp, "code", 0) not in (0, None) and not bool(resp):
            failed += len(docs)
        else:
            total += len(docs)

    for item in items:
        item_batch.append(item)
        if len(item_batch) >= batch_size:
            flush_batch(item_batch)
            item_batch = []
    flush_batch(item_batch)
    return {"inserted": total, "failed": failed, "collection": collection_name}
