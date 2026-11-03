-- Phase 1L-B hardening: durable creator/reviewer attribution and independent review.
-- Preserve the legacy reviewed_by text field as an audit snapshot for existing rows.
BEGIN;

ALTER TABLE phytochem_taxon_resolutions
    ADD COLUMN IF NOT EXISTS created_by INT;

ALTER TABLE phytochem_taxon_resolutions
    ADD COLUMN IF NOT EXISTS created_by_label VARCHAR(255);

ALTER TABLE phytochem_taxon_resolutions
    ADD COLUMN IF NOT EXISTS reviewed_by_user_id INT;

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_phytochem_taxon_resolution_created_by'
          AND conrelid = 'phytochem_taxon_resolutions'::regclass
    ) THEN
        ALTER TABLE phytochem_taxon_resolutions
            ADD CONSTRAINT fk_phytochem_taxon_resolution_created_by
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_phytochem_taxon_resolution_reviewed_by_user'
          AND conrelid = 'phytochem_taxon_resolutions'::regclass
    ) THEN
        ALTER TABLE phytochem_taxon_resolutions
            ADD CONSTRAINT fk_phytochem_taxon_resolution_reviewed_by_user
            FOREIGN KEY (reviewed_by_user_id) REFERENCES users(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_phytochem_taxon_resolution_independent_review'
          AND conrelid = 'phytochem_taxon_resolutions'::regclass
    ) THEN
        ALTER TABLE phytochem_taxon_resolutions
            ADD CONSTRAINT ck_phytochem_taxon_resolution_independent_review
            CHECK (
                created_by IS NULL
                OR reviewed_by_user_id IS NULL
                OR created_by <> reviewed_by_user_id
            );
    END IF;
END
$migration$;

CREATE INDEX IF NOT EXISTS ix_phytochem_taxon_resolution_created_by
    ON phytochem_taxon_resolutions(created_by);

CREATE INDEX IF NOT EXISTS ix_phytochem_taxon_resolution_reviewed_by_user
    ON phytochem_taxon_resolutions(reviewed_by_user_id);

COMMIT;
