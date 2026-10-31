"""Celery task for RNA-FM secondary-structure prediction."""
from pathlib import Path

from app.config import settings
from app.database import SessionLocal
from app.models.rna_structure_job import RnaStructureJob
from app.workers.celery_app import celery_app


@celery_app.task(bind=True, name="rna_structure.predict_secondary", max_retries=0, acks_late=True)
def predict_rna_secondary_structure_task(self, job_id: int) -> dict:
    db = SessionLocal()
    try:
        job = db.query(RnaStructureJob).filter(RnaStructureJob.id == job_id).first()
        if job is None:
            return {"success": False, "error": "job introuvable"}
        if job.status == "completed" and job.artifact_path:
            existing_path = Path(job.artifact_path).resolve()
            allowed_root = (Path(settings.TEMP_DIR) / "rna_secondary" / ("job_" + str(job.id))).resolve()
            if existing_path.is_relative_to(allowed_root) and existing_path.is_file():
                return {
                    "success": True,
                    "job_id": job.id,
                    "artifact": existing_path.name,
                    "idempotent_replay": True,
                }
        job.status, job.progress, job.error = "running", 5, None
        job.celery_task_id = self.request.id
        db.commit()

        output_dir = Path(settings.TEMP_DIR) / "rna_secondary" / ("job_" + str(job.id))
        output_dir.mkdir(parents=True, exist_ok=True)
        from app.core.protein.tamarind_runner import build_tamarind_secondary_structure
        path, error, metadata = build_tamarind_secondary_structure(
            job.sequence, str(output_dir), tamarind_api_key=settings.TAMARIND_API_KEY or None
        )
        job.metadata_json = metadata or {}
        if error or not path:
            job.status, job.progress = "failed", 0
            job.error = error or "Aucun artefact produit."
            db.commit()
            return {"success": False, "job_id": job.id, "error": job.error}

        resolved_path = Path(path).resolve()
        if not resolved_path.is_relative_to(output_dir.resolve()) or not resolved_path.is_file():
            job.status, job.progress = "failed", 0
            job.error = "L'artefact produit est hors du répertoire autorisé."
            db.commit()
            return {"success": False, "job_id": job.id, "error": job.error}

        job.artifact_path = str(resolved_path)
        job.status, job.progress, job.error = "completed", 100, None
        db.commit()
        return {"success": True, "job_id": job.id, "artifact": resolved_path.name}
    except Exception as exc:
        db.rollback()
        job = db.query(RnaStructureJob).filter(RnaStructureJob.id == job_id).first()
        if job:
            job.status, job.progress, job.error = "failed", 0, type(exc).__name__
            db.commit()
        return {"success": False, "job_id": job_id, "error": type(exc).__name__}
    finally:
        db.close()
