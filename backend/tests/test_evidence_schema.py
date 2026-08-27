"""
Evidence schema smoke tests.
Run:
  python -m backend.tests.test_evidence_schema
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, ROOT)
sys.path.insert(0, BACKEND)

from core.evidence import build_chain


def main():
    chain = build_chain(
        triples=[
            {
                "head": "阿司匹林",
                "relation": "参考依据",
                "tail": "PubMed 摘要",
                "source_id": "pubmed:123",
                "confidence": 0.8,
            },
            {
                "head": "无引用结论",
                "relation": "参考依据",
                "tail": "应被剔除",
                "source_id": "web:missing",
                "confidence": 0.4,
            },
        ],
        refs=[
            {
                "ref_id": "pubmed:123",
                "type": "pubmed",
                "label": "PubMed PMID 123",
                "locator": {"pmid": "123"},
                "snippet": "trial abstract",
            }
        ],
        final_claim="PubMed ref should be accepted and invalid triples removed.",
        confidence=0.8,
    )

    assert chain["refs"][0]["type"] == "pubmed"
    assert len(chain["triples"]) == 1
    assert chain["triples"][0]["source_id"] == "pubmed:123"
    print("[OK] evidence schema tests passed.")


if __name__ == "__main__":
    main()
