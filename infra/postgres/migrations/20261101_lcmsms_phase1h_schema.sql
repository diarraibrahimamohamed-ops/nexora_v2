BEGIN;

-- Phase 1H adds archived MSP-file metadata used by the integrated engine.
ALTER TABLE lcms_spectral_libraries
    ADD COLUMN IF NOT EXISTS reference_set_storage_path TEXT;
ALTER TABLE lcms_spectral_libraries
    ADD COLUMN IF NOT EXISTS reference_set_size_bytes INT;
ALTER TABLE lcms_spectral_libraries
    ADD COLUMN IF NOT EXISTS reference_set_original_filename VARCHAR(255);

-- Link one analytical processing run to its durable HFSM/DAG execution.
ALTER TABLE lcms_processing_runs
    ADD COLUMN IF NOT EXISTS scientific_workflow_run_id INT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'lcms_processing_runs'::regclass
          AND contype = 'f'
          AND pg_get_constraintdef(oid) ILIKE '%scientific_workflow_runs%'
    ) THEN
        ALTER TABLE lcms_processing_runs
            ADD CONSTRAINT fk_lcms_processing_runs_scientific_workflow
            FOREIGN KEY (scientific_workflow_run_id)
            REFERENCES scientific_workflow_runs(id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_lcms_processing_scientific_workflow_run
    ON lcms_processing_runs(scientific_workflow_run_id)
    WHERE scientific_workflow_run_id IS NOT NULL;

-- Preserve MS2 evidence when a detected feature is removed.
-- Phase 1G used ON DELETE CASCADE; the Phase 1H model and fresh schema
-- require ON DELETE SET NULL so an MS2 scan remains auditable.
DO $
DECLARE
    fk_name TEXT;
BEGIN
    FOR fk_name IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'lcms_ms2_spectra'::regclass
          AND contype = 'f'
          AND pg_get_constraintdef(oid)
              ILIKE 'FOREIGN KEY (feature_id) REFERENCES lcms_features(id)%'
          AND pg_get_constraintdef(oid) NOT ILIKE '%ON DELETE SET NULL%'
    LOOP
        EXECUTE format(
            'ALTER TABLE lcms_ms2_spectra DROP CONSTRAINT %I',
            fk_name
        );
    END LOOP;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'lcms_ms2_spectra'::regclass
          AND contype = 'f'
          AND pg_get_constraintdef(oid)
              ILIKE 'FOREIGN KEY (feature_id) REFERENCES lcms_features(id) ON DELETE SET NULL%'
    ) THEN
        ALTER TABLE lcms_ms2_spectra
            ADD CONSTRAINT fk_lcms_ms2_spectra_feature_id
            FOREIGN KEY (feature_id)
            REFERENCES lcms_features(id)
            ON DELETE SET NULL;
    END IF;
END $;

-- Phase 1H retains ambiguous/unmatched scans even when no feature can be assigned.
ALTER TABLE lcms_ms2_spectra
    ALTER COLUMN feature_id DROP NOT NULL;

ALTER TABLE lcms_ms2_spectra
    ADD COLUMN IF NOT EXISTS association_status VARCHAR(20) NOT NULL DEFAULT 'matched';
ALTER TABLE lcms_ms2_spectra
    ALTER COLUMN association_status SET DEFAULT 'unmatched';

ALTER TABLE lcms_ms2_spectra
    ADD COLUMN IF NOT EXISTS association_candidates JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE lcms_ms2_spectra
    ADD COLUMN IF NOT EXISTS spectrum_type VARCHAR(30) NOT NULL DEFAULT 'unknown';

-- Keep a matched MS2 scan when its linked feature is deleted. The association
-- becomes unmatched and the former feature ID is retained in its candidate
-- metadata before the FK sets feature_id to NULL.
CREATE OR REPLACE FUNCTION lcms_preserve_ms2_on_feature_delete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $
BEGIN
    UPDATE lcms_ms2_spectra
       SET association_status = 'unmatched',
           association_candidates = COALESCE(association_candidates, '[]'::jsonb)
               || jsonb_build_array(jsonb_build_object(
                    'former_feature_id', OLD.id,
                    'reason', 'source_feature_deleted'
               ))
     WHERE feature_id = OLD.id
       AND association_status = 'matched';
    RETURN OLD;
END;
$;

DROP TRIGGER IF EXISTS trg_lcms_preserve_ms2_on_feature_delete ON lcms_features;
CREATE TRIGGER trg_lcms_preserve_ms2_on_feature_delete
BEFORE DELETE ON lcms_features
FOR EACH ROW
EXECUTE FUNCTION lcms_preserve_ms2_on_feature_delete();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_lcms_ms2_association_status'
          AND conrelid = 'lcms_ms2_spectra'::regclass
    ) THEN
        ALTER TABLE lcms_ms2_spectra
            ADD CONSTRAINT ck_lcms_ms2_association_status
            CHECK (association_status IN ('matched', 'ambiguous', 'unmatched'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_lcms_ms2_association_feature_consistency'
          AND conrelid = 'lcms_ms2_spectra'::regclass
    ) THEN
        ALTER TABLE lcms_ms2_spectra
            ADD CONSTRAINT ck_lcms_ms2_association_feature_consistency
            CHECK (
                (association_status = 'matched' AND feature_id IS NOT NULL)
                OR (association_status <> 'matched' AND feature_id IS NULL)
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_lcms_ms2_spectrum_type'
          AND conrelid = 'lcms_ms2_spectra'::regclass
    ) THEN
        ALTER TABLE lcms_ms2_spectra
            ADD CONSTRAINT ck_lcms_ms2_spectrum_type
            CHECK (spectrum_type IN ('centroided', 'profile', 'unknown'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_lcms_ms2_spectra_association_status
    ON lcms_ms2_spectra(association_status);

COMMIT;
