"""API endpoints for the research-study FSM and audit timeline."""
from __future__ import annotations

from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.docking import DockingJob, JobStatus
from app.models.evidence import ResearchEvidence
from app.models.research import (
    ResearchDockingComparison, ResearchDockingRun, ResearchStudy,
    ResearchStructureComparison, ResearchTarget, ResearchVariant,
)
from app.models.user import User
from app.models.virtual_screening import ResearchScreeningCompound, ResearchScreeningRun
from app.models.workflow import ResearchWorkflowEvent
from app.services.auth_service import get_current_user
from app.services.research_workflow_fsm import (
    ACTION_DESCRIPTIONS, allowed_actions, resolve_transition,
)

router = APIRouter(prefix="/research", tags=["research workflow"])


def _owned_study(db: Session, study_id: int, user: User, *, lock: bool = False) -> ResearchStudy:
    query = db.query(ResearchStudy).filter(
        ResearchStudy.id == study_id,
        ResearchStudy.user_id == user.id,
    )
    if lock:
        query = query.with_for_update()
    study = query.first()
    if study is None:
        raise HTTPException(status_code=404, detail="Étude introuvable.")
    return study


def _result_counts(db: Session, study_id: int) -> dict:
    completed_runs = (
        db.query(ResearchDockingRun)
        .join(DockingJob, ResearchDockingRun.docking_job_id == DockingJob.id)
        .filter(ResearchDockingRun.study_id == study_id, DockingJob.status == JobStatus.completed)
        .count()
    )
    completed_compounds = (
        db.query(ResearchScreeningCompound)
        .join(ResearchScreeningRun, ResearchScreeningCompound.screening_id == ResearchScreeningRun.id)
        .join(DockingJob, ResearchScreeningCompound.docking_job_id == DockingJob.id)
        .filter(
            ResearchScreeningRun.study_id == study_id,
            DockingJob.status == JobStatus.completed,
        ).count()
    )
    structure_comparisons = db.query(ResearchStructureComparison).filter(
        ResearchStructureComparison.study_id == study_id,
        ResearchStructureComparison.status == "computed",
    ).count()
    docking_comparisons = db.query(ResearchDockingComparison).filter(
        ResearchDockingComparison.study_id == study_id,
        ResearchDockingComparison.status == "computed",
    ).count()
    evidence = db.query(ResearchEvidence).filter(
        ResearchEvidence.study_id == study_id
    ).count()
    return {
        "completed_docking_runs": completed_runs,
        "completed_screening_compounds": completed_compounds,
        "structure_comparisons": structure_comparisons,
        "docking_comparisons": docking_comparisons,
        "evidence_records": evidence,
        "computational_outputs": completed_runs + structure_comparisons + docking_comparisons,
    }


def _readiness(db: Session, study: ResearchStudy, action: str) -> list[str]:
    blockers: list[str] = []
    if action == "submit_for_review":
        if not (study.objective or "").strip():
            blockers.append("Définir un objectif scientifique.")
        if not (study.hypothesis or "").strip():
            blockers.append("Définir une hypothèse explicite et vérifiable.")
    elif action == "approve_data":
        variants = db.query(ResearchVariant).filter(ResearchVariant.study_id == study.id).count()
        targets = db.query(ResearchTarget).filter(ResearchTarget.study_id == study.id).count()
        if not variants and not targets:
            blockers.append("Ajouter au moins un variant ou une cible à l'étude.")
        for target in db.query(ResearchTarget).filter(ResearchTarget.study_id == study.id).all():
            if not (target.protein_sequence or "").strip():
                blockers.append(f"La cible #{target.id} ne possède pas de séquence protéique.")
    elif action == "submit_results":
        counts = _result_counts(db, study.id)
        if counts["computational_outputs"] == 0:
            blockers.append("Aucun résultat computationnel terminé ou comparaison calculée n'est disponible.")
    elif action == "complete_computational":
        counts = _result_counts(db, study.id)
        if counts["computational_outputs"] == 0:
            blockers.append("Impossible de clôturer une étude sans résultat computationnel vérifiable.")
    return blockers


