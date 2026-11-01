BEGIN;

CREATE TABLE IF NOT EXISTS lcms_feature_alignment_runs (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    batch_id INT NOT NULL REFERENCES lcms_analytical_batches(id) ON DELETE CASCADE,
    scientific_workflow_run_id INT UNIQUE REFERENCES scientific_workflow_runs(id) ON DELETE SET NULL,
    software_name VARCHAR(160) NOT NULL DEFAULT 'OpenMS',
    software_version VARCHAR(120) NOT NULL,
    algorithm_name VARCHAR(160) NOT NULL,
    algorithm_version VARCHAR(40) NOT NULL,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_processing_run_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_injection_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    input_sha256 VARCHAR(64) NOT NULL,
    output_sha256 VARCHAR(64),
    status VARCHAR(30) NOT NULL DEFAULT 'registered',
    alignment_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    CONSTRAINT uq_lcms_feature_alignment_batch_input UNIQUE(batch_id, input_sha256),
    CONSTRAINT ck_lcms_feature_alignment_status CHECK(status IN ('registered','processing','completed','failed'))
);
CREATE INDEX IF NOT EXISTS ix_lcms_feature_alignment_runs_study_id ON lcms_feature_alignment_runs(study_id);
CREATE INDEX IF NOT EXISTS ix_lcms_feature_alignment_runs_batch_id ON lcms_feature_alignment_runs(batch_id);
CREATE INDEX IF NOT EXISTS ix_lcms_feature_alignment_runs_workflow_id ON lcms_feature_alignment_runs(scientific_workflow_run_id);
CREATE INDEX IF NOT EXISTS ix_lcms_feature_alignment_runs_status ON lcms_feature_alignment_runs(status);
CREATE INDEX IF NOT EXISTS ix_lcms_feature_alignment_runs_created_by ON lcms_feature_alignment_runs(created_by);

CREATE TABLE IF NOT EXISTS lcms_aligned_features (
    id SERIAL PRIMARY KEY,
    alignment_run_id INT NOT NULL REFERENCES lcms_feature_alignment_runs(id) ON DELETE CASCADE,
    polarity VARCHAR(20) NOT NULL,
    consensus_mz NUMERIC(18,8) NOT NULL,
    aligned_retention_time NUMERIC(18,8) NOT NULL,
    retention_time_unit VARCHAR(30) NOT NULL DEFAULT 'second',
    member_count INT NOT NULL,
    injection_count INT NOT NULL,
    alignment_status VARCHAR(30) NOT NULL,
    quality_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_lcms_aligned_feature_mz_positive CHECK(consensus_mz > 0),
    CONSTRAINT ck_lcms_aligned_feature_rt_nonnegative CHECK(aligned_retention_time >= 0),
    CONSTRAINT ck_lcms_aligned_feature_member_counts CHECK(member_count >= 1 AND injection_count >= 1),
    CONSTRAINT ck_lcms_aligned_feature_polarity CHECK(polarity IN ('positive','negative','unknown')),
    CONSTRAINT ck_lcms_aligned_feature_status CHECK(alignment_status IN ('aligned','partial','singleton'))
);
CREATE INDEX IF NOT EXISTS ix_lcms_aligned_features_alignment_run_id ON lcms_aligned_features(alignment_run_id);
CREATE INDEX IF NOT EXISTS ix_lcms_aligned_features_run_polarity ON lcms_aligned_features(alignment_run_id, polarity);

