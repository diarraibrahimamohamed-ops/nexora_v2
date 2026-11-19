# Scientific evidence linkage for completed sequence analyses

## Purpose

This phase connects a completed asynchronous NEXORA sequence analysis to the existing
`ResearchEvidence` registry of a research study. It does not create a parallel evidence
store and does not promote a computational result to experimental evidence.

## API

`POST /api/v1/research/studies/{study_id}/evidence/from-analysis/{analysis_id}`

The authenticated user must own both the study and the analysis. Only completed
asynchronous sequence analyses with a verifiable provenance chain can be linked.

The operation validates:

1. the sealed `scientific_analysis_input` manifest;
2. the sealed `scientific_analysis` provenance manifest;
3. the SHA-256 of the persisted result against `scientific_output_sha256`;
4. the sequence row belongs to the analysis and matches the input manifest;
5. the provenance input and output hashes refer to those same artifacts.

A failed or incomplete integrity check returns HTTP 409 and creates no evidence row.

## Evidence semantics

A successful link creates a `ResearchEvidence` record with:

- `object_type=sequence` and `object_id={analysis_id}`;
- `evidence_kind=nexora_sequence_analysis`;
- `evidence_status=computational_hypothesis`;
- `evidence_grade=D`;
- input and output SHA-256 digests;
- algorithm name and version;
- links to the input and provenance manifest digests;
- a statement of interpretation limits.

The evidence remains `pending_review`. It is not treated as independently accepted
until the existing independent-review workflow records an administrator's decision.
The endpoint never assigns `documented_association` or `experimentally_validated`.

## Idempotence

A repeated request for the same study and analysis returns the existing evidence record
when the input and output digests are unchanged. If a record already exists with different
digests, the endpoint returns HTTP 409 instead of silently creating a second claim.

## Scope and limitations

This phase supports the persisted asynchronous DNA sequence-analysis workflow. It does
not yet auto-link synchronous analyses, alignments, docking, LC-MS/MS, or phyto-chemical
candidate records. Those domains should use their own typed provenance adapters and
evidence semantics rather than being forced into a sequence-specific endpoint.

SHA-256 verifies consistency against the stored manifest; it is not a digital signature,
does not authenticate authorship, and does not establish biological correctness.
