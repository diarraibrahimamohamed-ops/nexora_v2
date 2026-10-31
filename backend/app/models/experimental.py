"""Structured experimental assay records for N3XORA research studies.

Submitted records preserve measured observations and provenance. Administrative
review does not independently establish biological truth or reproducibility.
"""
from datetime import datetime, date

from sqlalchemy import (
    Column, Date, DateTime, ForeignKey, Index, Integer, JSON, Numeric,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class ResearchAssay(Base):
    __tablename__ = "research_assays"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = Column(Integer, ForeignKey("research_targets.id", ondelete="SET NULL"), nullable=True, index=True)
    variant_id = Column(Integer, ForeignKey("research_variants.id", ondelete="SET NULL"), nullable=True, index=True)
    screening_compound_id = Column(Integer, ForeignKey("research_screening_compounds.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    reviewer_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    assay_type = Column(String(40), nullable=False, index=True)
    assay_name = Column(String(255), nullable=False)
    test_article = Column(String(255), nullable=False)
    compound_identifier = Column(String(255), nullable=True)
    canonical_smiles = Column(String(1000), nullable=True)

    source_kind = Column(String(40), nullable=False, default="lab_generated")
    source_reference = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    laboratory = Column(String(255), nullable=True)
    operator_name = Column(String(255), nullable=True)
    method_name = Column(String(255), nullable=False)
    method_version = Column(String(120), nullable=True)
    protocol_reference = Column(Text, nullable=True)
    assay_date = Column(Date, nullable=True)

    organism = Column(String(255), nullable=True)
    strain = Column(String(255), nullable=True)
    cell_line = Column(String(255), nullable=True)
    conditions = Column(JSON, nullable=False, default=dict)
    notes = Column(Text, nullable=True)

    status = Column(String(30), nullable=False, default="draft", index=True)
    review_decision = Column(String(40), nullable=True)
    review_notes = Column(Text, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    comparability_status = Column(String(30), nullable=False, default="not_assessed", index=True)
    comparability_group = Column(String(120), nullable=True, index=True)
    comparability_rationale = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    study = relationship("ResearchStudy")
    target = relationship("ResearchTarget")
    variant = relationship("ResearchVariant")
    screening_compound = relationship("ResearchScreeningCompound")
    creator = relationship("User", foreign_keys=[created_by])
    reviewer = relationship("User", foreign_keys=[reviewer_id])
    measurements = relationship("ResearchAssayMeasurement", back_populates="assay", cascade="all, delete-orphan")
    endpoints = relationship("ResearchAssayEndpoint", back_populates="assay", cascade="all, delete-orphan")
    attachments = relationship("ResearchAssayAttachment", back_populates="assay", cascade="all, delete-orphan")
    events = relationship("ResearchAssayReviewEvent", back_populates="assay", cascade="all, delete-orphan")
    analyses = relationship("ResearchAssayAnalysis", back_populates="assay", cascade="all, delete-orphan")


class ResearchAssayMeasurement(Base):
    __tablename__ = "research_assay_measurements"

    id = Column(Integer, primary_key=True, index=True)
    assay_id = Column(Integer, ForeignKey("research_assays.id", ondelete="CASCADE"), nullable=False, index=True)
    sample_role = Column(String(30), nullable=False, index=True)
    sample_label = Column(String(255), nullable=False)
    replicate_id = Column(String(80), nullable=False)
    replicate_kind = Column(String(30), nullable=False, default="biological")
    biological_sample_id = Column(String(80), nullable=True)

    concentration_value = Column(Numeric(18, 8), nullable=True)
    concentration_unit = Column(String(40), nullable=True)
    response_name = Column(String(120), nullable=False)
    response_value = Column(Numeric(18, 8), nullable=False)
    response_unit = Column(String(60), nullable=False)
    qualifier = Column(String(20), nullable=False, default="exact")
    timepoint_value = Column(Numeric(18, 8), nullable=True)
    timepoint_unit = Column(String(40), nullable=True)
    well_id = Column(String(40), nullable=True)
    record_key = Column(String(120), nullable=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    assay = relationship("ResearchAssay", back_populates="measurements")

    __table_args__ = (
        UniqueConstraint("assay_id", "record_key", name="uq_research_assay_measurement_record_key"),
        Index("ix_research_assay_measurement_replicate", "assay_id", "sample_role", "replicate_id"),
        Index("ix_research_assay_measurement_biological_sample", "assay_id", "biological_sample_id"),
    )


class ResearchAssayEndpoint(Base):
    __tablename__ = "research_assay_endpoints"

    id = Column(Integer, primary_key=True, index=True)
    assay_id = Column(Integer, ForeignKey("research_assays.id", ondelete="CASCADE"), nullable=False, index=True)
    endpoint_name = Column(String(40), nullable=False, index=True)
    value = Column(Numeric(18, 8), nullable=False)
    unit = Column(String(40), nullable=False)
    qualifier = Column(String(20), nullable=False, default="exact")
    origin = Column(String(30), nullable=False, default="measured_reported")
    method = Column(String(255), nullable=True)
    method_version = Column(String(120), nullable=True)
    confidence_low = Column(Numeric(18, 8), nullable=True)
    confidence_high = Column(Numeric(18, 8), nullable=True)
    biological_replicates = Column(Integer, nullable=True)
    source_reference = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    assay = relationship("ResearchAssay", back_populates="endpoints")


class ResearchAssayAttachment(Base):
    __tablename__ = "research_assay_attachments"

    id = Column(Integer, primary_key=True, index=True)
    assay_id = Column(Integer, ForeignKey("research_assays.id", ondelete="CASCADE"), nullable=False, index=True)
    uploaded_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    original_name = Column(String(255), nullable=False)
    stored_name = Column(String(255), nullable=False)
    storage_path = Column(Text, nullable=False)
    role = Column(String(40), nullable=False, default="raw_data")
    content_type = Column(String(120), nullable=True)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    assay = relationship("ResearchAssay", back_populates="attachments")
    uploader = relationship("User")


class ResearchAssayReviewEvent(Base):
    __tablename__ = "research_assay_review_events"

    id = Column(Integer, primary_key=True, index=True)
    assay_id = Column(Integer, ForeignKey("research_assays.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type = Column(String(40), nullable=False)
    from_status = Column(String(30), nullable=True)
    to_status = Column(String(30), nullable=False)
    decision = Column(String(40), nullable=True)
    reason = Column(Text, nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    assay = relationship("ResearchAssay", back_populates="events")
    actor = relationship("User")


class ResearchAssayAnalysis(Base):
    """Immutable, versioned statistical analysis of submitted assay observations."""

    __tablename__ = "research_assay_analyses"

    id = Column(Integer, primary_key=True, index=True)
    assay_id = Column(Integer, ForeignKey("research_assays.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    analysis_type = Column(String(60), nullable=False, default="dose_response_4pl")
    algorithm = Column(String(80), nullable=False)
    algorithm_version = Column(String(40), nullable=False)
    analysis_key = Column(String(64), nullable=True, unique=True, index=True)
    output_sha256 = Column(String(64), nullable=True, index=True)
    status = Column(String(30), nullable=False, default="completed", index=True)
    parameters = Column(JSON, nullable=False, default=dict)
    result = Column(JSON, nullable=False, default=dict)
    input_sha256 = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    assay = relationship("ResearchAssay", back_populates="analyses")
    creator = relationship("User")
    __table_args__ = (
        Index("ix_research_assay_analyses_assay_created", "assay_id", "created_at"),
    )
