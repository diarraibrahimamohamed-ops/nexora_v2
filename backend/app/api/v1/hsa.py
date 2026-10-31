"""
app/api/v1/hsa.py — Heuristic Stability Analysis API (ex-QSA).
Endpoints pour soumettre, suivre et récupérer les résultats HSA.
"""
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models.hsa import HSAJob
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.scientific_workflow_presets import create_builtin_task_run

router = APIRouter(prefix="/hsa", tags=["hsa"])
# Compatibilité routes /qsa (frontend / docs anciens)
legacy_router = APIRouter(prefix="/qsa", tags=["hsa-legacy"])


class HSARequest(BaseModel):
    protein_sequence: str
    env_factors: dict = {}


def _submit(body: HSARequest, db: Session, current_user: User):
    if not body.protein_sequence:
        raise HTTPException(400, "Séquence requise")

    job = HSAJob(
        user_id=current_user.id,
        protein_sequence=body.protein_sequence,
        env_factors=body.env_factors,
        status="pending",
        progress=0,
    )
    db.add(job)
    db.flush()
    workflow_run, _ = create_builtin_task_run(
        db,
        task_name="hsa.run_persisted",
        args=[job.id],
        owner_user_id=current_user.id,
        scope_type="hsa_job",
        scope_id=job.id,
        context={"method": "HSA"},
        input_manifest={
            "protein_sequence_sha256": hashlib.sha256(
                body.protein_sequence.encode("ascii")
            ).hexdigest(),
            "protein_length": len(body.protein_sequence),
            "environment_factors_sha256": hashlib.sha256(
                json.dumps(
                    body.env_factors, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
            ).hexdigest(),
        },
    )
    job.workflow_run_id = workflow_run.id
    job.celery_task_id = f"workflow-run-{workflow_run.id}"
    db.commit()

    return {
        "job_id": job.id,
        "status": "pending",
        "task_id": f"workflow-run-{workflow_run.id}",
        "workflow_run_id": workflow_run.id,
        "dispatch": "scientific_workflow",
        "method": "HSA",
        "disclaimer": "Heuristic Stability Analysis — proxy pédagogique, non quantique",
    }


def _status(job_id: int, db: Session, current_user: User):
    job = (
        db.query(HSAJob)
        .filter(HSAJob.id == job_id, HSAJob.user_id == current_user.id)
        .first()
    )
    if not job:
        raise HTTPException(404, "Job introuvable")
    return {
        "job_id": job_id,
        "status": job.status,
        "progress": job.progress,
        "error": job.error,
        "workflow_run_id": job.workflow_run_id,
    }


def _results(job_id: int, db: Session, current_user: User):
    job = (
        db.query(HSAJob)
        .filter(HSAJob.id == job_id, HSAJob.user_id == current_user.id)
        .first()
    )
    if not job:
        raise HTTPException(404, "Job introuvable")
    if job.status not in ("completed", "done"):
        raise HTTPException(400, "Job pas encore terminé")

    return {
        "job_id": job.id,
        "protein_sequence": job.protein_sequence,
        "env_factors": job.env_factors,
        "results": job.results,
        "method": "HSA",
    }


def _history(skip: int, limit: int, db: Session, current_user: User):
    jobs = (
        db.query(HSAJob)
        .filter(HSAJob.user_id == current_user.id)
        .order_by(HSAJob.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return {
        "success": True,
        "jobs": [
            {
                "id": j.id,
                "protein_sequence": (
                    j.protein_sequence[:50] + "..."
                    if len(j.protein_sequence) > 50
                    else j.protein_sequence
                ),
                "status": j.status,
                "progress": j.progress,
                "created_at": j.created_at.isoformat() if j.created_at else None,
            }
            for j in jobs
        ],
    }


@router.post("/submit", status_code=202)
def submit_hsa(
    body: HSARequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Soumet une analyse heuristique de stabilité (HSA)."""
    return _submit(body, db, current_user)


@router.get("/history")
def get_hsa_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _history(skip, limit, db, current_user)


@router.get("/{job_id}/status")
def get_hsa_status(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _status(job_id, db, current_user)


@router.get("/{job_id}/results")
def get_hsa_results(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _results(job_id, db, current_user)


# ── Routes legacy /qsa → mêmes handlers ──────────────────────────────────────

@legacy_router.post("/submit", status_code=202)
def submit_qsa_legacy(
    body: HSARequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _submit(body, db, current_user)


@legacy_router.get("/history")
def get_qsa_history_legacy(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _history(skip, limit, db, current_user)


@legacy_router.get("/{job_id}/status")
def get_qsa_status_legacy(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _status(job_id, db, current_user)


@legacy_router.get("/{job_id}/results")
def get_qsa_results_legacy(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _results(job_id, db, current_user)
