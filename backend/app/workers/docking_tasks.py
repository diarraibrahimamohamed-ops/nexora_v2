"""
workers/docking_tasks.py — Tâche Celery pour AutoDock Vina + ASP (agrégation Boltzmann).
"""
import json
import os
import traceback

from app.config import settings
from app.database import SessionLocal
from app.models.docking import DockingJob, DockingResult, JobStatus, ModelingMethod
from app.workers.celery_app import celery_app


def _upd_job(db, job_id, raise_on_error=False, **kw):
    try:
        j = db.query(DockingJob).filter(DockingJob.id == job_id).first()
        if j:
            for k, v in kw.items():
                setattr(j, k, v)
            db.commit()
    except Exception as e:
        print(f"[docking_tasks] Error updating job {job_id}: {e}\n{traceback.format_exc()}", flush=True)
        db.rollback()
        if raise_on_error:
            raise e


def _upd_result(db, docking_result_id, raise_on_error=False, **kw):
    """Synchronise docking_results (correction A2)."""
    if not docking_result_id:
        return
    try:
        r = db.query(DockingResult).filter(DockingResult.id == docking_result_id).first()
        if r:
            for k, v in kw.items():
                setattr(r, k, v)
            db.commit()
    except Exception as e:
        print(f"[docking_tasks] Error updating docking_result {docking_result_id}: {e}\n{traceback.format_exc()}", flush=True)
        db.rollback()
        if raise_on_error:
            raise e


@celery_app.task(
    bind=True,
    name="docking.run_vina",
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
)
def run_vina_task(
    self,
    job_id: int,
    protein_seq: str,
    ligand_smiles: str,
    max_pockets: int = 3,
    docking_result_id: int = None,
    receptor_pdb_path: str = None,
) -> dict:
    db = SessionLocal()
    try:
        _upd_job(
            db,
            job_id,
            status=JobStatus.running,
            celery_task_id=self.request.id,
            progress=5,
            protein_seq=protein_seq,
            ligand_smiles=ligand_smiles,
            status_message="Initialisation du docking...",
        )
        _upd_result(db, docking_result_id, status=JobStatus.running)

        from app.core.docking.vina_runner import run_scientific_docking

        def progress_callback(stage, progress, message):
            _upd_job(db, job_id, progress=progress, status_message=message)
            print(f"[Docking Job {job_id}] {stage}: {progress}% - {message}", flush=True)

        result = run_scientific_docking(
            protein_sequence=protein_seq,
            smiles=ligand_smiles,
            max_pockets=max_pockets,
            progress_callback=progress_callback,
            colabfold_binary=settings.COLABFOLD_BINARY,
            colabfold_output_dir=settings.COLABFOLD_OUTPUT_DIR,
            colabfold_num_models=settings.COLABFOLD_NUM_MODELS,
            colabfold_num_recycle=settings.COLABFOLD_NUM_RECYCLE,
            structure_provider=("external_pdb" if receptor_pdb_path else settings.STRUCTURE_PROVIDER),
            receptor_pdb_path=(receptor_pdb_path or settings.RECEPTOR_PDB_PATH or None),
            structure_identity_cutoff=settings.STRUCTURE_IDENTITY_CUTOFF,
            structure_evalue_cutoff=settings.STRUCTURE_EVALUE_CUTOFF,
        )

        if not result.get("success"):
            err = result.get("error_message") or result.get("error") or "Erreur inconnue"
            _upd_job(
                db, job_id,
                status=JobStatus.failed, error=err, progress=0, status_message=err,
            )
            _upd_result(
                db, docking_result_id,
                status=JobStatus.failed, error_message=err,
            )
            return result

        os.makedirs(settings.RESULTS_DIR, exist_ok=True)
        result_path = os.path.join(settings.RESULTS_DIR, f"job_{job_id}.json")

        def _default(o):
            try:
                import numpy as np
                if isinstance(o, (np.floating, np.integer)):
                    return o.item()
                if isinstance(o, np.ndarray):
                    return o.tolist()
            except ImportError:
                pass
            return str(o)

        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, default=_default)

        _upd_job(
            db,
            job_id,
            raise_on_error=True,
            status=JobStatus.completed,
            result_path=result_path,
            progress=100,
            error=None,
            status_message="Docking terminé avec succès",
        )
        _upd_result(
            db,
            docking_result_id,
            raise_on_error=True,
            status=JobStatus.completed,
            docking_score=result.get("vina_best_score", result.get("docking_score")),
            effective_score=result.get("boltzmann_effective_score", result.get("effective_score")),
            pose_data={"poses": result.get("poses", [])[:20]},
            modeling_method=ModelingMethod.ASP,  # Vina + agrégation Boltzmann (pas une méthode de docking)
            execution_time=result.get("execution_time"),
            error_message=None,
        )
        return {
            "job_id": job_id,
            "status": "completed",
            "num_poses": result.get("num_poses", 0),
            "docking_score": result.get("docking_score"),
        }

    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        print(f"[docking_tasks] Job {job_id} failed:\n{traceback.format_exc()}", flush=True)
        _upd_job(
            db, job_id,
            status=JobStatus.failed, error=err_msg, progress=0, status_message=err_msg,
        )
        _upd_result(db, docking_result_id, status=JobStatus.failed, error_message=err_msg)
        retryable = not any(
            s in str(exc).lower()
            for s in ("smiles", "séquence", "sequence", "invalide", "trop court", "trop long")
        )
        if retryable and self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {"job_id": job_id, "status": "failed", "error": err_msg}
    finally:
        db.close()
