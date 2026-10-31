"""Authenticated API for RNA secondary-structure predictions through Tamarind RNA-FM."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.rna_structure_job import RnaStructureJob
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.scientific_workflow_presets import create_builtin_task_run

router = APIRouter(prefix="/rna-structure", tags=["RNA secondary structure"])


class RnaStructureRequest(BaseModel):
    sequence: str = Field(..., min_length=4, max_length=10000)


def _owned_job(db: Session, job_id: int, user: User) -> RnaStructureJob:
    job = db.query(RnaStructureJob).filter(
        RnaStructureJob.id == job_id,
        RnaStructureJob.user_id == user.id,
    ).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job de structure ARN introuvable.")
    return job


@router.post("/submit", status_code=202)
def submit(
    body: RnaStructureRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sequence = "".join(body.sequence.split()).upper()
    if len(sequence) < 4 or len(sequence) > 10000 or set(sequence) - set("ACGUT"):
        raise HTTPException(status_code=422, detail="Séquence invalide : 4 à 10 000 bases A/C/G/U/T requises.")
    key = (settings.TAMARIND_API_KEY or "").strip().lower()
    if not key or key.startswith("your_") or key in {"changeme", "change-me", "change_me_in_production"}:
        raise HTTPException(status_code=503, detail="TAMARIND_API_KEY n'est pas configurée.")

    job = RnaStructureJob(
        user_id=user.id, sequence=sequence, status="pending", progress=0, metadata_json={}
    )
    db.add(job)
    db.flush()
    workflow_run, _ = create_builtin_task_run(
        db,
        task_name="rna_structure.predict_secondary",
        args=[job.id],
        owner_user_id=user.id,
        scope_type="rna_structure_job",
        scope_id=job.id,
        context={"sequence_length": len(sequence)},
        input_manifest={
            "sequence_sha256": __import__("hashlib").sha256(sequence.encode("ascii")).hexdigest(),
            "sequence_length": len(sequence),
            "sequence_alphabet": "RNA_or_DNA",
        },
    )
    job.workflow_run_id = workflow_run.id
    job.celery_task_id = f"workflow-run-{workflow_run.id}"
    metadata = dict(job.metadata_json or {})
    metadata["workflow_run_id"] = workflow_run.id
    job.metadata_json = metadata
    db.commit()
    return {
        "success": True,
        "job_id": job.id,
        "task_id": f"workflow-run-{workflow_run.id}",
        "workflow_run_id": workflow_run.id,
        "dispatch": "scientific_workflow",
        "status": job.status,
    }


@router.get("/{job_id}")
def get_status(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    job = _owned_job(db, job_id, user)
    artifact_exists = False
    if job.artifact_path:
        path = Path(job.artifact_path).resolve()
        root = (Path(settings.TEMP_DIR) / "rna_secondary" / ("job_" + str(job.id))).resolve()
        artifact_exists = path.is_relative_to(root) and path.is_file()
    return {
        "job_id": job.id,
        "status": job.status,
        "workflow_run_id": job.workflow_run_id,
        "progress": job.progress,
        "error": job.error,
        "metadata": job.metadata_json or {},
        "sequence_length": len(job.sequence),
        "artifact_available": artifact_exists,
    }


@router.get("/{job_id}/artifact")
def download_artifact(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    job = _owned_job(db, job_id, user)
    if job.status != "completed" or not job.artifact_path:
        raise HTTPException(status_code=404, detail="Artefact indisponible.")
    path = Path(job.artifact_path).resolve()
    root = (Path(settings.TEMP_DIR) / "rna_secondary" / ("job_" + str(job.id))).resolve()
    if not path.is_file() or not path.is_relative_to(root):
        raise HTTPException(status_code=404, detail="Artefact indisponible.")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")
