from sqlalchemy import (Column, Integer, String, Text, DateTime,
                        ForeignKey, Enum as SAEnum, JSON, Numeric, SmallInteger, UniqueConstraint)
from sqlalchemy.orm import relationship
from app.database import Base
from datetime import datetime
import enum


class JobStatus(str, enum.Enum):
    """Statut unifié (A3) : pending / running / completed / failed."""
    pending   = "pending"
    running   = "running"
    completed = "completed"
    failed    = "failed"


# Même sémantique que JobStatus (table docking_results)
DockingStatus = JobStatus


class ModelingMethod(str, enum.Enum):
    """
    Enum PostgreSQL historique.

    Seule valeur réellement produite par le pipeline actuel : ASP.
    Signification formalisée : AutoDock Vina + Agrégation Statistique des Poses
    (Boltzmann) — ce n'est PAS une méthode de docking autonome.

    modeller / alphafold / homology : non implémentés (conservés pour lecture schéma).
    """
    ASP       = "ASP"
    modeller  = "modeller"
    alphafold = "alphafold"
    homology  = "homology"


class OrganismType(str, enum.Enum):
    bacteria  = "bacteria"
    virus     = "virus"
    eukaryote = "eukaryote"
    unknown   = "unknown"


class DockingJob(Base):
    """Job Celery — remplace shell_exec() synchrone PHP."""
    __tablename__ = "docking_jobs"
    id             = Column(Integer,    primary_key=True, index=True)
    user_id        = Column(Integer,    ForeignKey("users.id"), nullable=False, index=True)
    status         = Column(SAEnum(JobStatus, name="job_status", create_constraint=False),
                            default=JobStatus.pending, index=True)
    celery_task_id = Column(String(255), nullable=True)
    protein_seq    = Column(Text,        nullable=True)
    ligand_smiles  = Column(String(1000), nullable=True)
    result_path    = Column(Text,        nullable=True)
    progress       = Column(SmallInteger, nullable=False, default=0)
    error          = Column(Text,        nullable=True)
    status_message = Column(Text,        nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user           = relationship("User", back_populates="docking_jobs")


class DockingResult(Base):
    """Résultats persistés — compatible avec ancienne table docking_results."""
    __tablename__ = "docking_results"
    id               = Column(Integer, primary_key=True, index=True)
    analysis_id      = Column(Integer, ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True)
    protein_sequence = Column(Text,         nullable=False)
    ligand_smiles    = Column(String(1000), nullable=False)
    docking_score    = Column(Numeric(10,4), nullable=True, index=True)
    binding_energy   = Column(Numeric(10,4), nullable=True)
    effective_score  = Column(Numeric(10,4), nullable=True)
    effective_dg     = Column(Numeric(10,4), nullable=True)
    pose_data        = Column(JSON,          nullable=True)
    vina_log         = Column(Text,          nullable=True)
    modeling_method  = Column(
        SAEnum(ModelingMethod, name="modeling_method", create_constraint=False),
        default=ModelingMethod.ASP,
    )
    status           = Column(
        SAEnum(JobStatus, name="docking_status", create_constraint=False),
        default=JobStatus.pending,
        index=True,
    )
    error_message    = Column(Text,          nullable=True)
    execution_time   = Column(Numeric(8,3),  nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    analysis         = relationship("Analysis", back_populates="docking_results")


class ProteinMetadata(Base):
    __tablename__ = "protein_metadata"
    id                    = Column(Integer, primary_key=True, index=True)
    analysis_id           = Column(Integer, ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True)
    protein_sequence_hash = Column(String(64), nullable=False, unique=True, index=True)
    sequence_length       = Column(Integer,    nullable=False)
    organism_type         = Column(SAEnum(OrganismType), default=OrganismType.unknown, index=True)
    gene_name             = Column(String(255), nullable=True)
    protein_name          = Column(String(255), nullable=True)
    molecular_weight      = Column(Numeric(10,2), nullable=True)
    isoelectric_point     = Column(Numeric(5,2),  nullable=True)
    created_at            = Column(DateTime, default=datetime.utcnow)


class TempStructure(Base):
    __tablename__ = "temp_structures"
    __table_args__ = (UniqueConstraint("protein_sequence_hash", "modeling_method"),)
    id                    = Column(Integer, primary_key=True, index=True)
    protein_sequence_hash = Column(String(64), nullable=False, index=True)
    structure_pdb         = Column(Text,        nullable=False)
    modeling_method       = Column(
        SAEnum(ModelingMethod, name="modeling_method", create_constraint=False),
        default=ModelingMethod.ASP,
    )
    confidence_score      = Column(Numeric(5,4), nullable=True)
    expires_at            = Column(DateTime, nullable=False, index=True)
    created_at            = Column(DateTime, default=datetime.utcnow)
