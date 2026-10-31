"""Botanical material, LC-MS/MS acquisition and spectral evidence records.

The tables capture lineage and evidence metadata. Raw/vendor files and large
spectral artifacts are stored outside PostgreSQL and referenced by SHA-256.
No model status implies validated chemical identity or biological activity.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKey,
    Index, Integer, JSON, Numeric, String, Text, UniqueConstraint,
)

from app.database import Base


class BotanicalSpecimen(Base):
    __tablename__ = "botanical_specimens"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    specimen_code = Column(String(120), nullable=False)
    reported_taxon = Column(String(255), nullable=False)
    accepted_taxon = Column(String(255), nullable=True, index=True)
    taxon_identifier = Column(String(120), nullable=True, index=True)
    taxonomy_source = Column(String(255), nullable=True)
    identification_status = Column(String(30), nullable=False, default="unverified", index=True)
    identification_method = Column(String(255), nullable=True)
    identifier_name = Column(String(255), nullable=True)
    identified_at = Column(Date, nullable=True)
    voucher_code = Column(String(120), nullable=True, index=True)
    herbarium_name = Column(String(255), nullable=True)
    collector_name = Column(String(255), nullable=True)
    collection_date = Column(Date, nullable=True)
    country = Column(String(120), nullable=True)
    admin_region = Column(String(160), nullable=True)
    locality = Column(Text, nullable=True)
    latitude = Column(Numeric(9, 6), nullable=True)
    longitude = Column(Numeric(9, 6), nullable=True)
    coordinate_uncertainty_m = Column(Numeric(12, 2), nullable=True)
    coordinate_access = Column(String(20), nullable=False, default="restricted")
    field_notes = Column(Text, nullable=True)
    evidence_references = Column(JSON, nullable=False, default=list)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("study_id", "specimen_code", name="uq_botanical_specimen_study_code"),
        CheckConstraint("latitude IS NULL OR (latitude >= -90 AND latitude <= 90)", name="ck_botanical_specimen_latitude"),
        CheckConstraint("longitude IS NULL OR (longitude >= -180 AND longitude <= 180)", name="ck_botanical_specimen_longitude"),
        CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="ck_botanical_specimen_coordinate_pair"),
        CheckConstraint("coordinate_access IN ('restricted', 'coarse', 'public')", name="ck_botanical_specimen_coordinate_access"),
        CheckConstraint("identification_status IN ('unverified', 'provisional', 'verified', 'rejected')", name="ck_botanical_specimen_identification_status"),
        Index("ix_botanical_specimens_study_taxon", "study_id", "accepted_taxon"),
    )


class PlantMaterialSample(Base):
    __tablename__ = "plant_material_samples"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    specimen_id = Column(Integer, ForeignKey("botanical_specimens.id", ondelete="RESTRICT"), nullable=False, index=True)
    sample_code = Column(String(120), nullable=False)
    plant_part = Column(String(120), nullable=False)
    material_state = Column(String(30), nullable=False, default="fresh")
    collection_date = Column(Date, nullable=True)
    collector_name = Column(String(255), nullable=True)
    initial_mass_g = Column(Numeric(14, 6), nullable=True)
    moisture_percent = Column(Numeric(6, 3), nullable=True)
    preservation_method = Column(String(255), nullable=True)
    storage_conditions = Column(JSON, nullable=False, default=dict)
    storage_location = Column(String(255), nullable=True)
    sample_status = Column(String(30), nullable=False, default="registered")
    notes = Column(Text, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("study_id", "sample_code", name="uq_plant_sample_study_code"),
        CheckConstraint("initial_mass_g IS NULL OR initial_mass_g > 0", name="ck_plant_sample_mass_positive"),
        CheckConstraint("moisture_percent IS NULL OR (moisture_percent >= 0 AND moisture_percent <= 100)", name="ck_plant_sample_moisture_range"),
        CheckConstraint("material_state IN ('fresh', 'dried', 'frozen', 'powdered', 'other')", name="ck_plant_sample_material_state"),
        Index("ix_plant_samples_study_specimen", "study_id", "specimen_id"),
    )


class ExtractionBatch(Base):
    __tablename__ = "plant_extraction_batches"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    sample_id = Column(Integer, ForeignKey("plant_material_samples.id", ondelete="RESTRICT"), nullable=False, index=True)
    batch_code = Column(String(120), nullable=False)
    extraction_method = Column(String(160), nullable=False)
    method_version = Column(String(120), nullable=True)
    protocol_reference = Column(Text, nullable=True)
    solvent_system = Column(JSON, nullable=False, default=list)
    solid_liquid_ratio = Column(String(80), nullable=True)
    input_mass_g = Column(Numeric(14, 6), nullable=True)
    extract_mass_g = Column(Numeric(14, 6), nullable=True)
    yield_percent = Column(Numeric(9, 4), nullable=True)
    temperature_c = Column(Numeric(7, 3), nullable=True)
    duration_minutes = Column(Numeric(12, 3), nullable=True)
    ph_value = Column(Numeric(6, 3), nullable=True)
    operator_name = Column(String(255), nullable=True)
    performed_at = Column(DateTime, nullable=True)
    status = Column(String(30), nullable=False, default="registered")
    notes = Column(Text, nullable=True)
    parameters = Column(JSON, nullable=False, default=dict)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("study_id", "batch_code", name="uq_extraction_study_batch_code"),
        CheckConstraint("input_mass_g IS NULL OR input_mass_g > 0", name="ck_extraction_input_mass_positive"),
        CheckConstraint("extract_mass_g IS NULL OR extract_mass_g >= 0", name="ck_extraction_output_mass_nonnegative"),
        CheckConstraint("yield_percent IS NULL OR yield_percent >= 0", name="ck_extraction_yield_nonnegative"),
        Index("ix_extraction_batches_study_sample", "study_id", "sample_id"),
    )


class PlantFraction(Base):
    __tablename__ = "plant_fractions"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    extraction_batch_id = Column(Integer, ForeignKey("plant_extraction_batches.id", ondelete="RESTRICT"), nullable=False, index=True)
    fraction_code = Column(String(120), nullable=False)
    fraction_order = Column(Integer, nullable=True)
    separation_method = Column(String(160), nullable=False)
    method_version = Column(String(120), nullable=True)
    elution_system = Column(JSON, nullable=False, default=list)
    collected_volume_ml = Column(Numeric(14, 6), nullable=True)
    recovered_mass_g = Column(Numeric(14, 6), nullable=True)
    yield_percent = Column(Numeric(9, 4), nullable=True)
    status = Column(String(30), nullable=False, default="registered")
    notes = Column(Text, nullable=True)
    parameters = Column(JSON, nullable=False, default=dict)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("extraction_batch_id", "fraction_code", name="uq_plant_fraction_batch_code"),
        CheckConstraint("fraction_order IS NULL OR fraction_order >= 1", name="ck_plant_fraction_order_positive"),
        CheckConstraint("collected_volume_ml IS NULL OR collected_volume_ml >= 0", name="ck_plant_fraction_volume_nonnegative"),
        CheckConstraint("recovered_mass_g IS NULL OR recovered_mass_g >= 0", name="ck_plant_fraction_mass_nonnegative"),
    )


class LcmsAnalyticalBatch(Base):
    __tablename__ = "lcms_analytical_batches"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_code = Column(String(120), nullable=False)
    instrument_vendor = Column(String(120), nullable=False)
    instrument_model = Column(String(160), nullable=False)
    mass_analyzer = Column(String(120), nullable=True)
    ion_source = Column(String(120), nullable=True)
    chromatography_column = Column(String(255), nullable=True)
    mobile_phase_a = Column(Text, nullable=True)
    mobile_phase_b = Column(Text, nullable=True)
    gradient_program = Column(JSON, nullable=False, default=list)
    flow_rate = Column(Numeric(12, 6), nullable=True)
    flow_rate_unit = Column(String(40), nullable=True)
    column_temperature_c = Column(Numeric(7, 3), nullable=True)
    injection_volume = Column(Numeric(12, 6), nullable=True)
    injection_volume_unit = Column(String(40), nullable=True)
    acquisition_method = Column(String(255), nullable=False)
    acquisition_method_version = Column(String(120), nullable=True)
    polarity_modes = Column(JSON, nullable=False, default=list)
    operator_name = Column(String(255), nullable=True)
    acquired_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    qc_policy = Column(JSON, nullable=False, default=dict)
    status = Column(String(30), nullable=False, default="planned", index=True)
    notes = Column(Text, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("study_id", "batch_code", name="uq_lcms_batch_study_code"),
        CheckConstraint("flow_rate IS NULL OR flow_rate > 0", name="ck_lcms_batch_flow_rate_positive"),
        CheckConstraint("injection_volume IS NULL OR injection_volume > 0", name="ck_lcms_batch_injection_volume_positive"),
    )


class LcmsInjection(Base):
    __tablename__ = "lcms_injections"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_id = Column(Integer, ForeignKey("lcms_analytical_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    injection_order = Column(Integer, nullable=False)
    injection_type = Column(String(40), nullable=False)
    sample_label = Column(String(255), nullable=False)
    plant_sample_id = Column(Integer, ForeignKey("plant_material_samples.id", ondelete="RESTRICT"), nullable=True, index=True)
    fraction_id = Column(Integer, ForeignKey("plant_fractions.id", ondelete="RESTRICT"), nullable=True, index=True)
    technical_replicate = Column(Integer, nullable=True)
    injection_volume = Column(Numeric(12, 6), nullable=True)
    injection_volume_unit = Column(String(40), nullable=True)
    acquisition_reference = Column(String(255), nullable=True)
    status = Column(String(30), nullable=False, default="planned")
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("batch_id", "injection_order", name="uq_lcms_injection_batch_order"),
        CheckConstraint("injection_order >= 1", name="ck_lcms_injection_order_positive"),
        CheckConstraint("plant_sample_id IS NULL OR fraction_id IS NULL", name="ck_lcms_injection_single_material_source"),
        CheckConstraint("technical_replicate IS NULL OR technical_replicate >= 1", name="ck_lcms_injection_replicate_positive"),
    )


class LcmsDataArtifact(Base):
    __tablename__ = "lcms_data_artifacts"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    injection_id = Column(Integer, ForeignKey("lcms_injections.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_artifact_id = Column(Integer, ForeignKey("lcms_data_artifacts.id", ondelete="RESTRICT"), nullable=True, index=True)
    artifact_role = Column(String(40), nullable=False)
    data_format = Column(String(40), nullable=False)
    original_filename = Column(String(255), nullable=False)
    storage_path = Column(Text, nullable=False)
    content_type = Column(String(120), nullable=True)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    conversion_tool = Column(String(160), nullable=True)
    conversion_version = Column(String(120), nullable=True)
    conversion_parameters = Column(JSON, nullable=False, default=dict)
    validation_status = Column(String(40), nullable=False, default="checksum_verified")
    uploaded_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("injection_id", "artifact_role", "sha256", name="uq_lcms_artifact_injection_role_sha"),
        CheckConstraint("size_bytes > 0", name="ck_lcms_artifact_size_positive"),
        Index("ix_lcms_artifacts_injection_role", "injection_id", "artifact_role"),
    )


class LcmsQualityControlObservation(Base):
    __tablename__ = "lcms_quality_control_observations"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_id = Column(Integer, ForeignKey("lcms_analytical_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    injection_id = Column(Integer, ForeignKey("lcms_injections.id", ondelete="SET NULL"), nullable=True, index=True)
    qc_type = Column(String(40), nullable=False)
    metric_name = Column(String(120), nullable=False)
    metric_value = Column(Numeric(24, 10), nullable=True)
    metric_unit = Column(String(60), nullable=True)
    status = Column(String(20), nullable=False, default="not_assessed")
    assessment_method = Column(String(30), nullable=False, default="manual")
    acceptance_criteria = Column(JSON, nullable=False, default=dict)
    rationale = Column(Text, nullable=True)
    assessed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    assessed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        CheckConstraint("status IN ('not_assessed', 'pass', 'warning', 'fail')", name="ck_lcms_qc_status"),
        CheckConstraint("assessment_method IN ('manual', 'calculated', 'imported')", name="ck_lcms_qc_assessment_method"),
        Index("ix_lcms_qc_batch_type_status", "batch_id", "qc_type", "status"),
    )


class LcmsSpectralLibrary(Base):
    __tablename__ = "lcms_spectral_libraries"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(160), nullable=False)
    version = Column(String(120), nullable=False)
    source_url = Column(Text, nullable=True)
    source_license = Column(String(255), nullable=False)
    data_format = Column(String(40), nullable=True)
    reference_set_sha256 = Column(String(64), nullable=True)
    citation = Column(Text, nullable=True)
    coverage_notes = Column(Text, nullable=True)
    curation_status = Column(String(30), nullable=False, default="registered")
    registered_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_lcms_spectral_library_name_version"),
        CheckConstraint("curation_status IN ('registered', 'reviewed', 'rejected')", name="ck_lcms_library_curation_status"),
    )


class LcmsProcessingRun(Base):
    __tablename__ = "lcms_processing_runs"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_id = Column(Integer, ForeignKey("lcms_analytical_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    spectral_library_id = Column(Integer, ForeignKey("lcms_spectral_libraries.id", ondelete="SET NULL"), nullable=True, index=True)
    software_name = Column(String(160), nullable=False)
    software_version = Column(String(120), nullable=False)
    workflow_name = Column(String(160), nullable=False)
    workflow_version = Column(String(120), nullable=True)
    parameters = Column(JSON, nullable=False, default=dict)
    input_sha256 = Column(String(64), nullable=False)
    output_sha256 = Column(String(64), nullable=True)
    status = Column(String(30), nullable=False, default="registered", index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    provenance_manifest = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        CheckConstraint("status IN ('registered', 'processing', 'completed', 'failed')", name="ck_lcms_processing_status"),
    )


class LcmsFeature(Base):
    __tablename__ = "lcms_features"

    id = Column(Integer, primary_key=True, index=True)
    processing_run_id = Column(Integer, ForeignKey("lcms_processing_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    feature_key = Column(String(120), nullable=False)
    precursor_mz = Column(Numeric(18, 8), nullable=False)
    retention_time = Column(Numeric(18, 8), nullable=False)
    retention_time_unit = Column(String(30), nullable=False, default="second")
    polarity = Column(String(20), nullable=False)
    charge = Column(Integer, nullable=True)
    adduct = Column(String(80), nullable=True)
    molecular_formula_candidate = Column(String(160), nullable=True)
    isotope_group = Column(String(120), nullable=True)
    quality_flags = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("processing_run_id", "feature_key", name="uq_lcms_feature_processing_key"),
        CheckConstraint("precursor_mz > 0", name="ck_lcms_feature_mz_positive"),
        CheckConstraint("retention_time >= 0", name="ck_lcms_feature_rt_nonnegative"),
        CheckConstraint("polarity IN ('positive', 'negative', 'unknown')", name="ck_lcms_feature_polarity"),
    )


class LcmsFeatureObservation(Base):
    __tablename__ = "lcms_feature_observations"

    id = Column(Integer, primary_key=True, index=True)
    feature_id = Column(Integer, ForeignKey("lcms_features.id", ondelete="CASCADE"), nullable=False, index=True)
    injection_id = Column(Integer, ForeignKey("lcms_injections.id", ondelete="CASCADE"), nullable=False, index=True)
    observed_mz = Column(Numeric(18, 8), nullable=True)
    retention_time = Column(Numeric(18, 8), nullable=True)
    peak_area = Column(Numeric(24, 8), nullable=True)
    peak_height = Column(Numeric(24, 8), nullable=True)
    intensity = Column(Numeric(24, 8), nullable=True)
    signal_to_noise = Column(Numeric(18, 8), nullable=True)
    quality_flag = Column(String(40), nullable=False, default="not_assessed")
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("feature_id", "injection_id", name="uq_lcms_feature_observation_feature_injection"),
        CheckConstraint("observed_mz IS NULL OR observed_mz > 0", name="ck_lcms_feature_observation_mz_positive"),
        CheckConstraint("retention_time IS NULL OR retention_time >= 0", name="ck_lcms_feature_observation_rt_nonnegative"),
        CheckConstraint("peak_area IS NULL OR peak_area >= 0", name="ck_lcms_feature_observation_area_nonnegative"),
        CheckConstraint("peak_height IS NULL OR peak_height >= 0", name="ck_lcms_feature_observation_height_nonnegative"),
        CheckConstraint("intensity IS NULL OR intensity >= 0", name="ck_lcms_feature_observation_intensity_nonnegative"),
    )


class LcmsMs2Spectrum(Base):
    __tablename__ = "lcms_ms2_spectra"

    id = Column(Integer, primary_key=True, index=True)
    feature_id = Column(Integer, ForeignKey("lcms_features.id", ondelete="CASCADE"), nullable=False, index=True)
    injection_id = Column(Integer, ForeignKey("lcms_injections.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_id = Column(Integer, ForeignKey("lcms_data_artifacts.id", ondelete="SET NULL"), nullable=True, index=True)
    scan_identifier = Column(String(120), nullable=True)
    precursor_mz = Column(Numeric(18, 8), nullable=False)
    isolation_window = Column(Numeric(12, 6), nullable=True)
    collision_energy = Column(Numeric(12, 6), nullable=True)
    collision_energy_unit = Column(String(40), nullable=True)
    polarity = Column(String(20), nullable=False)
    charge = Column(Integer, nullable=True)
    fragments = Column(JSON, nullable=False, default=list)
    acquired_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        CheckConstraint("precursor_mz > 0", name="ck_lcms_ms2_precursor_positive"),
        CheckConstraint("polarity IN ('positive', 'negative', 'unknown')", name="ck_lcms_ms2_polarity"),
    )


class LcmsCompoundAnnotation(Base):
    __tablename__ = "lcms_compound_annotations"

    id = Column(Integer, primary_key=True, index=True)
    feature_id = Column(Integer, ForeignKey("lcms_features.id", ondelete="CASCADE"), nullable=False, index=True)
    phytochem_candidate_id = Column(Integer, ForeignKey("phytochem_candidates.id", ondelete="SET NULL"), nullable=True, index=True)
    spectral_library_id = Column(Integer, ForeignKey("lcms_spectral_libraries.id", ondelete="SET NULL"), nullable=True, index=True)
    reference_standard_injection_id = Column(Integer, ForeignKey("lcms_injections.id", ondelete="SET NULL"), nullable=True, index=True)
    annotation_method = Column(String(40), nullable=False)
    annotation_name = Column(String(255), nullable=True)
    molecular_formula = Column(String(160), nullable=True)
    inchikey = Column(String(64), nullable=True)
    smiles = Column(Text, nullable=True)
    msi_level = Column(Integer, nullable=False)
    score_type = Column(String(80), nullable=True)
    score_value = Column(Numeric(18, 8), nullable=True)
    evidence = Column(JSON, nullable=False, default=dict)
    status = Column(String(30), nullable=False, default="candidate", index=True)
    review_rationale = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        CheckConstraint("msi_level BETWEEN 1 AND 4", name="ck_lcms_annotation_msi_level"),
        CheckConstraint("annotation_method IN ('library_match', 'authentic_standard', 'in_silico', 'formula_prediction', 'manual')", name="ck_lcms_annotation_method"),
        CheckConstraint("status IN ('candidate', 'pending_review', 'accepted', 'rejected')", name="ck_lcms_annotation_status"),
    )