CREATE TABLE IF NOT EXISTS lcms_aligned_feature_members (
    id SERIAL PRIMARY KEY,
    alignment_run_id INT NOT NULL REFERENCES lcms_feature_alignment_runs(id) ON DELETE CASCADE,
    aligned_feature_id INT NOT NULL REFERENCES lcms_aligned_features(id) ON DELETE CASCADE,
    processing_run_id INT NOT NULL REFERENCES lcms_processing_runs(id) ON DELETE CASCADE,
    injection_id INT NOT NULL REFERENCES lcms_injections(id) ON DELETE CASCADE,
    feature_id INT NOT NULL REFERENCES lcms_features(id) ON DELETE CASCADE,
    source_observation_id INT REFERENCES lcms_feature_observations(id) ON DELETE SET NULL,
    original_mz NUMERIC(18,8) NOT NULL,
    original_retention_time NUMERIC(18,8) NOT NULL,
    aligned_retention_time NUMERIC(18,8) NOT NULL,
    retention_time_unit VARCHAR(30) NOT NULL DEFAULT 'second',
    delta_retention_time_seconds NUMERIC(18,8) NOT NULL DEFAULT 0,
    delta_mz_ppm NUMERIC(18,8) NOT NULL DEFAULT 0,
    raw_peak_area NUMERIC(24,8),
    raw_intensity NUMERIC(24,8),
    charge INT,
    adduct VARCHAR(80),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lcms_aligned_member_run UNIQUE(aligned_feature_id, processing_run_id),
    CONSTRAINT uq_lcms_aligned_member_source_feature UNIQUE(alignment_run_id, feature_id),
    CONSTRAINT ck_lcms_aligned_member_original_values CHECK(original_mz > 0 AND original_retention_time >= 0),
    CONSTRAINT ck_lcms_aligned_member_aligned_rt CHECK(aligned_retention_time >= 0),
    CONSTRAINT ck_lcms_aligned_member_peak_area CHECK(raw_peak_area IS NULL OR raw_peak_area >= 0),
    CONSTRAINT ck_lcms_aligned_member_intensity CHECK(raw_intensity IS NULL OR raw_intensity >= 0)
);
CREATE INDEX IF NOT EXISTS ix_lcms_aligned_feature_members_alignment_run_id ON lcms_aligned_feature_members(alignment_run_id);
CREATE INDEX IF NOT EXISTS ix_lcms_aligned_feature_members_aligned_feature_id ON lcms_aligned_feature_members(aligned_feature_id);
CREATE INDEX IF NOT EXISTS ix_lcms_aligned_feature_members_processing_run_id ON lcms_aligned_feature_members(processing_run_id);
CREATE INDEX IF NOT EXISTS ix_lcms_aligned_feature_members_injection_id ON lcms_aligned_feature_members(injection_id);
CREATE INDEX IF NOT EXISTS ix_lcms_aligned_feature_members_feature_id ON lcms_aligned_feature_members(feature_id);
CREATE INDEX IF NOT EXISTS ix_lcms_aligned_feature_members_source_observation_id ON lcms_aligned_feature_members(source_observation_id);

CREATE TABLE IF NOT EXISTS lcms_quantification_runs (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    batch_id INT NOT NULL REFERENCES lcms_analytical_batches(id) ON DELETE CASCADE,
    alignment_run_id INT NOT NULL REFERENCES lcms_feature_alignment_runs(id) ON DELETE RESTRICT,
    scientific_workflow_run_id INT UNIQUE REFERENCES scientific_workflow_runs(id) ON DELETE SET NULL,
    method VARCHAR(80) NOT NULL DEFAULT 'qc_rlsc_loess',
    method_version VARCHAR(40) NOT NULL,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_injection_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    input_sha256 VARCHAR(64) NOT NULL,
    output_sha256 VARCHAR(64),
    status VARCHAR(30) NOT NULL DEFAULT 'registered',
    quantification_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    CONSTRAINT uq_lcms_quant_alignment_input UNIQUE(alignment_run_id, input_sha256),
    CONSTRAINT ck_lcms_quantification_status CHECK(status IN ('registered','processing','completed','failed')),
    CONSTRAINT ck_lcms_quantification_method CHECK(method IN ('qc_rlsc_loess'))
);
CREATE INDEX IF NOT EXISTS ix_lcms_quantification_runs_study_id ON lcms_quantification_runs(study_id);
CREATE INDEX IF NOT EXISTS ix_lcms_quantification_runs_batch_id ON lcms_quantification_runs(batch_id);
CREATE INDEX IF NOT EXISTS ix_lcms_quantification_runs_alignment_run_id ON lcms_quantification_runs(alignment_run_id);
CREATE INDEX IF NOT EXISTS ix_lcms_quantification_runs_workflow_id ON lcms_quantification_runs(scientific_workflow_run_id);
CREATE INDEX IF NOT EXISTS ix_lcms_quantification_runs_status ON lcms_quantification_runs(status);
CREATE INDEX IF NOT EXISTS ix_lcms_quantification_runs_created_by ON lcms_quantification_runs(created_by);

