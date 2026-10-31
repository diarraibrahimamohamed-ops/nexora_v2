-- N3XORA Phase 1G — botanical chain of custody and LC-MS/MS evidence domain.
-- Additive only. Existing phytochem_candidates / ResearchAssay rows are untouched.
BEGIN;

CREATE TABLE IF NOT EXISTS botanical_specimens (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    specimen_code VARCHAR(120) NOT NULL,
    reported_taxon VARCHAR(255) NOT NULL,
    accepted_taxon VARCHAR(255),
    taxon_identifier VARCHAR(120),
    taxonomy_source VARCHAR(255),
    identification_status VARCHAR(30) NOT NULL DEFAULT 'unverified',
    identification_method VARCHAR(255),
    identifier_name VARCHAR(255),
    identified_at DATE,
    voucher_code VARCHAR(120),
    herbarium_name VARCHAR(255),
    collector_name VARCHAR(255),
    collection_date DATE,
    country VARCHAR(120),
    admin_region VARCHAR(160),
    locality TEXT,
    latitude NUMERIC(9,6),
    longitude NUMERIC(9,6),
    coordinate_uncertainty_m NUMERIC(12,2),
    coordinate_access VARCHAR(20) NOT NULL DEFAULT 'restricted',
    field_notes TEXT,
    evidence_references JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_botanical_specimen_study_code UNIQUE(study_id, specimen_code),
    CONSTRAINT ck_botanical_specimen_latitude CHECK(latitude IS NULL OR (latitude >= -90 AND latitude <= 90)),
    CONSTRAINT ck_botanical_specimen_longitude CHECK(longitude IS NULL OR (longitude >= -180 AND longitude <= 180)),
    CONSTRAINT ck_botanical_specimen_coordinate_pair CHECK((latitude IS NULL) = (longitude IS NULL)),
    CONSTRAINT ck_botanical_specimen_coordinate_access CHECK(coordinate_access IN ('restricted','coarse','public')),
    CONSTRAINT ck_botanical_specimen_identification_status CHECK(identification_status IN ('unverified','provisional','verified','rejected'))
);
CREATE INDEX IF NOT EXISTS ix_botanical_specimens_study_id ON botanical_specimens(study_id);
CREATE INDEX IF NOT EXISTS ix_botanical_specimens_accepted_taxon ON botanical_specimens(accepted_taxon);
CREATE INDEX IF NOT EXISTS ix_botanical_specimens_taxon_identifier ON botanical_specimens(taxon_identifier);
CREATE INDEX IF NOT EXISTS ix_botanical_specimens_voucher_code ON botanical_specimens(voucher_code);
CREATE INDEX IF NOT EXISTS ix_botanical_specimens_created_by ON botanical_specimens(created_by);
CREATE INDEX IF NOT EXISTS ix_botanical_specimens_study_taxon ON botanical_specimens(study_id, accepted_taxon);

CREATE TABLE IF NOT EXISTS plant_material_samples (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    specimen_id INT NOT NULL REFERENCES botanical_specimens(id) ON DELETE RESTRICT,
    sample_code VARCHAR(120) NOT NULL,
    plant_part VARCHAR(120) NOT NULL,
    material_state VARCHAR(30) NOT NULL DEFAULT 'fresh',
    collection_date DATE,
    collector_name VARCHAR(255),
    initial_mass_g NUMERIC(14,6),
    moisture_percent NUMERIC(6,3),
    preservation_method VARCHAR(255),
    storage_conditions JSONB NOT NULL DEFAULT '{}'::jsonb,
    storage_location VARCHAR(255),
    sample_status VARCHAR(30) NOT NULL DEFAULT 'registered',
    notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_plant_sample_study_code UNIQUE(study_id, sample_code),
    CONSTRAINT ck_plant_sample_mass_positive CHECK(initial_mass_g IS NULL OR initial_mass_g > 0),
    CONSTRAINT ck_plant_sample_moisture_range CHECK(moisture_percent IS NULL OR (moisture_percent >= 0 AND moisture_percent <= 100)),
    CONSTRAINT ck_plant_sample_material_state CHECK(material_state IN ('fresh','dried','frozen','powdered','other'))
);
CREATE INDEX IF NOT EXISTS ix_plant_material_samples_study_id ON plant_material_samples(study_id);
CREATE INDEX IF NOT EXISTS ix_plant_material_samples_specimen_id ON plant_material_samples(specimen_id);
CREATE INDEX IF NOT EXISTS ix_plant_material_samples_created_by ON plant_material_samples(created_by);
CREATE INDEX IF NOT EXISTS ix_plant_samples_study_specimen ON plant_material_samples(study_id, specimen_id);

