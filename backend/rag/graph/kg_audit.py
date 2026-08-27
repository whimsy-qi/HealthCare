from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

from rag.graph.retriever import _connect_driver, _graph_configured, retrieve_graph_evidence_sync


load_dotenv(find_dotenv(usecwd=True))

REQUIRED_NODE_LABELS = ["Disease", "Symptom", "Drug", "Department", "Check", "Food", "Cure", "Producer"]
REQUIRED_PROPS = ["source_name", "source_tier", "license", "updated_at"]
SMOKE_QUERIES = [
    {"query": "胸痛伴出汗怎么办", "intent": "symptom_dx"},
    {"query": "阿司匹林和华法林一起用有什么风险", "intent": "medication_safety"},
]


def _single_value(session, cypher: str, **params) -> Any:
    row = session.run(cypher, **params).single()
    return row[0] if row else None


def _count_by(session, cypher: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in session.run(cypher):
        key = row[0]
        out[str(key)] = int(row[1] or 0)
    return out


def _missing_prop_stats(session, *, entity: str, prop: str, total: int) -> dict:
    if entity == "node":
        missing = int(_single_value(session, f"MATCH (n) WHERE n.{prop} IS NULL RETURN count(n)") or 0)
    else:
        missing = int(_single_value(session, f"MATCH ()-[r]->() WHERE r.{prop} IS NULL RETURN count(r)") or 0)
    return {
        "missing": missing,
        "total": total,
        "missing_rate": round(missing / max(total, 1), 6),
    }


def _index_audit(session) -> dict:
    indexes = []
    try:
        for row in session.run("SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties RETURN name, type, entityType, labelsOrTypes, properties"):
            indexes.append({
                "name": row["name"],
                "type": row["type"],
                "entity_type": row["entityType"],
                "labels_or_types": list(row["labelsOrTypes"] or []),
                "properties": list(row["properties"] or []),
            })
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "indexes": []}
    return {
        "indexes": indexes,
        "vector_indexes": [idx["name"] for idx in indexes if str(idx["type"]).upper() == "VECTOR"],
        "fulltext_indexes": [idx["name"] for idx in indexes if str(idx["type"]).upper() == "FULLTEXT"],
    }


def _smoke_tests() -> list[dict]:
    rows = []
    for case in SMOKE_QUERIES:
        result = retrieve_graph_evidence_sync(
            case["query"],
            intent=case["intent"],
            top_k=5,
            max_hops=2,
            filters={"use_vector": True},
        )
        locator_valid = all(
            item.locator.get("neo4j_element_id") and item.locator.get("path_signature")
            for item in result.items
        )
        rows.append({
            "query": case["query"],
            "intent": case["intent"],
            "graph_available": bool(result.debug.get("graph_available")),
            "candidate_count": len(result.candidates),
            "path_count": len(result.paths),
            "locator_valid": locator_valid,
            "reason": result.debug.get("reason", ""),
        })
    return rows


def audit_kg() -> dict:
    if not _graph_configured():
        return {
            "status": "neo4j_not_configured",
            "error": "NEO4J_PASSWORD is not configured",
        }
    driver = _connect_driver()
    try:
        with driver.session() as session:
            node_total = int(_single_value(session, "MATCH (n) RETURN count(n)") or 0)
            rel_total = int(_single_value(session, "MATCH ()-[r]->() RETURN count(r)") or 0)
            labels = _count_by(session, "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) ORDER BY label")
            rel_types = _count_by(session, "MATCH ()-[r]->() RETURN type(r), count(*) ORDER BY type(r)")
            node_missing = {prop: _missing_prop_stats(session, entity="node", prop=prop, total=node_total) for prop in REQUIRED_PROPS}
            rel_missing = {prop: _missing_prop_stats(session, entity="relationship", prop=prop, total=rel_total) for prop in REQUIRED_PROPS}
            indexes = _index_audit(session)
    finally:
        driver.close()

    smoke = _smoke_tests()
    node_schema_ok = all(row["missing"] == 0 for row in node_missing.values())
    rel_schema_ok = all(row["missing"] == 0 for row in rel_missing.values())
    smoke_ok = all(row["graph_available"] and row["locator_valid"] for row in smoke)
    return {
        "status": "ok" if node_schema_ok and rel_schema_ok and smoke_ok else "schema_incomplete",
        "node_total": node_total,
        "relationship_total": rel_total,
        "labels": labels,
        "relationship_types": rel_types,
        "missing_node_properties": node_missing,
        "missing_relationship_properties": rel_missing,
        "indexes": indexes,
        "smoke_tests": smoke,
        "schema_ok": node_schema_ok and rel_schema_ok,
        "smoke_ok": smoke_ok,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Neo4j KG provenance and GraphRAG audit.")
    parser.add_argument("--out", default="")
    parser.add_argument("--allow-incomplete", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = audit_kg()
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.allow_incomplete:
        return 0
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
