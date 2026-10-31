# N3XORA — Experimental Analytics Engine v1

## Scope

The v1 engine adds a controlled tabular import path and a replicate-aware four-parameter logistic (4PL) dose-response analysis to the existing Experimental Evidence workflow.

It does not replace laboratory quality systems, establish biological truth, or infer therapeutic efficacy from docking. It analyzes only the measurements already attached to an assay dossier.

## Import protocol

Supported source files: CSV, TSV, TXT and XLSX (first worksheet, first row as header; maximum 20 MB and 5,000 measurement rows).

Canonical columns:

- Required: `sample_role`, `sample_label`, `replicate_id`, `concentration_value`, `concentration_unit`, `response_name`, `response_value`, `response_unit`.
- Optional: `replicate_kind`, `qualifier`, `timepoint_value`, `timepoint_unit`, `well_id`, `record_key`.

A conservative alias list accepts common names such as `concentration`, `dose`, `readout`, `response`, `replicate` and `unit`. It does not infer ambiguous columns or silently coerce unparseable values.

Import has two steps:

1. `POST /api/v1/research/studies/{study_id}/assays/{assay_id}/measurements/import-preview` parses the file and returns row errors, a preview, and the SHA-256 of the uploaded bytes. No database rows are written.
2. `POST /api/v1/research/studies/{study_id}/assays/{assay_id}/measurements/import?expected_sha256=<preview-hash>` reparses the uploaded file, verifies that its bytes match the preview hash, rejects the entire import if any row is invalid, then stores the measurements and archives the original source file as a private `raw_data` attachment.

All source units are preserved as entered. The importer never converts nM to µM, mg/L to µg/mL, or any other unit pair automatically. A repeated `record_key` within the assay is rejected; when the source contains no record key, a deterministic key is generated from the source hash and row number.

## Statistical analysis

Endpoint fitting is available through:

`POST /api/v1/research/studies/{study_id}/assays/{assay_id}/analyses/dose-response`

The dossier must already be in state `reviewed` with review decision `accepted_for_analysis`. Required parameters are the exact `response_name` and the response direction (`increasing` or `decreasing`). Optional parameters select IC50/EC50/GI50/CC50, a timepoint, the bootstrap iteration count (100–1,000), and a deterministic random seed.

The fitted model is a 4PL curve on log10 concentration:

`y(c) = bottom + (top - bottom) / (1 + exp(-s · h · (log10(c) - log10(EC50))))`

where (s=+1) for increasing responses and (s=-1) for decreasing responses, and (h>0) is the Hill slope. The estimated endpoint is the concentration corresponding to the midpoint between the two fitted asymptotes. This model is a descriptive dose-response model, not a mechanistic binding model.

### Quality gates

- Only `test_sample` rows matching the requested readout and timepoint enter the fit; controls are retained in the dossier but are not fitted as compound observations.
- All fitted concentrations must be positive and use exactly one declared concentration unit.
- All fitted responses must use exactly one response unit.
- Censored observations (`below`, `above`, etc.) are rejected in v1; they are not treated as exact numbers.
- At least five distinct concentrations are required for a four-parameter fit.
- At least two biological replicate IDs must contribute at every concentration.
- Only `replicate_kind=biological` contributes to the fit or bootstrap. Technical replicates and independent-run rows remain archived but are not counted as biological units by v1. Users must not relabel technical wells as biological replicates.
- The v1 engine does not pool assays, normalize units or merge different organisms, strains, methods, protocols, response readouts or exposure times.

### Uncertainty

The point estimate is fitted to biological-replicate observations after averaging any repeated rows sharing the same biological replicate ID and concentration. A 95% percentile cluster-bootstrap interval is estimated by resampling complete biological replicate curves (all included concentrations together), not individual wells.

At least three complete independent biological replicate curves and at least 100 successful bootstrap fits are required for an interval to be returned. If these conditions are not met, the point estimate may be returned but the confidence interval is explicitly marked `not_estimable` with a reason. An estimate outside the tested concentration range is flagged as extrapolation and does not receive a bootstrap interval.

The result includes the endpoint, asymptotes, Hill slope, R², RMSE, observation counts, concentration-level biological means and standard deviations, fitted curve coordinates, warnings, algorithm version and SHA-256 of the exact biological observations used. R² is a diagnostic only; it is not a validation criterion by itself.

## Persistence and provenance

Each successful fit creates an immutable row in `research_assay_analyses`, containing:
- assay and user identifiers;
- analysis type, algorithm and version;
- model parameters;
- full JSON result;
- SHA-256 of the input observations;
- creation timestamp.

