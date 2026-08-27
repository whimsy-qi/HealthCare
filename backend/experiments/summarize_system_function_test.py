from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _load_rows(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_success(value: Any) -> bool | None:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "pass", "passed", "成功"}:
        return True
    if text in {"false", "0", "no", "n", "fail", "failed", "失败"}:
        return False
    return None


def _rate(num: int, den: int) -> float | None:
    if den == 0:
        return None
    return round(num / den, 4)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if _is_success(row.get("success")) is not None]
    total_success = sum(1 for row in completed if _is_success(row.get("success")) is True)

    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_case[str(row.get("case_id"))].append(row)
        by_module[str(row.get("module"))].append(row)

    case_summary = []
    for case_id, items in sorted(by_case.items()):
        done = [row for row in items if _is_success(row.get("success")) is not None]
        success = sum(1 for row in done if _is_success(row.get("success")) is True)
        case_summary.append(
            {
                "case_id": case_id,
                "module": items[0].get("module"),
                "name": items[0].get("name"),
                "planned_runs": len(items),
                "completed_runs": len(done),
                "success_runs": success,
                "success_rate": _rate(success, len(done)),
                "failure_types": dict(Counter(row.get("failure_type") or "unspecified" for row in done if _is_success(row.get("success")) is False)),
            }
        )

    module_summary = []
    for module, items in sorted(by_module.items()):
        done = [row for row in items if _is_success(row.get("success")) is not None]
        success = sum(1 for row in done if _is_success(row.get("success")) is True)
        module_summary.append(
            {
                "module": module,
                "planned_runs": len(items),
                "completed_runs": len(done),
                "success_runs": success,
                "success_rate": _rate(success, len(done)),
            }
        )

    return {
        "planned_runs": len(rows),
        "completed_runs": len(completed),
        "success_runs": total_success,
        "success_rate": _rate(total_success, len(completed)),
        "case_summary": case_summary,
        "module_summary": module_summary,
        "not_completed_runs": [
            {
                "case_id": row.get("case_id"),
                "run_index": row.get("run_index"),
                "status": row.get("status"),
            }
            for row in rows
            if _is_success(row.get("success")) is None
        ],
    }


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# System Function Test Summary",
        "",
        f"Planned runs: {summary['planned_runs']}",
        f"Completed runs: {summary['completed_runs']}",
        f"Success runs: {summary['success_runs']}",
        f"Success rate: {summary['success_rate']}",
        "",
        "## By Module",
        "",
        "| Module | Planned | Completed | Success | Success rate |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in summary["module_summary"]:
        lines.append(
            f"| {row['module']} | {row['planned_runs']} | {row['completed_runs']} | "
            f"{row['success_runs']} | {row['success_rate']} |"
        )
    lines.extend(
        [
            "",
            "## By Case",
            "",
            "| Case | Module | Name | Planned | Completed | Success | Success rate | Failure types |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in summary["case_summary"]:
        lines.append(
            f"| {row['case_id']} | {row['module']} | {row['name']} | {row['planned_runs']} | "
            f"{row['completed_runs']} | {row['success_runs']} | {row['success_rate']} | "
            f"`{json.dumps(row['failure_types'], ensure_ascii=False)}` |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize 24x20 system function test records.")
    parser.add_argument("--input", required=True, help="Path to system_function_test_24x20.json after manual/automated runs are filled.")
    parser.add_argument("--output", required=True, help="Directory for summary.json and report.md.")
    args = parser.parse_args()

    rows = _load_rows(Path(args.input))
    summary = summarize(rows)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(out_dir / "report.md", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
