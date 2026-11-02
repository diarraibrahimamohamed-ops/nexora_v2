# Phase 1L-B — Botanical Identity & Taxonomic Resolution

**Version:** 1.0.0 — **Date:** 2 November 2026

POWO uses WCVP as its nomenclatural backbone. Nexora queries the GBIF ChecklistBank Species Match API v2 using the documented response contract (`usage`, `acceptedUsage`, `classification`, `diagnostics`), while retaining compatibility with flat payloads, and the explicit WCVP checklist key `f382f0ce-323a-4091-bb9f-add557f3a9a2` and `kingdom=Plantae`. Dataset DOI: `10.15468/6h8ucr`; Kew WCVP v16 repository DOI: `10.34885/egs6-cp24`.

A match creates a `pending_review` proposal. Matched/synonym and accepted usages remain separate; the full API response, checklist/version, retrieval mode and SHA-256 are retained. A synonym is not promoted from an identifier hint alone: its complete `acceptedUsage` record must be present. An administrator accepts/rejects with identity and rationale. Acceptance updates the normalized name and WCVP identifier only: it does not verify a specimen, chemical identity, plant–compound occurrence, bioactivity or efficacy. A specimen remains `provisional` until separate botanical identification review. Compound occurrence requires an independent primary source and curator attestation.

## API
- `POST /api/v1/phytochem/{candidate_id}/taxonomy/resolve`
- `GET /api/v1/phytochem/taxonomy-resolutions?review_status=pending_review&skip=0&limit=50` (admin curation queue)
- `GET /api/v1/phytochem/taxonomy-resolutions/{resolution_id}` (admin only)
- `POST /api/v1/phytochem/taxonomy-resolutions/{resolution_id}/review`
- `POST /api/v1/lcmsms/specimens/{specimen_id}/taxonomy/resolve`

This phase uses unit-level API requests; it does not download or redistribute the WCVP bulk archive. Kew and GBIF metadata expose different CC-BY version labels, so rights remain dataset/record scoped and must be rechecked before export or bulk import.

## References
- [POWO](https://powo.science.kew.org/about)
- [WCVP v16 — Kew repository DOI](https://doi.org/10.34885/egs6-cp24)
- [WCVP — GBIF dataset DOI](https://doi.org/10.15468/6h8ucr)
- [GBIF taxonomy interpretation](https://techdocs.gbif.org/en/data-processing/taxonomy-interpretation)
- [GBIF Species API v2 — endpoints and ID lookup](https://techdocs.gbif.org/en/openapi/v1/species)
- [GBIF ChecklistBank OpenAPI schema — `NameUsageMatch`](https://techdocs.gbif.org/openapi/checklistbank.json)
- [GBIF Species API v2](https://github.com/gbif/taxon-ws)
- [Kew terms of use](https://www.kew.org/science/collections-and-resources/data-and-digital/terms-of-use)
