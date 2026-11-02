-- Phase 1L-C: auditable plant-compound occurrence evidence.
-- Evidence claims are separate from taxonomic and chemical identity status.
BEGIN;
CREATE TABLE IF NOT EXISTS phytochem_occurrence_evidence (
    id SERIAL PRIMARY KEY,
    candidate_id INT NOT NULL REFERENCES phytochem_candidates(id) ON DELETE RESTRICT,
    taxon_resolution_id INT NOT NULL REFERENCES phytochem_taxon_resolutions(id) ON DELETE RESTRICT,
    specimen_id INT REFERENCES botanical_specimens(id) ON DELETE RESTRICT,
    sample_id INT REFERENCES plant_material_samples(id) ON DELETE RESTRICT,
    extraction_batch_id INT REFERENCES plant_extraction_batches(id) ON DELETE RESTRICT,
    fraction_id INT REFERENCES plant_fractions(id) ON DELETE RESTRICT,
    lcms_annotation_id INT REFERENCES lcms_compound_annotations(id) ON DELETE RESTRICT,
    evidence_type VARCHAR(40) NOT NULL,
    plant_part VARCHAR(120) NOT NULL,
    source_reference TEXT NOT NULL,
    source_title VARCHAR(500),
    source_locator VARCHAR(255),
    evidence_statement TEXT NOT NULL,
    analytical_method VARCHAR(255),
    measurement_value NUMERIC(24,10),
    measurement_unit VARCHAR(80),
    evidence_sha256 VARCHAR(64) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    review_status VARCHAR(30) NOT NULL DEFAULT 'pending_review',
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    reviewed_by INT REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMP,
    review_note TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_phytochem_occurrence_evidence_type CHECK (
        evidence_type IN ('literature_report','lcms_annotation','isolated_compound','other_experimental')
    ),
    CONSTRAINT ck_phytochem_occurrence_review_status CHECK (
        review_status IN ('pending_review','accepted','rejected','insufficient')
    ),
    CONSTRAINT ck_phytochem_occurrence_measurement_pair CHECK (
        (measurement_value IS NULL AND measurement_unit IS NULL)
        OR (measurement_value IS NOT NULL AND measurement_unit IS NOT NULL)
    ),
    CONSTRAINT ck_phytochem_occurrence_measurement_positive CHECK (
        measurement_value IS NULL OR measurement_value > 0
    ),
    CONSTRAINT ck_phytochem_occurrence_sample_has_specimen CHECK (
        sample_id IS NULL OR specimen_id IS NOT NULL
    ),
    CONSTRAINT ck_phytochem_occurrence_extraction_has_sample CHECK (
        extraction_batch_id IS NULL OR sample_id IS NOT NULL
    ),
    CONSTRAINT ck_phytochem_occurrence_fraction_has_extraction CHECK (
        fraction_id IS NULL OR extraction_batch_id IS NOT NULL
    ),
    CONSTRAINT ck_phytochem_occurrence_lcms_lineage CHECK (
        (evidence_type = 'lcms_annotation' AND lcms_annotation_id IS NOT NULL AND sample_id IS NOT NULL)
        OR (evidence_type <> 'lcms_annotation' AND lcms_annotation_id IS NULL)
    ),
    CONSTRAINT ck_phytochem_occurrence_experimental_specimen CHECK (
        evidence_type = 'literature_report' OR specimen_id IS NOT NULL
    )
);
CREATE INDEX IF NOT EXISTS ix_phytochem_occurrence_candidate_review
    ON phytochem_occurrence_evidence(candidate_id,review_status,created_at);
CREATE INDEX IF NOT EXISTS ix_phytochem_occurrence_taxon_part
    ON phytochem_occurrence_evidence(taxon_resolution_id,plant_part);
CREATE INDEX IF NOT EXISTS ix_phytochem_occurrence_specimen
    ON phytochem_occurrence_evidence(specimen_id);
CREATE INDEX IF NOT EXISTS ix_phytochem_occurrence_sample
    ON phytochem_occurrence_evidence(sample_id);
COMMIT;
