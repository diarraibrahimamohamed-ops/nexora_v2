from sqlalchemy import (Column, Integer, String, Text, DateTime,
                        ForeignKey, Enum as SAEnum, JSON, Numeric)
from sqlalchemy.orm import relationship
from app.database import Base
from datetime import datetime
import enum


class AnalysisStatus(str, enum.Enum):
    pending   = "pending"
    running   = "running"
    completed = "completed"
    failed    = "failed"


class SeqType(str, enum.Enum):
    dna     = "dna"
    rna     = "rna"
    protein = "protein"


class Analysis(Base):
    __tablename__ = "analyses"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    name       = Column(String(255), nullable=False)
    type       = Column(String(100), nullable=False, index=True)
    data       = Column(JSON,        nullable=False, default={})
    fasta_file = Column(String(500), nullable=True)
    status     = Column(SAEnum(AnalysisStatus), default=AnalysisStatus.pending, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user               = relationship("User",             back_populates="analyses")
    sequences          = relationship("Sequence",         back_populates="analysis", cascade="all, delete-orphan")
    mutations          = relationship("Mutation",         back_populates="analysis", cascade="all, delete-orphan")
    antibiotic_profiles = relationship("AntibioticProfile", back_populates="analysis", cascade="all, delete-orphan")
    docking_results    = relationship("DockingResult",    back_populates="analysis", cascade="all, delete-orphan")


class Sequence(Base):
    __tablename__ = "sequences"
    id          = Column(Integer, primary_key=True, index=True)
    analysis_id = Column(Integer, ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True)
    accession   = Column(String(100), nullable=True, index=True)
    header      = Column(Text,        nullable=False)
    sequence    = Column(Text,        nullable=False)
    length      = Column(Integer,     nullable=False)
    type        = Column(SAEnum(SeqType), default=SeqType.dna, index=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    analysis    = relationship("Analysis", back_populates="sequences")


class Mutation(Base):
    __tablename__ = "mutations"
    id          = Column(Integer, primary_key=True, index=True)
    analysis_id = Column(Integer, ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True)
    type        = Column(String(50), nullable=False, index=True)
    ref_pos     = Column(Integer,  nullable=True)
    qry_pos     = Column(Integer,  nullable=True)
    ref_base    = Column(String(1), nullable=True)
    qry_base    = Column(String(1), nullable=True)
    length      = Column(Integer,  nullable=False, default=1)
    created_at  = Column(DateTime, default=datetime.utcnow)
    analysis    = relationship("Analysis", back_populates="mutations")


class AntibioticProfile(Base):
    __tablename__ = "antibiotic_profiles"
    id          = Column(Integer, primary_key=True, index=True)
    analysis_id = Column(Integer, ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True)
    antibiotic  = Column(String(100), nullable=False, index=True)
    status      = Column(String(50),  nullable=False, index=True)
    score       = Column(Numeric(5, 2), nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    analysis    = relationship("Analysis", back_populates="antibiotic_profiles")


class FastaFile(Base):
    __tablename__ = "fasta_files"
    id            = Column(Integer, primary_key=True, index=True)
    filename      = Column(String(255), nullable=False)
    original_name = Column(String(255), nullable=False)
    size          = Column(Integer,     nullable=False)
    mime          = Column(String(100), nullable=False)
    hash          = Column(String(64),  nullable=False, index=True)
    uploaded_by   = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    uploaded_at   = Column(DateTime, default=datetime.utcnow)
