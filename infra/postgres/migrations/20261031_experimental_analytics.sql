-- N3XORA Experimental Analytics Engine v1.
-- Additive migration: stores immutable model fits and provenance without rewriting assay observations.
BEGIN;
CREATE TABLE IF NOT EXISTS research_assay_analyses (
    id SERIAL PRIMARY KEY,
    assay_id INT NOT NULL REFERENCES research_assays(id) ON DELETE CASCADE,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    analysis_type VARCHAR(60) NOT NULL DEFAULT 'dose_response_4pl',
    algorithm VARCHAR(80) NOT NULL,
    algorithm_version VARCHAR(40) NOT NULL,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    input_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_research_assay_analyses_assay_created
    ON research_assay_analyses(assay_id, created_at);
CREATE INDEX IF NOT EXISTS ix_research_assay_analyses_input_sha256
    ON research_assay_analyses(input_sha256);
COMMIT;
