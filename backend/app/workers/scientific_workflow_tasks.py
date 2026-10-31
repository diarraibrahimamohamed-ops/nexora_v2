"""Celery executor for a claimed scientific workflow step."""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from app.database import SessionLocal
from app.models.scientific_workflow import ScientificWorkflowStep
from app.services.scientific_workflow_adapters import (
    WorkflowAdapterError,
    execute_adapter,
)
from app.services.scientific_workflow_engine import (
    WorkflowConflict,
    complete_step,
    fail_step,
    renew_step_lease,
)
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
LEASE_SECONDS = 300
HEARTBEAT_SECONDS = 20


def _heartbeat_loop(step_id: int, lease_token: str, stop: threading.Event, lost: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_SECONDS):
        db = SessionLocal()
        try:
            renew_step_lease(
                db, step_id, lease_token=lease_token, lease_seconds=LEASE_SECONDS
            )
            db.commit()
        except Exception:
            db.rollback()
            lost.set()
            logger.exception("Workflow step heartbeat lost for step %s", step_id)
            return
        finally:
            db.close()


def execute_claimed_step(step_id: int, lease_token: str, worker_id: str) -> dict[str, Any]:
    """Execute exactly the currently leased step; a duplicate Celery delivery is a no-op."""
    db = SessionLocal()
    try:
        step = db.query(ScientificWorkflowStep).filter(
            ScientificWorkflowStep.id == step_id
        ).with_for_update().first()
        if step is None:
            return {"step_id": step_id, "status": "missing"}
        if step.state != "running" or not lease_token or step.lease_token != lease_token:
            return {"step_id": step_id, "status": "stale_delivery"}
        now = datetime.utcnow()
        if step.lease_expires_at is None or step.lease_expires_at <= now:
            return {"step_id": step_id, "status": "expired_delivery"}
        if step.execution_started_at is not None:
            return {"step_id": step_id, "status": "duplicate_delivery"}
        step.execution_started_at = now
        step.heartbeat_at = now
        step.lease_owner = (worker_id or "scientific-workflow-worker")[:160]
        task_name = step.task_name
        args = list(step.args or [])
        kwargs = dict(step.kwargs or {})
        run_id = step.workflow_run_id
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    stop = threading.Event()
    lost = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(step_id, lease_token, stop, lost),
        name=f"workflow-heartbeat-{step_id}",
        daemon=True,
    )
    heartbeat.start()

    try:
        output = execute_adapter(task_name, args, kwargs)
        if lost.is_set():
            return {"step_id": step_id, "workflow_run_id": run_id, "status": "lease_lost"}
        db = SessionLocal()
        try:
            complete_step(
                db, step_id, lease_token=lease_token,
                output=output, now=datetime.utcnow(),
            )
            db.commit()
            return {
                "step_id": step_id,
                "workflow_run_id": run_id,
                "status": "completed",
                "output": output,
            }
        except WorkflowConflict:
            db.rollback()
            logger.warning("Workflow step %s completion rejected due to stale lease", step_id)
            return {"step_id": step_id, "workflow_run_id": run_id, "status": "stale_completion"}
        finally:
            db.close()
    except WorkflowAdapterError as exc:
        if lost.is_set():
            return {"step_id": step_id, "workflow_run_id": run_id, "status": "lease_lost"}
        db = SessionLocal()
        try:
            fail_step(
                db, step_id, lease_token=lease_token,
                error_code=exc.code, error_message=str(exc),
                retryable=exc.retryable, now=datetime.utcnow(),
            )
            db.commit()
            return {
                "step_id": step_id, "workflow_run_id": run_id,
                "status": "retry_wait" if exc.retryable else "failed",
                "error_code": exc.code,
            }
        except WorkflowConflict:
            db.rollback()
            return {"step_id": step_id, "workflow_run_id": run_id, "status": "stale_failure"}
        finally:
            db.close()
    except Exception as exc:
        if lost.is_set():
            return {"step_id": step_id, "workflow_run_id": run_id, "status": "lease_lost"}
        db = SessionLocal()
        try:
            fail_step(
                db, step_id, lease_token=lease_token,
                error_code=type(exc).__name__,
                error_message=str(exc)[:4000],
                retryable=False,
                now=datetime.utcnow(),
            )
            db.commit()
            logger.exception("Scientific workflow step %s failed", step_id)
            return {
                "step_id": step_id, "workflow_run_id": run_id,
                "status": "failed", "error_code": type(exc).__name__,
            }
        except WorkflowConflict:
            db.rollback()
            return {"step_id": step_id, "workflow_run_id": run_id, "status": "stale_failure"}
        finally:
            db.close()
    finally:
        stop.set()
        heartbeat.join(timeout=HEARTBEAT_SECONDS + 2)


@celery_app.task(
    bind=True,
    name="scientific_workflow.execute_step",
    max_retries=0,
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=7200,
    soft_time_limit=7100,
)
def execute_scientific_workflow_step(self, step_id: int, lease_token: str) -> dict:
    request = getattr(self, "request", None)
    worker_id = getattr(request, "hostname", None) or "scientific-workflow-worker"
    return execute_claimed_step(step_id, lease_token, worker_id)
