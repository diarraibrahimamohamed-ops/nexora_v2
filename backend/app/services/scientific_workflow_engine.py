"""Transactional operations for generic HFSM/DAG scientific workflow runs."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.scientific_workflow import (
    ScientificWorkflowEvent,
    ScientificWorkflowRun,
    ScientificWorkflowStep,
    ScientificWorkflowStepDependency,
)
from app.services.scientific_workflow_policy import (
    WorkflowDefinition,
    resolve_transition,
    snapshot_definition,
    validate_dag,
)

MAX_LEASE_SECONDS = 3600
MIN_LEASE_SECONDS = 10
MAX_BACKOFF_SECONDS = 900


class WorkflowConflict(RuntimeError):
    """Raised when an execution transition cannot safely be applied."""


class InvalidWorkflow(RuntimeError):
    """Raised when a workflow definition or execution payload is invalid."""


def _json_payload(value: Any, label: str) -> Any:
    import json

    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InvalidWorkflow(f"{label} must be JSON serializable") from exc
    return value


def _event(
    db: Session,
    run: ScientificWorkflowRun,
    *,
    event_type: str,
    step: ScientificWorkflowStep | None = None,
    actor_user_id: int | None = None,
    from_state: str | None = None,
    to_state: str | None = None,
    action: str | None = None,
    reason: str | None = None,
    payload: dict | None = None,
    idempotency_key: str | None = None,
) -> ScientificWorkflowEvent:
    event = ScientificWorkflowEvent(
        workflow_run_id=run.id,
        step_id=step.id if step else None,
        actor_user_id=actor_user_id,
        event_type=event_type,
        from_state=from_state,
        to_state=to_state,
        action=action,
        reason=reason,
        payload=payload or {},
        idempotency_key=idempotency_key,
    )
    db.add(event)
    db.flush()
    return event


def create_workflow_run(
    db: Session,
    definition: WorkflowDefinition,
    *,
    owner_user_id: int | None,
    context: Mapping[str, Any] | None = None,
    scope_type: str | None = None,
    scope_id: str | int | None = None,
    input_manifest: Mapping[str, Any] | None = None,
    step_inputs: Mapping[str, Mapping[str, Any]] | None = None,
    idempotency_key: str | None = None,
) -> tuple[ScientificWorkflowRun, bool]:
    """Create a run and its immutable definition snapshot in the caller transaction.

    The definition is code-registered and task names/queues are already allow-listed
    by the policy module. Runtime inputs may supply args/kwargs but cannot select a
    different task or queue.
    """
    order = validate_dag(definition.steps)
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 128:
            raise InvalidWorkflow("idempotency_key must contain 1 to 128 characters")
        existing = db.query(ScientificWorkflowRun).filter(
            ScientificWorkflowRun.owner_user_id == owner_user_id,
            ScientificWorkflowRun.idempotency_key == idempotency_key,
        ).first()
        if existing:
            return existing, True

    safe_context = _json_payload(dict(context or {}), "context")
    safe_manifest = _json_payload(dict(input_manifest or {}), "input_manifest")
    provided_inputs = dict(step_inputs or {})
    step_keys = {step.key for step in definition.steps}
    unknown_inputs = set(provided_inputs) - step_keys
    if unknown_inputs:
        raise InvalidWorkflow(f"step input provided for unknown step(s): {', '.join(sorted(unknown_inputs))}")

    run = ScientificWorkflowRun(
        workflow_key=definition.key,
        definition_version=definition.version,
        owner_user_id=owner_user_id,
        scope_type=scope_type,
        scope_id=str(scope_id) if scope_id is not None else None,
        state="ready",
        state_version=0,
        requires_quality_review=definition.requires_quality_review,
        context=safe_context,
        definition_snapshot=snapshot_definition(definition),
        input_manifest=safe_manifest,
        output_manifest={},
        idempotency_key=idempotency_key,
    )
    db.add(run)
    db.flush()

    step_rows: dict[str, ScientificWorkflowStep] = {}
    for step_definition in definition.steps:
        runtime_input = dict(provided_inputs.get(step_definition.key) or {})
        if set(runtime_input) - {"args", "kwargs"}:
            raise InvalidWorkflow(
                f"runtime input for '{step_definition.key}' may only contain args/kwargs"
            )
        args = _json_payload(runtime_input.get("args", []), f"{step_definition.key}.args")
        kwargs = _json_payload(runtime_input.get("kwargs", {}), f"{step_definition.key}.kwargs")
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise InvalidWorkflow(f"args must be a list and kwargs a dictionary for '{step_definition.key}'")

        row = ScientificWorkflowStep(
            workflow_run_id=run.id,
            step_key=step_definition.key,
            task_name=step_definition.task_name,
            queue_name=step_definition.queue_name,
            state="pending",
            args=args,
            kwargs=kwargs,
            max_attempts=step_definition.max_attempts,
            attempts=0,
            available_at=datetime.utcnow(),
        )
        db.add(row)
        step_rows[step_definition.key] = row
    db.flush()

    for step_definition in definition.steps:
        for dependency_key in step_definition.depends_on:
            db.add(ScientificWorkflowStepDependency(
                workflow_run_id=run.id,
                step_id=step_rows[step_definition.key].id,
                depends_on_step_id=step_rows[dependency_key].id,
            ))

    _event(
        db, run,
        event_type="run_created",
        to_state="ready",
        payload={
            "workflow_key": definition.key,
            "definition_version": definition.version,
            "topological_order": order,
            "step_count": len(step_rows),
        },
        idempotency_key=f"run-created:{run.id}",
    )
    db.flush()
    return run, False


def transition_workflow_run(
    db: Session,
    run_id: int,
    *,
    action: str,
    actor_user_id: int | None = None,
    reason: str | None = None,
    expected_state_version: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[ScientificWorkflowRun, bool]:
    """Apply one version-checked HFSM transition and append its audit event."""
    run = db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == run_id
    ).with_for_update().first()
    if run is None:
        raise WorkflowConflict("workflow run not found")

    if idempotency_key:
        if len(idempotency_key) > 128:
            raise InvalidWorkflow("idempotency_key is limited to 128 characters")
        replay = db.query(ScientificWorkflowEvent).filter(
            ScientificWorkflowEvent.workflow_run_id == run_id,
            ScientificWorkflowEvent.idempotency_key == idempotency_key,
        ).first()
        if replay:
            return run, True

    if expected_state_version is not None and run.state_version != expected_state_version:
        raise WorkflowConflict(
            f"stale state version: expected {expected_state_version}, current {run.state_version}"
        )

    requested = (action or "").strip().lower()
    if requested in {"block", "cancel", "reject", "fail"} and not (reason or "").strip():
        raise InvalidWorkflow(f"a reason is required for action '{requested}'")

    decision = resolve_transition(run.state, requested, resume_state=run.resume_state)
    if not decision.allowed or decision.to_state is None:
        raise WorkflowConflict(decision.reason or "transition rejected")

    old_state = run.state
    if requested == "block":
        run.resume_state = old_state
    elif requested == "resume":
        run.resume_state = None

    run.state = decision.to_state
    run.state_version += 1
    run.updated_at = datetime.utcnow()
    if run.state.startswith("running.") and run.started_at is None:
        run.started_at = datetime.utcnow()
    if run.state in {"succeeded", "failed", "cancelled"}:
        run.completed_at = datetime.utcnow()
    if run.state == "failed":
        run.failure_code = run.failure_code or "WORKFLOW_FAILED"
        run.failure_message = (reason or "").strip() or run.failure_message

    if requested == "cancel":
        steps = db.query(ScientificWorkflowStep).filter(
            ScientificWorkflowStep.workflow_run_id == run.id,
            ScientificWorkflowStep.state.notin_(["succeeded", "failed", "cancelled", "skipped"]),
        ).with_for_update().all()
        for step in steps:
            old_step_state = step.state
            step.state = "cancelled"
            step.lease_owner = step.lease_token = None
            step.lease_expires_at = step.heartbeat_at = None
            step.completed_at = datetime.utcnow()
            _event(
                db, run, event_type="step_cancelled", step=step,
                from_state=old_step_state, to_state="cancelled",
                reason=(reason or "").strip(),
            )

    _event(
        db, run,
        event_type="run_transition",
        actor_user_id=actor_user_id,
        from_state=old_state,
        to_state=run.state,
        action=requested,
        reason=(reason or "").strip() or None,
        payload={"state_version": run.state_version},
        idempotency_key=idempotency_key,
    )
    db.flush()
    return run, False


def _verify_lease(
    step: ScientificWorkflowStep,
    lease_token: str,
    now: datetime,
) -> None:
    if step.state != "running":
        raise WorkflowConflict(f"step '{step.step_key}' is not running")
    if not lease_token or step.lease_token != lease_token:
        raise WorkflowConflict("stale or invalid step lease token")
    if step.lease_expires_at is None or step.lease_expires_at <= now:
        raise WorkflowConflict("step lease has expired")


def reclaim_expired_leases(
    db: Session,
    run_id: int,
    *,
    now: datetime | None = None,
) -> int:
    """Move expired running steps to retry_wait or failed; callers commit."""
    now = now or datetime.utcnow()
    rows = db.query(ScientificWorkflowStep).filter(
        ScientificWorkflowStep.workflow_run_id == run_id,
        ScientificWorkflowStep.state == "running",
        ScientificWorkflowStep.lease_expires_at <= now,
    ).with_for_update(skip_locked=True).all()

    reclaimed = 0
    for step in rows:
        old_token = step.lease_token
        old_state = step.state
        step.lease_owner = step.lease_token = None
        step.lease_expires_at = step.heartbeat_at = None
        step.error_code = "LEASE_EXPIRED"
        step.error_message = "Worker lease expired before completion."
        if step.attempts < step.max_attempts:
            step.state = "retry_wait"
            delay = min(MAX_BACKOFF_SECONDS, 2 ** min(step.attempts, 9))
            step.available_at = now + timedelta(seconds=delay)
        else:
            step.state = "failed"
            step.completed_at = now
        run = db.query(ScientificWorkflowRun).filter(
            ScientificWorkflowRun.id == run_id
        ).with_for_update().first()
        if run:
            if step.state == "failed" and run.state not in {"failed", "cancelled", "succeeded"}:
                run.state = "failed"
                run.state_version += 1
                run.failure_code = "STEP_LEASE_EXHAUSTED"
                run.failure_message = f"Step '{step.step_key}' exhausted its attempts after lease expiry."
                run.completed_at = now
                _event(
                    db, run, event_type="run_failed",
                    from_state=old_state, to_state="failed",
                    reason=run.failure_message,
                )
            _event(
                db, run, event_type="step_lease_expired",
                step=step, from_state=old_state, to_state=step.state,
                payload={"attempts": step.attempts, "previous_lease_token": old_token},
            )
        reclaimed += 1
    db.flush()
    return reclaimed


def claim_ready_step(
    db: Session,
    run_id: int,
    *,
    worker_id: str,
    lease_seconds: int = 300,
    now: datetime | None = None,
) -> ScientificWorkflowStep | None:
    """Claim one DAG-ready step using a lease token and row-level lock."""
    if not worker_id or len(worker_id) > 160:
        raise InvalidWorkflow("worker_id must contain 1 to 160 characters")
    if lease_seconds < MIN_LEASE_SECONDS or lease_seconds > MAX_LEASE_SECONDS:
        raise InvalidWorkflow(
            f"lease_seconds must be between {MIN_LEASE_SECONDS} and {MAX_LEASE_SECONDS}"
        )
    now = now or datetime.utcnow()
    run = db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == run_id
    ).with_for_update().first()
    if run is None:
        raise WorkflowConflict("workflow run not found")
    if run.state in {"blocked", "needs_user_action", "succeeded", "failed", "cancelled"}:
        return None

    reclaim_expired_leases(db, run_id, now=now)
    db.flush()
    if run.state == "failed":
        return None

    candidates = db.query(ScientificWorkflowStep).filter(
        ScientificWorkflowStep.workflow_run_id == run_id,
        ScientificWorkflowStep.state.in_(["pending", "retry_wait"]),
        ScientificWorkflowStep.available_at <= now,
    ).order_by(ScientificWorkflowStep.id.asc()).with_for_update(skip_locked=True).all()
    if not candidates:
        return None

    dependency_pairs = db.query(
        ScientificWorkflowStepDependency.step_id,
        ScientificWorkflowStepDependency.depends_on_step_id,
        ScientificWorkflowStep.state,
    ).join(
        ScientificWorkflowStep,
        ScientificWorkflowStep.id == ScientificWorkflowStepDependency.depends_on_step_id,
    ).filter(
        ScientificWorkflowStepDependency.workflow_run_id == run_id
    ).all()
    dependency_states: dict[int, list[str]] = {}
    for child_id, _parent_id, parent_status in dependency_pairs:
        dependency_states.setdefault(child_id, []).append(parent_status)

    for step in candidates:
        statuses = dependency_states.get(step.id, [])
        if any(status in {"failed", "cancelled", "skipped"} for status in statuses):
            old_state = step.state
            step.state = "skipped"
            step.error_code = "DEPENDENCY_FAILED"
            step.error_message = "A prerequisite step did not succeed."
            step.completed_at = now
            _event(
                db, run, event_type="step_skipped",
                step=step, from_state=old_state, to_state="skipped",
                reason=step.error_message,
            )
            continue
        if any(status != "succeeded" for status in statuses):
            continue

        old_state = step.state
        step.state = "running"
        step.attempts += 1
        step.lease_owner = worker_id
        step.lease_token = uuid4().hex
        step.lease_expires_at = now + timedelta(seconds=lease_seconds)
        step.heartbeat_at = now
        if step.started_at is None:
            step.started_at = now
        step.updated_at = now

        if run.state == "ready":
            run.state = "queued"
            run.state_version += 1
            _event(db, run, event_type="run_transition", from_state="ready", to_state="queued", action="queue")
        if run.state == "queued":
            run.state = "running.preparing"
            run.state_version += 1
            run.started_at = run.started_at or now
            _event(db, run, event_type="run_transition", from_state="queued", to_state="running.preparing", action="worker_started")
        if run.state == "running.preparing":
            run.state = "running.executing"
            run.state_version += 1
            _event(db, run, event_type="run_transition", from_state="running.preparing", to_state="running.executing", action="begin_execution")

        _event(
            db, run, event_type="step_claimed",
            step=step, from_state=old_state, to_state="running",
            payload={
                "worker_id": worker_id,
                "attempt": step.attempts,
                "lease_expires_at": step.lease_expires_at.isoformat(),
            },
        )
        db.flush()
        return step

    db.flush()
    return None


def renew_step_lease(
    db: Session,
    step_id: int,
    *,
    lease_token: str,
    lease_seconds: int = 300,
    now: datetime | None = None,
) -> ScientificWorkflowStep:
    if lease_seconds < MIN_LEASE_SECONDS or lease_seconds > MAX_LEASE_SECONDS:
        raise InvalidWorkflow(
            f"lease_seconds must be between {MIN_LEASE_SECONDS} and {MAX_LEASE_SECONDS}"
        )
    now = now or datetime.utcnow()
    step = db.query(ScientificWorkflowStep).filter(
        ScientificWorkflowStep.id == step_id
    ).with_for_update().first()
    if step is None:
        raise WorkflowConflict("workflow step not found")
    _verify_lease(step, lease_token, now)
    step.heartbeat_at = now
    step.lease_expires_at = now + timedelta(seconds=lease_seconds)
    step.updated_at = now
    db.flush()
    return step


def complete_step(
    db: Session,
    step_id: int,
    *,
    lease_token: str,
    output: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> ScientificWorkflowStep:
    now = now or datetime.utcnow()
    step = db.query(ScientificWorkflowStep).filter(
        ScientificWorkflowStep.id == step_id
    ).with_for_update().first()
    if step is None:
        raise WorkflowConflict("workflow step not found")
    _verify_lease(step, lease_token, now)
    safe_output = _json_payload(dict(output or {}), "step output")
    run = db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == step.workflow_run_id
    ).with_for_update().one()

    step.state = "succeeded"
    step.output = safe_output
    step.error_code = step.error_message = None
    step.lease_owner = step.lease_token = None
    step.lease_expires_at = step.heartbeat_at = None
    step.completed_at = now
    step.updated_at = now
    _event(
        db, run, event_type="step_succeeded",
        step=step, from_state="running", to_state="succeeded",
        payload={"output_keys": sorted(safe_output.keys())},
    )

    steps = db.query(ScientificWorkflowStep).filter(
        ScientificWorkflowStep.workflow_run_id == run.id
    ).all()
    if steps and all(item.state == "succeeded" for item in steps):
        if run.state == "running.executing":
            run.state = "running.postprocessing"
            run.state_version += 1
            _event(
                db, run, event_type="run_transition",
                from_state="running.executing", to_state="running.postprocessing",
                action="begin_postprocessing",
            )
        if run.requires_quality_review:
            run.state = "quality_review.pending"
            run.state_version += 1
            _event(
                db, run, event_type="run_transition",
                from_state="running.postprocessing", to_state="quality_review.pending",
                action="require_review",
            )
        else:
            run.state = "succeeded"
            run.state_version += 1
            run.completed_at = now
            _event(
                db, run, event_type="run_transition",
                from_state="running.postprocessing", to_state="succeeded",
                action="finalize",
            )

    db.flush()
    return step


def fail_step(
    db: Session,
    step_id: int,
    *,
    lease_token: str,
    error_code: str,
    error_message: str,
    retryable: bool = True,
    now: datetime | None = None,
) -> ScientificWorkflowStep:
    now = now or datetime.utcnow()
    step = db.query(ScientificWorkflowStep).filter(
        ScientificWorkflowStep.id == step_id
    ).with_for_update().first()
    if step is None:
        raise WorkflowConflict("workflow step not found")
    _verify_lease(step, lease_token, now)
    run = db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == step.workflow_run_id
    ).with_for_update().one()

    old_state = step.state
    step.lease_owner = step.lease_token = None
    step.lease_expires_at = step.heartbeat_at = None
    step.error_code = (error_code or "STEP_FAILED")[:120]
    step.error_message = error_message
    if retryable and step.attempts < step.max_attempts:
        step.state = "retry_wait"
        delay = min(MAX_BACKOFF_SECONDS, 2 ** min(step.attempts, 9))
        step.available_at = now + timedelta(seconds=delay)
    else:
        step.state = "failed"
        step.completed_at = now
        if run.state not in {"succeeded", "failed", "cancelled"}:
            previous = run.state
            run.state = "failed"
            run.state_version += 1
            run.failure_code = step.error_code
            run.failure_message = f"{step.step_key}: {error_message}"
            run.completed_at = now
            _event(
                db, run, event_type="run_failed",
                from_state=previous, to_state="failed",
                reason=run.failure_message,
            )

    _event(
        db, run, event_type="step_failed",
        step=step, from_state=old_state, to_state=step.state,
        reason=error_message,
        payload={"error_code": step.error_code, "attempts": step.attempts},
    )
    db.flush()
    return step


def cancel_workflow_run(
    db: Session,
    run_id: int,
    *,
    actor_user_id: int | None = None,
    reason: str,
) -> ScientificWorkflowRun:
    run, _ = transition_workflow_run(
        db, run_id, action="cancel",
        actor_user_id=actor_user_id, reason=reason,
    )
    return run
