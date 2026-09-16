from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text, JSON
from sqlalchemy.orm import relationship
from app.database import Base


class ResearchStudy(Base):
    __tablename__ = "research_studies"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    disease = Column(String(150), nullable=False, default="malaria")
    pathogen = Column(String(150), nullable=False, default="Plasmodium falciparum")
    country_focus = Column(String(120), nullable=False, default="Mali")
    region_scope = Column(String(120), nullable=False, default="Sahel")
    objective = Column(Text, nullable=True)
    hypothesis = Column(Text, nullable=True)
    status = Column(String(40), nullable=False, default="draft", index=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="research_studies")
    variants = relationship("ResearchVariant", back_populates="study", cascade="all, delete-orphan")
    targets = relationship("ResearchTarget", back_populates="study", cascade="all, delete-orphan")
    docking_runs = relationship("ResearchDockingRun", back_populates="study", cascade="all, delete-orphan")
    structure_comparisons = relationship("ResearchStructureComparison", back_populates="study", cascade="all, delete-orphan")
    docking_comparisons = relationship("ResearchDockingComparison", back_populates="study", cascade="all, delete-orphan")
    screening_runs = relationship("ResearchScreeningRun", back_populates="study", cascade="all, delete-orphan")


class ResearchVariant(Base):
    __tablename__ = "research_variants"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    source_analysis_id = Column(Integer, ForeignKey("analyses.id", ondelete="SET NULL"), nullable=True, index=True)
    source_accession = Column(String(120), nullable=True, index=True)
    gene_symbol = Column(String(120), nullable=True, index=True)
    nucleotide_position = Column(Integer, nullable=True)
    ref_nt = Column(String(1), nullable=True)
    alt_nt = Column(String(1), nullable=True)
    codon_ref = Column(String(3), nullable=True)
    codon_alt = Column(String(3), nullable=True)
    aa_ref = Column(String(1), nullable=True)
    aa_alt = Column(String(1), nullable=True)
    protein_position = Column(Integer, nullable=True)
    consequence = Column(String(80), nullable=True)
    annotation_method = Column(String(120), nullable=True)
    annotation_notes = Column(Text, nullable=True)
    variant_notation = Column(String(100), nullable=False)
    evidence_class = Column(String(40), nullable=False, default="candidate", index=True)
    evidence_sources = Column(JSON, nullable=False, default=list)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    study = relationship("ResearchStudy", back_populates="variants")
    targets = relationship("ResearchTarget", back_populates="variant")
    docking_runs = relationship("ResearchDockingRun", back_populates="variant")


class ResearchTarget(Base):
    __tablename__ = "research_targets"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    variant_id = Column(Integer, ForeignKey("research_variants.id", ondelete="SET NULL"), nullable=True, index=True)
    state = Column(String(20), nullable=False, default="wt", index=True)  # wt | variant
    gene_symbol = Column(String(120), nullable=True, index=True)
    protein_name = Column(String(255), nullable=True)
    protein_sequence = Column(Text, nullable=False)
    structure_source = Column(String(40), nullable=False, default="sequence_resolved")
    structure_accession = Column(String(120), nullable=True)
    structure_path = Column(Text, nullable=True)
    structure_confidence = Column(Numeric(6,4), nullable=True)
    structure_status = Column(String(50), nullable=False, default="not_generated", index=True)
    structure_sequence_identity = Column(Numeric(6,5), nullable=True)
    structure_sequence_coverage = Column(Numeric(6,5), nullable=True)
    structure_validation = Column(JSON, nullable=False, default=dict)
    provenance = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    study = relationship("ResearchStudy", back_populates="targets")
    variant = relationship("ResearchVariant", back_populates="targets")
    docking_runs = relationship("ResearchDockingRun", back_populates="target")


class ResearchDockingRun(Base):
    __tablename__ = "research_docking_runs"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = Column(Integer, ForeignKey("research_targets.id", ondelete="CASCADE"), nullable=False, index=True)
    variant_id = Column(Integer, ForeignKey("research_variants.id", ondelete="SET NULL"), nullable=True, index=True)
    docking_job_id = Column(Integer, ForeignKey("docking_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    ligand_smiles = Column(String(1000), nullable=False)
    reference_state = Column(String(20), nullable=False, default="wt")
    engine = Column(String(80), nullable=False, default="AutoDock Vina")
    aggregation_method = Column(String(120), nullable=False, default="ASP — analyse complémentaire")
    status = Column(String(30), nullable=False, default="pending", index=True)
    parameters = Column(JSON, nullable=False, default=dict)
    result_summary = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    study = relationship("ResearchStudy", back_populates="docking_runs")
    target = relationship("ResearchTarget", back_populates="docking_runs")
    variant = relationship("ResearchVariant", back_populates="docking_runs")


class ResearchStructureJob(Base):
    __tablename__ = "research_structure_jobs"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = Column(Integer, ForeignKey("research_targets.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String(40), nullable=False)
    status = Column(String(30), nullable=False, default="pending", index=True)
    celery_task_id = Column(String(255), nullable=True)
    progress = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    study = relationship("ResearchStudy")
    target = relationship("ResearchTarget")


class ResearchStructureComparison(Base):
    __tablename__ = "research_structure_comparisons"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    wt_target_id = Column(Integer, ForeignKey("research_targets.id", ondelete="CASCADE"), nullable=False, index=True)
    variant_target_id = Column(Integer, ForeignKey("research_targets.id", ondelete="CASCADE"), nullable=False, index=True)
    variant_id = Column(Integer, ForeignKey("research_variants.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(30), nullable=False, default="computed", index=True)
    parameters = Column(JSON, nullable=False, default=dict)
    report = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    study = relationship("ResearchStudy", back_populates="structure_comparisons")
    wt_target = relationship("ResearchTarget", foreign_keys=[wt_target_id])
    variant_target = relationship("ResearchTarget", foreign_keys=[variant_target_id])
    variant = relationship("ResearchVariant")


class ResearchDockingComparison(Base):
    """Paired WT/variant Vina experiment for the same ligand/protocol.

    This table stores orchestration and comparison metadata only. A score
    contrast is never interpreted as an experimental binding free-energy
    difference.
    """
    __tablename__ = "research_docking_comparisons"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    variant_id = Column(Integer, ForeignKey("research_variants.id", ondelete="SET NULL"), nullable=True, index=True)
    wt_run_id = Column(Integer, ForeignKey("research_docking_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    variant_run_id = Column(Integer, ForeignKey("research_docking_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    ligand_smiles = Column(String(1000), nullable=False)
    status = Column(String(30), nullable=False, default="pending", index=True)
    protocol = Column(JSON, nullable=False, default=dict)
    result_summary = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    study = relationship("ResearchStudy", back_populates="docking_comparisons")
    variant = relationship("ResearchVariant")
    wt_run = relationship("ResearchDockingRun", foreign_keys=[wt_run_id])
    variant_run = relationship("ResearchDockingRun", foreign_keys=[variant_run_id])
