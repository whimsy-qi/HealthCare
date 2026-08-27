from __future__ import annotations

import asyncio
import math
import os
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional

from dotenv import find_dotenv, load_dotenv

from rag.schema import RagIntent
from .schema import GraphCandidate, GraphPath, GraphRetrievalResult


load_dotenv(find_dotenv(usecwd=True))


GRAPH_INTENTS = {"symptom_dx", "medication_safety"}
NODE_LABELS = ["Disease", "Symptom", "Drug", "Department", "Check", "Food", "Cure", "Producer"]
VECTOR_INDEXES = {
    "Disease": "disease_embedding",
    "Symptom": "symptom_embedding",
    "Drug": "drug_embedding",
    "Department": "department_embedding",
    "Check": "check_embedding",
}
FULLTEXT_INDEXES = ["medical_entity_fulltext", "entity_name_fulltext"]

RELATION_WEIGHTS = {
    "HAS_SYMPTOM": 1.25,
    "CONTRAINDICATED_FOR": 1.35,
    "INTERACTS_WITH": 1.35,
    "NEED_CHECK": 1.12,
    "TREATS": 1.05,
    "COMMON_DRUG": 0.95,
    "RECOMMAND_DRUG": 0.95,
    "BELONGS_TO": 0.9,
    "ACOMPANY_WITH": 0.9,
}
TIER_WEIGHTS = {"T1": 1.0, "T2": 0.92, "T3": 0.78, "T4": 0.45}


def _graph_configured() -> bool:
    return bool(os.getenv("NEO4J_PASSWORD"))


def _target_labels(intent: RagIntent) -> List[str]:
    if intent == "symptom_dx":
        return ["Disease", "Check", "Department", "Drug"]
    if intent == "medication_safety":
        return ["Disease", "Drug", "Symptom"]
    if intent == "rumor_check":
        return ["Disease", "Drug", "Check", "Cure"]
    return ["Disease", "Drug", "Symptom", "Check", "Department"]


def _extract_entities(query: str, entities: Optional[List[str]] = None) -> List[str]:
    if entities:
        terms = entities[:]
    else:
        terms = []
        terms.extend(re.findall(r"[\u4e00-\u9fff]{2,12}", query or ""))
        terms.extend(re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", query or ""))
    stop = {
        "怎么办", "可能是什么", "有什么风险", "有哪些", "是否", "可以", "需要",
        "最新研究", "真的", "假的", "一起用", "为什么",
    }
    cleaned: List[str] = []
    for term in terms:
        term = term.strip(" ，。！？；:：、()（）[]【】")
        if not term or term in stop or len(term) < 2:
            continue
        if term not in cleaned:
            cleaned.append(term)
    if query and 2 <= len(query) <= 18 and query not in cleaned:
        cleaned.insert(0, query)
    return cleaned[:8]


def _relation_weight(rel_types: Iterable[str]) -> float:
    weights = [RELATION_WEIGHTS.get(rel, 0.8) for rel in rel_types]
    return max(weights) if weights else 1.0


def _tier_weight(tier: str) -> float:
    return TIER_WEIGHTS.get(tier or "T3", 0.78)


def _connect_driver():
    from neo4j import GraphDatabase

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7714")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")
    if not password:
        raise RuntimeError("NEO4J_PASSWORD is not configured")
    timeout = float(os.getenv("NEO4J_QUERY_TIMEOUT_SECONDS", os.getenv("RAG_GRAPHRAG_TIMEOUT_SECONDS", "1.0")))
    return GraphDatabase.driver(
        uri,
        auth=(user, password),
        connection_timeout=timeout,
        max_connection_lifetime=60,
    )


def _embed_terms(terms: List[str]) -> Dict[str, List[float]]:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key or not terms:
        return {}
    try:
        import dashscope

        dashscope.api_key = api_key
        resp = dashscope.TextEmbedding.call(
            model=dashscope.TextEmbedding.Models.text_embedding_v3,
            input=terms,
        )
        if getattr(resp, "status_code", None) != 200:
            return {}
        embeddings = resp.output["embeddings"]
        return {term: item["embedding"] for term, item in zip(terms, embeddings)}
    except Exception:
        return {}


def _exact_anchors(session, terms: List[str], limit: int) -> List[dict]:
    query = """
    UNWIND $terms AS term
    MATCH (n)
    WHERE (n:Disease OR n:Symptom OR n:Drug OR n:Department OR n:Check OR n:Food OR n:Cure OR n:Producer)
      AND n.name IS NOT NULL
      AND (n.name = term OR n.name CONTAINS term OR term CONTAINS n.name)
    WITH n, term,
      CASE
        WHEN n.name = term THEN 1.0
        WHEN term CONTAINS n.name THEN 0.92
        ELSE 0.82
      END AS score
    RETURN elementId(n) AS id, n.name AS name, labels(n)[0] AS label, term AS origin_entity,
           score,
           coalesce(n.source_name, 'local_medical_kg') AS source_name,
           coalesce(n.source_tier, 'T3') AS source_tier
    ORDER BY score DESC
    LIMIT $limit
    """
    return [dict(r) for r in session.run(query, terms=terms, limit=limit)]


