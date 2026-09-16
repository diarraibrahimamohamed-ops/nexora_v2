-- Phase 7: controlled virtual screening. No fabricated activity scores.
CREATE TABLE IF NOT EXISTS research_screening_runs (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    target_id INT NOT NULL REFERENCES research_targets(id) ON DELETE CASCADE,
    status VARCHAR(30) NOT NULL DEFAULT 'prepared',
    engine VARCHAR(80) NOT NULL DEFAULT 'AutoDock Vina',
    protocol JSONB NOT NULL DEFAULT '{}',
    summary JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_research_screening_study ON research_screening_runs(study_id);
CREATE INDEX IF NOT EXISTS idx_research_screening_target ON research_screening_runs(target_id);

CREATE TABLE IF NOT EXISTS research_screening_compounds (
    id SERIAL PRIMARY KEY,
    screening_id INT NOT NULL REFERENCES research_screening_runs(id) ON DELETE CASCADE,
    input_index INT NOT NULL,
    canonical_smiles VARCHAR(1000) NOT NULL,
    descriptors JSONB NOT NULL DEFAULT '{}',
    qc JSONB NOT NULL DEFAULT '{}',
    status VARCHAR(30) NOT NULL DEFAULT 'accepted',
    docking_job_id INT REFERENCES docking_jobs(id) ON DELETE SET NULL,
    research_docking_run_id INT REFERENCES research_docking_runs(id) ON DELETE SET NULL,
    docking_summary JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_research_screening_comp_screening ON research_screening_compounds(screening_id);
CREATE INDEX IF NOT EXISTS idx_research_screening_comp_job ON research_screening_compounds(docking_job_id);
