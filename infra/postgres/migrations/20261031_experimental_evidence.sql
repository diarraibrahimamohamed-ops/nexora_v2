-- N3XORA experimental evidence schema. Additive migration.
BEGIN;
-- Parent tables required by experimental assay links (also bootstrapped by the API on legacy volumes).
CREATE TABLE IF NOT EXISTS research_screening_runs (
 id SERIAL PRIMARY KEY, study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
 target_id INT NOT NULL REFERENCES research_targets(id) ON DELETE CASCADE, status VARCHAR(30) NOT NULL DEFAULT 'prepared',
 engine VARCHAR(80) NOT NULL DEFAULT 'AutoDock Vina', protocol JSONB NOT NULL DEFAULT '{}', summary JSONB,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_research_screening_runs_study ON research_screening_runs(study_id);
CREATE TABLE IF NOT EXISTS research_screening_compounds (
 id SERIAL PRIMARY KEY, screening_id INT NOT NULL REFERENCES research_screening_runs(id) ON DELETE CASCADE,
 input_index INT NOT NULL, canonical_smiles VARCHAR(1000) NOT NULL, descriptors JSONB NOT NULL DEFAULT '{}',
 qc JSONB NOT NULL DEFAULT '{}', status VARCHAR(30) NOT NULL DEFAULT 'accepted',
 docking_job_id INT REFERENCES docking_jobs(id) ON DELETE SET NULL,
 research_docking_run_id INT REFERENCES research_docking_runs(id) ON DELETE SET NULL,
 docking_summary JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_research_screening_compounds_screening ON research_screening_compounds(screening_id);

-- EXPERIMENTAL EVIDENCE: structured assays, measured observations and curator review.

CREATE TABLE IF NOT EXISTS research_assays (
 id SERIAL PRIMARY KEY, study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
 target_id INT REFERENCES research_targets(id) ON DELETE SET NULL, variant_id INT REFERENCES research_variants(id) ON DELETE SET NULL,
 screening_compound_id INT REFERENCES research_screening_compounds(id) ON DELETE SET NULL,
 created_by INT REFERENCES users(id) ON DELETE SET NULL, reviewer_id INT REFERENCES users(id) ON DELETE SET NULL,
 assay_type VARCHAR(40) NOT NULL, assay_name VARCHAR(255) NOT NULL, test_article VARCHAR(255) NOT NULL,
 compound_identifier VARCHAR(255), canonical_smiles VARCHAR(1000), source_kind VARCHAR(40) NOT NULL DEFAULT 'lab_generated',
 source_reference TEXT, source_url TEXT, laboratory VARCHAR(255), operator_name VARCHAR(255),
 method_name VARCHAR(255) NOT NULL, method_version VARCHAR(120), protocol_reference TEXT, assay_date DATE,
 organism VARCHAR(255), strain VARCHAR(255), cell_line VARCHAR(255), conditions JSONB NOT NULL DEFAULT '{}', notes TEXT,
 status VARCHAR(30) NOT NULL DEFAULT 'draft', review_decision VARCHAR(40), review_notes TEXT,
 submitted_at TIMESTAMPTZ, reviewed_at TIMESTAMPTZ,
 comparability_status VARCHAR(30) NOT NULL DEFAULT 'not_assessed', comparability_group VARCHAR(120), comparability_rationale TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_research_assays_study_status ON research_assays(study_id, status);
CREATE INDEX IF NOT EXISTS ix_research_assays_target ON research_assays(target_id);
CREATE INDEX IF NOT EXISTS ix_research_assays_comparability ON research_assays(comparability_status, comparability_group);

CREATE TABLE IF NOT EXISTS research_assay_measurements (
 id SERIAL PRIMARY KEY, assay_id INT NOT NULL REFERENCES research_assays(id) ON DELETE CASCADE,
 sample_role VARCHAR(30) NOT NULL, sample_label VARCHAR(255) NOT NULL, replicate_id VARCHAR(80) NOT NULL,
 replicate_kind VARCHAR(30) NOT NULL DEFAULT 'biological', concentration_value NUMERIC(18,8), concentration_unit VARCHAR(40),
 response_name VARCHAR(120) NOT NULL, response_value NUMERIC(18,8) NOT NULL, response_unit VARCHAR(60) NOT NULL,
 qualifier VARCHAR(20) NOT NULL DEFAULT 'exact', timepoint_value NUMERIC(18,8), timepoint_unit VARCHAR(40),
 well_id VARCHAR(40), record_key VARCHAR(120), metadata JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 CONSTRAINT uq_research_assay_measurement_record_key UNIQUE (assay_id, record_key)
);
CREATE INDEX IF NOT EXISTS ix_research_assay_measurement_replicate ON research_assay_measurements(assay_id, sample_role, replicate_id);
ALTER TABLE research_assay_measurements ADD COLUMN IF NOT EXISTS biological_sample_id VARCHAR(80);
CREATE INDEX IF NOT EXISTS ix_research_assay_measurement_biological_sample ON research_assay_measurements(assay_id, biological_sample_id);

CREATE TABLE IF NOT EXISTS research_assay_endpoints (
 id SERIAL PRIMARY KEY, assay_id INT NOT NULL REFERENCES research_assays(id) ON DELETE CASCADE,
 endpoint_name VARCHAR(40) NOT NULL, value NUMERIC(18,8) NOT NULL, unit VARCHAR(40) NOT NULL,
 qualifier VARCHAR(20) NOT NULL DEFAULT 'exact', origin VARCHAR(30) NOT NULL DEFAULT 'measured_reported',
 method VARCHAR(255), method_version VARCHAR(120), confidence_low NUMERIC(18,8), confidence_high NUMERIC(18,8),
 biological_replicates INT, source_reference TEXT, notes TEXT, metadata JSONB NOT NULL DEFAULT '{}',
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_research_assay_endpoints_name ON research_assay_endpoints(assay_id, endpoint_name);

CREATE TABLE IF NOT EXISTS research_assay_attachments (
 id SERIAL PRIMARY KEY, assay_id INT NOT NULL REFERENCES research_assays(id) ON DELETE CASCADE,
 uploaded_by INT REFERENCES users(id) ON DELETE SET NULL, original_name VARCHAR(255) NOT NULL, stored_name VARCHAR(255) NOT NULL,
 storage_path TEXT NOT NULL, role VARCHAR(40) NOT NULL DEFAULT 'raw_data', content_type VARCHAR(120),
 size_bytes INT NOT NULL, sha256 VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_research_assay_attachments_sha256 ON research_assay_attachments(sha256);

CREATE TABLE IF NOT EXISTS research_assay_review_events (
 id SERIAL PRIMARY KEY, assay_id INT NOT NULL REFERENCES research_assays(id) ON DELETE CASCADE,
 actor_id INT REFERENCES users(id) ON DELETE SET NULL, event_type VARCHAR(40) NOT NULL,
 from_status VARCHAR(30), to_status VARCHAR(30) NOT NULL, decision VARCHAR(40), reason TEXT,
 payload JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_research_assay_review_events_assay_time ON research_assay_review_events(assay_id, created_at);
CREATE TABLE IF NOT EXISTS research_assay_analyses (id SERIAL PRIMARY KEY, assay_id INT NOT NULL REFERENCES research_assays(id) ON DELETE CASCADE, created_by INT REFERENCES users(id) ON DELETE SET NULL, analysis_type VARCHAR(60) NOT NULL, algorithm_version VARCHAR(80) NOT NULL, analysis_key VARCHAR(64) NOT NULL UNIQUE, input_sha256 VARCHAR(64) NOT NULL, output_sha256 VARCHAR(64) NOT NULL, parameters JSONB NOT NULL DEFAULT '{}', result JSONB NOT NULL DEFAULT '{}', status VARCHAR(30) NOT NULL DEFAULT 'completed', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS ix_research_assay_analyses_assay ON research_assay_analyses(assay_id);
CREATE INDEX IF NOT EXISTS ix_research_assay_analyses_input ON research_assay_analyses(input_sha256);

COMMIT;