def _fulltext_anchors(session, terms: List[str], limit: int) -> List[dict]:
    anchors: List[dict] = []
    for term in terms:
        for index_name in FULLTEXT_INDEXES:
            try:
                rows = session.run(
                    f"""
                    CALL db.index.fulltext.queryNodes('{index_name}', $term, {{limit: $limit}})
                    YIELD node, score
                    RETURN elementId(node) AS id, node.name AS name, labels(node)[0] AS label,
                           $term AS origin_entity,
                           score,
                           coalesce(node.source_name, 'local_medical_kg') AS source_name,
                           coalesce(node.source_tier, 'T3') AS source_tier
                    """,
                    term=term,
                    limit=max(1, limit // max(len(terms), 1)),
                )
                anchors.extend(dict(r) for r in rows)
            except Exception:
                continue
    return anchors


def _vector_anchors(session, terms: List[str], limit: int) -> List[dict]:
    embeddings = _embed_terms(terms)
    if not embeddings:
        return []
    anchors: List[dict] = []
    per_index = max(1, limit // max(len(terms), 1))
    for term, vector in embeddings.items():
        for label, index_name in VECTOR_INDEXES.items():
            try:
                rows = session.run(
                    f"""
                    CALL db.index.vector.queryNodes('{index_name}', $top_k, $vector)
                    YIELD node, score
                    WHERE score >= $threshold
                    RETURN elementId(node) AS id, node.name AS name, labels(node)[0] AS label,
                           $term AS origin_entity,
                           score,
                           coalesce(node.source_name, 'local_medical_kg') AS source_name,
                           coalesce(node.source_tier, 'T3') AS source_tier
                    """,
                    top_k=per_index,
                    vector=vector,
                    threshold=0.55,
                    term=term,
                )
                anchors.extend(dict(r) for r in rows if r["label"] == label)
            except Exception:
                continue
    return anchors


def _dedupe_anchors(rows: List[dict], limit: int) -> List[dict]:
    by_id: Dict[str, dict] = {}
    for row in rows:
        node_id = row.get("id")
        if not node_id:
            continue
        current = by_id.get(node_id)
        if current is None or float(row.get("score") or 0) > float(current.get("score") or 0):
            by_id[node_id] = row
    return sorted(by_id.values(), key=lambda r: float(r.get("score") or 0), reverse=True)[:limit]


def _expand_paths(session, anchors: List[dict], *, intent: RagIntent, max_hops: int, limit: int) -> List[GraphPath]:
    max_hops = max(0, min(int(max_hops), 3))
    target_labels = _target_labels(intent)
    anchor_params = [
        {
            "id": a["id"],
            "name": a.get("name", ""),
            "label": a.get("label", ""),
            "score": float(a.get("score") or 0.0),
        }
        for a in anchors
    ]
    if not anchor_params:
        return []
    query = f"""
    UNWIND $anchors AS a
    MATCH (anchor) WHERE elementId(anchor) = a.id
    MATCH path = (anchor)-[*0..{max_hops}]-(target)
    WHERE any(label IN labels(target) WHERE label IN $target_labels)
    WITH a, target, path, length(path) AS hops
    RETURN elementId(target) AS target_id,
           target.name AS target_name,
           labels(target)[0] AS target_label,
           coalesce(target.source_name, 'local_medical_kg') AS source_name,
           coalesce(target.source_tier, 'T3') AS source_tier,
           a.name AS anchor_name,
           a.label AS anchor_label,
           a.score AS anchor_score,
           hops,
           [n IN nodes(path) | elementId(n)] AS node_ids,
           [n IN nodes(path) | n.name] AS node_names,
           [r IN relationships(path) | type(r)] AS rel_types
    LIMIT $limit
    """
    paths: List[GraphPath] = []
    for row in session.run(query, anchors=anchor_params, target_labels=target_labels, limit=limit):
        rel_types = [str(x) for x in (row["rel_types"] or [])]
        hop_count = int(row["hops"] or 0)
        source_tier = str(row["source_tier"] or "T3")
        score = (
            float(row["anchor_score"] or 0.0)
            * math.exp(-0.75 * hop_count)
            * _relation_weight(rel_types)
            * _tier_weight(source_tier)
        )
        paths.append(
            GraphPath(
                anchor_entity=str(row["anchor_name"] or ""),
                anchor_label=str(row["anchor_label"] or ""),
                target_name=str(row["target_name"] or ""),
                target_label=str(row["target_label"] or ""),
                node_ids=[str(x) for x in (row["node_ids"] or [])],
                node_names=[str(x) for x in (row["node_names"] or []) if x],
                relation_types=rel_types,
                hop_count=hop_count,
                score=score,
                source_name=str(row["source_name"] or "local_medical_kg"),
                source_tier=source_tier,
            )
        )
    return paths


def _paths_to_result(query: str, paths: List[GraphPath], debug: Dict, top_k: int) -> GraphRetrievalResult:
    grouped: Dict[str, List[GraphPath]] = defaultdict(list)
    for path in paths:
        grouped[f"{path.target_label}:{path.target_name}"].append(path)

    candidates: List[GraphCandidate] = []
    for key, grouped_paths in grouped.items():
        best = max(grouped_paths, key=lambda p: p.score)
        node_id = best.node_ids[-1] if best.node_ids else key
        score = sum(p.score for p in grouped_paths[:5])
        candidates.append(
            GraphCandidate(
                node_id=node_id,
                node_label=best.target_label,
                node_name=best.target_name,
                score=score,
                anchor_entity=best.anchor_entity,
                paths=sorted(grouped_paths, key=lambda p: p.score, reverse=True),
                source_name=best.source_name,
                source_tier=best.source_tier,
            )
        )
    candidates = sorted(candidates, key=lambda c: c.score, reverse=True)[:top_k]
    items = [candidate.to_evidence_item() for candidate in candidates]
    expansions: List[str] = []
    for candidate in candidates:
        for term in [candidate.node_name, *(name for p in candidate.paths[:2] for name in p.node_names)]:
            if term and term not in expansions:
                expansions.append(term)
    context = "\n".join(
        f"[G{idx}] {c.node_label}/{c.node_name} score={c.score:.3f}; "
        f"paths: {' | '.join(p.path_signature for p in c.paths[:2])}"
        for idx, c in enumerate(candidates, start=1)
    )
    refs = [item.to_ref() for item in items]
    return GraphRetrievalResult(
        query=query,
        candidates=candidates,
        paths=paths,
        entity_expansions=expansions[:12],
        context_text=context,
        refs=refs,
        items=items,
        debug={
            **debug,
            "graph_available": True,
            "candidate_count": len(candidates),
            "path_count": len(paths),
            "entity_expansion_count": min(len(expansions), 12),
        },
    )


def retrieve_graph_evidence_sync(
    query: str,
    *,
    intent: RagIntent,
    entities: Optional[List[str]] = None,
    top_k: int = 8,
    max_hops: int = 2,
    filters: Optional[dict] = None,
) -> GraphRetrievalResult:
    if intent not in GRAPH_INTENTS:
        return GraphRetrievalResult.empty(query, "intent_not_graph_enabled")
    if not _graph_configured():
        return GraphRetrievalResult.empty(query, "neo4j_not_configured")

    filters = filters or {}
    terms = _extract_entities(query, entities)
    if not terms:
        return GraphRetrievalResult.empty(query, "no_entities")

    debug = {"terms": terms, "intent": intent, "max_hops": max_hops}
    driver = _connect_driver()
    try:
        with driver.session() as session:
            anchors = []
            anchors.extend(_exact_anchors(session, terms, limit=max(top_k * 3, 12)))
            anchors.extend(_fulltext_anchors(session, terms, limit=max(top_k * 2, 8)))
            if filters.get("use_vector", True):
                anchors.extend(_vector_anchors(session, terms, limit=max(top_k * 2, 8)))
            anchors = _dedupe_anchors(anchors, limit=max(top_k * 2, 8))
            debug["anchor_count"] = len(anchors)
            debug["anchors"] = [
                {"name": a.get("name"), "label": a.get("label"), "score": round(float(a.get("score") or 0), 4)}
                for a in anchors[:10]
            ]
            paths = _expand_paths(
                session,
                anchors,
                intent=intent,
                max_hops=max_hops,
                limit=max(top_k * 16, 40),
            )
    except Exception as exc:
        return GraphRetrievalResult.empty(query, f"neo4j_error:{type(exc).__name__}")
    finally:
        driver.close()

    if not paths:
        return GraphRetrievalResult.empty(query, "no_paths")
    return _paths_to_result(query, paths, debug, top_k)


async def retrieve_graph_evidence(
    query: str,
    *,
    intent: RagIntent,
    entities: Optional[List[str]] = None,
    top_k: int = 8,
    max_hops: int = 2,
    filters: Optional[dict] = None,
) -> GraphRetrievalResult:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                retrieve_graph_evidence_sync,
                query,
                intent=intent,
                entities=entities,
                top_k=top_k,
                max_hops=max_hops,
                filters=filters,
            ),
            timeout=float(os.getenv("RAG_GRAPHRAG_TIMEOUT_SECONDS", "1.0")),
        )
    except Exception as exc:
        return GraphRetrievalResult.empty(query, f"graph_timeout_or_error:{type(exc).__name__}")
