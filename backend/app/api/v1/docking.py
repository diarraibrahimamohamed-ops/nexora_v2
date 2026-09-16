"""
api/v1/docking.py
POST /api/v1/docking/submit          → remplace api.php?action=start_docking
GET  /api/v1/docking/{id}/status     → suivi job Celery
GET  /api/v1/docking/{id}/results    → résultats JSON
GET  /api/v1/docking/history         → historique jobs utilisateur

Accepte soit (protein_sequence + ligand_smiles) soit (analysis_id + ligand_smiles)
comme l'ancien flux PHP qui chargeait la protéine depuis analyses.data.protein_data.
"""
import json
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.docking import DockingJob, DockingResult, DockingStatus, JobStatus, ModelingMethod
from app.models.sequence import Analysis
from app.models.user import User
from app.services.auth_service import get_current_user
from app.workers.docking_tasks import run_vina_task

router = APIRouter(prefix="/docking", tags=["docking"])

_AA_RE = re.compile(r"[^ACDEFGHIKLMNPQRSTVWY]", re.I)


def _clean_protein(seq: str) -> str:
    return _AA_RE.sub("", (seq or "").upper().strip())


def _extract_protein_from_analysis(analysis: Analysis) -> Optional[str]:
    """Reproduit la logique PHP: data.protein_data.sequence."""
    data = analysis.data or {}
    if not isinstance(data, dict):
        return None

    protein_data = data.get("protein_data")
    if isinstance(protein_data, str) and len(protein_data) >= 3:
        return _clean_protein(protein_data)
    if isinstance(protein_data, dict):
        for key in ("sequence", "protein_sequence", "seq"):
            val = protein_data.get(key)
            if isinstance(val, str) and len(val) >= 3:
                return _clean_protein(val)

    for key in ("protein_sequence", "sequence"):
        val = data.get(key)
        if isinstance(val, str):
            cleaned = _clean_protein(val)
            if len(cleaned) >= 3:
                return cleaned

    # Fallback: première séquence liée de type protéine
    for s in getattr(analysis, "sequences", []) or []:
        seq = getattr(s, "sequence", None) or ""
        cleaned = _clean_protein(seq)
        if len(cleaned) >= 3 and len(cleaned) / max(len(seq), 1) > 0.8:
            return cleaned
    return None


class SubmitRequest(BaseModel):
    protein_sequence: Optional[str] = None
    ligand_smiles: str = Field(..., min_length=1)
    max_pockets: int = Field(3, ge=1, le=10)
    analysis_id: Optional[int] = None

    @model_validator(mode="after")
    def require_protein_or_analysis(self):
        if not self.ligand_smiles or not self.ligand_smiles.strip():
            raise ValueError("ligand_smiles requis")
        if not self.protein_sequence and not self.analysis_id:
            raise ValueError("protein_sequence ou analysis_id requis")
        return self


