-- Phase 1L-B: auditable WCVP name-resolution proposals.
-- A taxon name match is not specimen identification or compound-occurrence evidence.
BEGIN;
CREATE TABLE IF NOT EXISTS phytochem_taxon_resolutions (
    id SERIAL PRIMARY KEY,
    candidate_id INT REFERENCES phytochem_candidates(id) ON DELETE CASCADE,
    specimen_id INT REFERENCES botanical_specimens(id) ON DELETE CASCADE,
    requested_name VARCHAR(255) NOT NULL,
    matched_name VARCHAR(255), matched_identifier VARCHAR(120), matched_status VARCHAR(40), matched_rank VARCHAR(40),
    accepted_name VARCHAR(255), accepted_identifier VARCHAR(120), accepted_status VARCHAR(40), accepted_rank VARCHAR(40),
    kingdom VARCHAR(80), family VARCHAR(160), match_type VARCHAR(40), confidence INTEGER,
    provider VARCHAR(120) NOT NULL, checklist_key VARCHAR(80) NOT NULL,
    dataset_doi VARCHAR(120) NOT NULL, dataset_version VARCHAR(120) NOT NULL,
    source_mode VARCHAR(20) NOT NULL DEFAULT 'LIVE', source_url TEXT NOT NULL,
    response_sha256 VARCHAR(64) NOT NULL, response_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    review_status VARCHAR(30) NOT NULL DEFAULT 'pending_review',
    reviewed_by VARCHAR(255), reviewed_at TIMESTAMP, review_note TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_phytochem_taxon_resolution_one_subject CHECK (
        (candidate_id IS NOT NULL AND specimen_id IS NULL)
        OR (candidate_id IS NULL AND specimen_id IS NOT NULL)
    ),
    CONSTRAINT ck_phytochem_taxon_resolution_review_status CHECK (
        review_status IN ('pending_review','accepted','rejected','unresolved')
    )
);
CREATE INDEX IF NOT EXISTS ix_phytochem_taxon_resolution_candidate_created ON phytochem_taxon_resolutions(candidate_id,created_at);
CREATE INDEX IF NOT EXISTS ix_phytochem_taxon_resolution_specimen_created ON phytochem_taxon_resolutions(specimen_id,created_at);
CREATE INDEX IF NOT EXISTS ix_phytochem_taxon_resolution_review_status ON phytochem_taxon_resolutions(review_status);
COMMIT;
