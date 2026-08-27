import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from .service import get_multimodal_context_v2, retrieve_medical_evidence
from .schema import EvidenceItem, RetrievalResult
from .graph import retrieve_graph_evidence

__all__ = [
    "EvidenceItem",
    "RetrievalResult",
    "get_multimodal_context_v2",
    "retrieve_medical_evidence",
    "retrieve_graph_evidence",
]
