# Phase 1L-C — Plant–Compound Occurrence Evidence

**Version:** 1.0.0 — **Date:** 2 November 2026

## Objective

Move plant-compound occurrence from a mutable candidate status into an auditable evidence register. A taxonomic name match alone is never occurrence evidence. A literature report, a specimen-linked experiment and an accepted analytical annotation are separate evidence types.

## Evidence model

`phytochem_occurrence_evidence` stores the candidate, accepted taxon resolution, plant part, source reference/locator, claim text, optional measurement/method, metadata snapshot, SHA-256 and review decision. Optional material lineage connects the claim to a botanically verified specimen, its plant material sample, extraction batch and fraction, and an accepted LC-MS/MS compound annotation.

The evidence content is not overwritten by review. Only reviewer, decision, time and rationale are added. The authenticated creator cannot approve their own claim: acceptance requires a different authenticated administrator. A specimen used for experimental occurrence evidence must first be independently curated as `verified`, with an accepted WCVP resolution linked to that specimen, matching WCVP name/identifier, taxonomic source, identification method, named identifier, voucher code and herbarium.

## Evidence gates

- **Literature report:** accepted WCVP resolution for the candidate, DOI/PMID/HTTP(S) primary source, plant part and a curator-readable statement. It may be taxon-level without a physical specimen; it is not a new Nexora analytical observation.
- **Specimen-linked experimental report:** accepted taxon matching the candidate, specimen identification status `verified`, and a primary source. If a sample is supplied, its stored plant part must match the claim. Extraction and fraction IDs must follow the sample lineage.
- **LC-MS/MS observation:** accepted annotation linked to the same candidate, MSI level 1, and paired feature observations from a plant-material injection and an authentic-standard injection in the same analytical batch. Nexora recomputes the measured m/z error (ppm) and retention-time delta (seconds) from those observations against explicit tolerances at creation and again at review; caller-supplied match flags or error values are not accepted as proof.

## API

- `POST /api/v1/phytochem/{candidate_id}/occurrence-evidence` — create a pending evidence claim (admin).
- `GET /api/v1/phytochem/{candidate_id}/occurrence-evidence?review_status=pending_review` — inspect claims (admin).
- `POST /api/v1/phytochem/occurrence-evidence/{evidence_id}/review` — accept, reject or mark insufficient (admin).

The legacy `POST /api/v1/phytochem/{candidate_id}/verify-occurrence` route remains compatible but now creates a literature proposal; a separate review action is required before occurrence is marked verified.

## State transitions

- Creation → `pending_review`; candidate occurrence status is unchanged.
- Acceptance → `accepted`; candidate occurrence status becomes `VÉRIFIÉE`, and source/plant-part/review summary fields are updated.
- Rejection → `rejected`; candidate occurrence status is not downgraded.
- Insufficient → `insufficient`; candidate occurrence status is not changed.

Chemical identity remains independent. Occurrence acceptance does not set chemical identity to verified or make a docking candidate eligible by itself.

## Migration and limitations

Apply migration `015_phytochem_occurrence_evidence.sql` after migration 014. SQLAlchemy schema initialization registers the same table. The SHA-256 fingerprints the structured claim and its material/source references; it is rechecked before review but is not a checksum of the contents of an externally hosted article. The candidate's stored WCVP identifier must also equal the identifier on the linked accepted taxon resolution.

A reported occurrence is not experimental confirmation by Nexora. An MSI level-1 annotation supports a stronger analytical occurrence claim, but does not prove pharmacological activity, safety or efficacy. No peak area is converted into a concentration unless a value and unit are explicitly supplied with analytical context.
