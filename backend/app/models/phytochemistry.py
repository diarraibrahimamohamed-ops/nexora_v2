"""Curated African natural-products candidates.

This table is intentionally separate from validated_ligands: source lists are
research leads, not verified chemical identities or evidence of efficacy.
"""
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, Numeric, String, Text
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
    # Numeric foreign keys provide durable attribution; labels preserve an audit
    # snapshot if a user account is later deactivated or removed.
    created_by = Column(Integer, ForeignKey(
        "users.id", ondelete="SET NULL", name="fk_phytochem_taxon_resolution_created_by"
    ), nullable=True)
    created_by_label = Column(String(255))
    reviewed_by_user_id = Column(Integer, ForeignKey(
        "users.id", ondelete="SET NULL", name="fk_phytochem_taxon_resolution_reviewed_by_user"
    ), nullable=True)
    reviewed_by = Column(String(255))
    reviewed_at = Column(DateTime)
    review_note = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    __table_args__ = (
        CheckConstraint("(candidate_id IS NOT NULL AND specimen_id IS NULL) OR (candidate_id IS NULL AND specimen_id IS NOT NULL)",
                        name="ck_phytochem_taxon_resolution_one_subject"),
        CheckConstraint("review_status IN ('pending_review','accepted','rejected','unresolved')",
                        name="ck_phytochem_taxon_resolution_review_status"),
        CheckConstraint("created_by IS NULL OR reviewed_by_user_id IS NULL OR created_by <> reviewed_by_user_id",
                        name="ck_phytochem_taxon_resolution_independent_review"),
        Index("ix_phytochem_taxon_resolution_candidate_created", "candidate_id", "created_at"),
        Index("ix_phytochem_taxon_resolution_specimen_created", "specimen_id", "created_at"),
        Index("ix_phytochem_taxon_resolution_created_by", "created_by"),
        Index("ix_phytochem_taxon_resolution_reviewed_by_user", "reviewed_by_user_id"),
    )


class PhytochemicalOccurrenceEvidence(Base):
    """Append-only scientific claim linking a candidate to taxon, material and source."""
    __tablename__ = "phytochem_occurrence_evidence"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("phytochem_candidates.id", ondelete="RESTRICT"), nullable=False, index=True)
    taxon_resolution_id = Column(Integer, ForeignKey("phytochem_taxon_resolutions.id", ondelete="RESTRICT"), nullable=False, index=True)
    specimen_id = Column(Integer, ForeignKey("botanical_specimens.id", ondelete="RESTRICT"), nullable=True, index=True)
    sample_id = Column(Integer, ForeignKey("plant_material_samples.id", ondelete="RESTRICT"), nullable=True, index=True)
    extraction_batch_id = Column(Integer, ForeignKey("plant_extraction_batches.id", ondelete="RESTRICT"), nullable=True, index=True)
    fraction_id = Column(Integer, ForeignKey("plant_fractions.id", ondelete="RESTRICT"), nullable=True, index=True)
    lcms_annotation_id = Column(Integer, ForeignKey("lcms_compound_annotations.id", ondelete="RESTRICT"), nullable=True, index=True)
    evidence_type = Column(String(40), nullable=False)
    plant_part = Column(String(120), nullable=False)
    source_reference = Column(Text, nullable=False)
    source_title = Column(String(500), nullable=True)
    source_locator = Column(String(255), nullable=True)
    evidence_statement = Column(Text, nullable=False)
    analytical_method = Column(String(255), nullable=True)
    measurement_value = Column(Numeric(24, 10), nullable=True)
    measurement_unit = Column(String(80), nullable=True)
    evidence_sha256 = Column(String(64), nullable=False)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    review_status = Column(String(30), nullable=False, default="pending_review", index=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        CheckConstraint("evidence_type IN ('literature_report','lcms_annotation','isolated_compound','other_experimental')",
                        name="ck_phytochem_occurrence_evidence_type"),
        CheckConstraint("review_status IN ('pending_review','accepted','rejected','insufficient')",
                        name="ck_phytochem_occurrence_review_status"),
        CheckConstraint("(measurement_value IS NULL AND measurement_unit IS NULL) OR (measurement_value IS NOT NULL AND measurement_unit IS NOT NULL)",
                        name="ck_phytochem_occurrence_measurement_pair"),
        CheckConstraint("measurement_value IS NULL OR measurement_value > 0",
                        name="ck_phytochem_occurrence_measurement_positive"),
        CheckConstraint("sample_id IS NULL OR specimen_id IS NOT NULL",
                        name="ck_phytochem_occurrence_sample_has_specimen"),
        CheckConstraint("extraction_batch_id IS NULL OR sample_id IS NOT NULL",
                        name="ck_phytochem_occurrence_extraction_has_sample"),
        CheckConstraint("fraction_id IS NULL OR extraction_batch_id IS NOT NULL",
                        name="ck_phytochem_occurrence_fraction_has_extraction"),
        CheckConstraint("(evidence_type = 'lcms_annotation' AND lcms_annotation_id IS NOT NULL AND sample_id IS NOT NULL) OR (evidence_type <> 'lcms_annotation' AND lcms_annotation_id IS NULL)",
                        name="ck_phytochem_occurrence_lcms_lineage"),
        CheckConstraint("evidence_type = 'literature_report' OR specimen_id IS NOT NULL",
                        name="ck_phytochem_occurrence_experimental_specimen"),
        Index("ix_phytochem_occurrence_candidate_review","candidate_id","review_status","created_at"),
        Index("ix_phytochem_occurrence_taxon_part","taxon_resolution_id","plant_part"),
    )
