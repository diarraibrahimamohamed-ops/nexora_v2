-- N3XORA-Sahel: preserve candidate-to-docking provenance.
-- Apply after 009_phytochem_african_library.sql and before deploying the API.
BEGIN;
ALTER TABLE docking_jobs
    ADD COLUMN IF NOT EXISTS phytochem_candidate_id INT
        REFERENCES phytochem_candidates(id) ON DELETE SET NULL;
ALTER TABLE docking_jobs ADD COLUMN IF NOT EXISTS target_accession VARCHAR(120);
ALTER TABLE docking_jobs ADD COLUMN IF NOT EXISTS protocol_id VARCHAR(120);
ALTER TABLE docking_jobs ADD COLUMN IF NOT EXISTS phytochem_provenance JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS ix_docking_jobs_phytochem_candidate
    ON docking_jobs(phytochem_candidate_id);
COMMIT;
