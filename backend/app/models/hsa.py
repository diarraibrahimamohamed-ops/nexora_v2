"""
app/models/hsa.py — Heuristic Stability Analysis Job model (ex-QSA).
"""
from sqlalchemy import Column, Integer, String, Text, JSON, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class HSAJob(Base):
    """Job Celery HSA — table historique qsa_jobs conservée pour compatibilité DB."""

    __tablename__ = "qsa_jobs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    protein_sequence = Column(Text, nullable=False)
    env_factors = Column(JSON, default={})
    status = Column(String, default="pending")  # pending, running, completed, failed
    progress = Column(Integer, default=0)
    results = Column(JSON, default={})
    error = Column(Text, nullable=True)
    celery_task_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="hsa_jobs")


# Alias de compatibilité temporaire
QSAJob = HSAJob