def _serialize_event(event: ResearchWorkflowEvent) -> dict:
    return {
        "id": event.id,
        "study_id": event.study_id,
        "actor_id": event.actor_id,
        "from_state": event.from_state,
        "to_state": event.to_state,
        "action": event.action,
        "reason": event.reason,
        "payload": event.payload or {},
        "idempotency_key": event.idempotency_key,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


class WorkflowTransitionRequest(BaseModel):
    action: str = Field(..., min_length=3, max_length=60)
    reason: Optional[str] = Field(None, max_length=4000)


@router.get("/studies/{study_id}/workflow")
def get_workflow(
    study_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    study = _owned_study(db, study_id, user)
    events = (
        db.query(ResearchWorkflowEvent)
        .filter(ResearchWorkflowEvent.study_id == study_id)
        .order_by(ResearchWorkflowEvent.created_at.asc(), ResearchWorkflowEvent.id.asc())
        .limit(500)
        .all()
    )
    metadata = study.metadata_json or {}
    return {
        "success": True,
        "study_id": study_id,
        "state": study.status or "draft",
        "allowed_actions": allowed_actions(study.status or "draft"),
        "resume_state": metadata.get("workflow_resume_state"),
        "counts": _result_counts(db, study_id),
        "events": [_serialize_event(item) for item in events],
    }


@router.post("/studies/{study_id}/workflow/transitions")
def transition_workflow(
    study_id: int,
    body: WorkflowTransitionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    study = _owned_study(db, study_id, user, lock=True)
    if idempotency_key:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 128:
            raise HTTPException(status_code=422, detail="Idempotency-Key doit contenir 1 à 128 caractères.")
        existing = db.query(ResearchWorkflowEvent).filter(
            ResearchWorkflowEvent.study_id == study_id,
            ResearchWorkflowEvent.idempotency_key == idempotency_key,
        ).first()
        if existing:
            return {
                "success": True,
                "idempotent_replay": True,
                "state": study.status,
                "event": _serialize_event(existing),
            }

    current_state = study.status or "draft"
    action = body.action.strip().lower()
    if action not in ACTION_DESCRIPTIONS:
        raise HTTPException(status_code=422, detail="Action de workflow inconnue.")

    resume_state = (study.metadata_json or {}).get("workflow_resume_state")
    decision = resolve_transition(current_state, action, resume_state=resume_state)
    if not decision.allowed:
        raise HTTPException(status_code=409, detail=decision.reason)

    if action in {"block", "cancel", "resume", "complete_computational"} and not (body.reason or "").strip():
        raise HTTPException(status_code=422, detail="Une justification est obligatoire pour cette transition.")

    blockers = _readiness(db, study, action)
    if blockers:
        raise HTTPException(status_code=409, detail={"message": "Préconditions scientifiques non satisfaites.", "blockers": blockers})

    metadata = dict(study.metadata_json or {})
    if action == "block":
        metadata["workflow_resume_state"] = current_state
    elif action == "resume":
        metadata.pop("workflow_resume_state", None)

    study.status = decision.to_state
    study.metadata_json = metadata
    study.updated_at = datetime.utcnow()
    event = ResearchWorkflowEvent(
        study_id=study_id,
        actor_id=user.id,
        from_state=current_state,
        to_state=decision.to_state,
        action=action,
        reason=(body.reason or "").strip() or None,
        payload={
            "description": ACTION_DESCRIPTIONS[action],
            "readiness": _result_counts(db, study_id),
            "policy_version": "research-study-fsm-v1",
        },
        idempotency_key=idempotency_key,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return {
        "success": True,
        "idempotent_replay": False,
        "state": study.status,
        "allowed_actions": allowed_actions(study.status),
        "event": _serialize_event(event),
    }
