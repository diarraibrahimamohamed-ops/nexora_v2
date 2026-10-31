"""Poll ready DAG steps and atomically enqueue the dedicated workflow executor."""
from __future__ import annotations

import logging
import os
from datetime import datetime

from app.database import SessionLocal
from app.models.scientific_workflow import ScientificWorkflowRun
from app.models.scientific_workflow import ScientificWorkflowStep
from app.services.scientific_workflow_engine import claim_ready_step
from app.services.task_outbox import enqueue_task

logger = logging.getLogger(__name__)
EXECUTOR_TASK = "scientific_workflow.execute_step"
EXECUTOR_QUEUE = "workflows"


def dispatch_ready_workflow_steps(
    *,
    limit: int = 25,
    worker_id: str | None = None,
    lease_seconds: int = 300,
    now: datetime | None = None,
) -> int:
    """Claim DAG-ready steps and enqueue executor messages in the same transaction.

    The outbox row and step lease are committed atomically. Duplicate dispatcher
    processes coordinate using PostgreSQL row locks and SKIP LOCKED.
    """
    db = SessionLocal()
    dispatched = 0
    scheduler_id = (worker_id or os.getenv("HOSTNAME") or "workflow-dispatcher")[:160]
    try:
        candidates = (
            db.query(ScientificWorkflowRun)
            .filter(ScientificWorkflowRun.state.in_([
                "ready", "queued", "running.preparing", "running.executing",
            ]))
            .order_by(ScientificWorkflowRun.id.asc())
            .with_for_update(skip_locked=True)
            .limit(max(1, min(int(limit), 100)))
            .all()
        )
        for run in candidates:
            run_id = run.id
            for _ in range(max(1, limit - dispatched)):
                if dispatched >= max(1, min(int(limit), 100)):
                    break
                try:
                    step = claim_ready_step(
                        db,
                        run_id,
                        worker_id=scheduler_id,
                        lease_seconds=lease_seconds,
                        now=now,
                    )
                    if step is None:
                        # claim_ready_step can mark descendants skipped when a parent failed.
                        db.commit()
                        break
                    event = enqueue_task(
                        db,
                        task_name=EXECUTOR_TASK,
                        args=[step.id, step.lease_token],
                        queue=EXECUTOR_QUEUE,
                    )
                    db.commit()
                    dispatched += 1
                    logger.info(
                        "Workflow step dispatched run=%s step=%s event=%s",
                        run_id, step.step_key, event.event_key,
                    )
                except Exception:
                    db.rollback()
                    logger.exception("Could not dispatch ready workflow run %s", run_id)
                    break
        return dispatched
    finally:
        db.close()
