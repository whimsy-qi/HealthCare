import asyncio
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import yaml


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, ROOT)

from rag.ingest.pdf import GuidelineChunk, quality_flags, should_ocr_page
from rag.ingest.pdf import build_guideline_chunks, classify_block_type
from rag.ingest import pdf_resume_cli
from rag.ingest import external_source_cli
from rag.ingest.cli import _chunk_to_evidence
from rag.ingest.drug_cli import DEFAULT_SEED, build_drug_items
from rag.ingest import local_drug_cli
from rag.ingest import local_drug_resume_cli
from rag.ingest import state_audit
from rag.ingest import pubmed_resume_cli
from rag.ingest import clinical_trials_resume_cli
from rag.ingest import rxnorm_resume_cli
from rag.ingest import openfda_label_resume_cli
from rag.ingest import pdf_quality_audit
from rag.ingest import pdf_ocr_dryrun
from rag.ingest import pdf_ocr_plan
from rag.ingest import local_drug_coverage_audit
from rag.ingest import promote_local_drug_metadata
from rag.ingest.local_drug_cli import build_local_drug_items, clean_cell, row_to_items
from rag.config import EMBEDDING_MODEL
from rag.eval import retrieval_error_analysis
from rag.eval import runner as eval_runner
from rag.graph import kg_audit
from rag.graph.schema import GraphCandidate, GraphPath, GraphRetrievalResult
from rag.retrieval import hybrid
from rag.retrieval import drug_normalizer
from rag.retrieval import medical_graphrag_client
from rag.retrieval import local_index
from rag.retrieval.hybrid import hybrid_retrieve
from rag import service as rag_service
from rag.rerank.scorer import rerank_items
from rag.schema import EvidenceItem
from rag.sources import load_default_registry
from rag import store
from rag.store import evidence_to_fields


def _manual_tmp_dir(name: str) -> Path:
    path = Path(os.getcwd()) / ".rag-test-tmp" / f"{name}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _chunk(title, text, page=1, dept="心血管病学", section="诊断与评估"):
    return GuidelineChunk(
        doc_id=f"doc:{title}",
        chunk_id=f"chunk:{title}:{page}",
        title=title,
        department=dept,
        section_title=section,
        page_start=page,
        page_end=page,
        text=text,
        text_hash=f"hash:{title}:{page}",
        quality=[],
        year=2024,
    )


def test_manifest_sources_have_required_fields():
    registry = load_default_registry()
    assert registry.require("local_chinese_guidelines").source_type == "guideline"
    assert registry.require("ncbi_pubmed").authority_tier == "T2"
    assert registry.require("dailymed_spl").authority_tier == "T1"
    assert registry.require("local_diseasekg_json").source_type == "kg"
    assert registry.require("local_diseasekg_json").authority_tier == "T3"


def test_medical_graphrag_client_row_maps_to_evidence_item():
    row = {
        "chunk_id": "drug-1",
        "doc_id": "doc-1",
        "text": "禁忌：严重肾功能损害禁用。",
        "text_hash": "hash-1",
        "collection": "drug_label_v2",
        "source_tier": "T1",
        "source_name": "nmpa_cfda_local_snapshot",
        "title": "二甲双胍片",
        "section_title": "禁忌",
        "page_start": None,
        "page_end": None,
        "license": "local_official_snapshot_review_required",
        "locator": {"section": "禁忌"},
        "score": 0.87,
        "embedding_model": "text-embedding-v4",
        "indexed_at": "2026-05-08T00:00:00Z",
    }

    item = medical_graphrag_client._row_to_item(row)

    assert item.source_type == "drug_label"
    assert item.source_tier == "T1"
    assert item.metadata["collection_name"] == "drug_label_v2"
    assert item.scores["medical_graphrag"] == 0.87
    assert item.locator["section"] == "禁忌"


def test_medical_graphrag_client_requires_service_token(monkeypatch):
    monkeypatch.delenv("MEDICAL_GRAPHRAG_API_TOKEN", raising=False)

    async def run():
        return await medical_graphrag_client.search_medical_graphrag(
            "二甲双胍有哪些禁忌",
            intent="medication_safety",
        )

    try:
        asyncio.run(run())
    except medical_graphrag_client.MedicalGraphRagClientError as exc:
        assert "MEDICAL_GRAPHRAG_API_TOKEN" in str(exc)
    else:
        raise AssertionError("missing service token should raise MedicalGraphRagClientError")


def test_medical_graphrag_policy_flags_are_flattened(monkeypatch):
    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {
                "items": [
                    {
                        "chunk_id": "drug-flag",
                        "doc_id": "doc-flag",
                        "text": "禁忌：严重肾功能不全者禁用。",
                        "collection": "drug_label_v2",
                        "source_tier": "T1",
                        "source_type": "drug_label",
                        "title": "二甲双胍片",
                        "section_title": "禁忌",
                        "locator": {"section": "禁忌"},
                    }
                ],
                "policy_flags": {
                    "unsafe_to_answer": False,
                    "research_source_missing": True,
                    "preferred_collection_hit": True,
                },
                "debug": {"execution_time_ms": 12.0},
            }

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setenv("MEDICAL_GRAPHRAG_API_TOKEN", "test-token")
    monkeypatch.setattr(medical_graphrag_client.aiohttp, "ClientSession", FakeSession)

    items, debug = asyncio.run(
        medical_graphrag_client.search_medical_graphrag(
            "二甲双胍能抗衰老吗",
            intent="latest_research",
        )
    )

    assert items[0].chunk_id == "drug-flag"
    assert debug["policy_flags"]["research_source_missing"] is True
    assert debug["research_source_missing"] is True
    assert debug["unsafe_to_answer"] is False
    assert debug["preferred_collection_hit"] is True


def test_medical_graphrag_backend_switch_uses_remote_client(monkeypatch):
    calls = {}

    async def fake_remote(query, *, intent, top_k, filters=None, collection=None):
        calls["query"] = query
        calls["intent"] = intent
        calls["top_k"] = top_k
        return [
            EvidenceItem(
                chunk_id="remote-1",
                doc_id="doc-remote",
                text="远程 Milvus 证据",
                source_type="guideline",
                source_tier="T1",
                title="远程指南",
                locator={"page": 1},
            )
        ], {"backend": "medical_graphrag"}

    monkeypatch.setenv("RAG_BACKEND", "medical_graphrag")
    monkeypatch.setattr(rag_service, "search_medical_graphrag", fake_remote)

    result = asyncio.run(rag_service.retrieve_medical_evidence("高血压诊断标准", intent="guideline_qa", top_k=3))

    assert calls["intent"] == "guideline_qa"
    assert calls["top_k"] == 3
    assert result.items[0].chunk_id == "remote-1"
    assert result.debug["rag_backend"] == "medical_graphrag"
    registry = load_default_registry()
    assert registry.require("nhc_official_guidelines").authority_tier == "T1"
    assert registry.require("medlineplus_topics").source_type == "patient_education"
    assert registry.require("fda_faers_signal").source_type == "drug_safety_signal"
    assert registry.require("ada_standards").authority_tier == "T1"
    assert registry.require("gold_copd").source_type == "guideline"
    assert registry.require("gina_asthma").source_type == "guideline"
    assert registry.require("kdigo_guidelines").authority_tier == "T1"
    assert registry.require("cochrane_reviews").source_type == "literature"


def test_expanded_seed_files_cover_first_batch_targets():
    sources_dir = Path(BACKEND) / "rag" / "sources"
    research = yaml.safe_load((sources_dir / "research_seed.yaml").read_text(encoding="utf-8"))
    trials = yaml.safe_load((sources_dir / "trial_seed.yaml").read_text(encoding="utf-8"))
    drugs = yaml.safe_load((sources_dir / "drug_seed.yaml").read_text(encoding="utf-8"))

    assert len(research["queries"]) >= 30
    assert len(trials["queries"]) >= 20
    assert len(drugs["drugs"]) >= 50
    assert any(q["query_id"] == "metformin_anti_aging" for q in research["queries"])
    assert any(q["query_id"] == "car_t_solid_tumor" for q in trials["queries"])
    assert any(d["drug_id"] == "semaglutide" for d in drugs["drugs"])


def test_external_seed_entries_are_manifest_registered_or_manifest_only():
    entries = external_source_cli.load_seed_entries(Path(BACKEND) / "rag" / "sources" / "external_seed.yaml")

    assert len(entries) >= 25
    assert any(entry.source_id == "ada_standards" for entry in entries)
    assert any(entry.collection_key == "cancer_evidence" and "PDQ" in entry.title for entry in entries)

    for entry in entries:
        flags = external_source_cli.validate_entry(entry)
        blocking = [flag for flag in flags if flag != "manifest_only"]
        assert blocking == []


def test_external_seed_entries_are_manifest_backed():
    entries = external_source_cli.load_seed_entries(Path(BACKEND) / "rag" / "sources" / "external_seed.yaml")
    registry = load_default_registry()

    assert entries
    assert all(registry.get(entry.source_id) for entry in entries)
    assert any(entry.collection_key == "patient_education" for entry in entries)
    assert any(entry.collection_key == "drug_safety_signal" for entry in entries)
    assert any("manifest_only" in external_source_cli.validate_entry(entry) for entry in entries if entry.source_id == "primekg")


