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

DO $phase1h$
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
END
$phase1h$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_lcms_processing_scientific_workflow_run
    ON lcms_processing_runs(scientific_workflow_run_id)
    WHERE scientific_workflow_run_id IS NOT NULL;

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

DO $phase1h$
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
END
$phase1h$;

CREATE INDEX IF NOT EXISTS ix_lcms_ms2_spectra_association_status
    ON lcms_ms2_spectra(association_status);

COMMIT;
