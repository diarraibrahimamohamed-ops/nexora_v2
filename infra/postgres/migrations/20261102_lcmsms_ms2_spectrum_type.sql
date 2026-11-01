BEGIN;

ALTER TABLE lcms_ms2_spectra
    ADD COLUMN IF NOT EXISTS spectrum_type VARCHAR(30) NOT NULL DEFAULT 'unknown';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_lcms_ms2_spectrum_type'
          AND conrelid = 'lcms_ms2_spectra'::regclass
    ) THEN
        ALTER TABLE lcms_ms2_spectra
            ADD CONSTRAINT ck_lcms_ms2_spectrum_type
            CHECK (spectrum_type IN ('centroided', 'profile', 'unknown'));
    END IF;
END $$;

COMMIT;
