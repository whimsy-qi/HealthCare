# Medical KG GraphRAG

## Role

The knowledge graph is a candidate and path-reasoning layer, not the final authority layer.

- Neo4j stores structured entities and relationships.
- DashVector stores text evidence such as guidelines, drug labels, literature, and trials.
- GraphRAG recalls candidate diseases, drugs, risks, checks, departments, and explanation paths.
- The final answer should still be closed by authoritative text evidence with a locator.

## Retrieval Contract

Use `retrieve_graph_evidence()` as the only GraphRAG entrypoint:

```python
await retrieve_graph_evidence(
    query,
    intent="symptom_dx",
    entities=["胸痛", "出汗"],
    top_k=8,
    max_hops=2,
)
```

It returns:

- `candidates`: ranked target entities.
- `paths`: Neo4j path records with relation types and hop counts.
- `entity_expansions`: terms for downstream guideline/drug/literature retrieval.
- `items`: `EvidenceItem(source_type="kg")` for the normal RAG pipeline.
- `refs`: KG refs with `neo4j_element_id` and `path_signature` locators.
- `debug`: anchor counts, path counts, and availability status.

## Evidence Policy

KG evidence is useful for:

- symptom-to-disease candidate generation;
- drug safety risk hints;
- disease-check-department relationship explanation;
- claim entity linking in rumor checks.

KG evidence is not enough for:

- diagnosis thresholds;
- treatment recommendations;
- drug contraindications and interactions;
- latest research claims.

For high-risk questions, KG-only results must remain low-confidence. The RAG pipeline should require:

- `medical_guideline_v2` for diagnosis/treatment;
- `drug_label_v2` for medication safety;
- PubMed/PMC/ClinicalTrials for latest research or efficacy disputes.

## Runtime Switch

GraphRAG is off by default until KG v2 is rebuilt and verified.

Enable globally:

```powershell
$env:RAG_ENABLE_GRAPHRAG="true"
```

Enable per call:

```python
await retrieve_medical_evidence(query, intent="symptom_dx", filters={"enable_graph": True})
```

## KG Build Requirements

`build_neo4j_graph_v2.py` writes source metadata to nodes and relationships:

- `source_name`
- `source_tier`
- `license`
- `updated_at`

Local DiseaseKG and local drug Excel edges are `T3/local_review_required`; they must not be presented as T1 authority.