def test_external_html_seed_builds_evidence_items():
    tmp = _manual_tmp_dir("external-html")
    html_path = tmp / "source.html"
    html_path.write_text(
        "<html><body><h1>Screening recommendation</h1><p>Adults at increased risk should receive structured screening and follow-up according to the recommendation grade.</p><p>Benefits and harms should be discussed with patients before preventive interventions.</p></body></html>",
        encoding="utf-8",
    )
    entry = external_source_cli.ExternalSeedEntry(
        source_id="uspstf_recommendations",
        collection_key="guideline",
        title="USPSTF test recommendation",
        url="file://" + html_path.as_posix(),
        source_type="guideline",
        source_tier="T1",
        department="preventive_medicine",
        language="en",
        license="public_us_government_review_required",
        ingest_mode="structured_recommendation_html",
        topic_tags="screening,prevention",
        priority=1,
    )

    items, quarantine = external_source_cli.build_items_from_cached_file(entry, html_path)

    assert not quarantine
    assert items
    assert items[0].source_type == "guideline"
    assert items[0].source_tier == "T1"
    assert items[0].locator["url"].startswith("file://")
    assert items[0].metadata["source_id"] == "uspstf_recommendations"


def test_external_resume_state_skip_rules():
    entry = external_source_cli.ExternalSeedEntry(
        source_id="medlineplus_topics",
        collection_key="patient_education",
        title="MedlinePlus test",
        url="https://example.test/medline.xml",
        source_type="patient_education",
        source_tier="T3",
        license="nlm_terms",
        ingest_mode="topic_xml_summary",
    )
    state = {
        "status": "completed",
        "entry_hash": entry.entry_hash,
        "ingest_version": external_source_cli.INGEST_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "collection": entry.collection_name,
    }

    assert external_source_cli.matching_completed_state(
        state,
        entry_hash=entry.entry_hash,
        collection_name=entry.collection_name,
        retry_failed=False,
    ) == (True, "completed")
    assert external_source_cli.matching_completed_state(
        {**state, "status": "failed"},
        entry_hash=entry.entry_hash,
        collection_name=entry.collection_name,
        retry_failed=False,
    ) == (True, "failed_previous_run")


def test_quality_flags_catch_bad_chunks():
    flags = quality_flags("ååå 乱码 text", page_start=None, title="")
    assert "mojibake" in flags
    assert "missing_page" in flags
    assert "missing_title" in flags


def test_local_guideline_search_prefers_title_and_page(monkeypatch):
    chunks = [
        _chunk("中国高血压临床实践指南", "高血压的诊断标准包括诊室血压和家庭血压评估，治疗建议强调生活方式干预和降压药物。"),
        _chunk("国家心力衰竭指南2023", "急性心力衰竭患者需要识别高血压危象等诱因，并启动相应紧急治疗措施。"),
        _chunk("中国成人失眠诊断与治疗指南", "失眠的诊断需要评估睡眠时间、日间功能和精神心理因素。", dept="神经病学"),
    ]
    monkeypatch.setattr(local_index, "load_local_guideline_chunks", lambda _root=None: chunks)

    hits = local_index.search_local_guidelines("高血压诊断标准和治疗建议", intent="guideline_qa", top_k=3)

    assert hits
    assert hits[0].title == "中国高血压临床实践指南"
    assert hits[0].page_start == 1
    assert hits[0].locator["page"] == 1


def test_hybrid_retrieve_dedupes_and_returns_refs(monkeypatch):
    chunks = [
        _chunk("中国高血压临床实践指南", "高血压诊断标准和治疗建议需要结合血压水平、危险分层和靶器官损害。"),
        _chunk("中国高血压临床实践指南", "高血压诊断标准和治疗建议需要结合血压水平、危险分层和靶器官损害。"),
    ]
    monkeypatch.setattr(local_index, "load_local_guideline_chunks", lambda _root=None: chunks)
    async def no_dense(*args, **kwargs):
        return []

    monkeypatch.setattr(hybrid, "search_dashvector", no_dense)

    items, debug = asyncio.run(hybrid_retrieve("高血压诊断标准和治疗建议", intent="guideline_qa", top_k=5))

    assert len(items) == 1
    assert items[0].to_ref()["locator"]["page"] == 1
    assert debug["returned_count"] == 1


def test_graph_candidate_builds_kg_evidence_with_locator():
    path = GraphPath(
        anchor_entity="chest pain",
        anchor_label="Symptom",
        target_name="acute coronary syndrome",
        target_label="Disease",
        node_ids=["symptom:1", "disease:1"],
        node_names=["chest pain", "acute coronary syndrome"],
        relation_types=["HAS_SYMPTOM"],
        hop_count=1,
        score=0.8,
        source_tier="T3",
    )
    candidate = GraphCandidate(
        node_id="disease:1",
        node_label="Disease",
        node_name="acute coronary syndrome",
        score=0.8,
        anchor_entity="chest pain",
        paths=[path],
    )

    item = candidate.to_evidence_item()

    assert item.source_type == "kg"
    assert item.locator["neo4j_element_id"] == "disease:1"
    assert item.locator["path_signature"] == "chest pain -[HAS_SYMPTOM]- acute coronary syndrome"
    assert item.metadata["relation_types"] == "HAS_SYMPTOM"


def test_hybrid_retrieve_uses_graph_expansions_without_making_kg_authoritative(monkeypatch):
    guideline = EvidenceItem(
        chunk_id="g1",
        text="Chest pain with sweating needs emergency evaluation for acute coronary syndrome.",
        source_type="guideline",
        source_tier="T1",
        title="Chest pain guideline",
        page_start=9,
        doc_id="guideline:chest-pain",
        locator={"doc": "guideline:chest-pain", "page": 9},
        scores={"local_bm25": 0.3},
        metadata={"collection_key": "local_guideline_bm25"},
    )
    graph_item = EvidenceItem(
        chunk_id="kg:disease:acs",
        text="KG candidate: acute coronary syndrome. Path: chest pain -[HAS_SYMPTOM]- acute coronary syndrome.",
        source_type="kg",
        source_tier="T3",
        title="KG: acute coronary syndrome",
        doc_id="kg:disease:acs",
        locator={"neo4j_element_id": "disease:acs", "path_signature": "chest pain -[HAS_SYMPTOM]- acute coronary syndrome"},
        scores={"graph": 0.8},
        metadata={"collection_key": "neo4j_graph", "node_name": "acute coronary syndrome"},
    )
    seen_queries = []

    def local_search(query, *args, **kwargs):
        seen_queries.append(query)
        return [guideline]

    async def graph_search(*args, **kwargs):
        return GraphRetrievalResult(
            query=args[0],
            candidates=[],
            paths=[],
            entity_expansions=["acute coronary syndrome", "emergency"],
            context_text="graph context",
            refs=[graph_item.to_ref()],
            items=[graph_item],
            debug={"graph_available": True},
        )

    async def no_dense(*args, **kwargs):
        return []

    monkeypatch.setattr(hybrid, "retrieve_graph_evidence", graph_search)
    monkeypatch.setattr(hybrid, "search_local_guidelines", local_search)
    monkeypatch.setattr(hybrid, "local_guideline_quality_stats", lambda: {"total": 1, "quarantine_filtered": 0})
    monkeypatch.setattr(hybrid, "search_dashvector", no_dense)

    items, debug = asyncio.run(
        hybrid_retrieve("chest pain sweating", intent="symptom_dx", top_k=5, filters={"enable_graph": True})
    )

    assert seen_queries
    assert "acute coronary syndrome" in seen_queries[0]
    assert debug["recall"]["neo4j_graph"] == 1
    assert debug["authority_evidence_closure"] is True
    assert any(item.source_type == "kg" for item in items)
    assert any(item.source_type == "guideline" for item in items)


def test_medication_safety_graph_only_remains_unsafe(monkeypatch):
    graph_item = EvidenceItem(
        chunk_id="kg:drug-risk",
        text="KG candidate: bleeding risk. Path: aspirin -[INTERACTS_WITH]- warfarin.",
        source_type="kg",
        source_tier="T3",
        title="KG: bleeding risk",
        doc_id="kg:drug-risk",
        locator={"neo4j_element_id": "risk:bleeding", "path_signature": "aspirin -[INTERACTS_WITH]- warfarin"},
        scores={"graph": 0.9},
        metadata={"collection_key": "neo4j_graph"},
    )

    async def graph_search(*args, **kwargs):
        return GraphRetrievalResult(
            query=args[0],
            candidates=[],
            paths=[],
            entity_expansions=["bleeding risk"],
            context_text="graph context",
            refs=[graph_item.to_ref()],
            items=[graph_item],
            debug={"graph_available": True},
        )

    async def no_dense(*args, **kwargs):
        return []

    monkeypatch.setattr(hybrid, "retrieve_graph_evidence", graph_search)
    monkeypatch.setattr(hybrid, "search_local_guidelines", lambda *args, **kwargs: [])
    monkeypatch.setattr(hybrid, "local_guideline_quality_stats", lambda: {"total": 0, "quarantine_filtered": 0})
    monkeypatch.setattr(hybrid, "search_dashvector", no_dense)

    items, debug = asyncio.run(
        hybrid_retrieve("aspirin warfarin interaction", intent="medication_safety", top_k=5, filters={"enable_graph": True})
    )

    assert items and items[0].source_type == "kg"
    assert debug["preferred_source_type_hit"] is False
    assert debug["unsafe_to_answer"] is True
    assert debug["kg_only_result"] is True


