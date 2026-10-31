"""Generic scientific workflow run, step, dependency and event models.

These records are deliberately separate from ResearchStudy and legacy job tables.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer,
    JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class ScientificWorkflowRun(Base):
    __tablename__ = "scientific_workflow_runs"

    id = Column(Integer, primary_key=True, index=True)
    workflow_key = Column(String(120), nullable=False, index=True)
    definition_version = Column(String(40), nullable=False)
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    scope_type = Column(String(60), nullable=True, index=True)
    scope_id = Column(String(120), nullable=True, index=True)

    state = Column(String(40), nullable=False, default="created", index=True)
    resume_state = Column(String(40), nullable=True)
    state_version = Column(Integer, nullable=False, default=0)
    requires_quality_review = Column(Boolean, nullable=False, default=True)

    context = Column(JSON, nullable=False, default=dict)
    definition_snapshot = Column(JSON, nullable=False, default=dict)
    input_manifest = Column(JSON, nullable=False, default=dict)
    output_manifest = Column(JSON, nullable=False, default=dict)

    idempotency_key = Column(String(128), nullable=True)
    failure_code = Column(String(120), nullable=True)
    failure_message = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    steps = relationship(
        "ScientificWorkflowStep", back_populates="run",
        cascade="all, delete-orphan", order_by="ScientificWorkflowStep.id",
    )
    events = relationship(
        "ScientificWorkflowEvent", back_populates="run",
        cascade="all, delete-orphan", order_by="ScientificWorkflowEvent.id",
    )

    __table_args__ = (
        UniqueConstraint("owner_user_id", "idempotency_key", name="uq_scientific_workflow_run_owner_idempotency"),
        Index("ix_scientific_workflow_run_scope", "scope_type", "scope_id"),
    )


class ScientificWorkflowStep(Base):
    __tablename__ = "scientific_workflow_steps"

    id = Column(Integer, primary_key=True, index=True)
    workflow_run_id = Column(
        Integer, ForeignKey("scientific_workflow_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    step_key = Column(String(120), nullable=False)
    task_name = Column(String(160), nullable=False)
    queue_name = Column(String(80), nullable=False)
    state = Column(String(20), nullable=False, default="pending", index=True)

    args = Column(JSON, nullable=False, default=list)
    kwargs = Column(JSON, nullable=False, default=dict)
    max_attempts = Column(Integer, nullable=False, default=3)
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    lease_owner = Column(String(160), nullable=True)
    lease_token = Column(String(64), nullable=True, index=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    heartbeat_at = Column(DateTime, nullable=True)

    output = Column(JSON, nullable=True)
    error_code = Column(String(120), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    run = relationship("ScientificWorkflowRun", back_populates="steps")
    dependencies = relationship(
        "ScientificWorkflowStepDependency",
        foreign_keys="ScientificWorkflowStepDependency.step_id",
        back_populates="step", cascade="all, delete-orphan",
    )
    dependents = relationship(
        "ScientificWorkflowStepDependency",
        foreign_keys="ScientificWorkflowStepDependency.depends_on_step_id",
        back_populates="depends_on", cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("workflow_run_id", "step_key", name="uq_scientific_workflow_step_key"),
        CheckConstraint("max_attempts >= 1", name="ck_scientific_workflow_step_max_attempts"),
        CheckConstraint("attempts >= 0", name="ck_scientific_workflow_step_attempts"),
        Index("ix_scientific_workflow_step_due", "workflow_run_id", "state", "available_at"),
    )


class ScientificWorkflowStepDependency(Base):
    __tablename__ = "scientific_workflow_step_dependencies"

    id = Column(Integer, primary_key=True, index=True)
    workflow_run_id = Column(
        Integer, ForeignKey("scientific_workflow_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    step_id = Column(
        Integer, ForeignKey("scientific_workflow_steps.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    depends_on_step_id = Column(
        Integer, ForeignKey("scientific_workflow_steps.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    step = relationship(
        "ScientificWorkflowStep", foreign_keys=[step_id], back_populates="dependencies",
    )
    depends_on = relationship(
        "ScientificWorkflowStep", foreign_keys=[depends_on_step_id], back_populates="dependents",
    )

    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id", "step_id", "depends_on_step_id",
            name="uq_scientific_workflow_step_dependency",
        ),
        CheckConstraint("step_id <> depends_on_step_id", name="ck_scientific_workflow_no_self_dependency"),
    )


class ScientificWorkflowEvent(Base):
    __tablename__ = "scientific_workflow_events"

    id = Column(Integer, primary_key=True, index=True)
    workflow_run_id = Column(
        Integer, ForeignKey("scientific_workflow_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    step_id = Column(
        Integer, ForeignKey("scientific_workflow_steps.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type = Column(String(60), nullable=False)
    from_state = Column(String(40), nullable=True)
    to_state = Column(String(40), nullable=True)
    action = Column(String(60), nullable=True)
    reason = Column(Text, nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    idempotency_key = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    run = relationship("ScientificWorkflowRun", back_populates="events")

    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id", "idempotency_key",
            name="uq_scientific_workflow_event_idempotency",
        ),
        Index("ix_scientific_workflow_event_run_time", "workflow_run_id", "created_at"),
    )
