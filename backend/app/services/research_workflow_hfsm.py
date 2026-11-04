"""Bridge ResearchStudy lifecycle events into the shared scientific HFSM.

A ResearchStudy lifecycle is human-controlled, not a Celery computation DAG.
It therefore uses a state-only ScientificWorkflowRun with zero DAG steps.
Existing ResearchWorkflowEvent rows are copied into the generic event stream
once, with their identifiers and timestamps preserved; original rows remain
untouched for backwards compatibility.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.research import ResearchStudy
from app.models.scientific_workflow import ScientificWorkflowEvent, ScientificWorkflowRun
from app.models.workflow import ResearchWorkflowEvent
from app.services.scientific_provenance import sha256_json
from app.services.scientific_workflow_engine import (
    WorkflowConflict,
    create_workflow_run,
    transition_workflow_run,
)
from app.services.scientific_workflow_policy import WorkflowDefinition

RESEARCH_LIFECYCLE_KEY = "nexora.research-study-lifecycle"
RESEARCH_LIFECYCLE_VERSION = "2.0.0"
RESEARCH_LIFECYCLE_DEFINITION = WorkflowDefinition(
    key=RESEARCH_LIFECYCLE_KEY,
    version=RESEARCH_LIFECYCLE_VERSION,
    description=(
        "Human-governed ResearchStudy lifecycle using the shared HFSM transition "
        "engine; no Celery DAG steps are scheduled."
    ),
    steps=(),
    requires_quality_review=False,
    state_only=True,
)


def _legacy_history_payload(events: list[ResearchWorkflowEvent]) -> list[dict[str, Any]]:
    return [
        {
            "legacy_event_id": event.id,
            "actor_id": event.actor_id,
            "from_state": event.from_state,
            "to_state": event.to_state,
            "action": event.action,
            "reason": event.reason,
            "payload": event.payload or {},
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
        for event in events
    ]


def _valid_initial_state(study: ResearchStudy) -> str:
    state = (study.status or "draft").strip().lower()
    allowed = {"draft", "data_review", "analysis", "results_review", "completed", "blocked", "cancelled"}
    if state not in allowed:
        raise WorkflowConflict(f"ResearchStudy #{study.id} has an unsupported lifecycle state: {state!r}")
    return state


def ensure_research_lifecycle_run(db: Session, study: ResearchStudy) -> tuple[ScientificWorkflowRun, bool]:
    """Return the study's single lifecycle run, lazily importing pre-HFSM history."""
    state = _valid_initial_state(study)
    idempotency_key = f"research-lifecycle:{study.id}"
    existing = (
        db.query(ScientificWorkflowRun)
        .filter(
            ScientificWorkflowRun.workflow_key == RESEARCH_LIFECYCLE_KEY,
            ScientificWorkflowRun.scope_type == "research_study",
            ScientificWorkflowRun.scope_id == str(study.id),
        )
        .with_for_update()
        .first()
    )
    if existing is not None:
        if existing.owner_user_id != study.user_id:
            raise WorkflowConflict("ResearchStudy lifecycle run owner does not match the study owner")
        if existing.state != state:
            raise WorkflowConflict(
                f"ResearchStudy/HFSM state divergence for study {study.id}: "
                f"legacy={state!r}, hfsm={existing.state!r}"
            )
        return existing, False

    legacy_events = (
        db.query(ResearchWorkflowEvent)
        .filter(ResearchWorkflowEvent.study_id == study.id)
        .order_by(ResearchWorkflowEvent.created_at.asc(), ResearchWorkflowEvent.id.asc())
        .all()
    )
    legacy_payload = _legacy_history_payload(legacy_events)
    transition_count = sum(
        1 for event in legacy_events
        if (event.action or "").strip().lower() not in {"created"}
    )
    metadata = study.metadata_json or {}
    resume_state = (metadata.get("workflow_resume_state") or "").strip().lower() or None
    context = {
        "domain": "research",
        "study_id": study.id,
        "owner_user_id": study.user_id,
        "legacy_fsm": "research-study-fsm-v2-independent-review",
        "history_imported": True,
    }
    input_manifest = {
        "study": {
            "id": study.id,
            "owner_user_id": study.user_id,
            "name": study.name,
            "disease": study.disease,
            "pathogen": study.pathogen,
            "country_focus": study.country_focus,
            "region_scope": study.region_scope,
            "created_at": study.created_at.isoformat() if study.created_at else None,
            "objective_sha256": hashlib.sha256((study.objective or "").encode("utf-8")).hexdigest(),
            "hypothesis_sha256": hashlib.sha256((study.hypothesis or "").encode("utf-8")).hexdigest(),
        },
        "legacy_history": {
            "event_count": len(legacy_payload),
            "sha256": sha256_json(legacy_payload),
            "source_table": "research_workflow_events",
        },
        "initial_state": state,
        "resume_state": resume_state,
    }
    run, replay = create_workflow_run(
        db,
        RESEARCH_LIFECYCLE_DEFINITION,
        owner_user_id=study.user_id,
        context=context,
        scope_type="research_study",
        scope_id=study.id,
        input_manifest=input_manifest,
        idempotency_key=idempotency_key,
        initial_state=state,
    )
    if replay:
        if (
            run.workflow_key != RESEARCH_LIFECYCLE_KEY
            or run.scope_type != "research_study"
            or run.scope_id != str(study.id)
            or run.state != state
        ):
            raise WorkflowConflict("ResearchStudy lifecycle idempotency key conflicts with existing scope/state")
        return run, False

    run.resume_state = resume_state if state == "blocked" else None
    run.state_version = transition_count
    if state in {"completed", "cancelled"}:
        run.completed_at = study.updated_at or datetime.utcnow()

    for event in legacy_events:
        db.add(ScientificWorkflowEvent(
            workflow_run_id=run.id,
            actor_user_id=event.actor_id,
            event_type="legacy_transition_imported",
            from_state=event.from_state,
            to_state=event.to_state,
            action=event.action,
            reason=event.reason,
            payload={
                "legacy_event_id": event.id,
                "legacy_payload": event.payload or {},
                "source_table": "research_workflow_events",
            },
            idempotency_key=f"legacy-research-event:{event.id}",
            created_at=event.created_at or datetime.utcnow(),
        ))
    db.flush()
    return run, True


