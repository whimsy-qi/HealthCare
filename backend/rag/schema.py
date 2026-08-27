from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


RagIntent = Literal[
    "symptom_dx",
    "medication_safety",
    "guideline_qa",
    "latest_research",
    "rumor_check",
    "report_interpretation",
    "general",
]


@dataclass
class EvidenceItem:
    chunk_id: str
    text: str
    source_type: str
    source_tier: str = "T3"
    title: str = ""
    organization: str = ""
    year: Optional[int] = None
    department: str = ""
    section_title: str = ""
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    doc_id: str = ""
    text_hash: str = ""
    license: str = ""
    evidence_level: str = ""
    locator: Dict[str, Any] = field(default_factory=dict)
    scores: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def ref_type(self) -> str:
        if self.source_type in {"pubmed", "pmc"}:
            return "pubmed"
        if self.source_type in {"drug_label", "rxnorm", "openfda", "dailymed"}:
            return "pdf"
        if self.source_type == "clinical_trial":
            return "web"
        if self.source_type == "kg":
            return "kg"
        return "pdf"

    def to_ref(self) -> Dict[str, Any]:
        locator = dict(self.locator or {})
        if self.doc_id and "doc" not in locator:
            locator["doc"] = self.doc_id
        if self.page_start is not None and "page" not in locator:
            locator["page"] = self.page_start
        is_kg = self.source_type == "kg"
        return {
            "ref_id": self.chunk_id if is_kg else f"doc:{self.doc_id or self.source_type}#{self.chunk_id}",
            "type": self.ref_type,
            "label": (self.title or self.section_title or self.source_type)[:80],
            "locator": locator,
            "snippet": str(self.metadata.get("snippet") or self.text[:300])[:600],
            "evidence_role": "constraint" if is_kg else self.metadata.get("role", "evidence"),
            "citation_allowed": False if is_kg else self.metadata.get("citation_allowed", True),
        }

    def to_source_card(self, idx: int) -> Dict[str, Any]:
        title = self.title or self.section_title or f"RAG evidence #{idx}"
        display_text = str(self.metadata.get("display_text") or self.text)
        snippet = str(self.metadata.get("snippet") or self.text[:300])
        if self.page_start is not None:
            page_label = f" P{self.page_start}"
            if self.page_end and self.page_end != self.page_start:
                page_label = f" P{self.page_start}-{self.page_end}"
            title = f"{title}{page_label}"
        return {
            "id": idx,
            "title": title[:100],
            "content": display_text,
            "snippet": snippet[:600],
            "raw_chunk": self.text,
            "display_text": display_text,
            "knowledge_card": self.metadata.get("knowledge_card") or {},
            "disease": self.metadata.get("disease", ""),
            "department": self.department,
            "type": "guide" if self.source_type == "guideline" else self.source_type,
            "is_internal": True,
            "ref_id": f"doc:{self.doc_id or self.source_type}#{self.chunk_id}",
            "locator": self.to_ref()["locator"],
            "source_tier": self.source_tier,
            "year": self.year,
            "scores": self.scores,
            "role": self.metadata.get("role", "evidence"),
            "evidence_role": "constraint" if self.source_type == "kg" else self.metadata.get("role", "evidence"),
            "citation_allowed": False if self.source_type == "kg" else self.metadata.get("citation_allowed", True),
            "rag_trace": self.metadata.get("rag_trace", {}),
        }


@dataclass
class RetrievalResult:
    query: str
    intent: RagIntent
    items: List[EvidenceItem]
    context_text: str
    refs: List[Dict[str, Any]]
    debug: Dict[str, Any] = field(default_factory=dict)

    def to_legacy_tuple(self) -> tuple[str, List[Dict[str, Any]], List[str]]:
        cards = [item.to_source_card(i + 1) for i, item in enumerate(self.items)]
        return self.context_text, cards, []
