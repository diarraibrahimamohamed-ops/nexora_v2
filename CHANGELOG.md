# Changelog

Entries describe repository changes and do not imply experimental or clinical validation.

## Unreleased

### Security
- Replace vulnerable python-jose usage with PyJWT and restrict JWT verification to HS256 with required exp and sub claims.
- Update Biopython to 1.88 and keep Entrez/XML handling under regression testing.
- Keep TLS verification enabled for ESM Atlas requests; fall back to Tamarind when TLS validation fails.
- Disable the deprecated NVIDIA ESMFold endpoint by default.

### Bioinformatics
- Add the Tamarind ESMFold provider to structure resolution and an RNA-FM secondary-structure workflow via Tamarind.
- Distinguish confidence values for predicted models from experimental B-factors.
- Strengthen AMRFinderPlus FASTA validation, taxonomic-group selection, engine/database readiness and provenance reporting.
- Simplify study creation using versioned profiles and optional advanced metadata.

### Developer experience
- Add AMRFinderPlus setup guidance, contribution instructions and a security policy.
- Add CI checks for RNA-FM/AMRFinderPlus, JavaScript syntax, Docker image build and Compose configuration.
- Move Nginx test configurations and the manual RCSB API diagnostic out of the repository root.
- Integrate Phase 10 benchmark execution into the main Compose file through the benchmark profile.
