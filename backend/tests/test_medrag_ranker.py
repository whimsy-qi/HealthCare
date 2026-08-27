import asyncio
import os
import sys
import types


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, ROOT)

fake_llm = types.ModuleType("core.llm_client")
fake_llm.DEFAULT_MODEL = "test-default-model"
fake_llm.FAST_MODEL = "test-fast-model"
fake_llm.REASONING_MODEL = "test-reasoning-model"
fake_llm.SUMMARY_MODEL = "test-summary-model"
fake_llm.shared_client = object()
sys.modules.setdefault("core.llm_client", fake_llm)

from core.blackboard import Blackboard
from agents.maddx import integration, kg_candidate_ranker, workflow
from agents.maddx.proposer import _merge_candidate_priors
from agents.maddx.tools import ToolRegistry


def test_medrag_ranker_basic(monkeypatch):
    async def fake_query(symptom_names, limit_per_symptom=30):
        assert "chest pain" in symptom_names
        return {
            "total_diseases": 10,
            "fallback": False,
            "symptoms": [
                {
                    "input_symptom": "chest pain",
                    "df": 3,
                    "edges": [
                        {
                            "disease": "acute myocardial infarction",
                            "matched_symptom": "chest pain",
                            "match_quality": 1.0,
                            "ref": "kg:Disease:AMI:HAS_SYMPTOM:chest pain",
                        },
                        {
                            "disease": "gastroesophageal reflux disease",
                            "matched_symptom": "chest pain",
                            "match_quality": 0.8,
                            "ref": "kg:Disease:GERD:HAS_SYMPTOM:chest pain",
                        },
                    ],
                },
                {
                    "input_symptom": "sweating",
                    "df": 1,
                    "edges": [
                        {
                            "disease": "acute myocardial infarction",
                            "matched_symptom": "sweating",
                            "match_quality": 1.0,
                            "ref": "kg:Disease:AMI:HAS_SYMPTOM:sweating",
                        }
                    ],
                },
            ],
        }

    monkeypatch.setattr(kg_candidate_ranker, "query_symptom_disease_edges", fake_query)
    result = asyncio.run(
        kg_candidate_ranker.rank_disease_candidates(
            [{"name": "chief complaint: chest pain"}, {"name": "sweating"}],
            top_k=5,
        )
    )

    candidates = result["candidates"]
    assert result["method"] == "medrag_kg_bm25_prior"
    assert candidates[0]["disease"] == "acute myocardial infarction"
    assert candidates[0]["support_count"] == 2
    assert candidates[0]["kg_prior_score"] >= candidates[1]["kg_prior_score"]
    assert result["stats"]["fallback"] is False


def test_medrag_ranker_empty_kg(monkeypatch):
    async def fake_query(symptom_names, limit_per_symptom=30):
        return {"total_diseases": 0, "symptoms": [], "fallback": True, "error": "neo4j_down"}

    monkeypatch.setattr(kg_candidate_ranker, "query_symptom_disease_edges", fake_query)
    result = asyncio.run(
        kg_candidate_ranker.rank_disease_candidates([{"name": "unknown symptom"}])
    )

    assert result["candidates"] == []
    assert result["stats"]["fallback"] is True
    assert result["stats"]["error"] == "neo4j_down"


def test_medrag_prior_merge():
    candidates = [
        {
            "disease": "acute myocardial infarction",
            "reasoning": "classic presentation",
            "supporting_symptoms": ["chest pain"],
            "confidence": 0.8,
            "evidence_refs": [],
        }
    ]
    priors = [
        {
            "rank": 1,
            "disease": "acute myocardial infarction",
            "kg_prior_score": 0.91,
            "matched_symptoms": ["chest pain", "sweating"],
            "evidence_refs": ["kg:Disease:AMI:HAS_SYMPTOM:chest pain"],
            "support_count": 2,
        },
        {
            "rank": 2,
            "disease": "gastroesophageal reflux disease",
            "kg_prior_score": 0.42,
            "matched_symptoms": ["chest pain"],
            "evidence_refs": ["kg:Disease:GERD:HAS_SYMPTOM:chest pain"],
            "support_count": 1,
        },
    ]

    merged = _merge_candidate_priors(candidates, priors, candidate_prior_ref=7)

    assert merged[0]["kg_prior_score"] == 0.91
    assert merged[0]["kg_evidence_refs"] == ["kg:Disease:AMI:HAS_SYMPTOM:chest pain"]
    assert 7 in merged[0]["evidence_refs"]
    assert merged[1]["disease"] == "gastroesophageal reflux disease"
    assert merged[1]["confidence"] <= 0.75