CREATE TABLE IF NOT EXISTS plant_extraction_batches (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    sample_id INT NOT NULL REFERENCES plant_material_samples(id) ON DELETE RESTRICT,
    batch_code VARCHAR(120) NOT NULL,
    extraction_method VARCHAR(160) NOT NULL,
    method_version VARCHAR(120),
    protocol_reference TEXT,
    solvent_system JSONB NOT NULL DEFAULT '[]'::jsonb,
    solid_liquid_ratio VARCHAR(80),
    input_mass_g NUMERIC(14,6),
    extract_mass_g NUMERIC(14,6),
    yield_percent NUMERIC(9,4),
    temperature_c NUMERIC(7,3),
    duration_minutes NUMERIC(12,3),
    ph_value NUMERIC(6,3),
    operator_name VARCHAR(255),
    performed_at TIMESTAMP,
    status VARCHAR(30) NOT NULL DEFAULT 'registered',
    notes TEXT,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_extraction_study_batch_code UNIQUE(study_id, batch_code),
    CONSTRAINT ck_extraction_input_mass_positive CHECK(input_mass_g IS NULL OR input_mass_g > 0),
    CONSTRAINT ck_extraction_output_mass_nonnegative CHECK(extract_mass_g IS NULL OR extract_mass_g >= 0),
    CONSTRAINT ck_extraction_yield_nonnegative CHECK(yield_percent IS NULL OR yield_percent >= 0)
);
CREATE INDEX IF NOT EXISTS ix_plant_extraction_batches_study_id ON plant_extraction_batches(study_id);
CREATE INDEX IF NOT EXISTS ix_plant_extraction_batches_sample_id ON plant_extraction_batches(sample_id);
CREATE INDEX IF NOT EXISTS ix_plant_extraction_batches_created_by ON plant_extraction_batches(created_by);
CREATE INDEX IF NOT EXISTS ix_extraction_batches_study_sample ON plant_extraction_batches(study_id, sample_id);

CREATE TABLE IF NOT EXISTS plant_fractions (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    extraction_batch_id INT NOT NULL REFERENCES plant_extraction_batches(id) ON DELETE RESTRICT,
    fraction_code VARCHAR(120) NOT NULL,
    fraction_order INT,
    separation_method VARCHAR(160) NOT NULL,
    method_version VARCHAR(120),
    elution_system JSONB NOT NULL DEFAULT '[]'::jsonb,
    collected_volume_ml NUMERIC(14,6),
    recovered_mass_g NUMERIC(14,6),
    yield_percent NUMERIC(9,4),
    status VARCHAR(30) NOT NULL DEFAULT 'registered',
    notes TEXT,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_plant_fraction_batch_code UNIQUE(extraction_batch_id, fraction_code),
    CONSTRAINT ck_plant_fraction_order_positive CHECK(fraction_order IS NULL OR fraction_order >= 1),
    CONSTRAINT ck_plant_fraction_volume_nonnegative CHECK(collected_volume_ml IS NULL OR collected_volume_ml >= 0),
    CONSTRAINT ck_plant_fraction_mass_nonnegative CHECK(recovered_mass_g IS NULL OR recovered_mass_g >= 0)
);
CREATE INDEX IF NOT EXISTS ix_plant_fractions_study_id ON plant_fractions(study_id);
CREATE INDEX IF NOT EXISTS ix_plant_fractions_extraction_batch_id ON plant_fractions(extraction_batch_id);
CREATE INDEX IF NOT EXISTS ix_plant_fractions_created_by ON plant_fractions(created_by);

