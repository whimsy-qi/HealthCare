"""
Emergency node static-template tests（不触发 LLM / 网络）。

运行:
  cd D:\\Health_system
  python -m backend.tests.test_emergency_node_static
"""
import asyncio
import os
import sys
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, ROOT)
sys.path.insert(0, BACKEND)

_MISSING = object()
_MODULE_BACKUP = {}


def _async_stub(*args, **kwargs):
    async def _inner():
        return None
    return _inner()


def _install_module(name, attrs=None, *, package=False):
    if name not in _MODULE_BACKUP:
        _MODULE_BACKUP[name] = sys.modules.get(name, _MISSING)
    mod = types.ModuleType(name)
    if package:
        mod.__path__ = []
    for key, value in (attrs or {}).items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


def _restore_import_stubs():
    for name, previous in reversed(list(_MODULE_BACKUP.items())):
        if previous is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    _MODULE_BACKUP.clear()


class _StateGraphStub:
    def __init__(self, *args, **kwargs):
        pass

    def add_node(self, *args, **kwargs):
        pass

    def add_edge(self, *args, **kwargs):
        pass

    def add_conditional_edges(self, *args, **kwargs):
        pass

    def compile(self):
        return self


class _AsyncOpenAIStub:
    def __init__(self, *args, **kwargs):
        pass


class _AsyncClientStub:
    def __init__(self, *args, **kwargs):
        pass


class _ReportAgentStub:
    def __init__(self, *args, **kwargs):
        pass


class _VectorGuidelineRetrieverStub:
    pass


class _BlackboardStub:
    pass


def _install_graph_engine_import_stubs():
    _install_module("httpx", {"AsyncClient": _AsyncClientStub})
    _install_module("openai", {"AsyncOpenAI": _AsyncOpenAIStub})
    _install_module("langgraph", package=True)
    _install_module("langgraph.graph", {"StateGraph": _StateGraphStub, "START": "__start__", "END": "__end__"})
    _install_module("scripts", package=True)
    _install_module("scripts.vision_tool", {"analyze_image_with_vision": _async_stub})
    _install_module("agents", package=True)
    _install_module("agents.triage_agent", {"triage_query": _async_stub})
    _install_module("agents.symptom_controller", {"run_symptom_track": _async_stub})
    _install_module("agents.general_agent", {"run_general_agent": _async_stub})
    _install_module("agents.rumor_agent", {"run_rumor_controller": _async_stub})
    _install_module("agents.rumor", package=True)
    _install_module("agents.rumor.integration", {"run_rumor_ctaew": _async_stub})
    _install_module("agents.medication_agent", {
        "run_med_extractor": _async_stub,
        "run_med_pharmacist": _async_stub,
        "run_med_reviewer": _async_stub,
        "log_medication_reflection_data": _async_stub,
    })
    _install_module("agents.report_agent", {
        "ReportAgent": _ReportAgentStub,
        "VectorGuidelineRetriever": _VectorGuidelineRetrieverStub,
    })
    _install_module("agents.hallucination_agent", {"guard_answer": _async_stub})
    _install_module("core.evidence", {"build_chain": lambda *a, **k: {}, "dedupe_refs": lambda refs: refs})
    _install_module("core.blackboard", {"Blackboard": _BlackboardStub})
    _install_module("core.insight_memory", {
        "harvest_from_hallucination_report": _async_stub,
        "retrieve_insights": _async_stub,
        "render_insights_as_fewshot": lambda *a, **k: "",
    })


_install_graph_engine_import_stubs()
import graph_engine
_restore_import_stubs()


class _ForbiddenLLM:
    class chat:
        class completions:
            @staticmethod
            async def create(*args, **kwargs):
                raise AssertionError("emergency_node must not call LLM")


async def test_emergency_node_static_template():
    graph_engine.client = _ForbiddenLLM()
    result = await graph_engine.emergency_node({
        "query": "我误服了一整瓶降压药，现在头很晕",
        "session_id": "test",
    })

    answer = result["final_answer"]
    assert result["current_route"] == "EMERGENCY_TRIGGER"
    assert result["next_agent"] == "END"
    assert result["is_finished"] is True
    assert result["trace_data"] == {"emergency_static_template": True, "llm_used": False}
    assert "120" in answer
    assert "不要自行催吐" in answer
    assert "不要自行服药" in answer
    assert "不要进食或饮水" in answer


def main():
    asyncio.run(test_emergency_node_static_template())
    print("[OK] emergency node static-template test passed.")


if __name__ == "__main__":
    main()
