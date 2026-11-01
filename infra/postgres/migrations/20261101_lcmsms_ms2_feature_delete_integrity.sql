BEGIN;

-- Upgrade already-installed Phase 1H schemas without rewriting historical DDL.
-- Retain MS2 scans when a detected feature is deleted.
DO $ms2_fk$
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
    LOOP
        EXECUTE format(
            'ALTER TABLE lcms_ms2_spectra DROP CONSTRAINT %I',
            fk_name
        );
    END LOOP;

    ALTER TABLE lcms_ms2_spectra
        ADD CONSTRAINT fk_lcms_ms2_spectra_feature_id
        FOREIGN KEY (feature_id)
        REFERENCES lcms_features(id)
        ON DELETE SET NULL;
END
$ms2_fk$;

-- A matched association cannot remain matched when its feature disappears.
-- Record the former feature ID before the FK sets feature_id to NULL.
CREATE OR REPLACE FUNCTION lcms_preserve_ms2_on_feature_delete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $ms2_delete$
BEGIN
    UPDATE lcms_ms2_spectra
       SET association_status = 'unmatched',
           feature_id = NULL,
           association_candidates = COALESCE(association_candidates, '[]'::jsonb)
               || jsonb_build_array(jsonb_build_object(
                    'former_feature_id', OLD.id,
                    'reason', 'source_feature_deleted'
               ))
     WHERE feature_id = OLD.id
       AND association_status = 'matched';
    RETURN OLD;
END
$ms2_delete$;

DROP TRIGGER IF EXISTS trg_lcms_preserve_ms2_on_feature_delete ON lcms_features;
CREATE TRIGGER trg_lcms_preserve_ms2_on_feature_delete
BEFORE DELETE ON lcms_features
FOR EACH ROW
EXECUTE FUNCTION lcms_preserve_ms2_on_feature_delete();

COMMIT;