def test_graph_retrieval_is_not_enabled_for_rumor_check(monkeypatch):
    async def graph_search(*args, **kwargs):
        raise AssertionError("GraphRAG should be limited to symptom_dx and medication_safety")

    async def no_dense(*args, **kwargs):
        return []

    monkeypatch.setattr(hybrid, "retrieve_graph_evidence", graph_search)
    monkeypatch.setattr(hybrid, "search_local_guidelines", lambda *args, **kwargs: [])
    monkeypatch.setattr(hybrid, "local_guideline_quality_stats", lambda: {"total": 0, "quarantine_filtered": 0})
    monkeypatch.setattr(hybrid, "search_dashvector", no_dense)

    items, debug = asyncio.run(
        hybrid_retrieve("supplement cures cancer", intent="rumor_check", top_k=5, filters={"enable_graph": True})
    )

    assert items == []
    assert debug["graph"]["reason"] == "intent_not_graph_enabled"


def test_kg_audit_reports_not_configured(monkeypatch):
    monkeypatch.setattr(kg_audit, "_graph_configured", lambda: False)

    report = kg_audit.audit_kg()

    assert report["status"] == "neo4j_not_configured"


def test_kg_audit_detects_missing_provenance(monkeypatch):
    class FakeResult:
        def __init__(self, rows):
            self.rows = rows

        def single(self):
            return self.rows[0] if self.rows else None

        def __iter__(self):
            return iter(self.rows)

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def run(self, cypher, **params):
            if "MATCH (n) RETURN count(n)" in cypher:
                return FakeResult([[2]])
            if "MATCH ()-[r]->() RETURN count(r)" in cypher:
                return FakeResult([[1]])
            if "UNWIND labels" in cypher:
                return FakeResult([("Disease", 1), ("Drug", 1)])
            if "RETURN type(r), count" in cypher:
                return FakeResult([("TREATS", 1)])
            if "SHOW INDEXES" in cypher:
                return FakeResult([
                    {"name": "disease_embedding", "type": "VECTOR", "entityType": "NODE", "labelsOrTypes": ["Disease"], "properties": ["embedding"]},
                    {"name": "medical_entity_fulltext", "type": "FULLTEXT", "entityType": "NODE", "labelsOrTypes": ["Disease"], "properties": ["name"]},
                ])
            if "source_tier IS NULL" in cypher:
                return FakeResult([[1]])
            if "IS NULL" in cypher:
                return FakeResult([[0]])
            return FakeResult([])

    class FakeDriver:
        def session(self):
            return FakeSession()

        def close(self):
            pass

    monkeypatch.setattr(kg_audit, "_graph_configured", lambda: True)
    monkeypatch.setattr(kg_audit, "_connect_driver", lambda: FakeDriver())
    monkeypatch.setattr(kg_audit, "_smoke_tests", lambda: [{"graph_available": True, "locator_valid": True}])

    report = kg_audit.audit_kg()

    assert report["status"] == "schema_incomplete"
    assert report["missing_node_properties"]["source_tier"]["missing"] == 1
    assert report["missing_relationship_properties"]["source_tier"]["missing"] == 1


def test_guideline_ingest_maps_required_fields():
    chunk = _chunk(
        "Hypertension clinical practice guideline",
        "Hypertension diagnosis and treatment recommendations require blood pressure assessment, risk stratification, and follow-up management.",
        page=3,
        dept="cardiology",
        section="diagnosis",
    )

    fields = evidence_to_fields(_chunk_to_evidence(chunk), indexed_at="2026-01-01T00:00:00+00:00")

    for key in {
        "doc_id",
        "chunk_id",
        "title",
        "department",
        "section_title",
        "page_start",
        "page_end",
        "text",
        "source_tier",
        "text_hash",
        "embedding_model",
        "indexed_at",
    }:
        assert key in fields
    assert fields["page_start"] == 3
    assert fields["embedding_model"] == EMBEDDING_MODEL


def test_pdf_contextual_chunk_metadata_maps_to_fields():
    chunk = _chunk(
        "Diabetes guideline",
        "Recommendation: metformin treatment should consider renal function and contraindications.",
        page=8,
        dept="endocrinology",
        section="治疗与管理",
    )
    chunk.parent_id = "parent-1"
    chunk.section_path = ["治疗与管理", "降糖药物"]
    chunk.block_type = "recommendation"
    chunk.embedding_text = "[文档] Diabetes guideline\n[章节] 治疗与管理 > 降糖药物\n[正文]\nRecommendation..."
    chunk.extraction_method = "pymupdf"
    chunk.layout_type = "paragraph"
    chunk.sibling_prev = "prev"
    chunk.sibling_next = "next"

    fields = evidence_to_fields(_chunk_to_evidence(chunk), indexed_at="2026-01-01T00:00:00+00:00")

    assert fields["parent_id"] == "parent-1"
    assert fields["section_path"] == "治疗与管理 > 降糖药物"
    assert fields["block_type"] == "recommendation"
    assert fields["embedding_text"].startswith("[文档]")
    assert fields["sibling_prev"] == "prev"
    assert fields["sibling_next"] == "next"


def test_pdf_ocr_metadata_maps_to_fields():
    chunk = _chunk("Scanned guideline", "OCR recovered recommendation text.", page=4, dept="cardiology")
    chunk.extraction_method = "paddleocr"
    chunk.ocr_confidence = 0.91

    fields = evidence_to_fields(_chunk_to_evidence(chunk), indexed_at="2026-01-01T00:00:00+00:00")

    assert fields["extraction_method"] == "paddleocr"
    assert fields["ocr_engine"] == "PaddleOCR"
    assert fields["ocr_confidence"] == 0.91


def test_pdf_block_type_classification_prioritizes_medical_blocks():
    assert classify_block_type("推荐意见：建议成人高血压患者进行危险分层。") == "recommendation"
    assert classify_block_type("诊断标准包括多次测量血压并结合家庭血压评估。") == "diagnostic_criteria"
    assert classify_block_type("禁忌：严重肾功能不全患者慎用。") == "medication_safety"


def test_pdf_utf8_section_and_embedding_labels_are_not_mojibake():
    chunk = _chunk(
        "中文指南",
        "推荐意见：建议结合临床表现、检查结果和风险分层制定治疗方案。",
        page=2,
        dept="心血管病学",
        section="治疗与管理",
    )
    chunk.section_path = ["治疗与管理", "降压药物"]
    chunk.embedding_text = "[文档] 中文指南\n[科室] 心血管病学\n[章节] 治疗与管理 > 降压药物\n[正文]\n推荐意见"

    fields = evidence_to_fields(_chunk_to_evidence(chunk), indexed_at="2026-01-01T00:00:00+00:00")

    assert fields["section_path"] == "治疗与管理 > 降压药物"
    assert fields["embedding_text"].startswith("[文档]")
    assert "锟" not in fields["embedding_text"]


def test_pdf_resume_completed_state_skip_rules():
    state = {
        "status": "completed",
        "doc_hash": "hash1",
        "chunking_version": "chunk-v1",
        "embedding_model": EMBEDDING_MODEL,
        "collection": "medical_guideline_v2",
    }

    assert pdf_resume_cli.matching_completed_state(
        state,
        doc_hash="hash1",
        chunking_version="chunk-v1",
        collection="medical_guideline_v2",
        retry_failed=False,
    ) == (True, "completed")
    assert pdf_resume_cli.matching_completed_state(
        state,
        doc_hash="hash2",
        chunking_version="chunk-v1",
        collection="medical_guideline_v2",
        retry_failed=False,
    ) == (False, "changed")


def test_pdf_resume_chunking_version_tracks_ocr_fallback():
    base = pdf_resume_cli.ChunkingConfig()
    ocr = pdf_resume_cli.ChunkingConfig(enable_ocr_fallback=True)

    assert base.version != ocr.version
    assert "ocr=0" in base.version
    assert "ocr=1" in ocr.version


def test_pdf_resume_failed_state_requires_retry_failed():
    state = {
        "status": "failed",
        "doc_hash": "hash1",
        "chunking_version": "chunk-v1",
        "embedding_model": EMBEDDING_MODEL,
        "collection": "medical_guideline_v2",
    }

    assert pdf_resume_cli.matching_completed_state(
        state,
        doc_hash="hash1",
        chunking_version="chunk-v1",
        collection="medical_guideline_v2",
        retry_failed=False,
    ) == (True, "failed_previous_run")
    assert pdf_resume_cli.matching_completed_state(
        state,
        doc_hash="hash1",
        chunking_version="chunk-v1",
        collection="medical_guideline_v2",
        retry_failed=True,
    ) == (False, "status_failed")