CREATE TABLE IF NOT EXISTS lcms_analytical_batches (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    batch_code VARCHAR(120) NOT NULL,
    instrument_vendor VARCHAR(120) NOT NULL,
    instrument_model VARCHAR(160) NOT NULL,
    mass_analyzer VARCHAR(120),
    ion_source VARCHAR(120),
    chromatography_column VARCHAR(255),
    mobile_phase_a TEXT,
    mobile_phase_b TEXT,
    gradient_program JSONB NOT NULL DEFAULT '[]'::jsonb,
    flow_rate NUMERIC(12,6),
    flow_rate_unit VARCHAR(40),
    column_temperature_c NUMERIC(7,3),
    injection_volume NUMERIC(12,6),
    injection_volume_unit VARCHAR(40),
    acquisition_method VARCHAR(255) NOT NULL,
    acquisition_method_version VARCHAR(120),
    polarity_modes JSONB NOT NULL DEFAULT '[]'::jsonb,
    operator_name VARCHAR(255),
    acquired_at TIMESTAMP,
    completed_at TIMESTAMP,
    qc_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(30) NOT NULL DEFAULT 'planned',
    notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lcms_batch_study_code UNIQUE(study_id, batch_code),
    CONSTRAINT ck_lcms_batch_flow_rate_positive CHECK(flow_rate IS NULL OR flow_rate > 0),
    CONSTRAINT ck_lcms_batch_injection_volume_positive CHECK(injection_volume IS NULL OR injection_volume > 0)
);
CREATE INDEX IF NOT EXISTS ix_lcms_analytical_batches_study_id ON lcms_analytical_batches(study_id);
CREATE INDEX IF NOT EXISTS ix_lcms_analytical_batches_status ON lcms_analytical_batches(status);
CREATE INDEX IF NOT EXISTS ix_lcms_analytical_batches_created_by ON lcms_analytical_batches(created_by);

CREATE TABLE IF NOT EXISTS lcms_injections (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    batch_id INT NOT NULL REFERENCES lcms_analytical_batches(id) ON DELETE CASCADE,
    injection_order INT NOT NULL,
    injection_type VARCHAR(40) NOT NULL,
    sample_label VARCHAR(255) NOT NULL,
    plant_sample_id INT REFERENCES plant_material_samples(id) ON DELETE RESTRICT,
    fraction_id INT REFERENCES plant_fractions(id) ON DELETE RESTRICT,
    technical_replicate INT,
    injection_volume NUMERIC(12,6),
    injection_volume_unit VARCHAR(40),
    acquisition_reference VARCHAR(255),
    status VARCHAR(30) NOT NULL DEFAULT 'planned',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lcms_injection_batch_order UNIQUE(batch_id, injection_order),
    CONSTRAINT ck_lcms_injection_order_positive CHECK(injection_order >= 1),
    CONSTRAINT ck_lcms_injection_single_material_source CHECK(plant_sample_id IS NULL OR fraction_id IS NULL),
    CONSTRAINT ck_lcms_injection_replicate_positive CHECK(technical_replicate IS NULL OR technical_replicate >= 1)
);
CREATE INDEX IF NOT EXISTS ix_lcms_injections_study_id ON lcms_injections(study_id);
CREATE INDEX IF NOT EXISTS ix_lcms_injections_batch_id ON lcms_injections(batch_id);
CREATE INDEX IF NOT EXISTS ix_lcms_injections_plant_sample_id ON lcms_injections(plant_sample_id);
CREATE INDEX IF NOT EXISTS ix_lcms_injections_fraction_id ON lcms_injections(fraction_id);
CREATE INDEX IF NOT EXISTS ix_lcms_injections_created_by ON lcms_injections(created_by);

