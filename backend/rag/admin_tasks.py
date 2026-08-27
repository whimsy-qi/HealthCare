from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from celery import Celery

from core.database import SessionLocal
from core.models import RagEvalRun, RagIngestTask, RagIngestTaskLog, RagSourceFile


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = BACKEND_ROOT / "rag" / "reports" / "admin_ingest_tasks"
EVAL_REPORT_ROOT = BACKEND_ROOT / "rag" / "reports" / "admin_eval_runs"


def _celery_url() -> str:
    return os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL") or "redis://localhost:6379/0"


celery_app = Celery(
    "health_system_admin",
    broker=_celery_url(),
    backend=os.getenv("CELERY_RESULT_BACKEND") or _celery_url(),
)


def _log(db, task_id: int, level: str, message: str, meta_data: dict | None = None) -> None:
    db.add(RagIngestTaskLog(task_id=task_id, level=level, message=message, meta_data=meta_data or {}))
    db.commit()


def _command_for_task(task: RagIngestTask, source_file: RagSourceFile | None, report_path: Path) -> list[str]:
    mode_args = ["--dry-run"] if task.mode == "dry_run" else []
    batch_size = str((task.summary or {}).get("options", {}).get("batch_size") or 16)
    common_report = ["--run-report", str(report_path)]

    if task.task_type in {"pdf", "guideline"}:
        if not source_file:
            raise ValueError("pdf task requires source_file_id")
        return [
            sys.executable,
            "-m",
            "rag.ingest.pdf_resume_cli",
            "--pdf-root",
            str(Path(source_file.storage_path).parent),
            "--collection",
            task.collection,
            "--batch-size",
            batch_size,
            "--force-doc",
            Path(source_file.storage_path).name,
            *mode_args,
            *common_report,
        ]

    if task.task_type == "drug_excel":
        if not source_file:
            raise ValueError("drug_excel task requires source_file_id")
        return [
            sys.executable,
            "-m",
            "rag.ingest.local_drug_resume_cli",
            "--drug-root",
            str(Path(source_file.storage_path).parent),
            "--collection",
            task.collection,
            "--batch-size",
            batch_size,
            "--row-batch-size",
            str((task.summary or {}).get("options", {}).get("row_batch_size") or 20),
            *mode_args,
            *common_report,
        ]

    if task.task_type == "pubmed_seed":
        seed = source_file.storage_path if source_file else str(BACKEND_ROOT / "rag" / "sources" / "research_seed.yaml")
        return [
            sys.executable,
            "-m",
            "rag.ingest.pubmed_resume_cli",
            "--seed",
            seed,
            "--collection",
            task.collection,
            "--batch-size",
            batch_size,
            *mode_args,
            *common_report,
        ]

    if task.task_type == "clinical_trial_seed":
        seed = source_file.storage_path if source_file else str(BACKEND_ROOT / "rag" / "sources" / "trial_seed.yaml")
        return [
            sys.executable,
            "-m",
            "rag.ingest.clinical_trials_resume_cli",
            "--seed",
            seed,
            "--collection",
            task.collection,
            "--batch-size",
            batch_size,
            *mode_args,
            *common_report,
        ]

    if task.task_type == "openfda_seed":
        seed = source_file.storage_path if source_file else str(BACKEND_ROOT / "rag" / "sources" / "drug_seed.yaml")
        return [
            sys.executable,
            "-m",
            "rag.ingest.openfda_label_resume_cli",
            "--seed",
            seed,
            "--collection",
            task.collection,
            "--batch-size",
            batch_size,
            *mode_args,
            *common_report,
        ]

    if task.task_type == "external_seed":
        seed = source_file.storage_path if source_file else str(BACKEND_ROOT / "rag" / "sources" / "external_seed.yaml")
        return [
            sys.executable,
            "-m",
            "rag.ingest.external_source_cli",
            "--seed",
            seed,
            "--batch-size",
            batch_size,
            *mode_args,
            *common_report,
        ]

    raise ValueError(f"unsupported task_type: {task.task_type}")


