"""
Hallucination guard action tests without LLM/network calls.
Run:
  python -m backend.tests.test_hallucination_guard
"""
import asyncio
import os
import sys
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, ROOT)
sys.path.insert(0, BACKEND)

fake_llm = types.ModuleType("core.llm_client")
fake_llm.DEFAULT_MODEL = "test-model"
fake_llm.FAST_MODEL = "test-model"
fake_llm.REASONING_MODEL = "test-model"
fake_llm.SUMMARY_MODEL = "test-model"
fake_llm.shared_client = object()
sys.modules.setdefault("core.llm_client", fake_llm)

from agents import hallucination_agent as h


def _report(action: str) -> h.HallucinationReport:
    return h.HallucinationReport(
        hallucination_score=0.4,
        confidence=0.6,
        action=action,
        summary="test report",
        claims=[
            h.ClaimAudit(
                claim="原回答中未被支持的事实",
                risk="HIGH",
                verdict="UNSUPPORTED",
                unsupported_span="未被支持的事实",
                rationale="证据不足",
            )
        ],
        stats={"n_claims": 1, "n_unsupported": 1, "n_contradicted": 0},
    )


async def _run_case(action: str):
    original_check = h.check_answer
    original_regen = h._regenerate_conservative_answer

    async def fake_check_answer(*args, **kwargs):
        return _report(action)

    async def fake_regenerate(answer, report, evidence, domain, constraints=None):
        return "### 保守改写\n已删除未被证据支持的断言。"

    h.check_answer = fake_check_answer
    h._regenerate_conservative_answer = fake_regenerate
    try:
        logs = []
        return await h.guard_answer(
            answer="原始回答：未被支持的事实。",
            evidence=[{"title": "e", "content": "supported only"}],
            domain="general",
            domain_risk="HIGH",
            audit_logs=logs,
        )
    finally:
        h.check_answer = original_check
        h._regenerate_conservative_answer = original_regen


async def main():
    abstain_text, abstain_report = await _run_case("ABSTAIN")
    assert "主动放弃本次回答" in abstain_text
    assert abstain_report["action"] == "ABSTAIN"

    warn_text, warn_report = await _run_case("WARN")
    assert warn_text.startswith("> 🟡 **可信度提示**")
    assert "原始回答" in warn_text
    assert warn_report["action"] == "WARN"

    regen_text, regen_report = await _run_case("REGENERATE")
    assert regen_text.startswith("### 保守改写")
    assert "原始回答" not in regen_text
    assert regen_report["action"] == "REGENERATE"

    print("[OK] hallucination guard action tests passed.")


def test_hallucination_guard_actions():
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