CREATE TABLE IF NOT EXISTS lcms_data_artifacts (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    injection_id INT NOT NULL REFERENCES lcms_injections(id) ON DELETE CASCADE,
    parent_artifact_id INT REFERENCES lcms_data_artifacts(id) ON DELETE RESTRICT,
    artifact_role VARCHAR(40) NOT NULL,
    data_format VARCHAR(40) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    storage_path TEXT NOT NULL,
    content_type VARCHAR(120),
    size_bytes INT NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    conversion_tool VARCHAR(160),
    conversion_version VARCHAR(120),
    conversion_parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation_status VARCHAR(40) NOT NULL DEFAULT 'checksum_verified',
    uploaded_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lcms_artifact_injection_role_sha UNIQUE(injection_id, artifact_role, sha256),
    CONSTRAINT ck_lcms_artifact_size_positive CHECK(size_bytes > 0)
);
CREATE INDEX IF NOT EXISTS ix_lcms_data_artifacts_study_id ON lcms_data_artifacts(study_id);
CREATE INDEX IF NOT EXISTS ix_lcms_data_artifacts_injection_id ON lcms_data_artifacts(injection_id);
CREATE INDEX IF NOT EXISTS ix_lcms_data_artifacts_parent_artifact_id ON lcms_data_artifacts(parent_artifact_id);
CREATE INDEX IF NOT EXISTS ix_lcms_data_artifacts_sha256 ON lcms_data_artifacts(sha256);
CREATE INDEX IF NOT EXISTS ix_lcms_data_artifacts_uploaded_by ON lcms_data_artifacts(uploaded_by);
CREATE INDEX IF NOT EXISTS ix_lcms_artifacts_injection_role ON lcms_data_artifacts(injection_id, artifact_role);

CREATE TABLE IF NOT EXISTS lcms_quality_control_observations (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    batch_id INT NOT NULL REFERENCES lcms_analytical_batches(id) ON DELETE CASCADE,
    injection_id INT REFERENCES lcms_injections(id) ON DELETE SET NULL,
    qc_type VARCHAR(40) NOT NULL,
    metric_name VARCHAR(120) NOT NULL,
    metric_value NUMERIC(24,10),
    metric_unit VARCHAR(60),
    status VARCHAR(20) NOT NULL DEFAULT 'not_assessed',
    assessment_method VARCHAR(30) NOT NULL DEFAULT 'manual',
    acceptance_criteria JSONB NOT NULL DEFAULT '{}'::jsonb,
    rationale TEXT,
    assessed_by INT REFERENCES users(id) ON DELETE SET NULL,
    assessed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_lcms_qc_status CHECK(status IN ('not_assessed','pass','warning','fail')),
    CONSTRAINT ck_lcms_qc_assessment_method CHECK(assessment_method IN ('manual','calculated','imported'))
);
CREATE INDEX IF NOT EXISTS ix_lcms_quality_control_observations_study_id ON lcms_quality_control_observations(study_id);
CREATE INDEX IF NOT EXISTS ix_lcms_quality_control_observations_batch_id ON lcms_quality_control_observations(batch_id);
CREATE INDEX IF NOT EXISTS ix_lcms_quality_control_observations_injection_id ON lcms_quality_control_observations(injection_id);
CREATE INDEX IF NOT EXISTS ix_lcms_quality_control_observations_assessed_by ON lcms_quality_control_observations(assessed_by);
CREATE INDEX IF NOT EXISTS ix_lcms_qc_batch_type_status ON lcms_quality_control_observations(batch_id, qc_type, status);

CREATE TABLE IF NOT EXISTS lcms_spectral_libraries (
    id SERIAL PRIMARY KEY,
    name VARCHAR(160) NOT NULL,
    version VARCHAR(120) NOT NULL,
    source_url TEXT,
    source_license VARCHAR(255) NOT NULL,
    data_format VARCHAR(40),
    reference_set_sha256 VARCHAR(64),
    citation TEXT,
    coverage_notes TEXT,
    curation_status VARCHAR(30) NOT NULL DEFAULT 'registered',
    registered_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lcms_spectral_library_name_version UNIQUE(name, version),
    CONSTRAINT ck_lcms_library_curation_status CHECK(curation_status IN ('registered','reviewed','rejected'))
);
CREATE INDEX IF NOT EXISTS ix_lcms_spectral_libraries_registered_by ON lcms_spectral_libraries(registered_by);

