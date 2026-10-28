"""Traceable scientific evidence attached to a N3XORA research study."""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class ResearchEvidence(Base):
    __tablename__ = "research_evidence"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    object_type = Column(String(60), nullable=False, index=True)
    object_id = Column(String(160), nullable=True, index=True)
    evidence_kind = Column(String(80), nullable=False)
    evidence_status = Column(String(40), nullable=False, default="computational_hypothesis", index=True)
    evidence_grade = Column(String(1), nullable=False, default="D", index=True)
    claim = Column(Text, nullable=False)
    organism = Column(String(255), nullable=True)
    region_context = Column(String(255), nullable=True)

    source_type = Column(String(60), nullable=True)
    source_id = Column(String(255), nullable=True)
    source_title = Column(String(500), nullable=True)
    source_url = Column(Text, nullable=True)
    study_reference = Column(Text, nullable=True)

    method = Column(String(255), nullable=True)
    tool_name = Column(String(255), nullable=True)
    tool_version = Column(String(120), nullable=True)
    reference_set_version = Column(String(255), nullable=True)
    input_sha256 = Column(String(64), nullable=True)
    output_sha256 = Column(String(64), nullable=True)

    limitations = Column(Text, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    study = relationship("ResearchStudy")
    creator = relationship("User")

    __table_args__ = (
        Index("ix_research_evidence_study_object", "study_id", "object_type", "object_id"),
        Index("ix_research_evidence_study_status", "study_id", "evidence_status"),
    )
