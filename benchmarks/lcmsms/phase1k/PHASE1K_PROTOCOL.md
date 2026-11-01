# Phase 1K — Experimental Validation & Analytical Qualification

**Protocol version:** 1.0.0  
**Status:** Proposed; freeze before analytical processing  
**Dataset candidate:** MTBLS15740  
**Current gate:** K0 — PENDING

## 1. Question and scope

Does the existing Nexora LC-MS/MS pipeline (Phase 1G experimental lineage, 1H OpenMS/matchms, 1I batch QC, 1J alignment and pooled-QC QC-RLSC) produce traceable and analytically coherent outputs on an independently deposited experimental dataset?

This protocol evaluates the current implementation. It does not tune production algorithms against the evaluation dataset. The first campaign is restricted to one method-homogeneous batch; matrices and polarities are separate strata. Technical injections are not biological replicates. A single study does not establish inter-laboratory reproducibility.

## 2. K0 — Eligibility and provenance

Required before raw-data download:
- accession resolves to the intended public study;
- accession-level licence and submitter terms are recorded;
- Investigation, Sample and all relevant Assay ISA-Tab files are retrieved and hashed;
- public archive listing is complete (listing caps or retrieval errors block K0);
- every selected Assay row maps to the expected raw/derived file;
- sample, pooled-QC, solvent blank, extraction blank and standard roles are confirmed against study protocol;
- positive/negative assay mapping is unambiguous;
- acquisition order is traceable to documented source records;
- mzML availability is confirmed, or a documented vendor-to-mzML conversion plan is approved.

Do not proceed to data download or OpenMS if licence, mapping, polarity, QC identity or injection order remains unresolved. K0 PASS also requires human review; successful execution of the inventory script is not itself a qualification.

## 3. K1 — Integrity and subset

After K0 approval, choose a small, method-homogeneous subset with available biological samples, blanks/standards where supplied, and enough pooled-QC injections to test the existing QC-RLSC eligibility defaults. Record accession, path, size, sample, assay, polarity, order, type, file role, checksum and parent/derived relationship. If the repository has no published checksum, calculate SHA-256 after controlled transfer and label it a local integrity hash.

## 4. K2 — OpenMS/matchms

Freeze Nexora commit, worker image digest, OpenMS/matchms versions and per-injection parameters. Report file validation, spectrum counts by MS level, polarity, RT units/range, detected features, MS2 association statuses, annotation candidates, runtime, resource use and input/output hashes. Inspect a stratified subset against source chromatograms/spectra. A feature is not a compound identification.

## 5. K3 — Phase 1I QC

Predeclare method-specific acceptance limits before analysis. Report pooled-QC repeatability, internal-standard stability, blank/QC ratios where comparable, mass accuracy, RT drift, suitability and carryover where present. Duplicate, mixed-unit, insufficient or threshold-free measures remain not assessed/insufficient. Default screening thresholds are not universal method acceptance limits.

## 6. K4 — Phase 1J alignment

Align one homogeneous method and one polarity at a time. Independently review unique anchor count/distribution, RT coverage and monotonicity, residuals/outliers, no-extrapolation behaviour, consensus membership, partial groups, singletons, charge/adduct compatibility and source immutability. Use a manually adjudicated subset. Report precision/recall only with documented sampling and adjudication; otherwise report descriptive agreement.

## 7. K5 — QC-RLSC

Freeze production QC-RLSC parameters for the primary evaluation. Report raw and corrected pooled-QC RSD, absolute/relative change, factor distributions, fractions corrected/unchanged/rejected/ineligible, missingness and samples outside QC range.

Add a validation-only leave-one-pooled-QC-out diagnostic in a separate harness: fit the existing LOESS model on the remaining eligible QC points and predict the held-out log-intensity. If the minimum QC count cannot be maintained, mark it not estimable. This diagnostic must not alter production outputs. A lower in-sample RSD alone is not evidence of better correction.

## 8. K6 — Annotation

Assess MSI levels only where appropriate evidence exists. MSI 1 requires an authentic standard under the same method and at least two independent orthogonal analytical properties. Library similarity alone does not establish MSI 1, pharmacological activity or efficacy.

## 9. K7 — Reproducibility and report

Repeat selected processing from the exact same source inputs, image digest and frozen configuration. Compare canonical scientific outputs after excluding technical run IDs/timestamps, and explain nondeterminism. Report provenance/licence, batch and sample selection, instrument/method, versions, parameters, endpoints, results by polarity/matrix, uncertainty, exclusions, manual adjudication and limitations.

## 10. Gate decisions

Each gate receives PENDING, PASS, CONDITIONAL or FAIL, with evidence, reviewer, timestamp and rationale. Automated tool success alone never yields PASS.

## 11. Explicit non-claims

This protocol cannot establish clinical validity, pharmacological efficacy, biomarker utility, universal method suitability, inter-laboratory reproducibility or regulatory qualification. One dataset can test an end-to-end use case and expose limits; broader claims require independent datasets and laboratories.
