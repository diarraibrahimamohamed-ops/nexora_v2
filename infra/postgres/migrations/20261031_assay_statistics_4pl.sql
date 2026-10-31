-- N3XORA replicate-aware assay statistics schema extension.
-- Additive and safe for databases that already run Experimental Analytics v1.
-- Legacy analysis rows remain readable: new key/hash fields are nullable.
BEGIN;

ALTER TABLE research_assay_measurements
    ADD COLUMN IF NOT EXISTS biological_sample_id VARCHAR(80);
CREATE INDEX IF NOT EXISTS ix_research_assay_measurement_biological_sample
    ON research_assay_measurements(assay_id, biological_sample_id);

ALTER TABLE research_assay_analyses
    ADD COLUMN IF NOT EXISTS analysis_key VARCHAR(64);
ALTER TABLE research_assay_analyses
    ADD COLUMN IF NOT EXISTS output_sha256 VARCHAR(64);
ALTER TABLE research_assay_analyses
    ADD COLUMN IF NOT EXISTS status VARCHAR(30) NOT NULL DEFAULT 'completed';

CREATE UNIQUE INDEX IF NOT EXISTS ix_research_assay_analyses_analysis_key
    ON research_assay_analyses(analysis_key)
    WHERE analysis_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_research_assay_analyses_output_sha256
    ON research_assay_analyses(output_sha256);
CREATE INDEX IF NOT EXISTS ix_research_assay_analyses_status
    ON research_assay_analyses(status);

COMMIT;