def test_pdf_resume_dry_run_writes_report_but_not_checkpoint(monkeypatch):
    tmp_root = _manual_tmp_dir("pdf-resume-dry-run")
    try:
        pdf_root = tmp_root / "pdfs"
        pdf_root.mkdir()
        pdf_path = pdf_root / "sample.pdf"
        pdf_path.write_bytes(b"%PDF fake")
        state_file = tmp_root / "state.jsonl"
        run_report = tmp_root / "run.jsonl"

        monkeypatch.setattr(
            pdf_resume_cli,
            "build_guideline_chunks",
            lambda *args, **kwargs: [
                _chunk("Sample guideline", "Recommendation: treatment should be individualized.", page=1, dept="cardiology")
            ],
        )
        monkeypatch.setattr(pdf_resume_cli, "upsert_evidence_items", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dry-run must not upsert")))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "pdf_resume_cli",
                "--pdf-root",
                str(pdf_root),
                "--state-file",
                str(state_file),
                "--run-report",
                str(run_report),
                "--dry-run",
            ],
        )

        assert pdf_resume_cli.main() == 0
        assert not state_file.exists()
        rows = [json.loads(line) for line in run_report.read_text(encoding="utf-8").splitlines()]
        assert rows[0]["status"] == "dry_run_completed"
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_pdf_resume_second_run_skips_completed_pdf(monkeypatch):
    tmp_root = _manual_tmp_dir("pdf-resume-skip")
    try:
        pdf_root = tmp_root / "pdfs"
        pdf_root.mkdir()
        pdf_path = pdf_root / "sample.pdf"
        pdf_path.write_bytes(b"%PDF fake")
        state_file = tmp_root / "state.jsonl"
        first_report = tmp_root / "first.jsonl"
        second_report = tmp_root / "second.jsonl"
        chunk = _chunk("Sample guideline", "Recommendation: treatment should be individualized.", page=1, dept="cardiology")

        monkeypatch.setattr(pdf_resume_cli, "build_guideline_chunks", lambda *args, **kwargs: [chunk])
        monkeypatch.setattr(
            pdf_resume_cli,
            "upsert_evidence_items",
            lambda items, **kwargs: {"inserted": len(list(items)), "failed": 0, "collection": kwargs["collection_name"]},
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "pdf_resume_cli",
                "--pdf-root",
                str(pdf_root),
                "--state-file",
                str(state_file),
                "--run-report",
                str(first_report),
            ],
        )

        assert pdf_resume_cli.main() == 0
        assert state_file.exists()

        monkeypatch.setattr(pdf_resume_cli, "build_guideline_chunks", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("completed pdf should skip")))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "pdf_resume_cli",
                "--pdf-root",
                str(pdf_root),
                "--state-file",
                str(state_file),
                "--run-report",
                str(second_report),
            ],
        )

        assert pdf_resume_cli.main() == 0
        rows = [json.loads(line) for line in second_report.read_text(encoding="utf-8").splitlines()]
        assert rows[0]["status"] == "skipped"
        assert rows[0]["error"] == "completed"
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _drug_row(name="Metformin", approval_no="H1", producer="Test producer"):
    row = {}
    for column in local_drug_cli.IDENTITY_COLUMNS.values():
        row[column] = ""
    for column in local_drug_cli.BODY_COLUMNS:
        row[column] = ""
    row[local_drug_cli.IDENTITY_COLUMNS["drug_name"]] = name
    row[local_drug_cli.IDENTITY_COLUMNS["brand_name"]] = "Brand"
    row[local_drug_cli.IDENTITY_COLUMNS["approval_no"]] = approval_no
    row[local_drug_cli.IDENTITY_COLUMNS["drug_class"]] = "chemical"
    row[local_drug_cli.IDENTITY_COLUMNS["producer"]] = producer
    row[local_drug_cli.IDENTITY_COLUMNS["related_diseases"]] = "diabetes"
    row[local_drug_cli.IDENTITY_COLUMNS["source_url"]] = "https://example.test/drug"
    for _, column, _ in local_drug_cli.SECTION_COLUMNS:
        row[column] = f"{column} section text for {name}. This content is long enough for cleaning and ingestion."
    return row


def test_local_drug_resume_completed_state_skip_rules():
    state = {
        "status": "completed",
        "row_hash": "row-hash-1",
        "section_schema_version": local_drug_resume_cli.SECTION_SCHEMA_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "collection": "drug_label_v2",
    }

    assert local_drug_resume_cli.matching_completed_state(
        state,
        row_hash_value="row-hash-1",
        collection="drug_label_v2",
        retry_failed=False,
    ) == (True, "completed")
    assert local_drug_resume_cli.matching_completed_state(
        state,
        row_hash_value="row-hash-2",
        collection="drug_label_v2",
        retry_failed=False,
    ) == (False, "changed")


def test_local_drug_resume_failed_state_requires_retry_failed():
    state = {
        "status": "failed",
        "row_hash": "row-hash-1",
        "section_schema_version": local_drug_resume_cli.SECTION_SCHEMA_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "collection": "drug_label_v2",
    }

    assert local_drug_resume_cli.matching_completed_state(
        state,
        row_hash_value="row-hash-1",
        collection="drug_label_v2",
        retry_failed=False,
    ) == (True, "failed_previous_run")
    assert local_drug_resume_cli.matching_completed_state(
        state,
        row_hash_value="row-hash-1",
        collection="drug_label_v2",
        retry_failed=True,
    ) == (False, "status_failed")


def test_local_drug_resume_dry_run_writes_report_but_not_checkpoint(monkeypatch):
    tmp_root = _manual_tmp_dir("local-drug-resume-dry-run")
    try:
        drug_root = tmp_root / "drug_data"
        drug_root.mkdir()
        excel_path = drug_root / "drug.xlsx"
        excel_path.write_bytes(b"fake")
        state_file = tmp_root / "state.jsonl"
        run_report = tmp_root / "run.jsonl"
        rows = [(excel_path, 2, _drug_row())]

        monkeypatch.setattr(local_drug_resume_cli.local_drug_cli, "iter_excel_rows", lambda *args, **kwargs: rows)
        monkeypatch.setattr(local_drug_resume_cli, "upsert_evidence_items", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dry-run must not upsert")))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "local_drug_resume_cli",
                "--drug-root",
                str(drug_root),
                "--state-file",
                str(state_file),
                "--run-report",
                str(run_report),
                "--dry-run",
            ],
        )

        assert local_drug_resume_cli.main() == 0
        assert not state_file.exists()
        rows_out = [json.loads(line) for line in run_report.read_text(encoding="utf-8").splitlines()]
        assert rows_out[0]["status"] == "dry_run_completed"
        assert rows_out[0]["accepted_chunks"] == len(local_drug_cli.SECTION_COLUMNS)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_local_drug_resume_second_run_skips_completed_row(monkeypatch):
    tmp_root = _manual_tmp_dir("local-drug-resume-skip")
    try:
        drug_root = tmp_root / "drug_data"
        drug_root.mkdir()
        excel_path = drug_root / "drug.xlsx"
        excel_path.write_bytes(b"fake")
        state_file = tmp_root / "state.jsonl"
        first_report = tmp_root / "first.jsonl"
        second_report = tmp_root / "second.jsonl"
        rows = [(excel_path, 2, _drug_row())]

        monkeypatch.setattr(local_drug_resume_cli.local_drug_cli, "iter_excel_rows", lambda *args, **kwargs: rows)
        monkeypatch.setattr(
            local_drug_resume_cli,
            "upsert_evidence_items",
            lambda items, **kwargs: {"inserted": len(list(items)), "failed": 0, "collection": kwargs["collection_name"]},
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "local_drug_resume_cli",
                "--drug-root",
                str(drug_root),
                "--state-file",
                str(state_file),
                "--run-report",
                str(first_report),
            ],
        )

        assert local_drug_resume_cli.main() == 0
        assert state_file.exists()

        monkeypatch.setattr(local_drug_resume_cli.local_drug_cli, "row_to_items", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("completed row should skip")))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "local_drug_resume_cli",
                "--drug-root",
                str(drug_root),
                "--state-file",
                str(state_file),
                "--run-report",
                str(second_report),
            ],
        )

        assert local_drug_resume_cli.main() == 0
        rows_out = [json.loads(line) for line in second_report.read_text(encoding="utf-8").splitlines()]
        assert rows_out[0]["status"] == "skipped"
        assert rows_out[0]["error"] == "completed"
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_store_upsert_batches_embedding_requests(monkeypatch):
    items = [
        EvidenceItem(
            chunk_id=f"drug_chunk_{idx}",
            text=f"section text {idx}",
            source_type="drug_label",
            source_tier="T3",
            title="Drug",
            section_title="禁忌",
            doc_id="drug_label:H1:test",
            metadata={},
        )
        for idx in range(7)
    ]
    embedding_batches = []
    upsert_batches = []

    class FakeCollection:
        def upsert(self, docs):
            upsert_batches.append(len(docs))
            return type("Resp", (), {"code": 0})()

    monkeypatch.setattr(store, "ensure_collection", lambda *args, **kwargs: FakeCollection())

    def fake_embed_texts(texts, **kwargs):
        embedding_batches.append(len(texts))
        return [[0.01] * store.EMBEDDING_DIM for _ in texts]

    monkeypatch.setattr(store, "embed_texts", fake_embed_texts)

    result = store.upsert_evidence_items(items, collection_name="drug_label_v2", batch_size=3)

    assert result["inserted"] == 7
    assert result["failed"] == 0
    assert embedding_batches == [3, 3, 1]
    assert upsert_batches == [3, 3, 1]


