"""Read-only, owner-scoped API for scientific workflow execution status."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.scientific_workflow import (
    ScientificWorkflowEvent,
    ScientificWorkflowRun,
    ScientificWorkflowStep,
    ScientificWorkflowStepDependency,
)
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/scientific-workflows", tags=["scientific workflows"])


def _iso(value):
    return value.isoformat() if value is not None else None


@router.get("/{workflow_run_id}")
def get_scientific_workflow(
    workflow_run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == workflow_run_id,
        ScientificWorkflowRun.owner_user_id == user.id,
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow scientifique introuvable.")

    steps = db.query(ScientificWorkflowStep).filter(
        ScientificWorkflowStep.workflow_run_id == run.id
    ).order_by(ScientificWorkflowStep.id.asc()).all()
    edges = db.query(ScientificWorkflowStepDependency).filter(
        ScientificWorkflowStepDependency.workflow_run_id == run.id
    ).all()
    events = db.query(ScientificWorkflowEvent).filter(
        ScientificWorkflowEvent.workflow_run_id == run.id
    ).order_by(ScientificWorkflowEvent.id.desc()).limit(250).all()

    step_keys = {step.id: step.step_key for step in steps}
    dependencies = {}
    for edge in edges:
        dependencies.setdefault(edge.step_id, []).append(step_keys.get(edge.depends_on_step_id))

    return {
        "success": True,
        "workflow": {
            "id": run.id,
            "workflow_key": run.workflow_key,
            "definition_version": run.definition_version,
            "state": run.state,
            "state_parent": (
                "running" if run.state.startswith("running.")
                else "quality_review" if run.state.startswith("quality_review.")
                else run.state
            ),
            "state_version": run.state_version,
            "resume_state": run.resume_state,
            "scope": {"type": run.scope_type, "id": run.scope_id},
            "requires_quality_review": run.requires_quality_review,
            "context": run.context or {},
            "definition": run.definition_snapshot or {},
            "input_manifest": run.input_manifest or {},
            "output_manifest": run.output_manifest or {},
            "failure": {
                "code": run.failure_code,
                "message": run.failure_message,
            } if run.failure_code or run.failure_message else None,
            "created_at": _iso(run.created_at),
            "updated_at": _iso(run.updated_at),
            "started_at": _iso(run.started_at),
            "completed_at": _iso(run.completed_at),
        },
        "steps": [
            {
                "id": step.id,
                "key": step.step_key,
                "task_name": step.task_name,
                "queue": step.queue_name,
                "state": step.state,
                "dependencies": sorted(dependencies.get(step.id, [])),
                "attempts": step.attempts,
                "max_attempts": step.max_attempts,
                "available_at": _iso(step.available_at),
                "lease_owner": step.lease_owner,
                "lease_expires_at": _iso(step.lease_expires_at),
                "heartbeat_at": _iso(step.heartbeat_at),
                "execution_started_at": _iso(step.execution_started_at),
                "executor_id": step.executor_id,
                "output": step.output,
                "error": {
                    "code": step.error_code,
                    "message": step.error_message,
                } if step.error_code or step.error_message else None,
                "started_at": _iso(step.started_at),
                "completed_at": _iso(step.completed_at),
            }
            for step in steps
        ],
        "events": [
            {
                "id": event.id,
                "step_id": event.step_id,
                "event_type": event.event_type,
                "from_state": event.from_state,
                "to_state": event.to_state,
                "action": event.action,
                "reason": event.reason,
                "payload": event.payload or {},
                "created_at": _iso(event.created_at),
            }
            for event in events
        ],
    }
