"""
Memory agent schema tests without LLM/DB calls.
Run:
  python -m backend.tests.test_memory_agent
"""
import os
import sys
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, ROOT)
sys.path.insert(0, BACKEND)

fake_dotenv = types.ModuleType("dotenv")
fake_dotenv.load_dotenv = lambda *args, **kwargs: None
fake_dotenv.find_dotenv = lambda *args, **kwargs: ""
sys.modules.setdefault("dotenv", fake_dotenv)

fake_llm = types.ModuleType("core.llm_client")
fake_llm.FAST_MODEL = "test-model"
fake_llm.shared_client = object()
sys.modules.setdefault("core.llm_client", fake_llm)

from agents.memory_agent import _normalize_health_updates, merge_updates_into_profile


def main():
    updates = _normalize_health_updates({
        "diseases": ["高血压"],
        "allergies": [],
        "surgeries": ["阑尾切除术"],
        "medications": ["阿司匹林"],
        "family_history": [{"relative": "父亲", "condition": "糖尿病"}],
        "lifestyle": {"smoking": "heavy", "sleep": ""},
        "unknown": ["ignore"],
    })

    assert updates["diseases"] == ["高血压"]
    assert "allergies" not in updates
    assert updates["medications"] == ["阿司匹林"]
    assert updates["family_history"][0]["condition"] == "糖尿病"
    assert updates["lifestyle"] == {"smoking": "heavy"}

    merged = merge_updates_into_profile(
        {
            "diseases": ["高血压"],
            "medications": ["二甲双胍"],
            "family_history": [],
            "lifestyle": {"exercise": "regular"},
        },
        updates,
    )
    assert merged["diseases"] == ["高血压"]
    assert merged["medications"] == ["二甲双胍", "阿司匹林"]
    assert merged["family_history"][0]["relative"] == "父亲"
    assert merged["lifestyle"] == {"exercise": "regular", "smoking": "heavy"}
    print("[OK] memory agent schema tests passed.")


if __name__ == "__main__":
    main()
