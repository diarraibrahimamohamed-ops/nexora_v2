-- Evidence provenance: fingerprint responses used for PubChem identity and GBIF taxon resolution.
BEGIN;
ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS identity_source VARCHAR(255);
ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS identity_reference_sha256 VARCHAR(64);
ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS taxonomy_resolution_source VARCHAR(255);
ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS taxonomy_cache_sha256 VARCHAR(64);
COMMIT;
