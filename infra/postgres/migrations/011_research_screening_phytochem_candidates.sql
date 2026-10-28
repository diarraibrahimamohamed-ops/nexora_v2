-- Link prepared research-screening compounds to their curated library origins.
BEGIN;
ALTER TABLE research_screening_compounds
    ADD COLUMN IF NOT EXISTS phytochem_candidate_id INT
        REFERENCES phytochem_candidates(id) ON DELETE SET NULL;
ALTER TABLE research_screening_compounds
    ADD COLUMN IF NOT EXISTS phytochem_candidate_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE INDEX IF NOT EXISTS ix_research_screening_phytochem_candidate
    ON research_screening_compounds(phytochem_candidate_id);
COMMIT;
