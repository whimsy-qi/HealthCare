"""
MedRAG-style KG candidate ranking for MADDx.

This module runs before the debate loop and turns structured symptom slots into
evidence-backed disease priors. It is deterministic: no LLM call and no open
Cypher surface.
"""
import math
import re
from typing import Any, Dict, List, Optional

from .tools import query_symptom_disease_edges


METHOD_NAME = "medrag_kg_bm25_prior"


def _normalize_name(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _split_symptom_text(text: str) -> List[str]:
    text = str(text or "").strip()
    if not text:
        return []

    variants = [text]
    for sep in (":", "："):
        if sep in text:
            variants.append(text.split(sep, 1)[1])

    terms: List[str] = []
    for raw in variants:
        for part in re.split(r"[，,、;/；\n\r]+", raw):
            term = part.strip()
            if 1 <= len(term) <= 40:
                terms.append(term)
    return terms


def extract_symptom_terms(symptoms: List[dict]) -> List[str]:
    terms: List[str] = []
    seen = set()
    for item in symptoms or []:
        if isinstance(item, dict):
            raw = item.get("name") or item.get("symptom") or ""
        else:
            raw = str(item or "")
        for term in _split_symptom_text(raw):
            key = _normalize_name(term)
            if not key or key in seen:
                continue
            seen.add(key)
            terms.append(term)
    return terms


def _fallback_result(input_count: int, error: str = "") -> Dict[str, Any]:
    return {
        "method": METHOD_NAME,
        "candidates": [],
        "stats": {
            "input_symptoms": input_count,
            "matched_symptoms": 0,
            "candidate_count": 0,
            "fallback": True,
            "error": error,
        },
    }


async def rank_disease_candidates(
    symptoms: List[dict],
    patient_profile: Optional[dict] = None,
    top_k: int = 8,
) -> Dict[str, Any]:
    """
    Rank disease candidates from symptom-to-disease KG edges.

    patient_profile is accepted for API stability and future personalization;
    v1 intentionally keeps scoring KG-only to avoid unverifiable hand weights.
    """
    del patient_profile

    symptom_terms = extract_symptom_terms(symptoms)
    if not symptom_terms:
        return _fallback_result(0, "empty_symptoms")

    kg = await query_symptom_disease_edges(symptom_terms, limit_per_symptom=30)
    if not kg or kg.get("fallback"):
        return _fallback_result(len(symptom_terms), str((kg or {}).get("error") or "kg_unavailable"))

    total_diseases = int(kg.get("total_diseases") or 0)
    blocks = kg.get("symptoms") or []
    if total_diseases <= 0:
        total_diseases = max(
            1,
            len({
                edge.get("disease")
                for block in blocks
                for edge in (block.get("edges") or [])
                if edge.get("disease")
            }),
        )

    disease_scores: Dict[str, Dict[str, Any]] = {}
    total_idf = 0.0
    matched_symptom_count = 0

    for block in blocks:
        input_symptom = str(block.get("input_symptom") or "").strip()
        edges = block.get("edges") or []
        df = int(block.get("df") or len({e.get("disease") for e in edges if e.get("disease")}) or 0)
        idf = math.log((total_diseases + 1.0) / (df + 1.0)) + 1.0
        total_idf += idf
        if edges:
            matched_symptom_count += 1

        for edge in edges:
            disease = str(edge.get("disease") or "").strip()
            if not disease:
                continue
            key = _normalize_name(disease)
            match_quality = float(edge.get("match_quality") or 0.8)
            ref = str(edge.get("ref") or f"kg:Disease:{disease}:HAS_SYMPTOM:{edge.get('matched_symptom')}")
            record = disease_scores.setdefault(
                key,
                {
                    "disease": disease,
                    "raw_score": 0.0,
                    "matched_symptoms": set(),
                    "evidence_refs": [],
                },
            )
            record["raw_score"] += idf * max(0.0, min(1.0, match_quality))
            if input_symptom:
                record["matched_symptoms"].add(input_symptom)
            if ref and ref not in record["evidence_refs"]:
                record["evidence_refs"].append(ref)

    if not disease_scores or total_idf <= 0:
        return _fallback_result(len(symptom_terms), "no_kg_candidates")

    ranked = []
    for record in disease_scores.values():
        matched = sorted(record["matched_symptoms"])
        score = max(0.0, min(1.0, float(record["raw_score"]) / total_idf))
        ranked.append({
            "disease": record["disease"],
            "kg_prior_score": round(score, 4),
            "matched_symptoms": matched,
            "evidence_refs": record["evidence_refs"],
            "support_count": len(matched),
        })

    ranked.sort(
        key=lambda x: (
            x["kg_prior_score"],
            x["support_count"],
            x["disease"],
        ),
        reverse=True,
    )

    limit = max(1, min(int(top_k or 8), 20))
    candidates = []
    for idx, item in enumerate(ranked[:limit], 1):
        out = dict(item)
        out["rank"] = idx
        candidates.append(out)

    return {
        "method": METHOD_NAME,
        "candidates": candidates,
        "stats": {
            "input_symptoms": len(symptom_terms),
            "matched_symptoms": matched_symptom_count,
            "candidate_count": len(candidates),
            "fallback": False,
            "error": "",
        },
    }
