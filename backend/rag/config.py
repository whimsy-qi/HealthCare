from __future__ import annotations

import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_PDF_ROOT = BACKEND_ROOT / "data_PDF"
DEFAULT_MANIFEST = BACKEND_ROOT / "rag" / "sources" / "manifest.yaml"

COLLECTIONS = {
    "guideline": os.getenv("RAG_GUIDELINE_COLLECTION", "medical_guideline_v2"),
    "literature": os.getenv("RAG_LITERATURE_COLLECTION", "medical_literature_v2"),
    "drug_label": os.getenv("RAG_DRUG_LABEL_COLLECTION", "drug_label_v2"),
    "clinical_trial": os.getenv("RAG_CLINICAL_TRIAL_COLLECTION", "clinical_trial_v2"),
    "kg": os.getenv("RAG_KG_COLLECTION", "medical_kg_v2"),
    "patient_education": os.getenv("RAG_PATIENT_EDUCATION_COLLECTION", "patient_education_v2"),
    "drug_safety_signal": os.getenv("RAG_DRUG_SAFETY_SIGNAL_COLLECTION", "drug_safety_signal_v2"),
    "cancer_evidence": os.getenv("RAG_CANCER_EVIDENCE_COLLECTION", "cancer_evidence_v2"),
    "legacy": os.getenv("RAG_LEGACY_COLLECTION", "multimodal_medical_db"),
}

EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "qwen3-vl-embedding")
EMBEDDING_DIM = int(os.getenv("RAG_EMBEDDING_DIM", "2560"))
ENABLE_DASHVECTOR = os.getenv("RAG_ENABLE_DASHVECTOR", "true").lower() not in {"0", "false", "no"}
ENABLE_LOCAL_PDF_INDEX = os.getenv("RAG_ENABLE_LOCAL_PDF_INDEX", "true").lower() not in {"0", "false", "no"}
ENABLE_GRAPHRAG = os.getenv("RAG_ENABLE_GRAPHRAG", "false").lower() in {"1", "true", "yes", "on"}
LOCAL_INDEX_MAX_PDFS = int(os.getenv("RAG_LOCAL_INDEX_MAX_PDFS", "120"))
