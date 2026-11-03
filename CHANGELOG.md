# Changelog

Entries describe repository changes and do not imply experimental or clinical validation.

## Unreleased

### Security
- Replace vulnerable python-jose usage with PyJWT and restrict JWT verification to HS256 with required exp and sub claims.
- Update Biopython to 1.88 and keep Entrez/XML handling under regression testing.
- Keep TLS verification enabled for ESM Atlas requests; fall back to Tamarind when TLS validation fails.
- Disable the deprecated NVIDIA ESMFold endpoint by default.

### Bioinformatics
- Add a read-only Phase 1L-D evidence projection joining WCVP taxonomy, botanical occurrence claims, MSI annotations and approved QC-RLSC sample signals; enforce study ownership and preserve signal/concentration and bioactivity distinctions.
- Require active, independent administrator review for workflows that gate QC-RLSC signals; block workflow-owner self-approval in the API and transition service, and withhold signals unless the recorded approval actor remains an active administrator.
- Require an accepted WCVP resolution on the same specimen before physical identification can be marked verified; retain independent curator, method, identifier and voucher/herbarium gates.
- Use the embedded GBIF v2 `acceptedUsage` record for synonyms; do not treat the external-ID lookup endpoint as a taxon record endpoint or promote identifier-only matches.
- Normalize documented nested GBIF Species Match v2 payloads (`usage`, `classification`, `diagnostics`) and retain flat-payload compatibility; bind specimen occurrence evidence to the exact accepted WCVP identifier.
- Add a versioned, licence-aware PhytoEvidence source registry and authenticated read-only API; keep Phase 1K LC-MS/MS qualification separate and disable automatic external dataset imports.
- Add WCVP v16 name-resolution proposals through GBIF ChecklistBank v2, with explicit checklist selection, synonym/accepted-name preservation, response hashes and mandatory human review; keep specimen identification and plant–compound occurrence as separate gates.
- Add auditable plant-compound occurrence evidence linked to WCVP resolutions, botanical specimens, plant samples, extraction batches, fractions and accepted MSI level-1 LC-MS/MS annotations; require independent human review before marking occurrence verified.
- Add durable creator/reviewer user attribution for WCVP taxonomic resolutions, reject self-review in both service logic and PostgreSQL, and preserve legacy reviewer labels through migration 016.
- Add the Tamarind ESMFold provider to structure resolution and an RNA-FM secondary-structure workflow via Tamarind.
- Distinguish confidence values for predicted models from experimental B-factors.
- Strengthen AMRFinderPlus FASTA validation, taxonomic-group selection, engine/database readiness and provenance reporting.
- Simplify study creation using versioned profiles and optional advanced metadata.

### Developer experience
- Add AMRFinderPlus setup guidance, contribution instructions and a security policy.
- Add CI checks for RNA-FM/AMRFinderPlus, JavaScript syntax, Docker image build and Compose configuration.
- Move Nginx test configurations and the manual RCSB API diagnostic out of the repository root.
- Integrate Phase 10 benchmark execution into the main Compose file through the benchmark profile.
