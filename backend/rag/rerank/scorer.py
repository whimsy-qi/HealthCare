from __future__ import annotations

import re
from typing import Dict, List

from rag.schema import EvidenceItem, RagIntent
from rag.retrieval.query import tokenize


SOURCE_WEIGHTS: Dict[RagIntent, Dict[str, float]] = {
    "guideline_qa": {"guideline": 0.45, "literature": 0.2, "pubmed": 0.2, "kg": 0.1, "patient_education": 0.02},
    "medication_safety": {"drug_label": 0.5, "rxnorm": 0.2, "guideline": 0.15, "drug_safety_signal": 0.08, "literature": 0.1, "kg": 0.03},
    "latest_research": {"pubmed": 0.45, "pmc": 0.45, "literature": 0.45, "cancer_evidence": 0.35, "clinical_trial": 0.2, "guideline": 0.1, "kg": 0.02},
    "rumor_check": {"guideline": 0.35, "pubmed": 0.3, "pmc": 0.3, "literature": 0.28, "cancer_evidence": 0.25, "clinical_trial": 0.15, "drug_label": 0.2, "drug_safety_signal": 0.08, "kg": 0.08, "patient_education": 0.02},
    "symptom_dx": {"guideline": 0.35, "kg": 0.25, "literature": 0.15},
    "report_interpretation": {"guideline": 0.35, "literature": 0.15},
    "general": {"patient_education": 0.28, "guideline": 0.3, "literature": 0.2, "kg": 0.1},
}

TIER_WEIGHTS = {"T1": 0.2, "T2": 0.12, "T3": 0.04, "T4": -0.15}
EVIDENCE_LEVEL_WEIGHTS = {
    "official_drug_label": 0.24,
    "official_drug_label_local_snapshot": 0.22,
    "systematic_review": 0.22,
    "meta_analysis": 0.22,
    "randomized_controlled_trial": 0.2,
    "clinical_trial_registry": 0.16,
    "clinical_trial": 0.14,
    "guideline": 0.14,
    "pubmed_abstract": 0.08,
}
SECTION_WEIGHTS = {
    "推荐意见": 0.12,
    "诊断与评估": 0.1,
    "治疗与管理": 0.1,
    "用药安全": 0.12,
    "禁忌": 0.18,
    "contraindications": 0.18,
    "药物相互作用": 0.18,
    "drug_interactions": 0.18,
    "不良反应": 0.16,
    "adverse_reactions": 0.16,
    "注意事项": 0.14,
    "precautions": 0.14,
    "孕妇及哺乳期妇女用药": 0.12,
    "儿童用药": 0.12,
    "老人用药": 0.12,
}
METADATA_TERM_FIELDS = {
    "drug_name",
    "drug_display_name",
    "drug_query",
    "generic_name",
    "brand_name",
    "related_diseases",
    "disease",
    "conditions",
    "interventions",
    "topic",
    "topic_tags",
    "mesh_terms",
    "publication_types",
    "journal",
}

DOSAGE_FORM_RE = re.compile(r"(肠溶片|缓释片|分散片|咀嚼片|胶囊|软胶囊|颗粒|注射液|注射剂|片|丸|散|膏|乳膏|滴眼液|口服液)$")


def _lexical_overlap(query: str, text: str) -> float:
    q = set(tokenize(query))
    if not q:
        return 0.0
    t = set(tokenize(text))
    return len(q & t) / max(len(q), 1)


def _metadata_overlap(query: str, item: EvidenceItem) -> float:
    q = set(tokenize(query))
    if not q:
        return 0.0
    values = []
    for key in METADATA_TERM_FIELDS:
        value = item.metadata.get(key)
        if value:
            values.append(str(value))
    if item.evidence_level:
        values.append(item.evidence_level)
    tokens = set(tokenize(" ".join(values)))
    return len(q & tokens) / max(len(q), 1)


def _evidence_level_weight(item: EvidenceItem) -> float:
    level = (item.evidence_level or "").lower().strip()
    if level in EVIDENCE_LEVEL_WEIGHTS:
        return EVIDENCE_LEVEL_WEIGHTS[level]
    pub_types = str(item.metadata.get("publication_types") or "").lower()
    if "meta-analysis" in pub_types or "meta analysis" in pub_types:
        return EVIDENCE_LEVEL_WEIGHTS["meta_analysis"]
    if "systematic review" in pub_types:
        return EVIDENCE_LEVEL_WEIGHTS["systematic_review"]
    if "randomized controlled trial" in pub_types:
        return EVIDENCE_LEVEL_WEIGHTS["randomized_controlled_trial"]
    if "clinical trial" in pub_types:
        return EVIDENCE_LEVEL_WEIGHTS["clinical_trial"]
    return 0.0


def _structured_locator_weight(item: EvidenceItem) -> float:
    locator = item.locator or {}
    if item.source_type == "pubmed" and locator.get("pmid"):
        return 0.12
    if item.source_type == "clinical_trial" and locator.get("nct_id"):
        return 0.12
    if item.source_type == "drug_label" and (locator.get("rxcui") or locator.get("set_id") or locator.get("section")):
        return 0.1
    if item.page_start is not None or locator:
        return 0.08
    return -0.08


