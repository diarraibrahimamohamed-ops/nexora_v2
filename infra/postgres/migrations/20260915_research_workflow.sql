-- N3XORA research workflow: sequence -> variant -> target -> structure -> docking.
-- Safe for existing installations; uses IF NOT EXISTS and no destructive changes.

CREATE TABLE IF NOT EXISTS research_studies (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    disease VARCHAR(150) NOT NULL DEFAULT 'malaria',
    pathogen VARCHAR(150) NOT NULL DEFAULT 'Plasmodium falciparum',
    country_focus VARCHAR(120) NOT NULL DEFAULT 'Mali',
    region_scope VARCHAR(120) NOT NULL DEFAULT 'Sahel',
    objective TEXT,
    hypothesis TEXT,
    status VARCHAR(40) NOT NULL DEFAULT 'draft',
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_research_studies_user ON research_studies(user_id);

CREATE TABLE IF NOT EXISTS research_variants (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    source_analysis_id INT REFERENCES analyses(id) ON DELETE SET NULL,
    source_accession VARCHAR(120),
    gene_symbol VARCHAR(120),
    nucleotide_position INT,
    ref_nt VARCHAR(1),
    alt_nt VARCHAR(1),
    codon_ref VARCHAR(3),
    codon_alt VARCHAR(3),
    aa_ref VARCHAR(1),
    aa_alt VARCHAR(1),
    variant_notation VARCHAR(100) NOT NULL,
    evidence_class VARCHAR(40) NOT NULL DEFAULT 'candidate',
    evidence_sources JSONB NOT NULL DEFAULT '[]',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_research_variants_study ON research_variants(study_id);

CREATE TABLE IF NOT EXISTS research_targets (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    variant_id INT REFERENCES research_variants(id) ON DELETE SET NULL,
    state VARCHAR(20) NOT NULL DEFAULT 'wt',
    gene_symbol VARCHAR(120),
    protein_name VARCHAR(255),
    protein_sequence TEXT NOT NULL,
    structure_source VARCHAR(40) NOT NULL DEFAULT 'sequence_resolved',
    structure_accession VARCHAR(120),
    structure_path TEXT,
    structure_confidence NUMERIC(6,4),
    provenance JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_research_targets_study ON research_targets(study_id);

CREATE TABLE IF NOT EXISTS research_docking_runs (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    target_id INT NOT NULL REFERENCES research_targets(id) ON DELETE CASCADE,
    variant_id INT REFERENCES research_variants(id) ON DELETE SET NULL,
    docking_job_id INT REFERENCES docking_jobs(id) ON DELETE SET NULL,
    ligand_smiles VARCHAR(1000) NOT NULL,
    reference_state VARCHAR(20) NOT NULL DEFAULT 'wt',
    engine VARCHAR(80) NOT NULL DEFAULT 'AutoDock Vina',
    aggregation_method VARCHAR(120) NOT NULL DEFAULT 'ASP — analyse complémentaire',
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    parameters JSONB NOT NULL DEFAULT '{}',
    result_summary JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_research_docking_study ON research_docking_runs(study_id);
CREATE INDEX IF NOT EXISTS idx_research_docking_job ON research_docking_runs(docking_job_id);