The point estimate is also added to `research_assay_endpoints` with origin `derived_from_raw_data`, method and method version. Its confidence bounds are populated only when the bootstrap interval is estimable. A review event records the analysis ID and input hash.

The analysis history is available at:

`GET /api/v1/research/studies/{study_id}/assays/{assay_id}/analyses`

The fit never changes the assay's BioEvidence status. The underlying submitted observation remains grade D unless independent biological validation is established separately.

## Deliberate limitations of v1

- No MIC/MBC inference, censored-dose likelihood, Bayesian dose-response model, or nonlinear mixed-effects model.
- No automated unit conversion or cross-assay pooling.
- No automatic inference that a favorable IC50 proves therapeutic efficacy, selectivity, in-vivo activity or clinical relevance.
- No automatic conversion of docking scores into biological potency.
- No claim of a biological replicate where the source metadata only identifies technical repeats.
- No extrapolation is presented as an experimentally observed concentration.

## Dependencies and deployment

Pinned runtime dependencies added to `backend/requirements.txt`:
- `scipy==1.13.1` for bounded nonlinear least squares and sigmoid functions;
- `openpyxl==3.1.5` for XLSX parsing.

Database migration for existing PostgreSQL volumes:

`infra/postgres/migrations/20261031_experimental_analytics.sql`

Fresh installations and development schema bootstrap also create `research_assay_analyses` via `infra/postgres/init.sql` and `backend/app/database.py`.

Never use `docker compose down -v` on a volume containing valuable research records.


## Biological-parent aware 4PL and docking rank comparison

The advanced statistical endpoint is exposed separately from the original v1 route:

- `POST /api/v1/research/studies/{study_id}/assays/{assay_id}/dose-response-analysis`
- `GET /api/v1/research/studies/{study_id}/assays/{assay_id}/analyses`
- `POST /api/v1/research/studies/{study_id}/assays/docking-potency-comparison`
- `GET /api/v1/research/studies/{study_id}/assays/{assay_id}/analyses/{analysis_id}`

The advanced 4PL model requires an explicit response name, response unit, concentration unit and response direction. It selects one sample label and one timepoint, rejects censored observations, and does not convert units. At least five distinct concentrations and three complete biological replicate profiles are required. Technical measurements are incorporated only when `biological_sample_id` points to a biological replicate recorded at the same concentration; independent-run rows are not promoted to biological replicates. Technical observations are collapsed within their biological parent before fitting. The bootstrap resamples complete biological profiles, not individual technical wells.

The reported 4PL concentration is the midpoint between the fitted lower and upper asymptotes. It is not necessarily the absolute concentration that produces exactly 50% of the raw response when the fitted asymptotes differ from 0 and 100. Quality flags include whether the midpoint is interpolated, plateau coverage, fit diagnostics and bootstrap convergence. These checks are review indicators, not universal pharmacological validity criteria.

The optional docking comparison pairs each accepted assay with one completed AutoDock Vina run. It requires a shared biological target, curator-defined comparability group, exact assay context, matching canonical ligand SMILES, common units, and identical docking protocol and structure fingerprints. It compares ranks using Spearman correlation on Vina scores and log10 experimental midpoints. Its permutation p-value and paired-compound bootstrap interval describe only the submitted cohort. Even a high correlation does not establish causality, calibrate Vina scores to IC50, or validate general predictive performance. Small cohorts remain underpowered; the API enforces a minimum of five pairs as a technical floor, not as a claim of adequate statistical power.

The integration adds `biological_sample_id` to measurement rows and `analysis_key`, `output_sha256` and `status` to analysis records. Fresh installations receive these fields from `infra/postgres/init.sql`. Existing installations must apply `infra/postgres/migrations/20261031_assay_statistics_4pl.sql` once before deploying the API update. The migration is additive and retains legacy analysis rows.

### Docking manifest required for paired comparison

The docking comparison accepts only runs with a stored protocol manifest containing a protocol identifier/version, the SHA-256 fingerprint of the exact target structure, the target accession, the selected pocket count, Vina seed, mode count, energy range and the engine's adaptive-exhaustiveness policy. New direct research docking runs record this manifest at launch. Screening-generated runs retain their screening manifest. Legacy runs with incomplete manifests are deliberately excluded; their exact historical receptor file and settings must not be guessed or reconstructed from defaults. The current Vina runner uses adaptive exhaustiveness based on protein length and ligand torsional degrees of freedom, so it is recorded as a policy rather than a single constant. CPU count is not included in the comparison fingerprint because the current worker does not pass an explicit CPU setting to Vina.

Before a rank comparison, every 4PL sample label must match the assay's declared test article, and all paired analyses must use the same endpoint, exact response name, exact response unit and response direction. These constraints reduce, but do not eliminate, assay comparability concerns.