@router.post("/submit", status_code=202)
def submit(
    body: SubmitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    protein_seq = _clean_protein(body.protein_sequence or "")
    analysis_id = body.analysis_id

    if analysis_id and not protein_seq:
        analysis = (
            db.query(Analysis)
            .filter(Analysis.id == analysis_id, Analysis.user_id == current_user.id)
            .first()
        )
        if not analysis:
            raise HTTPException(404, "Analyse introuvable")
        protein_seq = _extract_protein_from_analysis(analysis) or ""
        if not protein_seq:
            raise HTTPException(
                400,
                "Aucune séquence protéique trouvée dans cette analyse "
                "(attendu: data.protein_data.sequence)",
            )

    if not protein_seq or len(protein_seq) < 3:
        raise HTTPException(400, "Séquence protéique invalide (minimum 3 acides aminés)")
    if len(protein_seq) > 1000:
        raise HTTPException(400, "Séquence protéique trop longue (maximum 1000 aa)")

    smiles = body.ligand_smiles.strip()

    job = DockingJob(
        user_id=current_user.id,
        status=JobStatus.pending,
        protein_seq=protein_seq,
        ligand_smiles=smiles,
        progress=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Persistance compatible table docking_results (ancien schéma PHP)
    docking_result_id = None
    if analysis_id:
        dr = DockingResult(
            analysis_id=analysis_id,
            protein_sequence=protein_seq,
            ligand_smiles=smiles,
            status=DockingStatus.pending,
            modeling_method=ModelingMethod.ASP,  # Vina + ASP Boltzmann (pas une méthode de docking)
        )
        db.add(dr)
        db.commit()
        db.refresh(dr)
        docking_result_id = dr.id

    task = run_vina_task.apply_async(
        args=[job.id, protein_seq, smiles, body.max_pockets, docking_result_id],
        queue="docking",
    )
    job.celery_task_id = task.id
    db.commit()

    return {
        "success": True,
        "job_id": job.id,
        "status": "pending",
        "task_id": task.id,
        "analysis_id": analysis_id,
        "docking_result_id": docking_result_id,
        "protein_length": len(protein_seq),
        "message": "Job de docking soumis — suivez /docking/{job_id}/status",
    }


def _get_user_job(job_id: int, db: Session, user: User) -> DockingJob:
    job = (
        db.query(DockingJob)
        .filter(DockingJob.id == job_id, DockingJob.user_id == user.id)
        .first()
    )
    if not job:
        raise HTTPException(404, "Job introuvable")
    return job


def _status_value(status) -> str:
    if status is None:
        return "unknown"
    return status.value if hasattr(status, "value") else str(status)


@router.get("/history")
def history(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    jobs = (
        db.query(DockingJob)
        .filter(DockingJob.user_id == current_user.id)
        .order_by(DockingJob.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return {
        "success": True,
        "jobs": [
            {
                "job_id": j.id,
                "status": _status_value(j.status),
                "progress": j.progress,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "error": j.error,
                "ligand_smiles": j.ligand_smiles,
                "protein_length": len(j.protein_seq) if j.protein_seq else 0,
            }
            for j in jobs
        ],
    }


@router.get("/{job_id}/status")
def get_status(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = _get_user_job(job_id, db, current_user)
    return {
        "success": True,
        "job_id": job_id,
        "status": _status_value(job.status),
        "progress": job.progress or 0,
        "error": job.error,
        "status_message": job.status_message,
        "task_id": job.celery_task_id,
    }


@router.get("/{job_id}/results")
def get_results(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = _get_user_job(job_id, db, current_user)
    status = _status_value(job.status)

    if status == JobStatus.failed.value or status == "failed":
        raise HTTPException(400, job.error or "Job échoué")
    # Accepte completed (canonique) et done (legacy avant unification A3)
    if status not in (JobStatus.completed.value, "completed", "done"):
        raise HTTPException(404, f"Résultats non disponibles (statut: {status})")
    if not job.result_path or not os.path.exists(job.result_path):
        raise HTTPException(500, "Fichier résultats introuvable")

    with open(job.result_path, encoding="utf-8") as f:
        data = json.load(f)

    # Contrat de sortie canonique. Les anciens fichiers sont normalisés à la
    # lecture afin que le frontend n'ait pas à connaître les anciens schémas.
    if isinstance(data, dict):
        data.setdefault("success", True)
        data.setdefault("docking_id", job_id)
        data.setdefault("job_id", job_id)
        data.setdefault("docking_engine", "AutoDock Vina")
        data.setdefault("aggregation_method", "ASP — pondération de Boltzmann après clustering RMSD")
        if "vina_best_score" not in data:
            legacy_score = data.get("docking_score", data.get("binding_energy"))
            if legacy_score is not None:
                data["vina_best_score"] = legacy_score
        if "boltzmann_effective_score" not in data and data.get("effective_score") is not None:
            data["boltzmann_effective_score"] = data["effective_score"]
        if "boltzmann_mean_score" not in data:
            meta = data.get("aggregation_metadata") or data.get("metadata") or {}
            if meta.get("boltzmann_mean_score") is not None:
                data["boltzmann_mean_score"] = meta["boltzmann_mean_score"]
        if data.get("poses") and not data.get("pose_data"):
            best = data["poses"][0]
            data["pose_data"] = {
                "best_pose": best,
                "atoms": best.get("atoms", []),
            }
    return data
