"""Traceable scientific evidence attached to a N3XORA research study."""
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text
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
    review_status = Column(String(24), nullable=False, default="pending_review", index=True)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    study = relationship("ResearchStudy")
    creator = relationship("User", foreign_keys=[created_by])
    reviewer = relationship("User", foreign_keys=[reviewed_by])

    __table_args__ = (
        Index("ix_research_evidence_study_object", "study_id", "object_type", "object_id"),
        Index("ix_research_evidence_study_status", "study_id", "evidence_status"),
        CheckConstraint("review_status IN ('pending_review', 'accepted', 'rejected')", name="ck_research_evidence_review_status"),
        CheckConstraint("created_by IS NULL OR reviewed_by IS NULL OR created_by <> reviewed_by", name="ck_research_evidence_independent_review"),
        CheckConstraint(
            "(review_status = 'pending_review' AND reviewed_by IS NULL AND reviewed_at IS NULL) "
            "OR (review_status IN ('accepted', 'rejected') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_research_evidence_review_attribution",
        ),
    )
