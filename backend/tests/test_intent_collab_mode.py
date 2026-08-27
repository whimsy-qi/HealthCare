"""
Intent collaboration mode smoke tests（不触发 LLM / 网络）。

运行:
  cd D:\\Health_system
  python -m backend.tests.test_intent_collab_mode
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, ROOT)
sys.path.insert(0, BACKEND)

from core.intent_ontology import select_collab_mode


def assert_mode(cfg, expected):
    assert cfg["mode"] == expected, f"expected {expected}, got {cfg}"


def test_ask_basic_single_react():
    cfg = select_collab_mode("ASK", "BASIC", domain="GENERAL_CONSULTATION", sub_intent="GENERAL")
    assert_mode(cfg, "single_react")
    assert cfg["requires_evidence"] is False


def test_seek_help_caution_single_react_kg():
    cfg = select_collab_mode("SEEK_HELP", "CAUTION", domain="GENERAL_CONSULTATION", sub_intent="TREATMENT")
    assert_mode(cfg, "single_react_kg")
    assert cfg["requires_evidence"] is True


def test_open_uncertainty_goes_fusion_not_vote():
    cfg = select_collab_mode("ASK", "BASIC", uncertainty=0.45, domain="GENERAL_CONSULTATION")
    assert_mode(cfg, "fusion")


def test_no_vote_closed_mode_is_exposed():
    cases = [
        select_collab_mode("ASK", "BASIC", domain="GENERAL_CONSULTATION"),
        select_collab_mode("SEEK_HELP", "CAUTION", domain="GENERAL_CONSULTATION"),
        select_collab_mode("ANALYZE", "CHECKUP", domain="REPORT_INTERPRETATION"),
        select_collab_mode("DEBUNK", "CAUSE", domain="RUMOR_VERIFICATION"),
        select_collab_mode("ASK", "BASIC", uncertainty=0.45, domain="GENERAL_CONSULTATION"),
    ]
    assert all(cfg["mode"] != "vote_closed" for cfg in cases)
    assert all("is_closed_set" not in cfg for cfg in cases)


def test_act_level_fallback_no_default_vote():
    cfg = select_collab_mode("ASK", "", domain="GENERAL_CONSULTATION")
    assert_mode(cfg, "single_react")


def main():
    test_ask_basic_single_react()
    test_seek_help_caution_single_react_kg()
    test_open_uncertainty_goes_fusion_not_vote()
    test_no_vote_closed_mode_is_exposed()
    test_act_level_fallback_no_default_vote()
    print("[OK] intent collaboration mode tests passed.")


if __name__ == "__main__":
    main()
