"""Administrator supervision API backed by PostgreSQL and Celery."""

from datetime import datetime
from typing import Optional

from celery.app.control import Inspect
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.docking import DockingJob, DockingResult, JobStatus
from app.models.hsa import HSAJob
from app.models.sequence import Analysis, AnalysisStatus
from app.models.user import User, UserStatus
from app.services.auth_service import require_admin
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/admin", tags=["admin"])


class UserStatusUpdate(BaseModel):
    status: UserStatus


class UserAdminUpdate(BaseModel):
    is_admin: bool


def _status(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _celery_snapshot() -> dict:
    try:
        inspect: Inspect = celery_app.control.inspect(timeout=1.0)
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        stats = inspect.stats() or {}
        return {
            "reachable": bool(stats),
            "workers": [
                {
                    "name": name,
                    "status": "online",
                    "active_tasks": len(active.get(name, [])),
                    "reserved_tasks": len(reserved.get(name, [])),
                    "concurrency": data.get("pool", {}).get("max-concurrency"),
                }
                for name, data in stats.items()
            ],
        }
    except Exception:
        return {"reachable": False, "workers": []}


def _redis_snapshot() -> dict:
    try:
        import redis
        client = redis.from_url(settings.REDIS_URL, socket_timeout=1)
        return {"reachable": bool(client.ping())}
    except Exception:
        return {"reachable": False}


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    db.execute(text("SELECT 1"))
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "database": {"reachable": True},
        "redis": _redis_snapshot(),
        "celery": _celery_snapshot(),
        "counts": {
            "users": db.query(func.count(User.id)).scalar() or 0,
            "active_users": db.query(func.count(User.id)).filter(User.status == UserStatus.active).scalar() or 0,
            "analyses": db.query(func.count(Analysis.id)).scalar() or 0,
            "docking_jobs": db.query(func.count(DockingJob.id)).scalar() or 0,
            "hsa_jobs": db.query(func.count(HSAJob.id)).scalar() or 0,
            "failed_docking": db.query(func.count(DockingJob.id)).filter(DockingJob.status == JobStatus.failed).scalar() or 0,
        },
    }


@router.get("/users")
def users(
    q: Optional[str] = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    query = db.query(User).order_by(User.created_at.desc())
    if q:
        pattern = f"%{q.strip()}%"
        query = query.filter((User.username.ilike(pattern)) | (User.email.ilike(pattern)))
    return {
        "items": [
            {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "status": _status(user.status),
                "is_admin": bool(user.is_admin),
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "last_login": user.last_login.isoformat() if user.last_login else None,
            }
            for user in query.limit(limit).all()
        ]
    }


@router.patch("/users/{user_id}/status")
def update_user_status(
    user_id: int,
    body: UserStatusUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")
    if user.id == admin.id and body.status != UserStatus.active:
        raise HTTPException(400, "Un administrateur ne peut pas désactiver son propre compte")
    user.status = body.status
    db.commit()
    return {"success": True, "user_id": user.id, "status": _status(user.status)}


@router.patch("/users/{user_id}/admin")
def update_user_admin(
    user_id: int,
    body: UserAdminUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")
    if user.id == admin.id and not body.is_admin:
        raise HTTPException(400, "Un administrateur ne peut pas retirer ses propres droits")
    user.is_admin = body.is_admin
    db.commit()
    return {"success": True, "user_id": user.id, "is_admin": bool(user.is_admin)}


@router.get("/analyses")
def analyses(
    status: Optional[str] = Query(None, max_length=30),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    query = db.query(Analysis, User.username).outerjoin(User, Analysis.user_id == User.id).order_by(Analysis.created_at.desc())
    if status:
        query = query.filter(Analysis.status == status)
    return {
        "items": [
            {
                "id": analysis.id,
                "name": analysis.name,
                "type": analysis.type,
                "status": _status(analysis.status),
                "username": username,
                "created_at": analysis.created_at.isoformat() if analysis.created_at else None,
                "updated_at": analysis.updated_at.isoformat() if analysis.updated_at else None,
            }
            for analysis, username in query.limit(limit).all()
        ]
    }


@router.delete("/analyses/{analysis_id}")
def delete_analysis(
    analysis_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "Analyse introuvable")
    db.delete(analysis)
    db.commit()
    return {"success": True, "analysis_id": analysis_id}


@router.get("/jobs")
def jobs(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    docking = db.query(DockingJob, User.username).join(User, DockingJob.user_id == User.id).order_by(DockingJob.created_at.desc()).limit(limit).all()
    hsa = db.query(HSAJob, User.username).join(User, HSAJob.user_id == User.id).order_by(HSAJob.created_at.desc()).limit(limit).all()
    items = [
        {
            "kind": "docking", "id": job.id, "username": username,
            "status": _status(job.status), "progress": job.progress,
            "message": job.status_message, "error": job.error,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }
        for job, username in docking
    ]
    items.extend(
        {
            "kind": "hsa", "id": job.id, "username": username,
            "status": job.status, "progress": job.progress,
            "message": None, "error": job.error,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }
        for job, username in hsa
    )
    return {"items": sorted(items, key=lambda item: item["created_at"] or "", reverse=True)[:limit]}


@router.post("/jobs/{kind}/{job_id}/revoke")
def revoke_job(
    kind: str,
    job_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    job = db.query(DockingJob).filter(DockingJob.id == job_id).first() if kind == "docking" else db.query(HSAJob).filter(HSAJob.id == job_id).first() if kind == "hsa" else None
    if not job:
        raise HTTPException(404, "Job introuvable")
    if job.celery_task_id:
        celery_app.control.revoke(job.celery_task_id, terminate=False)
    job.status = JobStatus.failed if kind == "docking" else "failed"
    job.error = "Révoqué par un administrateur"
    db.commit()
    return {"success": True, "kind": kind, "job_id": job_id}