def test_maddx_trace_contains_medrag_node(monkeypatch):
    async def fake_proposer(
        bb,
        tools,
        symptoms,
        patient_profile,
        parent_refs=None,
        model_id=None,
        candidate_priors=None,
        candidate_prior_ref=None,
    ):
        assert candidate_priors
        assert candidate_prior_ref
        candidates = [
            {
                "disease": "acute myocardial infarction",
                "reasoning": "KG prior accepted",
                "supporting_symptoms": ["chest pain"],
                "confidence": 0.8,
                "evidence_refs": [candidate_prior_ref],
            }
        ]
        await bb.append("candidate_dx", candidates, agent_id="proposer", parent_refs=parent_refs or [])
        return candidates

    async def fake_critic(*args, **kwargs):
        bb = args[0]
        await bb.append("objections", [], agent_id="critic", parent_refs=kwargs.get("parent_refs") or [])
        return []

    async def fake_moderator(bb, *args, **kwargs):
        report = {
            "primary_dx": "acute myocardial infarction",
            "confidence": 0.8,
            "termination_reason": kwargs.get("termination_reason", "NO_VALID_OBJECTIONS"),
            "rounds_used": kwargs.get("rounds_used", 0),
        }
        await bb.append("final_diagnosis", report, agent_id="moderator", parent_refs=kwargs.get("parent_refs") or [])
        return report

    monkeypatch.setattr(workflow, "run_proposer", fake_proposer)
    monkeypatch.setattr(workflow, "run_critic", fake_critic)
    monkeypatch.setattr(workflow, "run_moderator", fake_moderator)

    ranking = {
        "method": "medrag_kg_bm25_prior",
        "candidates": [
            {
                "rank": 1,
                "disease": "acute myocardial infarction",
                "kg_prior_score": 0.9,
                "matched_symptoms": ["chest pain"],
                "evidence_refs": ["kg:Disease:AMI:HAS_SYMPTOM:chest pain"],
                "support_count": 1,
            }
        ],
        "stats": {"input_symptoms": 1, "matched_symptoms": 1, "candidate_count": 1, "fallback": False},
    }

    bb = Blackboard(session_id="medrag-trace")
    asyncio.run(
        workflow.run_maddx(
            bb=bb,
            symptoms=[{"name": "chest pain", "duration_days": 1, "severity": "severe"}],
            patient_profile={},
            tools=ToolRegistry(bb=bb, enabled=[]),
            candidate_ranking=ranking,
        )
    )

    dag = bb.to_trace_dag()
    assert any(n["label"] == "medrag_candidate_ranking" for n in dag["nodes"])


def test_disable_medrag_ranker(monkeypatch):
    monkeypatch.setenv("USE_MEDRAG_RANKER", "false")

    async def fail_ranker(*args, **kwargs):
        raise AssertionError("ranker should not be called when USE_MEDRAG_RANKER=false")

    async def fake_run_maddx(*args, **kwargs):
        assert kwargs.get("candidate_ranking") is None
        return {
            "primary_dx": "fallback",
            "confidence": 0.5,
            "termination_reason": "NO_VALID_OBJECTIONS",
            "rounds_used": 0,
        }

    monkeypatch.setattr(integration, "rank_disease_candidates", fail_ranker)
    monkeypatch.setattr(integration, "run_maddx", fake_run_maddx)

    markdown, bb, events = asyncio.run(
        integration.run_maddx_for_symptom_report(
            slots={"chief complaint": "chest pain"},
            patient_profile={},
            kg_context="",
            local_guide_context="",
        )
    )

    assert "fallback" in markdown
    assert bb.to_trace_dag()["nodes"] == []
    assert events == []