def _summarize_report(path: Path) -> dict:
    if not path.exists():
        return {"report_path": str(path), "rows": 0}
    summary = {"report_path": str(path), "rows": 0, "completed": 0, "failed": 0, "skipped": 0, "inserted_chunks": 0}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            summary["rows"] += 1
            try:
                row = json.loads(line)
            except Exception:
                continue
            status = row.get("status")
            if status in summary:
                summary[status] += 1
            summary["inserted_chunks"] += int(row.get("inserted_chunks") or 0)
    return summary


@celery_app.task(name="rag.admin_tasks.run_rag_ingest_task")
def run_rag_ingest_task(task_id: int) -> dict:
    db = SessionLocal()
    try:
        task = db.query(RagIngestTask).filter(RagIngestTask.id == task_id).first()
        if not task:
            raise ValueError(f"task not found: {task_id}")
        source_file = db.query(RagSourceFile).filter(RagSourceFile.id == task.source_file_id).first() if task.source_file_id else None

        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        report_path = REPORT_ROOT / f"{task.task_uid}.jsonl"
        task.status = "running"
        task.progress = 5
        task.started_at = datetime.now(timezone.utc)
        task.error = None
        db.commit()
        command = _command_for_task(task, source_file, report_path)
        _log(db, task.id, "info", "starting ingest command", {"command": command})

        proc = subprocess.run(command, cwd=str(BACKEND_ROOT), text=True, capture_output=True, timeout=60 * 60 * 6)
        summary = _summarize_report(report_path)
        summary["returncode"] = proc.returncode
        summary["stdout_tail"] = proc.stdout[-4000:]
        summary["stderr_tail"] = proc.stderr[-4000:]
        task.summary = {**(task.summary or {}), **summary}
        task.progress = 100
        task.finished_at = datetime.now(timezone.utc)
        if proc.returncode == 0:
            task.status = "completed"
            _log(db, task.id, "info", "ingest task completed", summary)
        else:
            task.status = "failed"
            task.error = proc.stderr[-4000:] or proc.stdout[-4000:] or f"command exited {proc.returncode}"
            _log(db, task.id, "error", "ingest task failed", summary)
        db.commit()
        return task.summary or {}
    except Exception as exc:
        task = db.query(RagIngestTask).filter(RagIngestTask.id == task_id).first()
        if task:
            task.status = "failed"
            task.progress = 100
            task.error = f"{type(exc).__name__}: {exc}"
            task.finished_at = datetime.now(timezone.utc)
            db.add(RagIngestTaskLog(task_id=task.id, level="error", message=task.error))
            db.commit()
        raise
    finally:
        db.close()


def _read_eval_metrics(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"report_path": str(path)}
    if isinstance(data, dict):
        return data.get("metrics") or data.get("summary") or data
    return {"report_path": str(path)}


@celery_app.task(name="rag.admin_tasks.run_rag_eval")
def run_rag_eval(eval_run_id: int) -> dict:
    db = SessionLocal()
    try:
        run = db.query(RagEvalRun).filter(RagEvalRun.id == eval_run_id).first()
        if not run:
            raise ValueError(f"eval run not found: {eval_run_id}")
        EVAL_REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        out_path = EVAL_REPORT_ROOT / f"{run.run_uid}.json"
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        run.report_path = str(out_path)
        run.error = None
        db.commit()

        suite = Path(run.suite_path)
        if not suite.is_absolute():
            suite = BACKEND_ROOT / suite
        command = [sys.executable, "-m", "rag.eval.runner", "--suite", str(suite), "--out", str(out_path)]
        proc = subprocess.run(command, cwd=str(BACKEND_ROOT), text=True, capture_output=True, timeout=60 * 60)
        run.finished_at = datetime.now(timezone.utc)
        if proc.returncode == 0:
            run.status = "completed"
            run.metrics = _read_eval_metrics(out_path)
        else:
            run.status = "failed"
            run.error = proc.stderr[-4000:] or proc.stdout[-4000:] or f"eval exited {proc.returncode}"
        db.commit()
        return run.metrics or {"status": run.status}
    except Exception as exc:
        run = db.query(RagEvalRun).filter(RagEvalRun.id == eval_run_id).first()
        if run:
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
        raise
    finally:
        db.close()
