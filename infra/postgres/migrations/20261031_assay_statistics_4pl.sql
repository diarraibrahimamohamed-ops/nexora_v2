-- N3XORA 4PL statistical analysis upgrade. Additive and idempotent.
BEGIN;

ALTER TABLE research_assay_measurements
    ADD COLUMN IF NOT EXISTS biological_sample_id VARCHAR(80);
CREATE INDEX IF NOT EXISTS ix_research_assay_measurement_biological_sample
    ON research_assay_measurements(assay_id, biological_sample_id);

CREATE TABLE IF NOT EXISTS research_assay_analyses (
    id SERIAL PRIMARY KEY,
    assay_id INT NOT NULL REFERENCES research_assays(id) ON DELETE CASCADE,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    analysis_type VARCHAR(60) NOT NULL,
    algorithm_version VARCHAR(80) NOT NULL,
    analysis_key VARCHAR(64) NOT NULL UNIQUE,
    input_sha256 VARCHAR(64) NOT NULL,
    output_sha256 VARCHAR(64) NOT NULL,
    parameters JSONB NOT NULL DEFAULT '{}',
    result JSONB NOT NULL DEFAULT '{}',
    status VARCHAR(30) NOT NULL DEFAULT 'completed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_research_assay_analyses_assay
    ON research_assay_analyses(assay_id);
CREATE INDEX IF NOT EXISTS ix_research_assay_analyses_input
    ON research_assay_analyses(input_sha256);

COMMIT;
