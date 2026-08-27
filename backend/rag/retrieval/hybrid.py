from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from rag.config import ENABLE_GRAPHRAG, ENABLE_LOCAL_PDF_INDEX
from rag.graph import retrieve_graph_evidence
from rag.rerank import rerank_items
from rag.retrieval.dashvector_backend import search_dashvector
from rag.retrieval.drug_normalizer import expand_drug_query
from rag.retrieval.local_index import local_guideline_quality_stats, search_local_guidelines
from rag.schema import EvidenceItem, RagIntent


GRAPH_INTENTS = {"symptom_dx", "medication_safety"}
RESEARCH_SOURCE_TYPES = {"pubmed", "pmc", "literature", "clinical_trial"}
STRICT_RESEARCH_SOURCE_TYPES = {"pubmed", "pmc", "clinical_trial"}
RESEARCH_COLLECTIONS = {"medical_literature_v2", "clinical_trial_v2"}
CANCER_EVIDENCE_COLLECTIONS = {"cancer_evidence", "cancer_evidence_v2"}
DRUG_LABEL_COLLECTIONS = {"drug_label_v2"}
ONCOLOGY_TERMS = {
    "cancer", "tumor", "tumour", "oncology", "carcinoma", "neoplasm", "malignancy",
    "lymphoma", "leukemia", "melanoma", "sarcoma", "pdq", "nci", "car-t",
    "\u764c", "\u80bf\u7624", "\u6076\u6027", "\u767d\u8840\u75c5", "\u6dcb\u5df4\u7624",
}
RESEARCH_RELEVANCE_THRESHOLD = 0.06
QUERY_RELEVANCE_ALIASES = {
    "\u4e8c\u7532\u53cc\u80cd": "metformin",
    "\u6297\u8870\u8001": "aging longevity",
    "\u51cf\u91cd": "obesity weight loss",
    "\u80a5\u80d6": "obesity",
    "\u75ab\u82d7": "vaccine vaccination",
    "\u963f\u5c14\u8328\u6d77\u9ed8": "alzheimer",
    "\u5b9e\u4f53\u7624": "solid tumor cancer",
    "\u764c": "cancer tumor oncology",
    "\u80bf\u7624": "cancer tumor oncology",
    "\u751f\u916e": "ketogenic diet",
    "\u4fdd\u5065\u54c1": "supplement",
}
SPECIFIC_RELEVANCE_ALIASES = {
    "car-t": ["car-t", "car t", "chimeric antigen receptor"],
    "\u751f\u916e": ["ketogenic", "ketogenic diet", "keto"],
    "\u75ab\u82d7": ["vaccine", "vaccination", "immunization"],
    "\u963f\u5c14\u8328\u6d77\u9ed8": ["alzheimer", "lecanemab", "donanemab"],
    "glp-1": ["glp-1", "glp 1", "semaglutide", "liraglutide", "tirzepatide"],
    "\u4e8c\u7532\u53cc\u80cd": ["metformin"],
    "metformin": ["metformin"],
    "\u4fdd\u5065\u54c1": ["supplement", "complementary", "alternative medicine"],
}