def test_local_drug_resume_batches_rows_before_checkpoint(monkeypatch):
    tmp_root = _manual_tmp_dir("local-drug-resume-row-batch")
    try:
        drug_root = tmp_root / "drug_data"
        drug_root.mkdir()
        excel_path = drug_root / "drug.xlsx"
        excel_path.write_bytes(b"fake")
        state_file = tmp_root / "state.jsonl"
        run_report = tmp_root / "run.jsonl"
        rows = [
            (excel_path, 2, _drug_row("Drug A", "H1", "Producer A")),
            (excel_path, 3, _drug_row("Drug B", "H2", "Producer B")),
            (excel_path, 4, _drug_row("Drug C", "H3", "Producer C")),
        ]
        upsert_sizes = []

        monkeypatch.setattr(local_drug_resume_cli.local_drug_cli, "iter_excel_rows", lambda *args, **kwargs: rows)

        def fake_upsert(items, **kwargs):
            materialized = list(items)
            upsert_sizes.append(len(materialized))
            return {"inserted": len(materialized), "failed": 0, "collection": kwargs["collection_name"]}

        monkeypatch.setattr(local_drug_resume_cli, "upsert_evidence_items", fake_upsert)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "local_drug_resume_cli",
                "--drug-root",
                str(drug_root),
                "--state-file",
                str(state_file),
                "--run-report",
                str(run_report),
                "--row-batch-size",
                "2",
                "--batch-size",
                "64",
            ],
        )

        assert local_drug_resume_cli.main() == 0
        rows_out = [json.loads(line) for line in run_report.read_text(encoding="utf-8").splitlines()]
        state_rows = [json.loads(line) for line in state_file.read_text(encoding="utf-8").splitlines()]
        assert upsert_sizes == [len(local_drug_cli.SECTION_COLUMNS) * 2, len(local_drug_cli.SECTION_COLUMNS)]
        assert [row["status"] for row in rows_out] == ["completed", "completed", "completed"]
        assert [row["status"] for row in state_rows] == ["completed", "completed", "completed"]
        assert all(row["inserted_chunks"] == len(local_drug_cli.SECTION_COLUMNS) for row in rows_out)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_local_drug_resume_marks_batch_failure_per_row(monkeypatch):
    tmp_root = _manual_tmp_dir("local-drug-resume-row-batch-fail")
    try:
        drug_root = tmp_root / "drug_data"
        drug_root.mkdir()
        excel_path = drug_root / "drug.xlsx"
        excel_path.write_bytes(b"fake")
        state_file = tmp_root / "state.jsonl"
        run_report = tmp_root / "run.jsonl"
        rows = [(excel_path, 2, _drug_row("Drug A", "H1", "Producer A"))]

        monkeypatch.setattr(local_drug_resume_cli.local_drug_cli, "iter_excel_rows", lambda *args, **kwargs: rows)
        monkeypatch.setattr(
            local_drug_resume_cli,
            "upsert_evidence_items",
            lambda items, **kwargs: {"inserted": 0, "failed": len(list(items)), "collection": kwargs["collection_name"]},
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "local_drug_resume_cli",
                "--drug-root",
                str(drug_root),
                "--state-file",
                str(state_file),
                "--run-report",
                str(run_report),
                "--row-batch-size",
                "10",
            ],
        )

        assert local_drug_resume_cli.main() == 1
        rows_out = [json.loads(line) for line in run_report.read_text(encoding="utf-8").splitlines()]
        assert rows_out[0]["status"] == "failed"
        assert rows_out[0]["failed_chunks"] == len(local_drug_cli.SECTION_COLUMNS)
        assert "batch_upsert_failed" in rows_out[0]["error"]
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_local_drug_resume_falls_back_to_row_upsert_after_batch_failure(monkeypatch):
    tmp_root = _manual_tmp_dir("local-drug-resume-row-batch-fallback")
    try:
        drug_root = tmp_root / "drug_data"
        drug_root.mkdir()
        excel_path = drug_root / "drug.xlsx"
        excel_path.write_bytes(b"fake")
        state_file = tmp_root / "state.jsonl"
        run_report = tmp_root / "run.jsonl"
        rows = [
            (excel_path, 2, _drug_row("Drug A", "H1", "Producer A")),
            (excel_path, 3, _drug_row("Drug B", "H2", "Producer B")),
        ]
        calls = []

        monkeypatch.setattr(local_drug_resume_cli.local_drug_cli, "iter_excel_rows", lambda *args, **kwargs: rows)

        def fake_upsert(items, **kwargs):
            materialized = list(items)
            calls.append(len(materialized))
            if len(materialized) > len(local_drug_cli.SECTION_COLUMNS):
                return {"inserted": 3, "failed": len(materialized) - 3, "collection": kwargs["collection_name"]}
            return {"inserted": len(materialized), "failed": 0, "collection": kwargs["collection_name"]}

        monkeypatch.setattr(local_drug_resume_cli, "upsert_evidence_items", fake_upsert)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "local_drug_resume_cli",
                "--drug-root",
                str(drug_root),
                "--state-file",
                str(state_file),
                "--run-report",
                str(run_report),
                "--row-batch-size",
                "2",
            ],
        )

        assert local_drug_resume_cli.main() == 0
        rows_out = [json.loads(line) for line in run_report.read_text(encoding="utf-8").splitlines()]
        assert calls == [len(local_drug_cli.SECTION_COLUMNS) * 2, len(local_drug_cli.SECTION_COLUMNS), len(local_drug_cli.SECTION_COLUMNS)]
        assert [row["status"] for row in rows_out] == ["completed", "completed"]
        assert all(row["error"] == "" for row in rows_out)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_local_drug_resume_failed_only_skips_unfailed_rows(monkeypatch):
    tmp_root = _manual_tmp_dir("local-drug-resume-failed-only")
    try:
        drug_root = tmp_root / "drug_data"
        drug_root.mkdir()
        excel_path = drug_root / "drug.xlsx"
        excel_path.write_bytes(b"fake")
        state_file = tmp_root / "state.jsonl"
        run_report = tmp_root / "run.jsonl"
        completed_row = _drug_row("Drug A", "H1", "Producer A")
        failed_row = _drug_row("Drug B", "H2", "Producer B")
        rows = [(excel_path, 2, completed_row), (excel_path, 3, failed_row)]
        state_rows = [
            local_drug_resume_cli.build_status_row(
                ingest_run_id="old",
                collection="drug_label_v2",
                file_rel_path="drug.xlsx",
                file_abs_path=excel_path,
                row_no=2,
                dedupe_key=local_drug_cli.row_key(completed_row),
                row_hash_value=local_drug_resume_cli.row_hash(completed_row),
                status="completed",
                started_at="2026-01-01T00:00:00+00:00",
                identity=local_drug_resume_cli.row_identity_fields(completed_row),
            ),
            local_drug_resume_cli.build_status_row(
                ingest_run_id="old",
                collection="drug_label_v2",
                file_rel_path="drug.xlsx",
                file_abs_path=excel_path,
                row_no=3,
                dedupe_key=local_drug_cli.row_key(failed_row),
                row_hash_value=local_drug_resume_cli.row_hash(failed_row),
                status="failed",
                started_at="2026-01-01T00:00:00+00:00",
                identity=local_drug_resume_cli.row_identity_fields(failed_row),
            ),
        ]
        state_file.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in state_rows), encoding="utf-8")
        monkeypatch.setattr(local_drug_resume_cli.local_drug_cli, "iter_excel_rows", lambda *args, **kwargs: rows)
        monkeypatch.setattr(
            local_drug_resume_cli,
            "upsert_evidence_items",
            lambda items, **kwargs: {"inserted": len(list(items)), "failed": 0, "collection": kwargs["collection_name"]},
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "local_drug_resume_cli",
                "--drug-root",
                str(drug_root),
                "--state-file",
                str(state_file),
                "--run-report",
                str(run_report),
                "--failed-only",
                "--retry-failed",
            ],
        )

        assert local_drug_resume_cli.main() == 0
        rows_out = [json.loads(line) for line in run_report.read_text(encoding="utf-8").splitlines()]
        assert len(rows_out) == 1
        assert rows_out[0]["drug_name"] == "Drug B"
        assert rows_out[0]["status"] == "completed"
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_local_drug_coverage_audit_counts_completed_sections(monkeypatch):
    tmp_root = _manual_tmp_dir("local-drug-coverage-audit")
    try:
        drug_root = tmp_root / "drug_data"
        drug_root.mkdir()
        excel_path = drug_root / "drug.xlsx"
        excel_path.write_bytes(b"fake")
        row = _drug_row("Drug A", "H1", "Producer A")
        key = local_drug_cli.row_key(row)
        state_file = tmp_root / "state.jsonl"
        state_row = local_drug_resume_cli.build_status_row(
            ingest_run_id="run",
            collection="drug_label_v2",
            file_rel_path="drug.xlsx",
            file_abs_path=excel_path,
            row_no=2,
            dedupe_key=key,
            row_hash_value=local_drug_resume_cli.row_hash(row),
            status="completed",
            started_at="2026-01-01T00:00:00+00:00",
            identity=local_drug_resume_cli.row_identity_fields(row),
            doc_id="drug_label:H1:test",
            accepted_chunks=len(local_drug_cli.SECTION_COLUMNS),
            inserted_chunks=len(local_drug_cli.SECTION_COLUMNS),
        )
        state_file.write_text(json.dumps(state_row, ensure_ascii=False) + "\n", encoding="utf-8")
        monkeypatch.setattr(local_drug_coverage_audit.local_drug_cli, "iter_excel_rows", lambda *args, **kwargs: [(excel_path, 2, row)])

        report = local_drug_coverage_audit.audit_local_drug_coverage(drug_root, state_file)

        assert report["completed_unique_rows"] == 1
        assert report["remaining_unique_rows"] == 0
        assert report["section_coverage"]["contraindications"]["rate"] == 1.0
        assert report["safety_section_coverage"]["drug_interactions"]["rate"] == 1.0
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_promote_local_drug_metadata_dry_run_does_not_update(monkeypatch):
    tmp_root = _manual_tmp_dir("promote-local-drug-dry-run")
    try:
        state_file = tmp_root / "state.jsonl"
        run_report = tmp_root / "report.jsonl"
        state_file.write_text(
            "\n".join([
                json.dumps({"status": "completed", "chunk_ids": ["drug_a", "drug_b"]}, ensure_ascii=False),
                json.dumps({"status": "failed", "chunk_ids": ["drug_failed"]}, ensure_ascii=False),
            ]),
            encoding="utf-8",
        )
        updated_docs = []

        class FakeResp:
            code = 0

            def __init__(self, output=None):
                self.output = output

            def __bool__(self):
                return True

        class FakeCollection:
            def fetch(self, ids):
                return FakeResp({
                    "drug_a": promote_local_drug_metadata.Doc(
                        id="drug_a",
                        fields={"source_type": "drug_label", "source_name": "yaozs_xlsx", "source_tier": "T3"},
                    ),
                    "drug_b": promote_local_drug_metadata.Doc(
                        id="drug_b",
                        fields={"source_type": "drug_label", "source_name": "openFDA/DailyMed", "source_tier": "T1"},
                    ),
                })

            def update(self, docs):
                updated_docs.extend(docs if isinstance(docs, list) else [docs])
                return FakeResp()

        monkeypatch.setattr(promote_local_drug_metadata, "ensure_collection", lambda *args, **kwargs: FakeCollection())
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promote_local_drug_metadata",
                "--state-file",
                str(state_file),
                "--run-report",
                str(run_report),
                "--dry-run",
            ],
        )

        assert promote_local_drug_metadata.main() == 0
        rows = [json.loads(line) for line in run_report.read_text(encoding="utf-8").splitlines()]
        assert [row["status"] for row in rows] == ["would_update", "skip_source_name"]
        assert updated_docs == []
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_promote_local_drug_metadata_updates_only_yaozs_source(monkeypatch):
    tmp_root = _manual_tmp_dir("promote-local-drug-update")
    try:
        state_file = tmp_root / "state.jsonl"
        run_report = tmp_root / "report.jsonl"
        state_file.write_text(
            json.dumps({"status": "completed", "chunk_ids": ["drug_a", "drug_b"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        updated_docs = []

        class FakeResp:
            code = 0

            def __init__(self, output=None):
                self.output = output

            def __bool__(self):
                return True

        class FakeCollection:
            def fetch(self, ids):
                return FakeResp([
                    promote_local_drug_metadata.Doc(
                        id="drug_a",
                        fields={
                            "source_type": "drug_label",
                            "source_name": "yaozs_xlsx",
                            "source_tier": "T3",
                            "license": "local_review_required",
                            "text": "old text",
                        },
                    ),
                    promote_local_drug_metadata.Doc(
                        id="drug_b",
                        fields={"source_type": "drug_label", "source_name": "openFDA/DailyMed", "source_tier": "T1"},
                    ),
                ])

            def update(self, docs):
                updated_docs.extend(docs if isinstance(docs, list) else [docs])
                return FakeResp()

        monkeypatch.setattr(promote_local_drug_metadata, "ensure_collection", lambda *args, **kwargs: FakeCollection())
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promote_local_drug_metadata",
                "--state-file",
                str(state_file),
                "--run-report",
                str(run_report),
            ],
        )

        assert promote_local_drug_metadata.main() == 0
        rows = [json.loads(line) for line in run_report.read_text(encoding="utf-8").splitlines()]
        assert sorted(row["status"] for row in rows) == ["skip_source_name", "updated"]
        assert len(updated_docs) == 1
        fields = updated_docs[0].fields
        assert updated_docs[0].id == "drug_a"
        assert fields["source_name"] == "nmpa_cfda_local_snapshot"
        assert fields["source_tier"] == "T1"
        assert fields["license"] == "local_official_snapshot_review_required"
        assert fields["evidence_level"] == "official_drug_label_local_snapshot"
        assert fields["official_source_assumption"] is True
        assert fields["source_verified_online"] is False
        assert fields["text"] == "old text"
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_drug_normalizer_expands_local_and_common_aliases():
    tmp_root = _manual_tmp_dir("drug-normalizer")
    try:
        state_file = tmp_root / "state.jsonl"
        state_file.write_text(
            json.dumps({"status": "completed", "drug_name": "阿司匹林肠溶片"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        drug_normalizer.load_local_drug_aliases.cache_clear()

        expanded, aliases = drug_normalizer.expand_drug_query(
            "拜阿司匹灵和华法林一起吃有什么风险",
            state_file=str(state_file),
        )

        assert "阿司匹林" in aliases
        assert "warfarin" in aliases
        assert "阿司匹林" in expanded
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_retrieval_error_analysis_classifies_missing_preferred_source():
    class Result:
        intent = "medication_safety"
        items = []
        debug = {
            "collection_hits": {"guideline": 1},
            "ranked_candidates_top20": [{"chunk_id": "g1", "source_type": "guideline"}],
            "selected_top": [{"chunk_id": "g1", "source_type": "guideline"}],
            "quota_dropped": [],
        }

    case = {"preferred_source_type": "drug_label", "must_match": ["阿司匹林"]}

    assert retrieval_error_analysis.classify_failure(case, Result()) == "preferred_source_missing"


def test_retrieval_error_analysis_reuses_eval_aliases_for_english_evidence():
    class Result:
        intent = "latest_research"
        items = [
            EvidenceItem(
                chunk_id="pmid1",
                text="Metformin longevity evidence from a randomized trial.",
                source_type="pubmed",
                source_tier="T2",
                title="Metformin and longevity",
                section_title="abstract",
            )
        ]
        debug = {
            "ranked_candidates_top20": [{"chunk_id": "pmid1"}],
            "selected_top": [{"chunk_id": "pmid1"}],
            "collection_hits": {"literature": 1},
            "quota_dropped": [],
        }

    case = {
        "preferred_source_type": ["pubmed", "literature"],
        "must_match": ["二甲双胍", "证据"],
    }

    assert retrieval_error_analysis.classify_failure(case, Result()) == "ok"


def test_eval_contains_any_supports_medical_term_aliases():
    assert eval_runner._contains_any("Warfarin may increase bleeding risk.", ["华法林", "出血"])
    assert eval_runner._contains_any("Ketogenic diet cancer evidence remains limited.", ["生酮", "癌症", "证据"])
    assert eval_runner._contains_any("Alzheimer's disease lecanemab randomized trial.", ["阿尔茨海默", "临床试验"])


def test_rerank_medication_safety_boosts_matching_drug_and_section():
    matching = EvidenceItem(
        chunk_id="ibuprofen-contra",
        doc_id="drug:ibuprofen",
        text="布洛芬禁用于对本品过敏者，活动性消化道溃疡患者慎用。",
        source_type="drug_label",
        source_tier="T1",
        title="布洛芬片说明书",
        section_title="禁忌",
        metadata={"drug_name": "布洛芬片", "section_key": "contraindications", "safety_critical": True},
        locator={"section": "禁忌"},
        evidence_level="official_drug_label_local_snapshot",
        scores={"dense": 0.4},
    )
    mismatched = EvidenceItem(
        chunk_id="other-contra",
        doc_id="drug:other",
        text="本品禁用于过敏者。",
        source_type="drug_label",
        source_tier="T1",
        title="脑蛋白水解物片说明书",
        section_title="禁忌",
        metadata={"drug_name": "脑蛋白水解物片", "section_key": "contraindications", "safety_critical": True},
        locator={"section": "禁忌"},
        evidence_level="official_drug_label_local_snapshot",
        scores={"dense": 0.45},
    )

    ranked = rerank_items("布洛芬有哪些禁忌", [mismatched, matching], intent="medication_safety")

    assert ranked[0].chunk_id == "ibuprofen-contra"
    assert ranked[0].scores["entity_exact_weight"] > 0
    assert ranked[0].scores["section_intent_weight"] > 0
    assert ranked[1].scores["topic_mismatch_penalty"] < 0


def test_drug_seed_offline_validates_seed_without_network():
    items, diagnostics = asyncio.run(build_drug_items(DEFAULT_SEED, offline=True))

    assert items == []
    assert len(diagnostics) >= 6
    assert {row["drug_id"] for row in diagnostics} >= {"metformin", "aspirin", "warfarin"}
    assert all(row["offline"] is True for row in diagnostics)


def test_medication_safety_prefers_drug_label_and_reports_debug(monkeypatch):
    guideline = EvidenceItem(
        chunk_id="g1",
        text="Metformin safety is discussed in diabetes guidelines.",
        source_type="guideline",
        source_tier="T1",
        title="Diabetes guideline",
        page_start=4,
        doc_id="guideline:diabetes",
        locator={"doc": "guideline:diabetes", "page": 4},
        scores={"local_bm25": 0.2},
        metadata={"collection_key": "local_guideline_bm25"},
    )
    drug_label = EvidenceItem(
        chunk_id="d1",
        text="Metformin contraindications include severe renal impairment and metabolic acidosis risk.",
        source_type="drug_label",
        source_tier="T1",
        title="Metformin drug label",
        doc_id="drug_label:metformin",
        locator={"doc": "drug_label:metformin", "rxcui": "860975"},
        scores={"dense": 0.1},
        metadata={"collection_key": "drug_label_v2"},
    )

    monkeypatch.setattr(hybrid, "search_local_guidelines", lambda *args, **kwargs: [guideline])
    monkeypatch.setattr(hybrid, "local_guideline_quality_stats", lambda: {"total": 1, "quarantine_filtered": 0})

    async def dense(*args, **kwargs):
        return [drug_label]

    monkeypatch.setattr(hybrid, "search_dashvector", dense)

    items, debug = asyncio.run(hybrid_retrieve("metformin contraindications", intent="medication_safety", top_k=5))

    assert items[0].source_type == "drug_label"
    assert debug["source_required"] == ["drug_label", "rxnorm"]
    assert debug["preferred_source_type_hit"] is True
    assert debug["unsafe_to_answer"] is False
    assert debug["collection_hits"]["drug_label_v2"] == 1


def test_medication_safety_without_drug_label_is_unsafe(monkeypatch):
    guideline = EvidenceItem(
        chunk_id="g1",
        text="A guideline mentions medication safety but is not a drug label.",
        source_type="guideline",
        source_tier="T1",
        title="Medication guideline",
        page_start=4,
        doc_id="guideline:drug",
        locator={"doc": "guideline:drug", "page": 4},
        scores={"local_bm25": 0.4},
        metadata={"collection_key": "local_guideline_bm25"},
    )

    monkeypatch.setattr(hybrid, "search_local_guidelines", lambda *args, **kwargs: [guideline])
    monkeypatch.setattr(hybrid, "local_guideline_quality_stats", lambda: {"total": 1, "quarantine_filtered": 0})

    async def no_dense(*args, **kwargs):
        return []

    monkeypatch.setattr(hybrid, "search_dashvector", no_dense)

    items, debug = asyncio.run(hybrid_retrieve("warfarin interactions", intent="medication_safety", top_k=5))

    assert items
    assert debug["preferred_source_type_hit"] is False
    assert debug["missing_required_source"] is True
    assert debug["unsafe_to_answer"] is True


def test_latest_research_flags_missing_research_source(monkeypatch):
    guideline = EvidenceItem(
        chunk_id="g1",
        text="A guideline mentions metformin but does not provide latest trial evidence.",
        source_type="guideline",
        source_tier="T1",
        title="Diabetes guideline",
        page_start=2,
        doc_id="guideline:diabetes",
        locator={"doc": "guideline:diabetes", "page": 2},
        scores={"local_bm25": 0.3},
        metadata={"collection_key": "local_guideline_bm25"},
    )

    monkeypatch.setattr(hybrid, "search_local_guidelines", lambda *args, **kwargs: [guideline])
    monkeypatch.setattr(hybrid, "local_guideline_quality_stats", lambda: {"total": 1, "quarantine_filtered": 0})

    async def no_dense(*args, **kwargs):
        return []

    monkeypatch.setattr(hybrid, "search_dashvector", no_dense)

    items, debug = asyncio.run(hybrid_retrieve("metformin anti aging latest research", intent="latest_research", top_k=5))

    assert items
    assert debug["source_required"] == ["pubmed", "pmc", "literature", "clinical_trial"]
    assert debug["preferred_source_type_hit"] is False
    assert debug["research_source_missing"] is True


def test_latest_research_expands_chinese_terms_before_dense_recall(monkeypatch):
    seen_queries = []
    monkeypatch.setattr(hybrid, "search_local_guidelines", lambda *args, **kwargs: [])
    monkeypatch.setattr(hybrid, "local_guideline_quality_stats", lambda: {"total": 0, "quarantine_filtered": 0})

    async def dense(query, *args, **kwargs):
        seen_queries.append(query)
        return []

    monkeypatch.setattr(hybrid, "search_dashvector", dense)

    items, debug = asyncio.run(hybrid_retrieve("阿尔茨海默病新药临床试验进展", intent="latest_research", top_k=5))

    assert items == []
    assert seen_queries
    assert "alzheimer" in seen_queries[0].lower()
    assert "alzheimer" in debug["retrieval_query"].lower()


def test_research_relevance_accepts_specific_alzheimer_pubmed_result():
    item = EvidenceItem(
        chunk_id="pmid_alz",
        text="A randomized trial comparing donanemab and lecanemab for Alzheimer's disease treatment.",
        source_type="pubmed",
        source_tier="T2",
        title="Comparisons of efficacy and safety of immunotherapies for Alzheimer's disease treatment",
        section_title="abstract",
        metadata={"collection_key": "literature", "collection_name": "medical_literature_v2"},
    )

    assert hybrid._is_research_item(item, "阿尔茨海默病新药临床试验进展 alzheimer")


def test_latest_research_source_quota_promotes_research_over_local_guideline(monkeypatch):
    guideline_items = [
        EvidenceItem(
            chunk_id=f"g{i}",
            text="A local guideline background mention with strong lexical match for metformin anti aging.",
            source_type="guideline",
            source_tier="T1",
            title=f"Guideline {i}",
            page_start=i + 1,
            doc_id=f"guideline:{i}",
            locator={"doc": f"guideline:{i}", "page": i + 1},
            scores={"local_bm25": 0.9 - i * 0.01},
            metadata={"collection_key": "local_guideline_bm25"},
        )
        for i in range(5)
    ]
    research_item = EvidenceItem(
        chunk_id="pmid_1",
        text="PubMed abstract about metformin aging randomized trial.",
        source_type="pubmed",
        source_tier="T2",
        title="Metformin aging trial",
        doc_id="pubmed:1",
        locator={"pmid": "1", "url": "https://pubmed.ncbi.nlm.nih.gov/1/"},
        scores={"dense": 0.1},
        metadata={"collection_key": "literature", "collection_name": "medical_literature_v2"},
    )

    monkeypatch.setattr(hybrid, "search_local_guidelines", lambda *args, **kwargs: guideline_items)
    monkeypatch.setattr(hybrid, "local_guideline_quality_stats", lambda: {"total": 5, "quarantine_filtered": 0})

    async def dense(*args, **kwargs):
        return [research_item]

    monkeypatch.setattr(hybrid, "search_dashvector", dense)

    items, debug = asyncio.run(hybrid_retrieve("metformin anti aging latest research", intent="latest_research", top_k=5))

    assert any(item.source_type == "pubmed" for item in items[:3])
    assert debug["research_source_missing"] is False
    assert debug["quota_filled"]["research"] == 1
    assert debug["source_quota"]["max"]["guideline"] == 2


def test_latest_research_rejects_cancer_evidence_for_non_oncology_query(monkeypatch):
    cancer_evidence = EvidenceItem(
        chunk_id="nci1",
        text="NCI PDQ cancer treatment evidence summary.",
        source_type="literature",
        source_tier="T2",
        title="NCI PDQ",
        doc_id="nci:pdq",
        locator={"url": "https://cancer.gov/pdq"},
        scores={"dense": 0.9},
        metadata={"collection_key": "cancer_evidence", "collection_name": "cancer_evidence_v2"},
    )
    guideline = EvidenceItem(
        chunk_id="g1",
        text="A guideline background mention.",
        source_type="guideline",
        source_tier="T1",
        title="Guideline",
        page_start=1,
        doc_id="guideline:1",
        locator={"doc": "guideline:1", "page": 1},
        scores={"local_bm25": 0.2},
        metadata={"collection_key": "local_guideline_bm25"},
    )

    monkeypatch.setattr(hybrid, "search_local_guidelines", lambda *args, **kwargs: [guideline])
    monkeypatch.setattr(hybrid, "local_guideline_quality_stats", lambda: {"total": 1, "quarantine_filtered": 0})

    async def dense(*args, **kwargs):
        return [cancer_evidence]

    monkeypatch.setattr(hybrid, "search_dashvector", dense)

    items, debug = asyncio.run(hybrid_retrieve("metformin anti aging latest research", intent="latest_research", top_k=5))

    assert items
    assert debug["research_source_missing"] is True
    assert debug["research_evidence_accepted_sources"] == []
    assert debug["research_evidence_rejected_sources"][0]["reason"] == "cancer_evidence_requires_oncology_query"


def test_state_audit_flags_completed_zero_insert_and_duplicate():
    rows = [
        {"pdf_path": "a.pdf", "status": "failed", "accepted_chunks": 2, "inserted_chunks": 0},
        {"pdf_path": "a.pdf", "status": "completed", "accepted_chunks": 2, "inserted_chunks": 0},
        {"pdf_path": "b.pdf", "status": "completed", "accepted_chunks": 2, "inserted_chunks": 2},
    ]

    report = state_audit.audit_rows(rows)

    assert report["anomaly_count"] >= 2
    assert report["duplicate_checkpoints"][0]["identity"] == "pdf_path:a.pdf"
    assert report["completed_zero_insert"][0]["identity"] == "pdf_path:a.pdf"


def test_pubmed_record_to_item_maps_locator_and_evidence_level():
    seed = {"query_id": "metformin_anti_aging", "query": "metformin aging", "topic": "metformin anti-aging"}
    record = {
        "pmid": "123",
        "title": "Metformin and aging",
        "abstract": "Randomized controlled trial abstract.",
        "journal": "Test Journal",
        "year": 2025,
        "publication_types": ["Randomized Controlled Trial"],
        "mesh_terms": ["Metformin"],
        "doi": "10.1/test",
        "url": "https://pubmed.ncbi.nlm.nih.gov/123/",
    }

    item = pubmed_resume_cli.record_to_item(record, seed)

    assert item.source_type == "pubmed"
    assert item.source_tier == "T2"
    assert item.evidence_level == "randomized_controlled_trial"
    assert item.locator["pmid"] == "123"
    assert item.metadata["collection_key"] == "literature"


def test_clinical_trial_record_to_item_maps_nct_locator():
    seed = {"query_id": "metformin_aging", "query": "metformin aging", "condition": "aging"}
    record = {
        "nct_id": "NCT00000001",
        "title": "Metformin aging trial",
        "status": "RECRUITING",
        "phase": "PHASE2",
        "study_type": "INTERVENTIONAL",
        "conditions": ["Aging"],
        "interventions": ["Metformin"],
        "brief_summary": "A trial summary.",
        "outcomes": ["Safety"],
        "eligibility": "Adults.",
        "start_date": "2025-01",
        "completion_date": "2027-01",
        "url": "https://clinicaltrials.gov/study/NCT00000001",
    }

    item = clinical_trials_resume_cli.record_to_item(record, seed)

    assert item.source_type == "clinical_trial"
    assert item.locator["nct_id"] == "NCT00000001"
    assert item.metadata["collection_key"] == "clinical_trial"
    assert "Metformin" in item.metadata["interventions"]


def test_rxnorm_seed_offline_status_row_is_t1():
    drug = {"drug_id": "metformin", "query": "metformin", "display_name": "metformin", "aliases": []}
    row = rxnorm_resume_cli.status_row(
        ingest_run_id="run",
        drug=drug,
        status="offline_seed_valid",
        started_at="2026-01-01T00:00:00+00:00",
        rxnorm={"rxcui": "860975", "query": "metformin"},
    )

    assert row["source_id"] == "rxnorm_rxnav"
    assert row["source_tier"] == "T1"
    assert row["rxcui"] == "860975"


def test_openfda_section_to_item_is_t1_and_safety_critical():
    drug = {"drug_id": "warfarin", "query": "warfarin", "display_name": "warfarin"}
    section = {
        "title": "Warfarin label",
        "section_title": "drug_interactions",
        "text": "Concomitant use with antiplatelet agents may increase bleeding risk.",
        "set_id": "set1",
        "source_url": "https://open.fda.gov/apis/drug/label/",
        "generic_name": "warfarin",
        "brand_name": "Coumadin",
    }

    item = openfda_label_resume_cli.section_to_item(drug, section, {"rxcui": "11289"})

    assert item.source_type == "drug_label"
    assert item.source_tier == "T1"
    assert item.metadata["safety_critical"] is True
    assert item.locator["rxcui"] == "11289"


def test_pdf_quality_audit_marks_ocr_candidate_for_bad_text():
    bad = pdf_quality_audit.text_quality("abc " * 80)
    good = pdf_quality_audit.text_quality("高血压诊断治疗建议。" * 20)

    assert "low_cjk_ratio" in bad["flags"]
    assert "ocr_candidate" in bad["flags"]
    assert "ocr_candidate" not in good["flags"]


def test_pdf_should_ocr_page_uses_same_low_quality_policy():
    assert should_ocr_page("")
    assert should_ocr_page("abc " * 80)
    assert not should_ocr_page("高血压诊断治疗建议。" * 20)


def test_pdf_ocr_dryrun_selects_limited_candidate_pages():
    report = {
        "pdfs": [
            {"pdf_path": "data_PDF/普通指南.pdf", "ocr_candidate_rate": 0.2, "ocr_candidate_pages": [1, 2, 3]},
            {"pdf_path": "data_PDF/中国高血压临床实践指南.pdf", "ocr_candidate_rate": 0.2, "ocr_candidate_pages": [4, 5, 6]},
            {"pdf_path": "data_PDF/扫描版指南.pdf", "ocr_candidate_rate": 1.0, "ocr_candidate_pages": [7, 8, 9, 10]},
        ]
    }

    selected = pdf_ocr_dryrun.select_candidate_pages(report, limit_pages=4, max_pages_per_pdf=2)

    assert len(selected) == 4
    assert selected[0]["pdf_path"].endswith("扫描版指南.pdf")
    assert sum(1 for row in selected if row["pdf_path"].endswith("扫描版指南.pdf")) == 2


def test_pdf_ocr_dryrun_decision_rules_accept_and_reject():
    decision, reason = pdf_ocr_dryrun.decide_ocr(
        pymupdf_text="abc",
        ocr_text="高血压诊断治疗建议。" * 20,
        ocr_confidence=0.92,
    )
    assert decision == "accept_ocr"
    assert reason == "ocr_quality_improved"

    decision, reason = pdf_ocr_dryrun.decide_ocr(
        pymupdf_text="abc",
        ocr_text="高血压诊断治疗建议。" * 20,
        ocr_confidence=0.2,
    )
    assert decision == "reject_ocr"
    assert reason == "low_confidence"


def test_pdf_ocr_plan_groups_accepted_pages_by_pdf():
    dryrun = {
        "summary": {"accept_ocr_rate": 0.5, "ocr_failed_rate": 0.0},
        "pages": [
            {"pdf_path": "a.pdf", "page": 1, "decision": "accept_ocr", "reason": "ok"},
            {"pdf_path": "a.pdf", "page": 2, "decision": "accept_ocr", "reason": "ok"},
            {"pdf_path": "b.pdf", "page": 3, "decision": "manual_review", "reason": "low_ocr_cjk_ratio"},
            {"pdf_path": "c.pdf", "page": 4, "decision": "reject_ocr", "reason": "low_confidence"},
        ],
    }
    audit = {"pdfs": [
        {"pdf_path": "a.pdf", "ocr_candidate_pages": [1, 2, 5]},
        {"pdf_path": "b.pdf", "ocr_candidate_pages": [3]},
    ]}

    plan = pdf_ocr_plan.build_incremental_plan(dryrun, audit)

    assert plan["recommended_force_docs"] == ["a.pdf"]
    assert plan["estimated_pdf_count"] == 1
    assert plan["estimated_page_count"] == 3
    assert len(plan["manual_review_pages"]) == 1


def test_local_drug_cleaning_builds_section_chunks():
    row = {
        "通用名称": "二甲双胍片",
        "商品名称": "格华止",
        "批准文号": "国药准字H20023370",
        "药品分类": "化学药品",
        "生产企业": "测试药企",
        "相关疾病": "2型糖尿病",
        "标题链接": "https://example.test/drug",
        "适应症": "用于2型糖尿病的血糖控制。",
        "禁忌": "严重肾功能不全、代谢性酸中毒患者禁用。",
        "不良反应": "可见胃肠道反应。",
        "用法用量": "遵医嘱口服。",
        "注意事项": "用药期间监测肾功能。",
        "孕妇及哺乳期妇女用药": "妊娠及哺乳期遵医嘱。",
        "儿童用药": "儿童遵医嘱。",
        "老人用药": "老年患者注意肾功能。",
        "药物相互作用": "与影响肾功能的药物合用需谨慎。",
        "药理毒理": "降低血糖。",
        "药代动力学": "经肾排泄。",
    }

    items = row_to_items(row)
    fields = evidence_to_fields(items[1], indexed_at="2026-01-01T00:00:00+00:00")

    assert len(items) == 11
    assert items[1].section_title == "禁忌"
    assert items[1].source_type == "drug_label"
    assert items[1].source_tier == "T1"
    assert items[1].metadata["safety_critical"] is True
    assert fields["drug_name"] == "二甲双胍片"
    assert fields["approval_no"] == "国药准字H20023370"
    assert fields["source_name"] == "nmpa_cfda_local_snapshot"
    assert fields["license"] == "local_official_snapshot_review_required"
    assert fields["evidence_level"] == "official_drug_label_local_snapshot"
    assert fields["official_source_assumption"] is True
    assert fields["source_verified_online"] is False


def test_local_drug_cli_dedupes_and_quarantines(monkeypatch):
    base_row = {
        "通用名称": "布洛芬片",
        "商品名称": "",
        "批准文号": "国药准字H1",
        "药品分类": "化学药品",
        "生产企业": "测试药企",
        "相关疾病": "疼痛",
        "适应症": "用于缓解疼痛和发热。",
        "禁忌": "对本品过敏者禁用。",
        "不良反应": "可见胃肠道不适。",
        "用法用量": "口服。",
        "注意事项": "避免长期大量使用。",
        "孕妇及哺乳期妇女用药": "遵医嘱。",
        "儿童用药": "遵医嘱。",
        "老人用药": "遵医嘱。",
        "药物相互作用": "与抗凝药合用增加出血风险。",
        "药理毒理": "NSAID。",
        "药代动力学": "尚不明确。",
    }
    missing_name = {**base_row, "通用名称": "", "批准文号": "国药准字H2"}
    empty_body = {**base_row, "通用名称": "空正文药", "批准文号": "国药准字H3"}
    for key in local_drug_cli.BODY_COLUMNS:
        empty_body[key] = ""
    rows = [
        ("drug.xlsx", 2, base_row),
        ("drug.xlsx", 3, dict(base_row)),
        ("drug.xlsx", 4, missing_name),
        ("drug.xlsx", 5, empty_body),
    ]
    monkeypatch.setattr(local_drug_cli, "iter_excel_rows", lambda *args, **kwargs: rows)

    items, stats, quarantine, dedupe = build_local_drug_items(os.path.abspath("."))

    assert stats["rows"] == 4
    assert stats["accepted_rows"] == 1
    assert stats["duplicate_rows"] == 1
    assert stats["quarantined_rows"] == 2
    assert dedupe[0]["dedupe_key"] == ["布洛芬片", "国药准字H1", "测试药企"]
    assert {row["quality"][0] for row in quarantine} >= {"missing_drug_name", "body_too_short"}
    assert any(item.section_title == "药物相互作用" for item in items)


def test_clean_cell_normalizes_html_nan_and_whitespace():
    assert clean_cell(" <b>禁忌</b>\n\n  内容， ") == "禁忌\n内容"
    assert clean_cell("nan") == ""
