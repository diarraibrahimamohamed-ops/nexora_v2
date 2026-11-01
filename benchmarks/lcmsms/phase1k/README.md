# Nexora v2 — Phase 1K: Experimental validation of the LC-MS/MS pipeline

## Purpose and current state

Phase 1K qualifies the integrated Phases 1G–1J against an externally deposited LC-MS/MS study. It is a validation phase, not an algorithm-expansion phase. The initial candidate is MetaboLights MTBLS15740.

The public OmicsDI catalogue record describes untargeted reverse-phase LC-MS in positive and negative modes, mouse feces, intestinal contents and serum, pooled-QC samples, analytical blanks and raw mass-spectrometry files. These are catalogue-level facts only. They do not prove that this accession currently exposes mzML files, or establish its exact licence or ISA-Tab mapping.

**Gate K0 remains PENDING.** Study-specific ISA-Tab, complete archive listing, exact formats, mzML references, injection order and accession-level licence must be retrieved and reviewed. No source data files have been downloaded or processed.

The curated baseline is datasets/MTBLS15740_inventory.json. The live metadata-only result must be written separately so it cannot silently overwrite the reviewed baseline.

## Metadata-only inventory

From the repository root, in an HTTPS-enabled environment:

```bash
python tools/metabolights_inventory.py MTBLS15740 \
  --output benchmarks/lcmsms/phase1k/datasets/MTBLS15740_live_inventory.json
```

The script queries MetaboLights metadata and the public archive listing. It downloads only ISA-Tab metadata; it never downloads files under FILES/. It records metadata SHA-256, paths, sample-to-file references and blockers. Sample roles and polarities are heuristic candidates and require manual review. Injection order is not inferred from filenames or row order.

## Work packages

1. K0 — accession-level licence, public availability, ISA-Tab, archive inventory, sample/QC/blank/standard roles, polarities, injection order and mzML/RAW availability.
2. K1 — approved source subset, file integrity, checksums and parent/derived provenance.
3. K2 — frozen OpenMS + matchms reprocessing and MS1/MS2 coverage review.
4. K3 — Phase 1I QC against predeclared method-specific limits.
5. K4 — Phase 1J polarity-separated alignment, anchor/residual review and manual adjudication.
6. K5 — QC-RLSC diagnostics, raw/corrected metrics and validation-only leave-one-QC-out prediction.
7. K6 — annotation review only where suitable standards and reference data exist.
8. K7 — reproducibility assessment and final scientific report.

## Data governance

- No RAW or derived instrument files in Git.
- No redistribution before study-specific licence and submitter terms are confirmed.
- Keep vendor originals immutable; record conversion software, parameters and parent hashes.
- Do not mix matrices, methods, batches, gradients, instruments or polarities without explicit comparability evidence.
- Preserve raw intensities and source features; QC-RLSC remains a separate result layer.
- Do not infer chemical identity or pharmacological activity from alignment, correction or library similarity.

## Sources

- MTBLS15740 catalogue: https://www.omicsdi.org/dataset/metabolights_dataset/MTBLS15740
- MetaboLights Files guide: https://ebi-metabolights.github.io/guides/Files/
- MetaboLights Assay guide: https://ebi-metabolights.github.io/guides/Assay/
- MetaboLights guides and submission policy: https://ebi-metabolights.github.io/guides/