CREATE TABLE IF NOT EXISTS lcms_processing_runs (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    batch_id INT NOT NULL REFERENCES lcms_analytical_batches(id) ON DELETE CASCADE,
    spectral_library_id INT REFERENCES lcms_spectral_libraries(id) ON DELETE SET NULL,
    software_name VARCHAR(160) NOT NULL,
    software_version VARCHAR(120) NOT NULL,
    workflow_name VARCHAR(160) NOT NULL,
    workflow_version VARCHAR(120),
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    input_sha256 VARCHAR(64) NOT NULL,
    output_sha256 VARCHAR(64),
    status VARCHAR(30) NOT NULL DEFAULT 'registered',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    provenance_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_lcms_processing_status CHECK(status IN ('registered','processing','completed','failed'))
);
CREATE INDEX IF NOT EXISTS ix_lcms_processing_runs_study_id ON lcms_processing_runs(study_id);
CREATE INDEX IF NOT EXISTS ix_lcms_processing_runs_batch_id ON lcms_processing_runs(batch_id);
CREATE INDEX IF NOT EXISTS ix_lcms_processing_runs_spectral_library_id ON lcms_processing_runs(spectral_library_id);
CREATE INDEX IF NOT EXISTS ix_lcms_processing_runs_status ON lcms_processing_runs(status);
CREATE INDEX IF NOT EXISTS ix_lcms_processing_runs_created_by ON lcms_processing_runs(created_by);

CREATE TABLE IF NOT EXISTS lcms_features (
    id SERIAL PRIMARY KEY,
    processing_run_id INT NOT NULL REFERENCES lcms_processing_runs(id) ON DELETE CASCADE,
    feature_key VARCHAR(120) NOT NULL,
    precursor_mz NUMERIC(18,8) NOT NULL,
    retention_time NUMERIC(18,8) NOT NULL,
    retention_time_unit VARCHAR(30) NOT NULL DEFAULT 'second',
    polarity VARCHAR(20) NOT NULL,
    charge INT,
    adduct VARCHAR(80),
    molecular_formula_candidate VARCHAR(160),
    isotope_group VARCHAR(120),
    quality_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lcms_feature_processing_key UNIQUE(processing_run_id, feature_key),
    CONSTRAINT ck_lcms_feature_mz_positive CHECK(precursor_mz > 0),
    CONSTRAINT ck_lcms_feature_rt_nonnegative CHECK(retention_time >= 0),
    CONSTRAINT ck_lcms_feature_polarity CHECK(polarity IN ('positive','negative','unknown'))
);
CREATE INDEX IF NOT EXISTS ix_lcms_features_processing_run_id ON lcms_features(processing_run_id);

CREATE TABLE IF NOT EXISTS lcms_feature_observations (
    id SERIAL PRIMARY KEY,
    feature_id INT NOT NULL REFERENCES lcms_features(id) ON DELETE CASCADE,
    injection_id INT NOT NULL REFERENCES lcms_injections(id) ON DELETE CASCADE,
    observed_mz NUMERIC(18,8),
    retention_time NUMERIC(18,8),
    peak_area NUMERIC(24,8),
    peak_height NUMERIC(24,8),
    intensity NUMERIC(24,8),
    signal_to_noise NUMERIC(18,8),
    quality_flag VARCHAR(40) NOT NULL DEFAULT 'not_assessed',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lcms_feature_observation_feature_injection UNIQUE(feature_id, injection_id),
    CONSTRAINT ck_lcms_feature_observation_mz_positive CHECK(observed_mz IS NULL OR observed_mz > 0),
    CONSTRAINT ck_lcms_feature_observation_rt_nonnegative CHECK(retention_time IS NULL OR retention_time >= 0),
    CONSTRAINT ck_lcms_feature_observation_area_nonnegative CHECK(peak_area IS NULL OR peak_area >= 0),
    CONSTRAINT ck_lcms_feature_observation_height_nonnegative CHECK(peak_height IS NULL OR peak_height >= 0),
    CONSTRAINT ck_lcms_feature_observation_intensity_nonnegative CHECK(intensity IS NULL OR intensity >= 0)
);
CREATE INDEX IF NOT EXISTS ix_lcms_feature_observations_feature_id ON lcms_feature_observations(feature_id);
CREATE INDEX IF NOT EXISTS ix_lcms_feature_observations_injection_id ON lcms_feature_observations(injection_id);