def _dedupe(items: List[EvidenceItem]) -> List[EvidenceItem]:
    seen = set()
    out: List[EvidenceItem] = []
    for item in items:
        key = item.text_hash or f"{item.doc_id}:{item.chunk_id}" or item.text[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _collection_key(item: EvidenceItem) -> str:
    return str(item.metadata.get("collection_key") or item.metadata.get("collection_name") or item.source_type)


def _collection_name(item: EvidenceItem) -> str:
    return str(item.metadata.get("collection_name") or _collection_key(item))


def _is_oncology_query(query: str) -> bool:
    q = (query or "").lower()
    return any(term in q for term in ONCOLOGY_TERMS)


def _expanded_query_for_relevance(query: str) -> str:
    expanded = query or ""
    for term, aliases in QUERY_RELEVANCE_ALIASES.items():
        if term in expanded:
            expanded = f"{expanded} {aliases}"
    return expanded


def _is_cancer_evidence_item(item: EvidenceItem) -> bool:
    return _collection_key(item) in CANCER_EVIDENCE_COLLECTIONS or _collection_name(item) in CANCER_EVIDENCE_COLLECTIONS


def _research_relevance_score(query: str, item: EvidenceItem) -> float:
    from rag.retrieval.query import tokenize

    q_tokens = set(tokenize(_expanded_query_for_relevance(query)))
    if not q_tokens:
        return 0.0
    haystack = " ".join([
        item.title,
        item.section_title,
        item.text[:1200],
        str(item.metadata.get("query") or ""),
        str(item.metadata.get("topic") or ""),
        str(item.metadata.get("conditions") or ""),
        str(item.metadata.get("interventions") or ""),
        str(item.metadata.get("mesh_terms") or ""),
        str(item.metadata.get("publication_types") or ""),
    ])
    h_tokens = set(tokenize(haystack))
    return len(q_tokens & h_tokens) / max(len(q_tokens), 1)


def _specific_relevance_required(query: str) -> list[str]:
    q = (query or "").lower()
    required: list[str] = []
    for trigger, aliases in SPECIFIC_RELEVANCE_ALIASES.items():
        if trigger.lower() in q:
            required.extend(aliases)
    return required


def _specific_relevance_met(query: str, item: EvidenceItem) -> bool:
    required = _specific_relevance_required(query)
    if not required:
        return True
    haystack = " ".join([
        item.title,
        item.section_title,
        item.text[:1800],
        str(item.metadata.get("query") or ""),
        str(item.metadata.get("topic") or ""),
        str(item.metadata.get("conditions") or ""),
        str(item.metadata.get("interventions") or ""),
        str(item.metadata.get("mesh_terms") or ""),
    ]).lower()
    return any(alias.lower() in haystack for alias in required)


def _is_research_item(item: EvidenceItem, query: str = "") -> bool:
    if _research_relevance_score(query, item) < RESEARCH_RELEVANCE_THRESHOLD:
        return False
    if not _specific_relevance_met(query, item):
        return False
    if _is_cancer_evidence_item(item):
        return _is_oncology_query(query)
    if item.source_type in STRICT_RESEARCH_SOURCE_TYPES:
        return True
    if item.source_type == "literature":
        return True
    return _collection_name(item) in RESEARCH_COLLECTIONS or _collection_key(item) in {"literature", "clinical_trial"}


def _research_evidence_debug(query: str, items: List[EvidenceItem]) -> tuple[list[dict], list[dict]]:
    accepted: list[dict] = []
    rejected: list[dict] = []
    for item in items:
        if not (
            item.source_type in RESEARCH_SOURCE_TYPES
            or _collection_key(item) in {"literature", "clinical_trial", "cancer_evidence"}
            or _collection_name(item) in (RESEARCH_COLLECTIONS | CANCER_EVIDENCE_COLLECTIONS)
        ):
            continue
        row = {
            "chunk_id": item.chunk_id,
            "source_type": item.source_type,
            "collection_key": _collection_key(item),
            "collection_name": _collection_name(item),
            "title": item.title[:120],
        }
        if _is_research_item(item, query):
            accepted.append({**row, "relevance": round(_research_relevance_score(query, item), 6)})
        else:
            reason = "below_relevance_threshold"
            if _research_relevance_score(query, item) >= RESEARCH_RELEVANCE_THRESHOLD and not _specific_relevance_met(query, item):
                reason = "missing_specific_intervention_term"
            if _is_cancer_evidence_item(item) and not _is_oncology_query(query):
                reason = "cancer_evidence_requires_oncology_query"
            rejected.append({**row, "reason": reason, "relevance": round(_research_relevance_score(query, item), 6)})
    return accepted, rejected


def _quota_policy(intent: RagIntent, top_k: int) -> Dict:
    if intent == "guideline_qa":
        return {"min": {"guideline": min(4, top_k)}, "max": {"patient_education": 1}}
    if intent == "medication_safety":
        return {"min": {"drug_label": min(3, top_k)}, "max": {"patient_education": 0}}
    if intent == "latest_research":
        return {"min": {"research": min(3, top_k)}, "max": {"guideline": 2, "patient_education": 0}}
    if intent == "rumor_check":
        return {"min": {"research": min(2, top_k)}, "max": {"patient_education": 1}}
    if intent == "general":
        return {"min": {}, "max": {"kg": 1}}
    return {"min": {}, "max": {"patient_education": 1}}


def _matches_quota_group(item: EvidenceItem, group: str, query: str = "") -> bool:
    if group == "research":
        return _is_research_item(item, query)
    if group == "drug_label":
        return item.source_type == "drug_label" or _collection_name(item) in DRUG_LABEL_COLLECTIONS
    return item.source_type == group or _collection_key(item) == group


def _apply_source_quota(ranked: List[EvidenceItem], *, intent: RagIntent, top_k: int, query: str) -> tuple[List[EvidenceItem], Dict]:
    policy = _quota_policy(intent, top_k)
    chosen: List[EvidenceItem] = []
    chosen_ids: set[str] = set()
    filled: Dict[str, int] = {}

    def add(item: EvidenceItem) -> bool:
        key = item.chunk_id
        if key in chosen_ids or len(chosen) >= top_k:
            return False
        chosen.append(item)
        chosen_ids.add(key)
        return True

    for group, minimum in policy.get("min", {}).items():
        count = 0
        for item in ranked:
            if count >= minimum or len(chosen) >= top_k:
                break
            if _matches_quota_group(item, group, query) and add(item):
                count += 1
        filled[group] = count

    max_counts = policy.get("max", {})
    current_counts: Dict[str, int] = {}
    for item in chosen:
        for group in max_counts:
            if _matches_quota_group(item, group, query):
                current_counts[group] = current_counts.get(group, 0) + 1

    for item in ranked:
        if len(chosen) >= top_k:
            break
        blocked = False
        for group, maximum in max_counts.items():
            if maximum <= 0 and _matches_quota_group(item, group, query):
                blocked = True
                break
            if _matches_quota_group(item, group, query) and current_counts.get(group, 0) >= maximum:
                blocked = True
                break
        if blocked:
            continue
        if add(item):
            for group in max_counts:
                if _matches_quota_group(item, group, query):
                    current_counts[group] = current_counts.get(group, 0) + 1

    for item in ranked:
        if len(chosen) >= top_k:
            break
        add(item)

    dropped = [
        item.chunk_id
        for item in ranked[: max(top_k * 2, top_k)]
        if item.chunk_id not in chosen_ids
    ]
    return chosen, {
        "source_quota": policy,
        "quota_filled": filled,
        "quota_dropped": dropped,
    }


def _debug_item(item: EvidenceItem, rank: int) -> dict:
    return {
        "rank": rank,
        "chunk_id": item.chunk_id,
        "doc_id": item.doc_id,
        "source_type": item.source_type,
        "source_tier": item.source_tier,
        "title": item.title[:120],
        "section_title": item.section_title[:80],
        "collection_key": _collection_key(item),
        "score": round(float(item.scores.get("rerank", item.scores.get("dense", 0.0)) or 0.0), 6),
    }


async def hybrid_retrieve(
    query: str,
    *,
    intent: RagIntent,
    top_k: int = 8,
    filters: Optional[Dict] = None,
) -> Tuple[List[EvidenceItem], Dict]:
    filters = filters or {}
    required_sources = _required_sources(intent)
    debug = {"recall": {}, "filters": filters, "source_required": required_sources}
    recalled: List[EvidenceItem] = []
    graph_items: List[EvidenceItem] = []
    graph_expansions: List[str] = []
    graph_enabled = bool(filters.get("enable_graph")) or ENABLE_GRAPHRAG
    retrieval_query = query
    drug_aliases: list[str] = []
    if intent == "medication_safety":
        retrieval_query, drug_aliases = expand_drug_query(retrieval_query)
    elif intent in {"latest_research", "rumor_check"}:
        retrieval_query = _expanded_query_for_relevance(retrieval_query)

    if graph_enabled and intent in GRAPH_INTENTS:
        graph_result = await retrieve_graph_evidence(
            query,
            intent=intent,
            top_k=max(4, min(top_k, 8)),
            max_hops=int(filters.get("graph_max_hops", 2)),
            filters=filters.get("graph_filters") or {},
        )
        graph_items = graph_result.items
        graph_expansions = graph_result.entity_expansions
        debug["recall"]["neo4j_graph"] = len(graph_items)
        debug["graph"] = graph_result.debug
        debug["graph_entity_expansions"] = graph_expansions
        debug["graph_path_count"] = len(graph_result.paths)
        if graph_expansions:
            retrieval_query = f"{retrieval_query} {' '.join(graph_expansions[:8])}"[:800]
    else:
        debug["graph"] = {
            "graph_available": False,
            "reason": "disabled" if not graph_enabled else "intent_not_graph_enabled",
        }

    if ENABLE_LOCAL_PDF_INDEX and intent in {
        "symptom_dx",
        "guideline_qa",
        "report_interpretation",
        "rumor_check",
        "general",
        "latest_research",
    }:
        local_items = search_local_guidelines(
            retrieval_query,
            intent=intent,
            top_k=max(top_k * 3, 12),
            department_filter=filters.get("departments"),
        )
        debug["recall"]["local_guideline_bm25"] = len(local_items)
        debug["quarantine_filtered"] = local_guideline_quality_stats().get("quarantine_filtered", 0)
        recalled.extend(local_items)

    dense_items = await search_dashvector(retrieval_query, intent=intent, top_k=max(top_k * 2, 8), filter_expr=filters.get("filter_expr"))
    debug["recall"]["dashvector_dense"] = len(dense_items)
    recalled.extend(dense_items)

    drug_label_hits = sum(1 for item in dense_items if item.source_type == "drug_label")
    if ENABLE_LOCAL_PDF_INDEX and intent == "medication_safety" and drug_label_hits < top_k:
        local_items = search_local_guidelines(
            retrieval_query,
            intent=intent,
            top_k=max((top_k - drug_label_hits) * 2, 6),
            department_filter=filters.get("departments"),
        )
        debug["recall"]["local_guideline_bm25"] = len(local_items)
        debug["quarantine_filtered"] = local_guideline_quality_stats().get("quarantine_filtered", 0)
        recalled.extend(local_items)

    recalled.extend(graph_items)
    unique = _dedupe(recalled)
    ranked = rerank_items(retrieval_query, unique, intent=intent)
    selected, quota_debug = _apply_source_quota(ranked, intent=intent, top_k=top_k, query=retrieval_query)
    collection_hits: Dict[str, int] = {}
    for item in unique:
        key = str(item.metadata.get("collection_key") or item.source_type)
        collection_hits[key] = collection_hits.get(key, 0) + 1
    debug["candidate_count"] = len(unique)
    debug["returned_count"] = len(selected)
    debug["ranked_candidates_top20"] = [_debug_item(item, idx + 1) for idx, item in enumerate(ranked[:20])]
    debug["selected_top"] = [_debug_item(item, idx + 1) for idx, item in enumerate(selected)]
    debug["graph_enabled"] = graph_enabled
    debug["retrieval_query"] = retrieval_query
    debug["drug_query_aliases"] = drug_aliases
    debug["collection_hits"] = collection_hits
    debug.update(quota_debug)
    research_accepted, research_rejected = _research_evidence_debug(retrieval_query, selected)
    debug["research_evidence_accepted_sources"] = research_accepted
    debug["research_evidence_rejected_sources"] = research_rejected
    if intent == "latest_research":
        debug["preferred_source_type_hit"] = bool(research_accepted)
    else:
        debug["preferred_source_type_hit"] = any(item.source_type in set(required_sources) for item in selected)
    debug["preferred_collection_hit"] = any(
        _collection_key(item) in set(required_sources) or _collection_name(item) in {f"{src}_v2" for src in required_sources}
        for item in selected
    )
    debug["missing_required_source"] = not debug["preferred_source_type_hit"]
    debug["evidence_policy"] = "drug_label_required" if intent == "medication_safety" else "preferred_source_required"
    debug["unsafe_to_answer"] = (
        intent == "medication_safety"
        and not any(item.source_type == "drug_label" for item in selected)
    )
    debug["research_source_missing"] = (
        intent == "latest_research"
        and not bool(research_accepted)
    )
    authoritative_hits = [
        item for item in selected
        if item.source_type != "kg" and (item.source_type in set(required_sources) or item.source_tier in {"T1", "T2"})
    ]
    debug["authority_evidence_closure"] = bool(authoritative_hits) if graph_items else None
    debug["kg_only_result"] = bool(graph_items) and not bool(authoritative_hits)
    return selected, debug


def _required_sources(intent: RagIntent) -> List[str]:
    if intent == "medication_safety":
        return ["drug_label", "rxnorm"]
    if intent == "latest_research":
        return ["pubmed", "pmc", "literature", "clinical_trial"]
    if intent in {"guideline_qa", "symptom_dx", "report_interpretation"}:
        return ["guideline"]
    if intent == "rumor_check":
        return ["guideline", "pubmed", "pmc", "clinical_trial", "drug_label"]
    return ["guideline", "literature", "kg"]
