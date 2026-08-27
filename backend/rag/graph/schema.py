from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from rag.schema import EvidenceItem


@dataclass
class GraphPath:
    anchor_entity: str
    anchor_label: str
    target_name: str
    target_label: str
    node_ids: List[str]
    node_names: List[str]
    relation_types: List[str]
    hop_count: int
    score: float
    source_name: str = "local_medical_kg"
    source_tier: str = "T3"

    @property
    def path_signature(self) -> str:
        if not self.node_names:
            return self.target_name
        parts = [self.node_names[0]]
        for idx, rel in enumerate(self.relation_types):
            tail = self.node_names[idx + 1] if idx + 1 < len(self.node_names) else self.target_name
            parts.append(f"-[{rel}]-")
            parts.append(tail)
        return " ".join(parts)


@dataclass
class GraphCandidate:
    node_id: str
    node_label: str
    node_name: str
    score: float
    anchor_entity: str
    paths: List[GraphPath] = field(default_factory=list)
    source_name: str = "local_medical_kg"
    source_tier: str = "T3"

    def to_evidence_item(self) -> EvidenceItem:
        top_paths = sorted(self.paths, key=lambda p: p.score, reverse=True)[:3]
        path_lines = [f"{p.path_signature} (score={p.score:.3f})" for p in top_paths]
        body = "\n".join(path_lines) if path_lines else f"{self.anchor_entity} -> {self.node_name}"
        text = (
            f"KG candidate: {self.node_name} ({self.node_label}). "
            f"Anchor: {self.anchor_entity}. Graph paths:\n{body}"
        )
        locator: Dict[str, Any] = {
            "neo4j_element_id": self.node_id,
            "anchor_entity": self.anchor_entity,
            "path_signature": top_paths[0].path_signature if top_paths else self.node_name,
        }
        return EvidenceItem(
            chunk_id=f"kg:{self.node_id}",
            text=text,
            source_type="kg",
            source_tier=self.source_tier,
            title=f"KG: {self.node_name}",
            section_title="graph_path",
            doc_id=f"kg:{self.node_id}",
            locator=locator,
            scores={"graph": round(self.score, 6)},
            metadata={
                "node_id": self.node_id,
                "node_label": self.node_label,
                "node_name": self.node_name,
                "anchor_entity": self.anchor_entity,
                "path_count": len(self.paths),
                "relation_types": ",".join(sorted({r for p in self.paths for r in p.relation_types})),
                "source_name": self.source_name,
                "collection_key": "neo4j_graph",
                "evidence_role": "constraint",
                "citation_allowed": False,
            },
        )


@dataclass
class GraphRetrievalResult:
    query: str
    candidates: List[GraphCandidate]
    paths: List[GraphPath]
    entity_expansions: List[str]
    context_text: str
    refs: List[Dict[str, Any]]
    items: List[EvidenceItem]
    debug: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def empty(cls, query: str, reason: str = "graph_disabled") -> "GraphRetrievalResult":
        return cls(
            query=query,
            candidates=[],
            paths=[],
            entity_expansions=[],
            context_text="",
            refs=[],
            items=[],
            debug={"graph_available": False, "reason": reason},
        )
