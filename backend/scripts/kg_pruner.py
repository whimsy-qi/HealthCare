from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import List

from dotenv import find_dotenv, load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from rag.graph import GraphRetrievalResult, retrieve_graph_evidence_sync


load_dotenv(find_dotenv(usecwd=True))
logger = logging.getLogger("KGPruner")


class KnowledgeGraphPruner:
    """
    Backward-compatible wrapper around the RAG v2 GraphRAG retriever.

    Existing agents still call `execute_pruning(keywords, top_k)` and expect a
    plain text context. New RAG code should call `rag.graph.retrieve_graph_evidence`
    to get structured candidates, paths, refs, and debug metadata.
    """

    def __init__(self):
        self.uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.enabled = bool(os.getenv("NEO4J_PASSWORD"))
        if not self.enabled:
            logger.warning("[KGPruner] NEO4J_PASSWORD is not configured; GraphRAG is disabled.")

    def close(self):
        return None

    def execute_pruning_structured(
        self,
        extracted_keywords: List[str],
        top_k: int = 3,
        intent: str = "symptom_dx",
    ) -> GraphRetrievalResult:
        query = " ".join(k.strip() for k in extracted_keywords if k and k.strip())
        return retrieve_graph_evidence_sync(
            query,
            intent=intent,  # type: ignore[arg-type]
            entities=extracted_keywords,
            top_k=top_k,
            max_hops=2,
            filters={"use_vector": True},
        )

    def execute_pruning(self, extracted_keywords: List[str], top_k: int = 3, base_threshold: float = 0.0) -> str:
        if not extracted_keywords:
            return "图谱推理引擎未收到有效医学实体。"
        result = self.execute_pruning_structured(extracted_keywords, top_k=top_k)
        if not result.items:
            reason = result.debug.get("reason", "no_graph_result")
            return f"未能从知识图谱召回可靠候选路径。原因：{reason}"

        lines = ["【基于 Neo4j GraphRAG 的候选实体与关系路径】"]
        for idx, candidate in enumerate(result.candidates[:top_k], start=1):
            lines.append(
                f"\n核心候选 {idx}: [{candidate.node_name}] "
                f"(类型: {candidate.node_label}, 图谱分数: {candidate.score:.3f})"
            )
            for path in candidate.paths[:3]:
                lines.append(
                    f"  - 路径: {path.path_signature} "
                    f"(anchor={path.anchor_entity}, hops={path.hop_count}, score={path.score:.3f})"
                )
        lines.append("\n注意：KG 结果只作为候选和路径解释，高风险医学结论必须由指南、药品标签或论文证据闭环。")
        return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    pruner = KnowledgeGraphPruner()
    print(pruner.execute_pruning(["胸痛", "出汗"], top_k=3))
