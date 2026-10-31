"""Transactional outbox for reliable, at-least-once Celery publication."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, or_

from app.database import SessionLocal
from app.models.workflow import ResearchTaskOutbox
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
STALE_LOCK_SECONDS = 120
MAX_BACKOFF_SECONDS = 300


def enqueue_task(
    db,
    *,
    task_name: str,
    args: list[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
    queue: str,
) -> ResearchTaskOutbox:
    """Add a task intent to the caller's transaction; do not commit here."""
    event = ResearchTaskOutbox(
        event_key=uuid4().hex,
        task_name=task_name,
        queue_name=queue,
        args=list(args or []),
        kwargs=dict(kwargs or {}),
        status="pending",
        attempts=0,
        available_at=datetime.utcnow(),
    )
    db.add(event)
    db.flush()
    return event


def dispatch_once(limit: int = 25, sender=None) -> int:
    """Publish one batch and return the number successfully published.

    Rows are claimed transactionally with SKIP LOCKED. A stale publishing row
    is eligible again after a dispatcher crash. Since a crash can happen after
    broker acceptance but before the database acknowledgement, delivery is
    intentionally at-least-once; task IDs are stable and consumers should be
    idempotent.
    """
    sender = sender or celery_app.send_task
    now = datetime.utcnow()
    stale_before = now - timedelta(seconds=STALE_LOCK_SECONDS)
    db = SessionLocal()
    try:
        rows = (
            db.query(ResearchTaskOutbox)
            .filter(
                or_(
                    and_(ResearchTaskOutbox.status == "pending", ResearchTaskOutbox.available_at <= now),
                    and_(ResearchTaskOutbox.status == "publishing", ResearchTaskOutbox.locked_at < stale_before),
                )
            )
            .order_by(ResearchTaskOutbox.id.asc())
            .with_for_update(skip_locked=True)
            .limit(max(1, min(int(limit), 100)))
            .all()
        )
        if not rows:
            db.rollback()
            return 0

        claimed = []
        for row in rows:
            row.status = "publishing"
            row.locked_at = now
            row.attempts = int(row.attempts or 0) + 1
            claimed.append({
                "id": row.id,
                "event_key": row.event_key,
                "task_name": row.task_name,
                "queue_name": row.queue_name,
                "args": row.args or [],
                "kwargs": row.kwargs or {},
                "attempts": row.attempts,
            })
        db.commit()

        published = 0
        for item in claimed:
            try:
                sender(
                    item["task_name"],
                    args=item["args"],
                    kwargs=item["kwargs"],
                    queue=item["queue_name"],
                    task_id=item["event_key"],
                )
            except Exception as exc:
                delay = min(MAX_BACKOFF_SECONDS, 2 ** min(item["attempts"], 8))
                row = db.query(ResearchTaskOutbox).filter(ResearchTaskOutbox.id == item["id"]).first()
                if row:
                    row.status = "pending"
                    row.locked_at = None
                    row.available_at = datetime.utcnow() + timedelta(seconds=delay)
                    row.last_error = type(exc).__name__
                    db.commit()
                logger.warning("Outbox publication failed for event %s (%s)", item["event_key"], type(exc).__name__)
                continue

            row = db.query(ResearchTaskOutbox).filter(ResearchTaskOutbox.id == item["id"]).first()
            if row:
                row.status = "published"
                row.locked_at = None
                row.published_at = datetime.utcnow()
                row.last_error = None
                db.commit()
                published += 1
        return published
    finally:
        db.close()


def run_dispatcher(poll_seconds: float = 2.0) -> None:
    """Long-running process used by the dedicated Docker Compose service."""
    logger.info("N3XORA task outbox dispatcher started")
    while True:
        try:
            from app.services.scientific_workflow_scheduler import dispatch_ready_workflow_steps
            dispatch_ready_workflow_steps()
        except Exception:
            logger.exception("Scientific workflow scheduling cycle failed")
        try:
            dispatch_once()
        except Exception:
            logger.exception("Task outbox publication cycle failed")
        time.sleep(max(0.5, poll_seconds))
