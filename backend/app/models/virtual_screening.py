from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import relationship
from app.database import Base


class ResearchScreeningRun(Base):
    __tablename__ = "research_screening_runs"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = Column(Integer, ForeignKey("research_targets.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="prepared", index=True)
    engine = Column(String(80), nullable=False, default="AutoDock Vina")
    protocol = Column(JSON, nullable=False, default=dict)
    summary = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    study = relationship("ResearchStudy", back_populates="screening_runs")
    target = relationship("ResearchTarget")
    compounds = relationship("ResearchScreeningCompound", back_populates="screening", cascade="all, delete-orphan")


class ResearchScreeningCompound(Base):
    __tablename__ = "research_screening_compounds"

    id = Column(Integer, primary_key=True, index=True)
    screening_id = Column(Integer, ForeignKey("research_screening_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    input_index = Column(Integer, nullable=False)
    canonical_smiles = Column(String(1000), nullable=False)
    descriptors = Column(JSON, nullable=False, default=dict)
    qc = Column(JSON, nullable=False, default=dict)
    status = Column(String(30), nullable=False, default="accepted", index=True)
    docking_job_id = Column(Integer, ForeignKey("docking_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    research_docking_run_id = Column(Integer, ForeignKey("research_docking_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    docking_summary = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    screening = relationship("ResearchScreeningRun", back_populates="compounds")