CREATE TABLE IF NOT EXISTS lcms_normalized_feature_quantifications (
    id SERIAL PRIMARY KEY,
    quantification_run_id INT NOT NULL REFERENCES lcms_quantification_runs(id) ON DELETE CASCADE,
    alignment_run_id INT NOT NULL REFERENCES lcms_feature_alignment_runs(id) ON DELETE CASCADE,
    aligned_feature_id INT NOT NULL REFERENCES lcms_aligned_features(id) ON DELETE CASCADE,
    injection_id INT NOT NULL REFERENCES lcms_injections(id) ON DELETE CASCADE,
    processing_run_id INT REFERENCES lcms_processing_runs(id) ON DELETE SET NULL,
    feature_id INT REFERENCES lcms_features(id) ON DELETE SET NULL,
    qc_type VARCHAR(40) NOT NULL,
    raw_intensity NUMERIC(24,8),
    corrected_intensity NUMERIC(24,8),
    correction_factor NUMERIC(18,10),
    normalization_status VARCHAR(50) NOT NULL,
    qc_rsd_before_pct NUMERIC(18,8),
    qc_rsd_after_pct NUMERIC(18,8),
    quality_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lcms_normalized_feature_injection UNIQUE(quantification_run_id, aligned_feature_id, injection_id),
    CONSTRAINT ck_lcms_normalized_feature_status CHECK(
        normalization_status IN (
            'corrected','no_correction_needed','not_detected','insufficient_qc',
            'insufficient_qc_span','outside_qc_range','correction_factor_out_of_bounds',
            'correction_not_improving_qc_rsd','non_positive_qc_signal'
        )
    ),
    CONSTRAINT ck_lcms_normalized_raw_intensity CHECK(raw_intensity IS NULL OR raw_intensity >= 0),
    CONSTRAINT ck_lcms_normalized_corrected_intensity CHECK(corrected_intensity IS NULL OR corrected_intensity >= 0),
    CONSTRAINT ck_lcms_normalized_correction_factor CHECK(correction_factor IS NULL OR correction_factor > 0)
);
CREATE INDEX IF NOT EXISTS ix_lcms_normalized_feature_quantifications_quantification_run_id ON lcms_normalized_feature_quantifications(quantification_run_id);
CREATE INDEX IF NOT EXISTS ix_lcms_normalized_feature_quantifications_alignment_run_id ON lcms_normalized_feature_quantifications(alignment_run_id);
CREATE INDEX IF NOT EXISTS ix_lcms_normalized_feature_quantifications_aligned_feature_id ON lcms_normalized_feature_quantifications(aligned_feature_id);
CREATE INDEX IF NOT EXISTS ix_lcms_normalized_feature_quantifications_injection_id ON lcms_normalized_feature_quantifications(injection_id);
CREATE INDEX IF NOT EXISTS ix_lcms_normalized_feature_quantifications_processing_run_id ON lcms_normalized_feature_quantifications(processing_run_id);
CREATE INDEX IF NOT EXISTS ix_lcms_normalized_feature_quantifications_feature_id ON lcms_normalized_feature_quantifications(feature_id);

COMMIT;
