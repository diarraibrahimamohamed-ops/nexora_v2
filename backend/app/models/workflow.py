"""Persistent workflow audit events and transactional task outbox."""
from datetime import datetime

from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint,
)
from app.database import Base


class ResearchWorkflowEvent(Base):
    __tablename__ = "research_workflow_events"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(
        Integer, ForeignKey("research_studies.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    actor_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    from_state = Column(String(40), nullable=True)
    to_state = Column(String(40), nullable=False)
    action = Column(String(60), nullable=False)
    reason = Column(Text, nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    idempotency_key = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("study_id", "idempotency_key", name="uq_research_workflow_event_idempotency"),
        Index("ix_research_workflow_event_study_time", "study_id", "created_at"),
    )


class ResearchTaskOutbox(Base):
    """At-least-once publication records for Celery tasks.

    The outbox makes task intent atomic with the database transaction that
    creates the underlying job. Consumers must therefore be idempotent.
    """
    __tablename__ = "research_task_outbox"

    id = Column(Integer, primary_key=True, index=True)
    event_key = Column(String(64), nullable=False, unique=True, index=True)
    task_name = Column(String(160), nullable=False)
    queue_name = Column(String(80), nullable=False)
    args = Column(JSON, nullable=False, default=list)
    kwargs = Column(JSON, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    locked_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    last_error = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_research_task_outbox_due", "status", "available_at"),
    )
