# N3XORA — Experimental Evidence & Validation

## Goal and scientific boundary

The module stores experimental assay dossiers as structured, traceable records attached to an existing research study. It separates assay metadata, raw measured observations, reported/derived endpoints, source files and curator events.

The system does **not** fit dose-response curves, infer MIC/IC50 from raw measurements, convert units silently, or infer biological validity from a docking score. Endpoint values are declared by their source/operator and retain their origin and method. The same numeric value in different units, biological systems, strains, methods or protocols is not automatically comparable.

An assay's administrative review checks dossier completeness and provenance. `accepted_for_analysis` does not mean the biological effect was independently replicated or clinically validated. Its BioEvidence record remains `observation`, grade `D`; it is never auto-promoted to `experimentally_validated`.

## Data model

- `research_assays`: assay type, test article, study/target/variant/screening links, organism/strain/cell line, method/SOP, laboratory/operator, date, protocol reference, source kind, conditions and review state.
- `research_assay_measurements`: one row per observation, with sample/control role, replicate ID and kind, concentration value/unit, readout name/value/unit, qualifier, timepoint and optional well/record key.
- `research_assay_endpoints`: IC50, EC50, MIC, MBC, CC50, GI50, IC90, EC90, KD, Ki or OTHER. Each stores the exact value/unit, qualifier, origin (`measured_reported`, `literature_reported`, `derived_from_raw_data`), fitting/reporting method, confidence interval and replicate count.
- `research_assay_attachments`: original filename, MIME, size, role, private storage path and SHA-256.
- `research_assay_review_events`: append-only creation, submission and curator-review timeline.

Raw concentration and response units are preserved exactly as entered. No cross-unit normalization or conversion is performed by this module.

## States and guards

`draft` → `submitted` → `reviewed` or `rejected`.

Only draft assays can receive new measurements, endpoints or attachments. Submission requires at least one measured observation or endpoint. Lab-generated dossiers require laboratory identity and assay date. Measured lab/public datasets require at least one test-sample row and at least one declared control. Dose-response submissions require at least three distinct test concentrations and at least two identified replicates. Susceptibility/dose-response test rows require explicit concentration units.

Curator review is restricted to an administrator. A dossier may be `accepted_for_analysis` or `rejected`. Comparability is separately declared as `not_assessed`, `comparable` or `not_comparable`. To mark `comparable`, the reviewer must specify a comparison group and rationale; the dossier must include an endpoint, method, biological system and conditions. The API does not calculate pooled effects or cross-assay rankings. Subsequent dossiers assigned to the same comparison group must match the group's assay type, method, organism, strain/cell line, conditions, endpoint names and exact endpoint units. No automatic unit conversion is performed.

## API

- `POST /api/v1/research/studies/{study_id}/assays` — create draft dossier.
- `GET /api/v1/research/studies/{study_id}/assays` — list dossiers.
- `GET /api/v1/research/studies/{study_id}/assays/{assay_id}` — retrieve metadata, measurements, endpoints, files and review events.
- `POST /api/v1/research/studies/{study_id}/assays/{assay_id}/measurements` — append up to 5,000 observation rows.
- `POST /api/v1/research/studies/{study_id}/assays/{assay_id}/endpoints` — append reported/derived endpoint records.
- `POST /api/v1/research/studies/{study_id}/assays/{assay_id}/attachments` — upload CSV, TSV, TXT, PDF, PNG, JPG/JPEG or XLSX (20 MB maximum).
- `POST /api/v1/research/studies/{study_id}/assays/{assay_id}/submit` — freeze dossier, write a canonical manifest SHA-256 and create a grade-D BioEvidence observation.
- `GET /api/v1/research/assays/pending-review` — administrator queue of submitted dossiers.
- `GET /api/v1/research/assays/{assay_id}/review-data` — administrator-only full dossier view.
- `POST /api/v1/research/studies/{study_id}/assays/{assay_id}/review` — administrator review and comparability declaration.
- `GET /api/v1/research/studies/{study_id}/assays/{assay_id}/attachments/{attachment_id}` — owner-scoped download with SHA-256 verification.
- `GET /api/v1/research/studies/{study_id}/assays/{assay_id}/review-history` — audit history.

## Integrity and privacy

Files are stored under `UPLOAD_DIR/experimental_evidence/study_{id}/assay_{id}` under randomized names. The API accepts a conservative extension/MIME allowlist, enforces a 20 MB limit, stores SHA-256 and checks the digest again when a file is downloaded. Download endpoints are scoped to the study owner; administrators may perform the separate review operation.

The submission manifest hashes normalized dossier metadata, measurements, endpoints and attachment hashes. It is an integrity fingerprint for the submitted record, not proof that the original measurements are accurate.

## Migration and deployment

Migration: `infra/postgres/migrations/20261031_experimental_evidence.sql`.

Fresh PostgreSQL installations also receive the tables from `infra/postgres/init.sql`. Existing installations use the explicit migration; the idempotent application bootstrap contains corresponding schema patches for development installations.

Do not run `docker compose down -v` against a database with valuable research data.

## Deliberate exclusions

- No automated IC50/MIC/EC50 curve fitting or statistical pooling.
- No implicit unit conversions or comparability assumptions.
- No automated promotion to experimentally validated evidence.
- No public attachment URLs or unrestricted file serving.
- No claim that a curator's completeness review independently reproduces the experiment.