def _section_intent_weight(query: str, item: EvidenceItem, intent: RagIntent) -> float:
    if intent != "medication_safety":
        return 0.0
    q = query or ""
    section = f"{item.section_title} {item.metadata.get('section_key') or ''}".lower()
    rules = [
        (("禁忌", "不能用", "不适合", "contraindication"), ("禁忌", "contraindications"), 0.22),
        (("相互作用", "合用", "一起用", "interaction"), ("药物相互作用", "drug_interactions"), 0.22),
        (("不良反应", "副作用", "adverse"), ("不良反应", "adverse_reactions"), 0.18),
        (("注意事项", "风险", "警告", "warning"), ("注意事项", "precautions", "warnings"), 0.14),
        (("孕妇", "妊娠", "哺乳"), ("孕妇及哺乳期妇女用药", "specific_populations"), 0.14),
        (("儿童", "小儿"), ("儿童用药", "pediatric_use"), 0.14),
        (("老人", "老年"), ("老人用药", "geriatric_use"), 0.14),
    ]
    for triggers, sections, weight in rules:
        if any(trigger in q for trigger in triggers) and any(name.lower() in section for name in sections):
            return weight
    return 0.0


def _entity_exact_weight(query: str, item: EvidenceItem, intent: RagIntent) -> float:
    metadata_values = [
        str(item.metadata.get("drug_name") or ""),
        str(item.metadata.get("drug_display_name") or ""),
        str(item.metadata.get("generic_name") or ""),
        str(item.metadata.get("brand_name") or ""),
        item.title,
    ]
    if intent == "medication_safety":
        for value in metadata_values:
            value = value.strip()
            if value and len(value) >= 2 and value in query:
                return 0.28
            base = DOSAGE_FORM_RE.sub("", value)
            if base and len(base) >= 2 and base in query:
                return 0.2
    if intent in {"guideline_qa", "symptom_dx", "latest_research", "rumor_check"}:
        for term in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9+-]{2,}", query):
            if term and any(term.lower() in value.lower() for value in metadata_values):
                return 0.12
    return 0.0


def _topic_mismatch_penalty(query: str, item: EvidenceItem, intent: RagIntent) -> float:
    if intent != "medication_safety" or item.source_type != "drug_label":
        return 0.0
    drug_fields = " ".join([
        str(item.metadata.get("drug_name") or ""),
        str(item.metadata.get("drug_display_name") or ""),
        str(item.metadata.get("generic_name") or ""),
        str(item.metadata.get("brand_name") or ""),
        item.title,
    ])
    query_terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9+-]{2,}", query))
    drug_terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9+-]{2,}", drug_fields))
    if not query_terms or not drug_terms:
        return 0.0
    if query_terms & drug_terms:
        return 0.0
    safety_terms = {"禁忌", "不良反应", "副作用", "相互作用", "合用", "风险", "注意事项", "孕妇", "儿童", "老人"}
    non_safety_query_terms = {term for term in query_terms if term not in safety_terms}
    if non_safety_query_terms:
        return -0.18
    return 0.0


def rerank_items(query: str, items: List[EvidenceItem], *, intent: RagIntent) -> List[EvidenceItem]:
    source_weights = SOURCE_WEIGHTS.get(intent, SOURCE_WEIGHTS["general"])
    current_year = 2026
    for item in items:
        base = max(item.scores.values()) if item.scores else 0.0
        lexical = _lexical_overlap(query, f"{item.title} {item.section_title} {item.text}")
        source_weight = source_weights.get(item.source_type, source_weights.get("guideline", 0.05))
        tier_weight = TIER_WEIGHTS.get(item.source_tier, 0.0)
        section_weight = SECTION_WEIGHTS.get(item.section_title, SECTION_WEIGHTS.get(str(item.metadata.get("section_key") or ""), 0.0))
        safety_weight = 0.12 if intent == "medication_safety" and item.metadata.get("safety_critical") else 0.0
        locator_weight = _structured_locator_weight(item)
        evidence_weight = _evidence_level_weight(item)
        metadata_overlap = _metadata_overlap(query, item)
        section_intent_weight = _section_intent_weight(query, item, intent)
        entity_exact_weight = _entity_exact_weight(query, item, intent)
        topic_mismatch_penalty = _topic_mismatch_penalty(query, item, intent)
        official_drug_weight = 0.16 if intent == "medication_safety" and item.source_type == "drug_label" and item.source_tier == "T1" else 0.0
        year_weight = 0.0
        if item.year:
            year_weight = max(0.0, 1.0 - ((current_year - item.year) / 15.0)) * 0.08
        title_hit = 0.12 if any(term and term in item.title for term in re.findall(r"[\u4e00-\u9fff]{2,}", query)) else 0.0
        final = (
            base
            + lexical * 1.4
            + metadata_overlap * 0.8
            + source_weight
            + tier_weight
            + section_weight
            + safety_weight
            + official_drug_weight
            + evidence_weight
            + locator_weight
            + year_weight
            + title_hit
            + section_intent_weight
            + entity_exact_weight
            + topic_mismatch_penalty
        )
        item.scores["rerank"] = round(final, 6)
        item.scores["lexical_overlap"] = round(lexical, 6)
        item.scores["metadata_overlap"] = round(metadata_overlap, 6)
        item.scores["evidence_level_weight"] = round(evidence_weight, 6)
        item.scores["section_intent_weight"] = round(section_intent_weight, 6)
        item.scores["entity_exact_weight"] = round(entity_exact_weight, 6)
        item.scores["topic_mismatch_penalty"] = round(topic_mismatch_penalty, 6)
    return sorted(items, key=lambda i: i.scores.get("rerank", 0.0), reverse=True)
