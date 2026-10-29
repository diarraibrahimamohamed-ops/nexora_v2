"""Persistence model for asynchronous RNA secondary-structure prediction."""
from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from app.database import Base


class RnaStructureJob(Base):
    __tablename__ = "rna_structure_jobs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)
    progress = Column(Integer, nullable=False, default=0)
    celery_task_id = Column(String(255), nullable=True)
    artifact_path = Column(String(500), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
