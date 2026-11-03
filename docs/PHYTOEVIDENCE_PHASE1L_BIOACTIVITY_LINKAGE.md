# Phase 1L-E — Experimental Bioactivity Evidence Linkage

## Scope

Phase 1L-E extends the read-only PhytoEvidence integration view so that a phytochemical candidate can be connected to structured experimental assay dossiers and their existing statistical analyses.

It does not create a second assay model, import duplicate endpoint records, or execute a second dose-response fit. The existing Research Assay domain, 4PL service and biological-replicate cluster bootstrap remain the source of truth.

## Explicit linkage

The projection accepts only this persisted relation:

`PhytochemicalCandidate.id`
→ `ResearchScreeningCompound.phytochem_candidate_id`
→ `ResearchScreeningRun`
→ `ResearchAssay.screening_compound_id`
→ `ResearchAssayMeasurement / ResearchAssayEndpoint / ResearchAssayAnalysis`

A compound may represent a deduplicated ligand mapped to multiple catalog candidates. The projection recognizes both the primary candidate foreign key and exact candidate IDs retained in the screening compound's `phytochem_candidate_ids` array. It validates exact integer membership after a portable prefilter; it does not infer a link from a compound name, InChIKey, SMILES string, assay label or similar endpoint value. The canonical SMILES comparison is reported as a separate consistency signal; it does not create the relational link.

## Read API

Existing endpoint:

`GET /api/v1/phytochem/{candidate_id}/evidence-integration?assay_limit=50`

New response section: `experimental_bioactivity`.

The projection includes:

- assay identity, method/version, laboratory, biological system, strain/cell line, date and declared conditions;
- source reference and curation state, including `accepted_for_analysis` and explicit comparability group/rationale;
- linked screening compound/run identifiers and candidate-to-screening canonical SMILES comparison;
- measurement counts, test-sample counts, biological replicate counts, technical measurement counts, concentration levels and response definitions;
- reported or derived endpoints with units, qualifiers, methods and confidence bounds;
- versioned analysis identifiers, algorithm, input/output SHA-256, 4PL midpoint, Hill slope, R²/RMSE, 95% confidence interval, bootstrap convergence, selected measurement IDs, bounded per-concentration biological summaries, fitted curve points, replicate/concentration counts and quality warnings;
- attachment metadata and hashes, without exposing filesystem paths or public attachment URLs.

Pagination is bounded. The response reports total counts and truncation flags for assays, endpoints, analyses and attachments.

## Access control

For non-administrators, only assays belonging to research studies owned by the authenticated user are projected. Administrators may inspect all linked records. The candidate itself is not a study-owned object; the study scope is enforced on every structured assay join.

A record is included only if its `ResearchScreeningCompound.phytochem_candidate_id` equals the requested candidate and its assay and screening run belong to the same study.

## Curation and scientific interpretation

The assay API refuses self-review: an administrator who created a submitted assay dossier cannot approve that same dossier. The read projection independently verifies the recorded reviewer is a different, active administrator before reporting the dossier as accepted for analysis; historical or altered self-reviews, inactive accounts and non-administrator reviewers do not pass this gate.

The following distinctions remain mandatory:

- `submitted` is not the same as curator-reviewed;
- `accepted_for_analysis` is an administrative completeness/provenance decision, not independent biological validation;
- a 4PL result is a derived analysis of the selected declared measurements;
- a fitted midpoint labelled IC50/EC50 is relative to the fitted asymptotes, not necessarily the absolute concentration producing exactly 50% of the raw response;
- the 95% interval is a biological-replicate cluster-bootstrap interval; technical wells do not increase the biological replicate count;
- `comparable` and its group are curator declarations subject to the existing method/biological-system/conditions/endpoint checks. The view does not pool assays or normalize units;
- generated analysis evidence remains grade D / `computational_hypothesis`.

The projection does not claim potency beyond the stated assay system, infer selectivity, infer in-vivo exposure, or establish efficacy, safety or clinical relevance. Docking scores are not calibrated into IC50, Ki, Kd or free-energy measurements.

## Data and migration impact

No new table or PostgreSQL migration is introduced. Phase 1L-E reads the existing `research_assays`, `research_assay_measurements`, `research_assay_endpoints`, `research_assay_analyses`, `research_assay_attachments`, `research_screening_compounds` and `research_screening_runs` records.

## Validation requirements

Regression tests cover:
- candidate-to-screening-to-assay linkage without name-based inference;
- owner-only visibility and administrator visibility;
- measured assay counts, biological replicate counts, endpoint units and confidence intervals;
- explicit structure mismatch reporting without creating or rejecting the candidate link;
- absence of assay evidence when no persisted screening-compound relation exists;
- refusal of assay creator self-review;
- invalid pagination bounds and no mutation of underlying scientific records.
