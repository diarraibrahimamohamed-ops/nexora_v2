"""Celery executor adapter for allow-listed scientific workflow steps.

Legacy scientific task implementations remain the calculation engines. This wrapper
adds workflow leases, heartbeat renewal, result verification and auditable completion.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import threading
import time
from datetime import datetime
from typing import Any

from app.database import SessionLocal
from app.models.scientific_workflow import ScientificWorkflowStep
from app.services.scientific_workflow_engine import (
    WorkflowConflict,
    begin_step_execution,
    complete_step,
    fail_step,
    renew_step_lease,
)
from app.services.scientific_workflow_policy import TASK_CONTRACTS
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

WORKFLOW_EXECUTOR_TASK = "scientific_workflow.execute_step"
LEASE_SECONDS = 300
HEARTBEAT_SECONDS = 60

TASK_MODULES = {
    "analysis.run": "app.workers.analysis_tasks",
    "analysis.run_afribio_alignment": "app.workers.analysis_tasks",
    "analysis.run_afribio_phylo": "app.workers.analysis_tasks",
    "docking.run_vina": "app.workers.docking_tasks",
    "hsa.run": "app.workers.hsa_tasks",
    "rna_structure.predict_secondary": "app.workers.rna_structure_tasks",
    "research.resolve_structure": "app.workers.research_structure_tasks",
}

TASK_RESULT_SUMMARIES = {
    "analysis.run": ("analysis_id", "status"),
    "analysis.run_afribio_alignment": ("job_id", "status", "metrics"),
    "analysis.run_afribio_phylo": ("job_id", "status"),
    "docking.run_vina": ("job_id", "status", "num_poses", "docking_score"),
    "hsa.run": ("job_id", "status"),
    "rna_structure.predict_secondary": ("job_id", "success", "artifact"),
    "research.resolve_structure": ("job_id", "success", "target_id", "structure_path"),
}

TASK_RESULT_REFERENCES = {
    "analysis.run": "analysis",
    "docking.run_vina": "docking_job",
    "hsa.run": "hsa_job",
    "rna_structure.predict_secondary": "rna_structure_job",
    "research.resolve_structure": "research_structure_job",
}


class WorkflowTaskFailure(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def _load_registered_task(task_name: str):
    contract = TASK_CONTRACTS.get(task_name)
    module_name = TASK_MODULES.get(task_name)
    if contract is None or module_name is None:
        raise WorkflowTaskFailure(f"Task '{task_name}' is not allowed by the scientific registry.")
    importlib.import_module(module_name)
    task = celery_app.tasks.get(task_name)
    if task is None:
        raise WorkflowTaskFailure(f"Registered Celery task '{task_name}' is unavailable.")
    return contract, task


def _invoke_scientific_task(task_name: str, args: list, kwargs: dict, task_id: str):
    """Run an existing Celery task in-process; avoid nested queue waits/deadlocks."""
    contract, task = _load_registered_task(task_name)
    eager_result = task.apply(
        args=args,
        kwargs=kwargs,
        task_id=task_id,
        throw=False,
    )
    if eager_result.state == "RETRY":
        raise WorkflowTaskFailure(
            f"Underlying task requested retry: {type(eager_result.result).__name__}",
            retryable=True,
        )
    if eager_result.state in {"FAILURE", "REVOKED"}:
        error = eager_result.result
        raise WorkflowTaskFailure(
            f"Underlying task {task_name} failed: {type(error).__name__}",
            retryable=True,
        )
    if eager_result.state != "SUCCESS":
        raise WorkflowTaskFailure(
            f"Underlying task {task_name} ended in unexpected state {eager_result.state!r}."
        )

    result = eager_result.result
    if not isinstance(result, dict):
        raise WorkflowTaskFailure(
            f"Underlying task {task_name} must return a JSON object, got {type(result).__name__}."
        )
    if result.get("status") in {"failed", "error"} or result.get("success") is False:
        raise WorkflowTaskFailure(
            str(result.get("error") or result.get("message") or f"{task_name} returned failure"),
            retryable=False,
        )
    if result.get("status") != "completed" and result.get("success") is not True:
        raise WorkflowTaskFailure(
            f"Underlying task {task_name} returned no explicit success status."
        )
    return result


def _json_default(value):
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"Unsupported workflow result value: {type(value).__name__}")


def _summarize_result(task_name: str, result: dict) -> dict:
    canonical = json.dumps(
        result, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False, default=_json_default,
    )
    result_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    selected = TASK_RESULT_SUMMARIES.get(task_name, ())
    summary = {key: result[key] for key in selected if key in result}
    reference_kind = TASK_RESULT_REFERENCES.get(task_name)
    reference_id = result.get("analysis_id") or result.get("job_id")
    return {
        "task_name": task_name,
        "result_sha256": result_sha256,
        "summary": summary,
        "result_reference": (
            {"object_type": reference_kind, "object_id": str(reference_id)}
            if reference_kind and reference_id is not None else None
        ),
    }


class _LeaseHeartbeat(threading.Thread):
    def __init__(self, step_id: int, lease_token: str):
        super().__init__(name=f"workflow-step-heartbeat-{step_id}", daemon=True)
        self.step_id = step_id
        self.lease_token = lease_token
        self.stop_event = threading.Event()
        self.lease_lost = False

    def stop(self):
        self.stop_event.set()

    def run(self):
        while not self.stop_event.wait(HEARTBEAT_SECONDS):
            db = SessionLocal()
            try:
                renew_step_lease(
                    db,
                    self.step_id,
                    lease_token=self.lease_token,
                    lease_seconds=LEASE_SECONDS,
                )
                db.commit()
            except Exception as exc:
                db.rollback()
                self.lease_lost = True
                logger.warning(
                    "Workflow step lease lost step=%s error=%s",
                    self.step_id, type(exc).__name__,
                )
                self.stop_event.set()
            finally:
                db.close()


def _finalize_failure(step_id: int, lease_token: str, exc: Exception, retryable: bool) -> dict:
    db = SessionLocal()
    try:
        failed = fail_step(
            db,
            step_id,
            lease_token=lease_token,
            error_code=type(exc).__name__,
            error_message=str(exc)[:4000],
            retryable=retryable,
        )
        db.commit()
        return {
            "success": False,
            "step_id": step_id,
            "state": failed.state,
            "retryable": failed.state == "retry_wait",
            "error": type(exc).__name__,
        }
    except WorkflowConflict:
        db.rollback()
        return {"success": False, "step_id": step_id, "state": "lease_lost"}
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name=WORKFLOW_EXECUTOR_TASK,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0,
)
def execute_scientific_workflow_step(self, step_id: int, lease_token: str) -> dict:
    """Execute one claimed step, fenced by its lease and a one-delivery start marker."""
    executor_id = (getattr(self.request, "hostname", None) or "celery-worker")[:160]
    db = SessionLocal()
    try:
        step = db.query(ScientificWorkflowStep).filter(
            ScientificWorkflowStep.id == step_id
        ).first()
        if step is None:
            return {"success": False, "step_id": step_id, "state": "missing"}
        task_name = step.task_name
        args = list(step.args or [])
        kwargs = dict(step.kwargs or {})
        contract, _ = _load_registered_task(task_name)
        if step.queue_name != contract.queue_name:
            raise WorkflowTaskFailure("Workflow step queue does not match its registered task contract.")
        started_step, replay = begin_step_execution(
            db,
            step_id,
            lease_token=lease_token,
            executor_id=executor_id,
        )
        if replay:
            db.commit()
            return {"success": True, "step_id": step_id, "state": "already_executing"}
        attempt = started_step.attempts
        db.commit()
    except WorkflowConflict:
        db.rollback()
        return {"success": False, "step_id": step_id, "state": "stale_delivery"}
    except Exception as exc:
        db.rollback()
        logger.exception("Workflow step could not start id=%s", step_id)
        return _finalize_failure(step_id, lease_token, exc, retryable=False)
    finally:
        db.close()

    heartbeat = _LeaseHeartbeat(step_id, lease_token)
    heartbeat.start()
    try:
        result = _invoke_scientific_task(
            task_name,
            args,
            kwargs,
            task_id=f"workflow-step-{step_id}-attempt-{attempt}",
        )
        heartbeat.stop()
        heartbeat.join(timeout=2)
        if heartbeat.lease_lost:
            return {"success": False, "step_id": step_id, "state": "lease_lost"}

        output = _summarize_result(task_name, result)
        db = SessionLocal()
        try:
            completed = complete_step(
                db, step_id, lease_token=lease_token, output=output
            )
            db.commit()
            return {
                "success": True,
                "step_id": step_id,
                "state": completed.state,
                "output_sha256": output["result_sha256"],
            }
        finally:
            db.close()

    except WorkflowTaskFailure as exc:
        heartbeat.stop()
        heartbeat.join(timeout=2)
        if heartbeat.lease_lost:
            return {"success": False, "step_id": step_id, "state": "lease_lost"}
        return _finalize_failure(step_id, lease_token, exc, retryable=exc.retryable)
    except Exception as exc:
        heartbeat.stop()
        heartbeat.join(timeout=2)
        if heartbeat.lease_lost:
            return {"success": False, "step_id": step_id, "state": "lease_lost"}
        logger.exception("Scientific workflow adapter failed step=%s", step_id)
        return _finalize_failure(step_id, lease_token, exc, retryable=True)
    finally:
        heartbeat.stop()


def execute_task_locally_for_test(task_name: str, args: list, kwargs: dict | None = None):
    """Small seam for unit tests; production execution uses the worker task above."""
    task = _load_registered_task(task_name)[1]
    eager = task.apply(args=args, kwargs=kwargs or {}, throw=False)
    return eager.state, eager.result