def transition_research_lifecycle(
    db: Session,
    study: ResearchStudy,
    *,
    action: str,
    expected_to_state: str,
    actor_user_id: int,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> ScientificWorkflowRun:
    """Apply a Research lifecycle transition through the generic versioned HFSM."""
    run, _created = ensure_research_lifecycle_run(db, study)
    current_state = (study.status or "draft").strip().lower()
    if run.state != current_state:
        raise WorkflowConflict(
            f"ResearchStudy/HFSM state divergence for study {study.id}: legacy={current_state!r}, hfsm={run.state!r}"
        )
    hfsm_key = None
    if idempotency_key:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        hfsm_key = f"research:{action}:{digest}"
    run, replay = transition_workflow_run(
        db,
        run.id,
        action=action,
        actor_user_id=actor_user_id,
        reason=reason,
        expected_state_version=run.state_version,
        idempotency_key=hfsm_key,
    )
    if replay:
        if run.state != expected_to_state:
            raise WorkflowConflict("Idempotent HFSM replay does not match the expected ResearchStudy state")
    elif run.state != expected_to_state:
        raise WorkflowConflict(
            f"HFSM transition produced {run.state!r}; ResearchStudy expected {expected_to_state!r}"
        )
    return run


def serialize_research_lifecycle_run(run: ScientificWorkflowRun) -> dict:
    return {
        "run_id": run.id,
        "workflow_key": run.workflow_key,
        "definition_version": run.definition_version,
        "state": run.state,
        "state_version": run.state_version,
        "execution_mode": (run.definition_snapshot or {}).get("execution_mode", "state_machine"),
        "state_only": True,
        "step_count": 0,
        "input_manifest_sha256": (run.input_manifest or {}).get("manifest_sha256"),
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
    }