CREATE TABLE IF NOT EXISTS lcms_ms2_spectra (
    id SERIAL PRIMARY KEY,
    feature_id INT NOT NULL REFERENCES lcms_features(id) ON DELETE CASCADE,
    injection_id INT NOT NULL REFERENCES lcms_injections(id) ON DELETE CASCADE,
    artifact_id INT REFERENCES lcms_data_artifacts(id) ON DELETE SET NULL,
    scan_identifier VARCHAR(120),
    precursor_mz NUMERIC(18,8) NOT NULL,
    isolation_window NUMERIC(12,6),
    collision_energy NUMERIC(12,6),
    collision_energy_unit VARCHAR(40),
    polarity VARCHAR(20) NOT NULL,
    charge INT,
    fragments JSONB NOT NULL DEFAULT '[]'::jsonb,
    acquired_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_lcms_ms2_precursor_positive CHECK(precursor_mz > 0),
    CONSTRAINT ck_lcms_ms2_polarity CHECK(polarity IN ('positive','negative','unknown'))
);
CREATE INDEX IF NOT EXISTS ix_lcms_ms2_spectra_feature_id ON lcms_ms2_spectra(feature_id);
CREATE INDEX IF NOT EXISTS ix_lcms_ms2_spectra_injection_id ON lcms_ms2_spectra(injection_id);
CREATE INDEX IF NOT EXISTS ix_lcms_ms2_spectra_artifact_id ON lcms_ms2_spectra(artifact_id);

CREATE TABLE IF NOT EXISTS lcms_compound_annotations (
    id SERIAL PRIMARY KEY,
    feature_id INT NOT NULL REFERENCES lcms_features(id) ON DELETE CASCADE,
    phytochem_candidate_id INT REFERENCES phytochem_candidates(id) ON DELETE SET NULL,
    spectral_library_id INT REFERENCES lcms_spectral_libraries(id) ON DELETE SET NULL,
    reference_standard_injection_id INT REFERENCES lcms_injections(id) ON DELETE SET NULL,
    annotation_method VARCHAR(40) NOT NULL,
    annotation_name VARCHAR(255),
    molecular_formula VARCHAR(160),
    inchikey VARCHAR(64),
    smiles TEXT,
    msi_level INT NOT NULL,
    score_type VARCHAR(80),
    score_value NUMERIC(18,8),
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(30) NOT NULL DEFAULT 'candidate',
    review_rationale TEXT,
    reviewed_by INT REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMP,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_lcms_annotation_msi_level CHECK(msi_level BETWEEN 1 AND 4),
    CONSTRAINT ck_lcms_annotation_method CHECK(annotation_method IN ('library_match','authentic_standard','in_silico','formula_prediction','manual')),
    CONSTRAINT ck_lcms_annotation_status CHECK(status IN ('candidate','pending_review','accepted','rejected'))
);
CREATE INDEX IF NOT EXISTS ix_lcms_compound_annotations_feature_id ON lcms_compound_annotations(feature_id);
CREATE INDEX IF NOT EXISTS ix_lcms_compound_annotations_phytochem_candidate_id ON lcms_compound_annotations(phytochem_candidate_id);
CREATE INDEX IF NOT EXISTS ix_lcms_compound_annotations_spectral_library_id ON lcms_compound_annotations(spectral_library_id);
CREATE INDEX IF NOT EXISTS ix_lcms_compound_annotations_reference_standard_injection_id ON lcms_compound_annotations(reference_standard_injection_id);
CREATE INDEX IF NOT EXISTS ix_lcms_compound_annotations_status ON lcms_compound_annotations(status);
CREATE INDEX IF NOT EXISTS ix_lcms_compound_annotations_reviewed_by ON lcms_compound_annotations(reviewed_by);
CREATE INDEX IF NOT EXISTS ix_lcms_compound_annotations_created_by ON lcms_compound_annotations(created_by);

COMMIT;
