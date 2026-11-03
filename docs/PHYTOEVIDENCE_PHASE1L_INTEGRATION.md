# Phase 1L-D — Integrated Phytochemical Evidence View

**Version:** 1.0.0 — **Date:** 2 November 2026  
**Type:** Read-only projection; no new persistence table or migration.

## Purpose

Phase 1L-D connects existing Nexora records into one candidate-centred research view without copying or collapsing their source data. It brings together:

1. chemical identity and its provenance;
2. accepted WCVP nomenclature and taxonomic review;
3. bibliographic or specimen-linked plant–compound occurrence claims;
4. physical material lineage (specimen → sample → extraction → fraction);
5. LC-MS/MS annotations and source observations;
6. QC-RLSC corrected signal values where the analytical and quality-review gates are satisfied;
7. literature-reported bioactivity metadata, kept separate from analytical evidence.

The endpoint is a projection over existing rows. It does not create an identity, an occurrence claim, an LC-MS/MS annotation, a concentration, or a pharmacological conclusion.

## API

`GET /api/v1/phytochem/{CANDIDATE_ID}/evidence-integration`

Authentication is required. Optional query parameters:

- `annotation_limit`: 1–100, default 50.
- `quantification_cell_limit`: 1–1000, default 250.

The endpoint returns identity, taxonomy, occurrence, reported bioactivity, analytical annotations, interpretation gates, limitations, and pagination information.

### Example

```http
GET /api/v1/phytochem/42/evidence-integration?annotation_limit=50&quantification_cell_limit=250
Authorization: Bearer <token>
```

## Access and privacy

For a non-administrator, LC-MS/MS annotations and signal values are limited to research studies owned by the authenticated user. An annotation, injection, sample, specimen, aligned member and quantification cell must remain linked to the same study and material lineage. An administrator can inspect all study scopes.

Specimen coordinates, locality, and other precise collection geography are not included in this projection. The material snapshot contains only non-geographic identifiers and curation fields (specimen code, accepted taxon, WCVP identifier, identification state, voucher/herbarium, sample code, plant part, and relevant extraction/fraction metadata).

Candidate-level literature claims without private specimen links remain available as shared curated evidence. Specimen-linked claims are included only when their specimen belongs to an accessible study.

## Scientific interpretation gates

### Chemical identity

The candidate identity state is returned independently. A taxonomic match, a feature mass, a library match, or a candidate annotation does not change the chemical identity state.

### Taxonomic nomenclature

The view exposes the accepted WCVP name and identifier, resolution provenance, and review state. An accepted nomenclatural name does not identify a physical specimen and does not establish occurrence of a compound in that plant.

### Plant–compound occurrence

Occurrence claims retain their source, plant part, evidence type, review state, and integrity hash. The candidate-level occurrence status and the count of accepted source-linked claims are shown separately; the projection does not silently convert one into the other.

### LC-MS/MS identity and occurrence

Annotations remain separated by status and MSI level. Only an annotation with status `accepted` and MSI level 1 is counted as an accepted MSI-1 annotation. A pending or candidate annotation is labelled `candidate_feature_signal_only`, not as a confirmed compound occurrence.

Feature observations are linked to their injection and available material snapshot. Cross-study or inconsistent material links are excluded rather than inferred.

### QC-RLSC signal

A normalized signal is included only when all of these conditions hold:

- the source feature is a member of a completed, sealed alignment run;
- the alignment workflow has reached `succeeded` and has an accepted quality-review event recorded by an authenticated administrator other than the workflow owner;
- the quantification run is `completed`, has a non-empty output SHA-256, and belongs to the same study and batch as the alignment;
- the quantification workflow has reached `succeeded` and has an accepted quality-review event recorded by an authenticated administrator other than the workflow owner;
- the matrix cell corresponds to the exact aligned feature, injection, processing run and feature member;
- the injection is a sample injection linked to material already observed for that candidate feature.

Only sample-injection cells are returned; pooled-QC and blank injections are not presented as biological samples. Each cell retains its normalization status, QC RSD before/after and quality metrics. A cell that is `not_detected`, `insufficient_qc`, `outside_qc_range`, or otherwise not corrected keeps that state; it is not converted to zero or presented as a corrected value.

**Unit semantics:** `raw_intensity` and `corrected_intensity` are instrument signal intensities. They are not concentrations in mg/g, mg/mL, molarity, or an absolute abundance. No standard-curve conversion is performed by this view.

The projection does not pool biological replicates into a single mean and does not collapse technical replicates. Each injection remains separately traceable.

The `POST /api/v1/scientific-workflows/{run_id}/actions` endpoint permits `approve` and `reject` only to an administrator distinct from the workflow owner. The workflow transition service independently rejects missing reviewer identities and owner self-review. An event that merely has action `approve` is insufficient for this view unless its actor differs from the recorded owner.

### Reported bioactivity

The candidate-level target, assay type, endpoint, unit, reference, and evidence grade are returned as reported metadata, not as a newly validated assay result. No docking score, spectral match, taxonomy match, or normalized intensity is interpreted as pharmacological efficacy, safety, or clinical benefit.

## Persistence and migrations

Phase 1L-D introduces no database table and no migration. It reads the existing candidate, taxonomic resolution, occurrence evidence, botanical material, LC-MS/MS annotation, alignment, quantification, and workflow review records. Raw observations, quantification cells, and evidence claims are not updated by the GET request.

## Current limitations

- The view is candidate-centred; it does not deduplicate different candidate records by InChIKey.
- It does not fit dose–response models, estimate IC50, or compute absolute concentration.
- It does not combine technical or biological replicates into statistical estimates.
- It does not infer bioactivity or therapeutic suitability.
- It does not perform a new spectral library search or modify an annotation.
- The number of annotations and cells returned is bounded by the request limits.

## Validation

The regression suite covers read-only behaviour, owner isolation, hidden geographic fields, candidate-versus-accepted annotation semantics, and the requirement for approved alignment and QC-RLSC workflows before sample signal values are included.
