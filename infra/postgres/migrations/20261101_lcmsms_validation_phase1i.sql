BEGIN;

CREATE TABLE IF NOT EXISTS lcms_batch_validation_reports (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    batch_id INT NOT NULL REFERENCES lcms_analytical_batches(id) ON DELETE CASCADE,
    protocol_version VARCHAR(40) NOT NULL,
    validation_status VARCHAR(30) NOT NULL,
    criteria JSONB NOT NULL DEFAULT '{}'::jsonb,
    coverage JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics JSONB NOT NULL DEFAULT '[]'::jsonb,
    limitations JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_processing_run_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_observation_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    input_sha256 VARCHAR(64) NOT NULL,
    report_sha256 VARCHAR(64) NOT NULL,
    report_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lcms_batch_validation_report_hash UNIQUE(batch_id, report_sha256),
    CONSTRAINT ck_lcms_batch_validation_status CHECK(
        validation_status IN ('pass','warning','fail','insufficient_data')
    )
);

CREATE INDEX IF NOT EXISTS ix_lcms_batch_validation_reports_study_id
    ON lcms_batch_validation_reports(study_id);
CREATE INDEX IF NOT EXISTS ix_lcms_batch_validation_reports_batch_id
    ON lcms_batch_validation_reports(batch_id);
CREATE INDEX IF NOT EXISTS ix_lcms_batch_validation_reports_status
    ON lcms_batch_validation_reports(validation_status);
CREATE INDEX IF NOT EXISTS ix_lcms_batch_validation_reports_created_by
    ON lcms_batch_validation_reports(created_by);

COMMIT;
