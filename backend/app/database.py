import logging
logger = logging.getLogger(__name__)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

# SQLite n'accepte pas pool_size/max_overflow
_url = settings.DATABASE_URL
if _url.startswith("sqlite"):
    engine = create_engine(_url, connect_args={"check_same_thread": False})
else:
    engine = create_engine(_url, pool_pre_ping=True, pool_size=10, max_overflow=20)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema() -> None:
    """Apply idempotent schema patches that init.sql missed on existing volumes."""
    statements = (
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE",
        "CREATE INDEX IF NOT EXISTS idx_users_admin ON users(is_admin)",
        "CREATE TABLE IF NOT EXISTS research_studies (id SERIAL PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE, name VARCHAR(255) NOT NULL, disease VARCHAR(150) NOT NULL DEFAULT 'malaria', pathogen VARCHAR(150) NOT NULL DEFAULT 'Plasmodium falciparum', country_focus VARCHAR(120) NOT NULL DEFAULT 'Mali', region_scope VARCHAR(120) NOT NULL DEFAULT 'Sahel', objective TEXT, hypothesis TEXT, status VARCHAR(40) NOT NULL DEFAULT 'draft', metadata JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS idx_research_studies_user ON research_studies(user_id)",
        "CREATE TABLE IF NOT EXISTS research_variants (id SERIAL PRIMARY KEY, study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE, source_analysis_id INT REFERENCES analyses(id) ON DELETE SET NULL, source_accession VARCHAR(120), gene_symbol VARCHAR(120), nucleotide_position INT, ref_nt VARCHAR(1), alt_nt VARCHAR(1), codon_ref VARCHAR(3), codon_alt VARCHAR(3), aa_ref VARCHAR(1), aa_alt VARCHAR(1), variant_notation VARCHAR(100) NOT NULL, evidence_class VARCHAR(40) NOT NULL DEFAULT 'candidate', evidence_sources JSONB NOT NULL DEFAULT '[]', notes TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS idx_research_variants_study ON research_variants(study_id)",
        "ALTER TABLE research_variants ADD COLUMN IF NOT EXISTS protein_position INT",
        "ALTER TABLE research_variants ADD COLUMN IF NOT EXISTS consequence VARCHAR(80)",
        "ALTER TABLE research_variants ADD COLUMN IF NOT EXISTS annotation_method VARCHAR(120)",
        "ALTER TABLE research_variants ADD COLUMN IF NOT EXISTS annotation_notes TEXT",
        "CREATE TABLE IF NOT EXISTS research_targets (id SERIAL PRIMARY KEY, study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE, variant_id INT REFERENCES research_variants(id) ON DELETE SET NULL, state VARCHAR(20) NOT NULL DEFAULT 'wt', gene_symbol VARCHAR(120), protein_name VARCHAR(255), protein_sequence TEXT NOT NULL, structure_source VARCHAR(40) NOT NULL DEFAULT 'sequence_resolved', structure_accession VARCHAR(120), structure_path TEXT, structure_confidence NUMERIC(6,4), structure_status VARCHAR(50) NOT NULL DEFAULT 'not_generated', structure_sequence_identity NUMERIC(6,5), structure_sequence_coverage NUMERIC(6,5), structure_validation JSONB NOT NULL DEFAULT '{}', provenance JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "ALTER TABLE research_targets ADD COLUMN IF NOT EXISTS structure_status VARCHAR(50) NOT NULL DEFAULT 'not_generated'",
        "ALTER TABLE research_targets ADD COLUMN IF NOT EXISTS structure_sequence_identity NUMERIC(6,5)",
        "ALTER TABLE research_targets ADD COLUMN IF NOT EXISTS structure_sequence_coverage NUMERIC(6,5)",
        "ALTER TABLE research_targets ADD COLUMN IF NOT EXISTS structure_validation JSONB NOT NULL DEFAULT '{}'",
        "CREATE INDEX IF NOT EXISTS idx_research_targets_study ON research_targets(study_id)",
        "CREATE TABLE IF NOT EXISTS research_structure_jobs (id SERIAL PRIMARY KEY, study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE, target_id INT NOT NULL REFERENCES research_targets(id) ON DELETE CASCADE, provider VARCHAR(40) NOT NULL, status VARCHAR(30) NOT NULL DEFAULT 'pending', celery_task_id VARCHAR(255), progress INT NOT NULL DEFAULT 0, error TEXT, metadata JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS idx_research_structure_jobs_target ON research_structure_jobs(target_id)",
        "CREATE INDEX IF NOT EXISTS idx_research_structure_jobs_study ON research_structure_jobs(study_id)",
        "CREATE TABLE IF NOT EXISTS research_docking_runs (id SERIAL PRIMARY KEY, study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE, target_id INT NOT NULL REFERENCES research_targets(id) ON DELETE CASCADE, variant_id INT REFERENCES research_variants(id) ON DELETE SET NULL, docking_job_id INT REFERENCES docking_jobs(id) ON DELETE SET NULL, ligand_smiles VARCHAR(1000) NOT NULL, reference_state VARCHAR(20) NOT NULL DEFAULT 'wt', engine VARCHAR(80) NOT NULL DEFAULT 'AutoDock Vina', aggregation_method VARCHAR(120) NOT NULL DEFAULT 'ASP — analyse complémentaire', status VARCHAR(30) NOT NULL DEFAULT 'pending', parameters JSONB NOT NULL DEFAULT '{}', result_summary JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS idx_research_docking_study ON research_docking_runs(study_id)",
        "CREATE INDEX IF NOT EXISTS idx_research_docking_job ON research_docking_runs(docking_job_id)",
        "CREATE TABLE IF NOT EXISTS research_structure_comparisons (id SERIAL PRIMARY KEY, study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE, wt_target_id INT NOT NULL REFERENCES research_targets(id) ON DELETE CASCADE, variant_target_id INT NOT NULL REFERENCES research_targets(id) ON DELETE CASCADE, variant_id INT REFERENCES research_variants(id) ON DELETE SET NULL, status VARCHAR(30) NOT NULL DEFAULT 'computed', parameters JSONB NOT NULL DEFAULT '{}', report JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS idx_research_structure_comp_study ON research_structure_comparisons(study_id)",
        "CREATE INDEX IF NOT EXISTS idx_research_structure_comp_targets ON research_structure_comparisons(wt_target_id, variant_target_id)",
        "CREATE TABLE IF NOT EXISTS research_docking_comparisons (id SERIAL PRIMARY KEY, study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE, variant_id INT REFERENCES research_variants(id) ON DELETE SET NULL, wt_run_id INT NOT NULL REFERENCES research_docking_runs(id) ON DELETE CASCADE, variant_run_id INT NOT NULL REFERENCES research_docking_runs(id) ON DELETE CASCADE, ligand_smiles VARCHAR(1000) NOT NULL, status VARCHAR(30) NOT NULL DEFAULT 'pending', protocol JSONB NOT NULL DEFAULT '{}', result_summary JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS idx_research_docking_comp_study ON research_docking_comparisons(study_id)",
        "CREATE INDEX IF NOT EXISTS idx_research_docking_comp_runs ON research_docking_comparisons(wt_run_id, variant_run_id)",
        "CREATE TABLE IF NOT EXISTS research_screening_runs (id SERIAL PRIMARY KEY, study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE, target_id INT NOT NULL REFERENCES research_targets(id) ON DELETE CASCADE, status VARCHAR(30) NOT NULL DEFAULT 'prepared', engine VARCHAR(80) NOT NULL DEFAULT 'AutoDock Vina', protocol JSONB NOT NULL DEFAULT '{}', summary JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS idx_research_screening_study ON research_screening_runs(study_id)",
        "CREATE INDEX IF NOT EXISTS idx_research_screening_target ON research_screening_runs(target_id)",
        "CREATE TABLE IF NOT EXISTS research_screening_compounds (id SERIAL PRIMARY KEY, screening_id INT NOT NULL REFERENCES research_screening_runs(id) ON DELETE CASCADE, input_index INT NOT NULL, canonical_smiles VARCHAR(1000) NOT NULL, descriptors JSONB NOT NULL DEFAULT '{}', qc JSONB NOT NULL DEFAULT '{}', status VARCHAR(30) NOT NULL DEFAULT 'accepted', docking_job_id INT REFERENCES docking_jobs(id) ON DELETE SET NULL, research_docking_run_id INT REFERENCES research_docking_runs(id) ON DELETE SET NULL, docking_summary JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS idx_research_screening_comp_screening ON research_screening_compounds(screening_id)",
        "CREATE INDEX IF NOT EXISTS idx_research_screening_comp_job ON research_screening_compounds(docking_job_id)",
        "CREATE TABLE IF NOT EXISTS phytochem_candidates (id SERIAL PRIMARY KEY, name_raw VARCHAR(255) NOT NULL, canonical_name VARCHAR(255), synonyms JSONB NOT NULL DEFAULT '[]'::jsonb, pubchem_cid INTEGER, inchikey VARCHAR(64), smiles_raw TEXT, smiles_canonical TEXT, formula VARCHAR(120), stereochemistry TEXT, descriptors JSONB NOT NULL DEFAULT '{}'::jsonb, source_plant VARCHAR(255), accepted_taxon VARCHAR(255), taxon_identifier VARCHAR(120), plant_part VARCHAR(120), occurrence_source TEXT, occurrence_status VARCHAR(40) NOT NULL DEFAULT 'A_VÉRIFIER', library_source VARCHAR(40) NOT NULL, source_file_sha256 VARCHAR(64), source_file_path VARCHAR(500), identity_source VARCHAR(255), identity_reference_sha256 VARCHAR(64), taxonomy_resolution_source VARCHAR(255), taxonomy_cache_sha256 VARCHAR(64), source_row VARCHAR(128), identity_status VARCHAR(40) NOT NULL DEFAULT 'CANDIDAT_IMPORTÉ', evidence_grade VARCHAR(1) NOT NULL DEFAULT 'D', chemical_class VARCHAR(160), target_organism VARCHAR(255), target_accession VARCHAR(120), assay_type VARCHAR(120), endpoint_value DOUBLE PRECISION, endpoint_unit VARCHAR(80), study_reference TEXT, curator VARCHAR(255), reviewed_at TIMESTAMP, review_note TEXT, import_warnings JSONB NOT NULL DEFAULT '[]'::jsonb, created_at TIMESTAMP NOT NULL DEFAULT NOW(), updated_at TIMESTAMP NOT NULL DEFAULT NOW())",
        "ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS source_file_path VARCHAR(500)",
        "ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS identity_source VARCHAR(255)",
        "ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS identity_reference_sha256 VARCHAR(64)",
        "ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS taxonomy_resolution_source VARCHAR(255)",
        "ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS taxonomy_cache_sha256 VARCHAR(64)",
        "CREATE INDEX IF NOT EXISTS ix_phytochem_name ON phytochem_candidates(name_raw)",
        "CREATE INDEX IF NOT EXISTS ix_phytochem_canonical_name ON phytochem_candidates(canonical_name)",
        "CREATE INDEX IF NOT EXISTS ix_phytochem_pubchem_cid ON phytochem_candidates(pubchem_cid)",
        "CREATE INDEX IF NOT EXISTS ix_phytochem_inchikey ON phytochem_candidates(inchikey)",
        "CREATE INDEX IF NOT EXISTS ix_phytochem_source ON phytochem_candidates(library_source)",
        "ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS source_license VARCHAR(255)",
        "ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS identity_source VARCHAR(40)",
        "ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS identity_reference_sha256 VARCHAR(64)",
        "ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS taxonomy_resolution_source VARCHAR(40)",
        "ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS taxonomy_cache_sha256 VARCHAR(64)",
        "CREATE INDEX IF NOT EXISTS ix_phytochem_identity_status ON phytochem_candidates(identity_status)",
        "CREATE INDEX IF NOT EXISTS ix_phytochem_occurrence_status ON phytochem_candidates(occurrence_status)",
        "CREATE INDEX IF NOT EXISTS ix_phytochem_chemical_class ON phytochem_candidates(chemical_class)",
        "CREATE INDEX IF NOT EXISTS ix_phytochem_accepted_taxon ON phytochem_candidates(accepted_taxon)",
        "CREATE INDEX IF NOT EXISTS ix_phytochem_source_row_name ON phytochem_candidates(library_source, source_row, name_raw)",
        "ALTER TABLE docking_jobs ADD COLUMN IF NOT EXISTS phytochem_candidate_id INT REFERENCES phytochem_candidates(id) ON DELETE SET NULL",
        "ALTER TABLE docking_jobs ADD COLUMN IF NOT EXISTS target_accession VARCHAR(120)",
        "ALTER TABLE docking_jobs ADD COLUMN IF NOT EXISTS protocol_id VARCHAR(120)",
        "ALTER TABLE docking_jobs ADD COLUMN IF NOT EXISTS phytochem_provenance JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE research_screening_compounds ADD COLUMN IF NOT EXISTS phytochem_candidate_id INT REFERENCES phytochem_candidates(id) ON DELETE SET NULL",
        "ALTER TABLE research_screening_compounds ADD COLUMN IF NOT EXISTS phytochem_candidate_ids JSONB NOT NULL DEFAULT '[]'::jsonb",
        "CREATE INDEX IF NOT EXISTS ix_research_screening_phytochem_candidate ON research_screening_compounds(phytochem_candidate_id)",
        "CREATE TABLE IF NOT EXISTS research_evidence (id SERIAL PRIMARY KEY, study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE, object_type VARCHAR(60) NOT NULL, object_id VARCHAR(160), evidence_kind VARCHAR(80) NOT NULL, evidence_status VARCHAR(40) NOT NULL DEFAULT 'computational_hypothesis', evidence_grade VARCHAR(1) NOT NULL DEFAULT 'D', claim TEXT NOT NULL, organism VARCHAR(255), region_context VARCHAR(255), source_type VARCHAR(60), source_id VARCHAR(255), source_title VARCHAR(500), source_url TEXT, study_reference TEXT, method VARCHAR(255), tool_name VARCHAR(255), tool_version VARCHAR(120), reference_set_version VARCHAR(255), input_sha256 VARCHAR(64), output_sha256 VARCHAR(64), limitations TEXT, metadata JSONB NOT NULL DEFAULT '{}'::jsonb, created_by INT REFERENCES users(id) ON DELETE SET NULL, created_at TIMESTAMP NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS ix_research_evidence_study ON research_evidence(study_id)",
        "CREATE INDEX IF NOT EXISTS ix_research_evidence_object_type ON research_evidence(object_type)",
        "CREATE INDEX IF NOT EXISTS ix_research_evidence_object_id ON research_evidence(object_id)",
        "CREATE INDEX IF NOT EXISTS ix_research_evidence_status ON research_evidence(evidence_status)",
        "CREATE INDEX IF NOT EXISTS ix_research_evidence_grade ON research_evidence(evidence_grade)",
        "CREATE INDEX IF NOT EXISTS ix_research_evidence_created_by ON research_evidence(created_by)",
        "CREATE INDEX IF NOT EXISTS ix_research_evidence_study_object ON research_evidence(study_id, object_type, object_id)",
        "CREATE INDEX IF NOT EXISTS ix_research_evidence_study_status ON research_evidence(study_id, evidence_status)",
        "CREATE TABLE IF NOT EXISTS rna_structure_jobs (id SERIAL PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE, sequence TEXT NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'pending', progress INT NOT NULL DEFAULT 0, celery_task_id VARCHAR(255), artifact_path VARCHAR(500), metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb, error TEXT, created_at TIMESTAMP NOT NULL DEFAULT NOW(), updated_at TIMESTAMP NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS ix_rna_structure_jobs_user ON rna_structure_jobs(user_id)",
        "CREATE TABLE IF NOT EXISTS research_workflow_events (id SERIAL PRIMARY KEY, study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE, actor_id INT REFERENCES users(id) ON DELETE SET NULL, from_state VARCHAR(40), to_state VARCHAR(40) NOT NULL, action VARCHAR(60) NOT NULL, reason TEXT, payload JSONB NOT NULL DEFAULT '{}'::jsonb, idempotency_key VARCHAR(128), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), CONSTRAINT uq_research_workflow_event_idempotency UNIQUE (study_id, idempotency_key))",
        "CREATE INDEX IF NOT EXISTS ix_research_workflow_event_study_time ON research_workflow_events(study_id, created_at)",
        "CREATE TABLE IF NOT EXISTS research_task_outbox (id SERIAL PRIMARY KEY, event_key VARCHAR(64) NOT NULL UNIQUE, task_name VARCHAR(160) NOT NULL, queue_name VARCHAR(80) NOT NULL, args JSONB NOT NULL DEFAULT '[]'::jsonb, kwargs JSONB NOT NULL DEFAULT '{}'::jsonb, status VARCHAR(20) NOT NULL DEFAULT 'pending', attempts INT NOT NULL DEFAULT 0, available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), locked_at TIMESTAMPTZ, published_at TIMESTAMPTZ, last_error VARCHAR(120), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS ix_research_task_outbox_due ON research_task_outbox(status, available_at)",
        "CREATE TABLE IF NOT EXISTS research_assays (id SERIAL PRIMARY KEY, study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE, target_id INT REFERENCES research_targets(id) ON DELETE SET NULL, variant_id INT REFERENCES research_variants(id) ON DELETE SET NULL, screening_compound_id INT REFERENCES research_screening_compounds(id) ON DELETE SET NULL, created_by INT REFERENCES users(id) ON DELETE SET NULL, reviewer_id INT REFERENCES users(id) ON DELETE SET NULL, assay_type VARCHAR(40) NOT NULL, assay_name VARCHAR(255) NOT NULL, test_article VARCHAR(255) NOT NULL, compound_identifier VARCHAR(255), canonical_smiles VARCHAR(1000), source_kind VARCHAR(40) NOT NULL DEFAULT 'lab_generated', source_reference TEXT, source_url TEXT, laboratory VARCHAR(255), operator_name VARCHAR(255), method_name VARCHAR(255) NOT NULL, method_version VARCHAR(120), protocol_reference TEXT, assay_date DATE, organism VARCHAR(255), strain VARCHAR(255), cell_line VARCHAR(255), conditions JSONB NOT NULL DEFAULT '{}', notes TEXT, status VARCHAR(30) NOT NULL DEFAULT 'draft', review_decision VARCHAR(40), review_notes TEXT, submitted_at TIMESTAMPTZ, reviewed_at TIMESTAMPTZ, comparability_status VARCHAR(30) NOT NULL DEFAULT 'not_assessed', comparability_group VARCHAR(120), comparability_rationale TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS ix_research_assays_study_status ON research_assays(study_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_research_assays_target ON research_assays(target_id)",
        "CREATE INDEX IF NOT EXISTS ix_research_assays_comparability ON research_assays(comparability_status, comparability_group)",
        "CREATE TABLE IF NOT EXISTS research_assay_measurements (id SERIAL PRIMARY KEY, assay_id INT NOT NULL REFERENCES research_assays(id) ON DELETE CASCADE, sample_role VARCHAR(30) NOT NULL, sample_label VARCHAR(255) NOT NULL, replicate_id VARCHAR(80) NOT NULL, replicate_kind VARCHAR(30) NOT NULL DEFAULT 'biological', concentration_value NUMERIC(18,8), concentration_unit VARCHAR(40), response_name VARCHAR(120) NOT NULL, response_value NUMERIC(18,8) NOT NULL, response_unit VARCHAR(60) NOT NULL, qualifier VARCHAR(20) NOT NULL DEFAULT 'exact', timepoint_value NUMERIC(18,8), timepoint_unit VARCHAR(40), well_id VARCHAR(40), record_key VARCHAR(120), metadata JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), CONSTRAINT uq_research_assay_measurement_record_key UNIQUE (assay_id, record_key))",
        "CREATE INDEX IF NOT EXISTS ix_research_assay_measurement_replicate ON research_assay_measurements(assay_id, sample_role, replicate_id)",
        "CREATE TABLE IF NOT EXISTS research_assay_endpoints (id SERIAL PRIMARY KEY, assay_id INT NOT NULL REFERENCES research_assays(id) ON DELETE CASCADE, endpoint_name VARCHAR(40) NOT NULL, value NUMERIC(18,8) NOT NULL, unit VARCHAR(40) NOT NULL, qualifier VARCHAR(20) NOT NULL DEFAULT 'exact', origin VARCHAR(30) NOT NULL DEFAULT 'measured_reported', method VARCHAR(255), method_version VARCHAR(120), confidence_low NUMERIC(18,8), confidence_high NUMERIC(18,8), biological_replicates INT, source_reference TEXT, notes TEXT, metadata JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS ix_research_assay_endpoints_name ON research_assay_endpoints(assay_id, endpoint_name)",
        "CREATE TABLE IF NOT EXISTS research_assay_attachments (id SERIAL PRIMARY KEY, assay_id INT NOT NULL REFERENCES research_assays(id) ON DELETE CASCADE, uploaded_by INT REFERENCES users(id) ON DELETE SET NULL, original_name VARCHAR(255) NOT NULL, stored_name VARCHAR(255) NOT NULL, storage_path TEXT NOT NULL, role VARCHAR(40) NOT NULL DEFAULT 'raw_data', content_type VARCHAR(120), size_bytes INT NOT NULL, sha256 VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS ix_research_assay_attachments_sha256 ON research_assay_attachments(sha256)",
        "CREATE TABLE IF NOT EXISTS research_assay_review_events (id SERIAL PRIMARY KEY, assay_id INT NOT NULL REFERENCES research_assays(id) ON DELETE CASCADE, actor_id INT REFERENCES users(id) ON DELETE SET NULL, event_type VARCHAR(40) NOT NULL, from_status VARCHAR(30), to_status VARCHAR(30) NOT NULL, decision VARCHAR(40), reason TEXT, payload JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE TABLE IF NOT EXISTS research_assay_analyses (id SERIAL PRIMARY KEY, assay_id INT NOT NULL REFERENCES research_assays(id) ON DELETE CASCADE, created_by INT REFERENCES users(id) ON DELETE SET NULL, analysis_type VARCHAR(60) NOT NULL DEFAULT 'dose_response_4pl', algorithm VARCHAR(80) NOT NULL, algorithm_version VARCHAR(40) NOT NULL, parameters JSONB NOT NULL DEFAULT '{}', result JSONB NOT NULL DEFAULT '{}', input_sha256 VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE INDEX IF NOT EXISTS ix_research_assay_analyses_assay_created ON research_assay_analyses(assay_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_research_assay_analyses_input_sha256 ON research_assay_analyses(input_sha256)",
        "CREATE TABLE IF NOT EXISTS scientific_workflow_runs (id SERIAL PRIMARY KEY, workflow_key VARCHAR(120) NOT NULL, definition_version VARCHAR(40) NOT NULL, owner_user_id INT REFERENCES users(id) ON DELETE SET NULL, scope_type VARCHAR(60), scope_id VARCHAR(120), state VARCHAR(40) NOT NULL DEFAULT 'created', resume_state VARCHAR(40), state_version INT NOT NULL DEFAULT 0, requires_quality_review BOOLEAN NOT NULL DEFAULT TRUE, context JSONB NOT NULL DEFAULT '{}', definition_snapshot JSONB NOT NULL DEFAULT '{}', input_manifest JSONB NOT NULL DEFAULT '{}', output_manifest JSONB NOT NULL DEFAULT '{}', idempotency_key VARCHAR(128), failure_code VARCHAR(120), failure_message TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, CONSTRAINT uq_scientific_workflow_run_owner_idempotency UNIQUE(owner_user_id, idempotency_key))",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_runs_workflow_key ON scientific_workflow_runs(workflow_key)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_runs_owner_user_id ON scientific_workflow_runs(owner_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_runs_state ON scientific_workflow_runs(state)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_runs_scope_type ON scientific_workflow_runs(scope_type)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_runs_scope_id ON scientific_workflow_runs(scope_id)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_run_scope ON scientific_workflow_runs(scope_type, scope_id)",
        "CREATE TABLE IF NOT EXISTS scientific_workflow_steps (id SERIAL PRIMARY KEY, workflow_run_id INT NOT NULL REFERENCES scientific_workflow_runs(id) ON DELETE CASCADE, step_key VARCHAR(120) NOT NULL, task_name VARCHAR(160) NOT NULL, queue_name VARCHAR(80) NOT NULL, state VARCHAR(20) NOT NULL DEFAULT 'pending', args JSONB NOT NULL DEFAULT '[]', kwargs JSONB NOT NULL DEFAULT '{}', max_attempts INT NOT NULL DEFAULT 3 CHECK(max_attempts >= 1), attempts INT NOT NULL DEFAULT 0 CHECK(attempts >= 0), available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), lease_owner VARCHAR(160), lease_token VARCHAR(64), lease_expires_at TIMESTAMPTZ, heartbeat_at TIMESTAMPTZ, execution_started_at TIMESTAMPTZ, output JSONB, error_code VARCHAR(120), error_message TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, CONSTRAINT uq_scientific_workflow_step_key UNIQUE(workflow_run_id, step_key))",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_steps_workflow_run_id ON scientific_workflow_steps(workflow_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_steps_state ON scientific_workflow_steps(state)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_step_due ON scientific_workflow_steps(workflow_run_id, state, available_at)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_steps_lease_token ON scientific_workflow_steps(lease_token)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_steps_lease_expires_at ON scientific_workflow_steps(lease_expires_at)",
        "ALTER TABLE scientific_workflow_steps ADD COLUMN IF NOT EXISTS execution_started_at TIMESTAMPTZ",
        "CREATE TABLE IF NOT EXISTS scientific_workflow_step_dependencies (id SERIAL PRIMARY KEY, workflow_run_id INT NOT NULL REFERENCES scientific_workflow_runs(id) ON DELETE CASCADE, step_id INT NOT NULL REFERENCES scientific_workflow_steps(id) ON DELETE CASCADE, depends_on_step_id INT NOT NULL REFERENCES scientific_workflow_steps(id) ON DELETE CASCADE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), CONSTRAINT uq_scientific_workflow_step_dependency UNIQUE(workflow_run_id, step_id, depends_on_step_id), CONSTRAINT ck_scientific_workflow_no_self_dependency CHECK(step_id <> depends_on_step_id))",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_step_dependencies_workflow_run_id ON scientific_workflow_step_dependencies(workflow_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_step_dependencies_step_id ON scientific_workflow_step_dependencies(step_id)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_step_dependencies_depends_on_step_id ON scientific_workflow_step_dependencies(depends_on_step_id)",
        "CREATE TABLE IF NOT EXISTS scientific_workflow_events (id SERIAL PRIMARY KEY, workflow_run_id INT NOT NULL REFERENCES scientific_workflow_runs(id) ON DELETE CASCADE, step_id INT REFERENCES scientific_workflow_steps(id) ON DELETE SET NULL, actor_user_id INT REFERENCES users(id) ON DELETE SET NULL, event_type VARCHAR(60) NOT NULL, from_state VARCHAR(40), to_state VARCHAR(40), action VARCHAR(60), reason TEXT, payload JSONB NOT NULL DEFAULT '{}', idempotency_key VARCHAR(128), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), CONSTRAINT uq_scientific_workflow_event_idempotency UNIQUE(workflow_run_id, idempotency_key))",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_events_workflow_run_id ON scientific_workflow_events(workflow_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_events_step_id ON scientific_workflow_events(step_id)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_events_actor_user_id ON scientific_workflow_events(actor_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_event_run_time ON scientific_workflow_events(workflow_run_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_scientific_workflow_events_created_at ON scientific_workflow_events(created_at)",
    )
    # Isolate each statement: PostgreSQL marks a transaction aborted after a SQL
    # error, so a shared transaction silently prevents every subsequent patch.
    for sql in statements:
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
        except Exception as exc:
            logger.warning("Schema patch failed (%s): %s", sql[:120], exc)
            # Partial test schemas may omit tables; production PostgreSQL must fail
            # closed rather than start against a silently incomplete schema.
            if not settings.DEBUG and not _url.startswith("sqlite"):
                raise RuntimeError(
                    "N3XORA schema initialization failed; inspect the preceding SQL patch error."
                ) from exc


def bootstrap_admin() -> None:
    """Promote only the explicitly configured administrator email.

    A username such as "admin" is not proof of authorization. ADMIN_EMAIL does
    not create an account; it may promote an existing account after the operator
    has verified ownership of the address.
    """
    from sqlalchemy import func
    from app.models.user import User

    email = (settings.ADMIN_EMAIL or "").strip().lower()
    db = SessionLocal()
    try:
        # Revoke only the known test seed with its unchanged placeholder hash.
        seed = db.query(User).filter(
            User.username == "admin",
            func.lower(User.email) == "admin@nexora.test",
            User.password == "$2b$12$PLACEHOLDER_BCRYPT_HASH_FOR_TESTING_ONLY",
        ).first()
        if seed and email != "admin@nexora.test":
            seed.is_admin = False

        if email:
            user = db.query(User).filter(func.lower(User.email) == email).first()
            if user and not user.is_admin:
                user.is_admin = True
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not bootstrap the configured N3XORA administrator")
        if not settings.DEBUG and not _url.startswith("sqlite"):
            raise
    finally:
        db.close()
