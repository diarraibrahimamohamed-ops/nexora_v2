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
    )
    with engine.begin() as conn:
        for sql in statements:
            try:
                conn.execute(text(sql))
            except Exception:
                # Table may not exist yet (unit tests before create_all).
                pass


def bootstrap_admin() -> None:
    """Promote ADMIN_EMAIL and the historical seed account if present."""
    from sqlalchemy import func, or_
    from app.models.user import User

    email = (settings.ADMIN_EMAIL or "").strip().lower()
    db = SessionLocal()
    try:
        query = db.query(User).filter(User.username == "admin")
        if email:
            query = db.query(User).filter(
                or_(User.username == "admin", func.lower(User.email) == email)
            )
        updated = False
        for user in query.all():
            if not user.is_admin:
                user.is_admin = True
                updated = True
        if updated:
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
