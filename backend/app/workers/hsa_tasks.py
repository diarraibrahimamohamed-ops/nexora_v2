"""app/workers/hsa_tasks.py — Heuristic Stability Analysis (async Celery)."""
import traceback

from app.database import SessionLocal
from app.models.hsa import HSAJob
from app.workers.celery_app import celery_app


def _upd(db, job_id, **kw):
    try:
        j = db.query(HSAJob).filter(HSAJob.id == job_id).first()
        if j:
            for k, v in kw.items():
                setattr(j, k, v)
            db.commit()
    except Exception:
        db.rollback()


def _execute_hsa(task, job_id: int, protein_seq: str, env_factors: dict) -> dict:
    """Exécute HSA et persiste statut + résultats en base (correction A1)."""
    db = SessionLocal()
    try:
        _upd(
            db,
            job_id,
            status="running",
            progress=10,
            celery_task_id=getattr(getattr(task, "request", None), "id", None),
        )

        from app.core.hsa.stability_analyzer import analyze_stability

        result = analyze_stability(protein_seq, env_factors)
        _upd(db, job_id, status="completed", progress=100, results=result, error=None)
        return {"job_id": job_id, "status": "completed", "result": result}
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        print(f"[hsa_tasks] Job {job_id} failed:\n{traceback.format_exc()}", flush=True)
        _upd(db, job_id, status="failed", progress=0, error=err_msg)
        if task.request.retries < task.max_retries:
            raise task.retry(exc=exc)
        return {"job_id": job_id, "status": "failed", "error": err_msg}
    finally:
        db.close()


@celery_app.task(name="hsa.run", bind=True, max_retries=1)
def run_hsa_task(self, job_id: int, protein_seq: str, env_factors: dict) -> dict:
    return _execute_hsa(self, job_id, protein_seq, env_factors)


# Alias nom historique pour workers / files déjà configurés
@celery_app.task(name="qsa.run", bind=True, max_retries=1)
def run_qsa_task(self, job_id: int, protein_seq: str, env_factors: dict) -> dict:
    return _execute_hsa(self, job_id, protein_seq, env_factors)
