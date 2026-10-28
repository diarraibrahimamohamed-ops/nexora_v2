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
        "CREATE TABLE IF NOT EXISTS phytochem_candidates (id SERIAL PRIMARY KEY, name_raw VARCHAR(255) NOT NULL, canonical_name VARCHAR(255), synonyms JSONB NOT NULL DEFAULT '[]'::jsonb, pubchem_cid INTEGER, inchikey VARCHAR(64), smiles_raw TEXT, smiles_canonical TEXT, formula VARCHAR(120), stereochemistry TEXT, descriptors JSONB NOT NULL DEFAULT '{}'::jsonb, source_plant VARCHAR(255), accepted_taxon VARCHAR(255), taxon_identifier VARCHAR(120), plant_part VARCHAR(120), occurrence_source TEXT, occurrence_status VARCHAR(40) NOT NULL DEFAULT 'A_VÉRIFIER', library_source VARCHAR(40) NOT NULL, source_file_sha256 VARCHAR(64), source_file_path VARCHAR(500), source_row VARCHAR(128), identity_status VARCHAR(40) NOT NULL DEFAULT 'CANDIDAT_IMPORTÉ', evidence_grade VARCHAR(1) NOT NULL DEFAULT 'D', chemical_class VARCHAR(160), target_organism VARCHAR(255), target_accession VARCHAR(120), assay_type VARCHAR(120), endpoint_value DOUBLE PRECISION, endpoint_unit VARCHAR(80), study_reference TEXT, curator VARCHAR(255), reviewed_at TIMESTAMP, review_note TEXT, import_warnings JSONB NOT NULL DEFAULT '[]'::jsonb, created_at TIMESTAMP NOT NULL DEFAULT NOW(), updated_at TIMESTAMP NOT NULL DEFAULT NOW())",
        "ALTER TABLE phytochem_candidates ADD COLUMN IF NOT EXISTS source_file_path VARCHAR(500)",
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
