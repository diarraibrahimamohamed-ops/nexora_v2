-- N3XORA-Sahel BioEvidence: African phyto-chemical candidate catalogue.
-- Candidates remain quarantined until chemical identity and plant occurrence
-- are reviewed. This migration deliberately does not alter validated_ligands.
BEGIN;

CREATE TABLE IF NOT EXISTS phytochem_candidates (
    id SERIAL PRIMARY KEY,
    name_raw VARCHAR(255) NOT NULL,
    canonical_name VARCHAR(255),
    synonyms JSONB NOT NULL DEFAULT '[]'::jsonb,
    pubchem_cid INTEGER,
    inchikey VARCHAR(64),
    smiles_raw TEXT,
    smiles_canonical TEXT,
    formula VARCHAR(120),
    stereochemistry TEXT,
    descriptors JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_plant VARCHAR(255),
    accepted_taxon VARCHAR(255),
    taxon_identifier VARCHAR(120),
    plant_part VARCHAR(120),
    occurrence_source TEXT,
    occurrence_status VARCHAR(40) NOT NULL DEFAULT 'A_VÉRIFIER',
    library_source VARCHAR(40) NOT NULL,
    source_license VARCHAR(255),
    source_file_sha256 VARCHAR(64),
    source_row VARCHAR(128),
    identity_status VARCHAR(40) NOT NULL DEFAULT 'CANDIDAT_IMPORTÉ',
    evidence_grade VARCHAR(1) NOT NULL DEFAULT 'D',
    chemical_class VARCHAR(160),
    target_organism VARCHAR(255),
    target_accession VARCHAR(120),
    assay_type VARCHAR(120),
    endpoint_value DOUBLE PRECISION,
    endpoint_unit VARCHAR(80),
    study_reference TEXT,
    curator VARCHAR(255),
    reviewed_at TIMESTAMP,
    review_note TEXT,
    import_warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_phytochem_name ON phytochem_candidates(name_raw);
CREATE INDEX IF NOT EXISTS ix_phytochem_canonical_name ON phytochem_candidates(canonical_name);
CREATE INDEX IF NOT EXISTS ix_phytochem_pubchem_cid ON phytochem_candidates(pubchem_cid);
CREATE INDEX IF NOT EXISTS ix_phytochem_inchikey ON phytochem_candidates(inchikey);
CREATE INDEX IF NOT EXISTS ix_phytochem_source ON phytochem_candidates(library_source);
CREATE INDEX IF NOT EXISTS ix_phytochem_identity_status ON phytochem_candidates(identity_status);
CREATE INDEX IF NOT EXISTS ix_phytochem_occurrence_status ON phytochem_candidates(occurrence_status);
CREATE INDEX IF NOT EXISTS ix_phytochem_chemical_class ON phytochem_candidates(chemical_class);
CREATE INDEX IF NOT EXISTS ix_phytochem_accepted_taxon ON phytochem_candidates(accepted_taxon);
CREATE INDEX IF NOT EXISTS ix_phytochem_source_row_name ON phytochem_candidates(library_source, source_row, name_raw);

COMMIT;
