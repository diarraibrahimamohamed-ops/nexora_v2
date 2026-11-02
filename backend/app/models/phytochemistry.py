"""Curated African natural-products candidates.

This table is intentionally separate from validated_ligands: source lists are
research leads, not verified chemical identities or evidence of efficacy.
"""
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from app.database import Base


class PhytochemicalCandidate(Base):
    __tablename__ = "phytochem_candidates"

    id = Column(Integer, primary_key=True, index=True)
    name_raw = Column(String(255), nullable=False, index=True)
    canonical_name = Column(String(255), nullable=True, index=True)
    synonyms = Column(JSON, nullable=False, default=list)

    pubchem_cid = Column(Integer, nullable=True, index=True)
    inchikey = Column(String(64), nullable=True, index=True)
    smiles_raw = Column(Text, nullable=True)
    smiles_canonical = Column(Text, nullable=True)
    formula = Column(String(120), nullable=True)
    stereochemistry = Column(Text, nullable=True)
    descriptors = Column(JSON, nullable=False, default=dict)

    source_plant = Column(String(255), nullable=True, index=True)
    accepted_taxon = Column(String(255), nullable=True, index=True)
    taxon_identifier = Column(String(120), nullable=True)
    plant_part = Column(String(120), nullable=True)
    occurrence_source = Column(Text, nullable=True)
    occurrence_status = Column(String(40), nullable=False, default="A_VÉRIFIER", index=True)

    library_source = Column(String(40), nullable=False, index=True)
    source_license = Column(String(255), nullable=True)
    source_file_sha256 = Column(String(64), nullable=True)
    source_file_path = Column(String(500), nullable=True)
    identity_source = Column(String(255), nullable=True)
    identity_reference_sha256 = Column(String(64), nullable=True)
    taxonomy_resolution_source = Column(String(255), nullable=True)
    taxonomy_cache_sha256 = Column(String(64), nullable=True)
    source_row = Column(String(128), nullable=True)
    identity_status = Column(String(40), nullable=False, default="CANDIDAT_IMPORTÉ", index=True)
    evidence_grade = Column(String(1), nullable=False, default="D", index=True)
    chemical_class = Column(String(160), nullable=True, index=True)

    target_organism = Column(String(255), nullable=True)
    target_accession = Column(String(120), nullable=True)
    assay_type = Column(String(120), nullable=True)
    endpoint_value = Column(Float, nullable=True)
    endpoint_unit = Column(String(80), nullable=True)
    study_reference = Column(Text, nullable=True)

    curator = Column(String(255), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_note = Column(Text, nullable=True)
    import_warnings = Column(JSON, nullable=False, default=list)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_phytochem_source_row_name", "library_source", "source_row", "name_raw"),
    )


class TaxonResolution(Base):
    """Auditable botanical-name resolution proposal; not specimen or occurrence proof."""
    __tablename__ = "phytochem_taxon_resolutions"
    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("phytochem_candidates.id", ondelete="CASCADE"), nullable=True, index=True)
    specimen_id = Column(Integer, ForeignKey("botanical_specimens.id", ondelete="CASCADE"), nullable=True, index=True)
    requested_name = Column(String(255), nullable=False)
    matched_name = Column(String(255))
    matched_identifier = Column(String(120))
    matched_status = Column(String(40))
    matched_rank = Column(String(40))
    accepted_name = Column(String(255))
    accepted_identifier = Column(String(120))
    accepted_status = Column(String(40))
    accepted_rank = Column(String(40))
    kingdom = Column(String(80))
    family = Column(String(160))
    match_type = Column(String(40))
    confidence = Column(Integer)
    provider = Column(String(120), nullable=False)
    checklist_key = Column(String(80), nullable=False)
    dataset_doi = Column(String(120), nullable=False)
    dataset_version = Column(String(120), nullable=False)
    source_mode = Column(String(20), nullable=False, default="LIVE")
    source_url = Column(Text, nullable=False)
    response_sha256 = Column(String(64), nullable=False)
    response_payload = Column(JSON, nullable=False, default=dict)
    review_status = Column(String(30), nullable=False, default="pending_review", index=True)
    reviewed_by = Column(String(255))
    reviewed_at = Column(DateTime)
    review_note = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    __table_args__ = (
        CheckConstraint("(candidate_id IS NOT NULL AND specimen_id IS NULL) OR (candidate_id IS NULL AND specimen_id IS NOT NULL)",
                        name="ck_phytochem_taxon_resolution_one_subject"),
        CheckConstraint("review_status IN ('pending_review','accepted','rejected','unresolved')",
                        name="ck_phytochem_taxon_resolution_review_status"),
        Index("ix_phytochem_taxon_resolution_candidate_created", "candidate_id", "created_at"),
        Index("ix_phytochem_taxon_resolution_specimen_created", "specimen_id", "created_at"),
    )
