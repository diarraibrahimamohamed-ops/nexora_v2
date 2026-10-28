-- Preserve immutable source-file references for reproducible phytochemical curation.
BEGIN;
ALTER TABLE phytochem_candidates
    ADD COLUMN IF NOT EXISTS source_file_path VARCHAR(500);
COMMIT;
