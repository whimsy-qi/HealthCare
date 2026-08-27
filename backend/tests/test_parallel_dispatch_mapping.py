"""
Parallel dispatch static contract test.
Run:
  python -m backend.tests.test_parallel_dispatch_mapping
"""
import ast
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GRAPH_ENGINE = os.path.join(ROOT, "backend", "graph_engine.py")


def main():
    with open(GRAPH_ENGINE, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    route_map = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_PARALLEL_DOMAIN_ROUTES":
                    route_map = ast.literal_eval(node.value)
                    break

    assert route_map is not None, "_PARALLEL_DOMAIN_ROUTES missing"
    assert route_map["SYMPTOM_ANALYSIS"] == "symptom"
    assert route_map["MEDICATION_REVIEW"] == "medication_subgraph"
    assert route_map["RUMOR_VERIFICATION"] == "rumor_subgraph"
    assert route_map["REPORT_INTERPRETATION"] == "report"
    assert route_map["GENERAL_CONSULTATION"] == "general"
    print("[OK] parallel dispatch mapping tests passed.")


if __name__ == "__main__":
    main()
