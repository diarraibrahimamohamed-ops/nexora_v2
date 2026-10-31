"""Polling dispatcher: claim ready DAG steps and publish execution through the outbox."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime

from sqlalchemy import and_, or_

from app.database import SessionLocal
from app.models.scientific_workflow import ScientificWorkflowRun, ScientificWorkflowStep
from app.services.scientific_workflow_engine import claim_ready_step
from app.services.task_outbox import enqueue_task

logger = logging.getLogger(__name__)
EXECUTOR_TASK = "scientific_workflow.execute_step"
MAX_STEPS_PER_CYCLE = 40
LEASE_SECONDS = 300


def dispatch_ready_workflows_once(
    *,
    max_runs: int = 100,
    max_steps_per_run: int = 4,
) -> int:
    """Claim due DAG steps and atomically persist their Celery outbox events.

    A step moves to running and its execute-step outbox event is inserted in the
    same PostgreSQL transaction. If the broker is unavailable, the outbox
    dispatcher retries publication; if a worker disappears, the lease expires
    and this dispatcher can reclaim the step with a new fencing token.
    """
    now = datetime.utcnow()
    scan = SessionLocal()
    try:
        due_runs = (
            scan.query(ScientificWorkflowStep.workflow_run_id)
            .join(
                ScientificWorkflowRun,
                ScientificWorkflowRun.id == ScientificWorkflowStep.workflow_run_id,
            )
            .filter(
                ScientificWorkflowRun.state.notin_(
                    ["blocked", "needs_user_action", "succeeded", "failed", "cancelled"]
                ),
                or_(
                    and_(
                        ScientificWorkflowStep.state.in_(["pending", "retry_wait"]),
                        ScientificWorkflowStep.available_at <= now,
                    ),
                    and_(
                        ScientificWorkflowStep.state == "running",
                        ScientificWorkflowStep.lease_expires_at <= now,
                    ),
                ),
            )
            .distinct()
            .order_by(ScientificWorkflowStep.workflow_run_id.asc())
            .limit(max(1, min(int(max_runs), 1000)))
            .all()
        )
        run_ids = [int(row[0]) for row in due_runs]
    finally:
        scan.close()

    published_intents = 0
    worker_id = f"workflow-dispatcher:{os.getpid()}"
    for run_id in run_ids:
        for _ in range(max(1, min(int(max_steps_per_run), MAX_STEPS_PER_CYCLE))):
            db = SessionLocal()
            try:
                step = claim_ready_step(
                    db,
                    run_id,
                    worker_id=worker_id,
                    lease_seconds=LEASE_SECONDS,
                    now=datetime.utcnow(),
                )
                if step is None:
                    # Persist lease expiry events and dependency-failure skips.
                    db.commit()
                    break
                event = enqueue_task(
                    db,
                    task_name=EXECUTOR_TASK,
                    args=[step.id, step.lease_token],
                    queue=step.queue_name,
                )
                db.commit()
                logger.info(
                    "Queued scientific workflow step run=%s step=%s key=%s queue=%s event=%s",
                    run_id, step.id, step.step_key, step.queue_name, event.event_key,
                )
                published_intents += 1
            except Exception:
                db.rollback()
                logger.exception("Could not schedule workflow run %s", run_id)
                break
            finally:
                db.close()
    return published_intents


def run_dispatcher(poll_seconds: float = 2.0) -> None:
    logger.info("N3XORA scientific workflow dispatcher started")
    while True:
        try:
            dispatch_ready_workflows_once()
        except Exception:
            logger.exception("Scientific workflow dispatch cycle failed")
        time.sleep(max(0.5, float(poll_seconds)))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_dispatcher()
