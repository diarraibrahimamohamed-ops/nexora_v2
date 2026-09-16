-- ============================================================
-- NEXORA v2 — Schema PostgreSQL COMPLET
-- Migré depuis : database_schema.sql + docking_schema.sql + validated_ligands.sql
-- ============================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- ENUMS
-- ============================================================
CREATE TYPE user_status       AS ENUM ('active', 'inactive', 'banned');
CREATE TYPE analysis_status   AS ENUM ('pending', 'running', 'completed', 'failed');
CREATE TYPE seq_type          AS ENUM ('dna', 'rna', 'protein');
CREATE TYPE job_status        AS ENUM ('pending', 'running', 'completed', 'failed');
CREATE TYPE docking_status    AS ENUM ('pending', 'running', 'completed', 'failed');
CREATE TYPE modeling_method   AS ENUM ('modeller', 'alphafold', 'homology', 'ASP');
CREATE TYPE organism_type     AS ENUM ('bacteria', 'virus', 'eukaryote', 'unknown');
CREATE TYPE ligand_status     AS ENUM ('approved', 'experimental', 'clinical_trial', 'research');

-- ============================================================
-- USERS
-- ============================================================
CREATE TABLE users (
    id         SERIAL PRIMARY KEY,
    username   VARCHAR(50)  NOT NULL UNIQUE,
    email      VARCHAR(255) NOT NULL UNIQUE,
    password   VARCHAR(255) NOT NULL,
    token      VARCHAR(255),
    is_admin   BOOLEAN NOT NULL DEFAULT FALSE,
    status     user_status  NOT NULL DEFAULT 'active',
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_users_email    ON users(email);
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_status   ON users(status);
CREATE INDEX idx_users_admin    ON users(is_admin);

-- ============================================================
-- LOGIN ATTEMPTS (rate-limiting / protection brute-force)
-- ============================================================
CREATE TABLE login_attempts (
    id           SERIAL PRIMARY KEY,
    ip_address   VARCHAR(45)  NOT NULL,
    identifier   VARCHAR(255) NOT NULL,
    user_agent   TEXT,
    attempted_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_login_ip_time  ON login_attempts(ip_address, attempted_at);
CREATE INDEX idx_login_id_time  ON login_attempts(identifier, attempted_at);

-- ============================================================
-- ANALYSES
-- ============================================================
CREATE TABLE analyses (
    id         SERIAL PRIMARY KEY,
    user_id    INT REFERENCES users(id) ON DELETE SET NULL,
    name       VARCHAR(255)    NOT NULL,
    type       VARCHAR(100)    NOT NULL,
    data       JSONB           NOT NULL DEFAULT '{}',
    fasta_file VARCHAR(500),
    status     analysis_status NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_analyses_user       ON analyses(user_id);
CREATE INDEX idx_analyses_type       ON analyses(type);
CREATE INDEX idx_analyses_status     ON analyses(status);
CREATE INDEX idx_analyses_created_at ON analyses(created_at DESC);

-- ============================================================
-- SEQUENCES
-- ============================================================
CREATE TABLE sequences (
    id          SERIAL PRIMARY KEY,
    analysis_id INT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    accession   VARCHAR(100),
    header      TEXT    NOT NULL,
    sequence    TEXT    NOT NULL,
    length      INT     NOT NULL,
    type        seq_type NOT NULL DEFAULT 'dna',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_sequences_analysis  ON sequences(analysis_id);
CREATE INDEX idx_sequences_accession ON sequences(accession);
CREATE INDEX idx_sequences_type      ON sequences(type);
CREATE INDEX idx_sequences_fts       ON sequences USING GIN(to_tsvector('simple', sequence));

-- ============================================================
-- MUTATIONS
-- ============================================================
CREATE TABLE mutations (
    id          SERIAL PRIMARY KEY,
    analysis_id INT     NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    type        VARCHAR(50) NOT NULL,
    ref_pos     INT,
    qry_pos     INT,
    ref_base    CHAR(1),
    qry_base    CHAR(1),
    length      INT     NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_mutations_analysis ON mutations(analysis_id);
CREATE INDEX idx_mutations_type     ON mutations(type);

-- ============================================================
-- ANTIBIOTIC PROFILES (résistance)
-- ============================================================
CREATE TABLE antibiotic_profiles (
    id          SERIAL PRIMARY KEY,
    analysis_id INT          NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    antibiotic  VARCHAR(100) NOT NULL,
    status      VARCHAR(50)  NOT NULL,
    score       NUMERIC(5,2),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_abprofiles_analysis   ON antibiotic_profiles(analysis_id);
CREATE INDEX idx_abprofiles_antibiotic ON antibiotic_profiles(antibiotic);
CREATE INDEX idx_abprofiles_status     ON antibiotic_profiles(status);

-- ============================================================
-- NCBI SEQUENCES (cache local)
-- ============================================================
CREATE TABLE ncbi_sequences (
    id            SERIAL PRIMARY KEY,
    accession     VARCHAR(100) NOT NULL UNIQUE,
    database_name VARCHAR(50)  NOT NULL,
    organism      VARCHAR(255),
    title         TEXT,
    sequence      TEXT         NOT NULL,
    length        INT          NOT NULL,
    cached_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ncbi_accession ON ncbi_sequences(accession);
CREATE INDEX idx_ncbi_organism  ON ncbi_sequences(organism);
CREATE INDEX idx_ncbi_cached_at ON ncbi_sequences(cached_at DESC);
CREATE INDEX idx_ncbi_title_fts ON ncbi_sequences USING GIN(to_tsvector('simple', coalesce(title,'')));

-- ============================================================
-- FASTA FILES
-- ============================================================
CREATE TABLE fasta_files (
    id            SERIAL PRIMARY KEY,
    filename      VARCHAR(255) NOT NULL,
    original_name VARCHAR(255) NOT NULL,
    size          INT          NOT NULL,
    mime          VARCHAR(100) NOT NULL,
    hash          VARCHAR(64)  NOT NULL,
    uploaded_by   INT REFERENCES users(id) ON DELETE SET NULL,
    uploaded_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_fasta_hash        ON fasta_files(hash);
CREATE INDEX idx_fasta_uploaded_by ON fasta_files(uploaded_by);

-- ============================================================
-- CACHE ENTRIES
-- ============================================================
CREATE TABLE cache_entries (
    id          SERIAL PRIMARY KEY,
    cache_key   VARCHAR(255) NOT NULL UNIQUE,
    cache_value TEXT         NOT NULL,
    expires_at  TIMESTAMPTZ  NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_cache_expires ON cache_entries(expires_at);
CREATE INDEX idx_cache_key     ON cache_entries(cache_key);

-- ============================================================
-- VALIDATED LIGANDS
-- ============================================================
CREATE TABLE validated_ligands (
    id                          SERIAL PRIMARY KEY,
    name                        VARCHAR(200)  NOT NULL,
    smiles                      VARCHAR(500)  NOT NULL UNIQUE,
    molecular_weight            NUMERIC(10,2) NOT NULL,
    logp                        NUMERIC(4,2)  NOT NULL,
    hydrogen_bond_donors        INT           NOT NULL,
    hydrogen_bond_acceptors     INT           NOT NULL,
    rotatable_bonds             INT           NOT NULL,
    topological_polar_surface_area NUMERIC(8,2) NOT NULL,
    drug_likeness_score         NUMERIC(5,3)  NOT NULL,
    category                    VARCHAR(100)  NOT NULL,
    description                 TEXT,
    pubchem_cid                 INT,
    chebi_id                    VARCHAR(50),
    uniprot_target              VARCHAR(50),
    binding_affinity_kcal       NUMERIC(6,3),
    status                      ligand_status NOT NULL DEFAULT 'research',
    created_at                  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ligands_category ON validated_ligands(category);
CREATE INDEX idx_ligands_status   ON validated_ligands(status);
CREATE INDEX idx_ligands_name_fts ON validated_ligands USING GIN(to_tsvector('simple', name));

-- INSERT des 30 ligands validés scientifiquement
INSERT INTO validated_ligands (name, smiles, molecular_weight, logp, hydrogen_bond_donors, hydrogen_bond_acceptors, rotatable_bonds, topological_polar_surface_area, drug_likeness_score, category, description, pubchem_cid, status) VALUES
('Acétaminophène',  'CC(=O)NC1=CC=C(C=C1)O',                      151.16,  0.49, 2, 3,  1,  49.33, 0.847, 'analgesic',         'Analgésique et antipyrétique courant',          1983,     'approved'),
('Ibuprofène',      'CC(C)CC1=CC=C(C=C1)C(C)C(=O)O',              206.28,  3.50, 1, 2,  4,  37.30, 0.721, 'anti-inflammatory', 'AINS anti-inflammatoire',                       3672,     'approved'),
('Aspirine',        'CC(=O)OC1=CC=CC=C1C(=O)O',                   180.16,  1.19, 1, 4,  3,  63.60, 0.756, 'anti-inflammatory', 'AINS et antiplaquettaire',                      2244,     'approved'),
('Caféine',         'CN1C=NC2=C1C(=O)N(C(=O)N2C)C',               194.19, -0.07, 0, 6,  0,  58.44, 0.693, 'stimulant',         'Stimulant du système nerveux central',          2519,     'approved'),
('Morphine',        'CN1CC[C@]23C4=C5C=CC(=C4[C@H]1CC2=C3C(=C5)O)O', 285.34, 0.89, 2, 5, 2, 52.93, 0.634, 'opioid',          'Analgésique opioïde puissant',                  5288826,  'approved'),
('Diazépam',        'CN1C(=O)CN=C(C2=CC=CC=C2)C3=CC=CC=C13',      284.74,  2.83, 0, 3,  1,  32.69, 0.812, 'benzodiazepine',    'Anxiolytique benzodiazépine',                   3016,     'approved'),
('Imatinib',        'CC1=CC=C(C=C1NC(=O)C2=CC=C(CN3CCN(CC3)C)C=C2)NC4=NC=CC(=N4)C5=CN=CC=C5', 493.60, 3.73, 2, 8, 8, 94.56, 0.523, 'kinase_inhibitor', 'Inhibiteur de tyrosine kinase BCR-ABL', 5291, 'experimental'),
('Gefitinib',       'COC1=CC2=C(C=C1OCCCN3CCOCC3)C(=NC=N2)NC4=CC=C(C=C4)F', 446.90, 2.84, 1, 9, 7, 76.31, 0.587, 'kinase_inhibitor', 'Inhibiteur EGFR', 123631, 'experimental'),
('Erlotinib',       'COCCOC1=C(OCCO)C=C2C(=C1)C(=NC=N2)NC3=CC=CC(=C3)C#C', 393.42, 2.79, 0, 6, 3, 58.72, 0.634, 'kinase_inhibitor', 'Inhibiteur EGFR', 176870, 'experimental'),
('Ritonavir',       'CC(C)C1=CC(=C(C=C1)N)C(=O)NC(CC(CC(=O)NC(CC2=CC=NC=C2)CC(=O)NC(C(C)C)C(=O)NC(CC3=CC=CC=C3)CSC4=NC=CS4)O)CC5=CC=CC=C5', 720.94, 4.46, 2, 10, 11, 146.54, 0.412, 'protease_inhibitor', 'Inhibiteur de protéase VIH', 392622, 'approved'),
('Lopinavir',       'CC1=CC(=CC=C1OCC(=O)NC(CC2=CC=CC=C2)CC(=O)NC(CC3CCCCC3)CC(=O)NC(CO)C(C)C)N', 628.80, 4.31, 1, 8, 9, 124.67, 0.456, 'protease_inhibitor', 'Inhibiteur de protéase VIH', 92727, 'approved'),
('Remdesivir',      'CCC(CC)COC(=O)[C@@H]1C[C@H](C[C@@H]1OP(=O)(OC[C@H]2O[C@H]([C@H](O)[C@@H]2O)N3C=CC(=O)NC3=O)OC4=CC=CC=C4)O', 602.58, 1.73, 2, 11, 8, 137.89, 0.389, 'antiviral', 'Antiviral à large spectre', 121304016, 'approved'),
('Favipiravir',     'NC(=O)C1=NC(F)=CN=C1O',                      157.10, -0.04, 2, 4,  0,  71.83, 0.678, 'antiviral',         'Inhibiteur ARN polymérase',                     492405,   'approved'),
('Quercétine',      'C1=CC(=C(C=C1C2=CC(=O)C3=C(O2)C=C(C=C3O)O)O)O', 302.24, 1.54, 5, 7, 1, 131.36, 0.567, 'flavonoid',       'Flavonoïde antioxydant',                        5280343,  'research'),
('Resvératrol',     'OC1=CC(=CC=C1)/C=C/C2=CC(=CC(=C2)O)O',       228.24,  3.10, 3, 3,  2,  60.15, 0.723, 'polyphenol',        'Polyphénol antioxydant',                        445154,   'research'),
('Curcumine',       'COC1=CC(=CC=C1O)/C=C/C(=O)CC(=O)/C=C/C2=CC(=C(C=C2)O)OC', 368.38, 3.29, 2, 6, 2, 93.07, 0.634, 'polyphenol', 'Curcuminoïde anti-inflammatoire', 969516, 'research'),
('Ciprofloxacine',  'C1CN(C1)C2=C(C=C3C(=C2)N(C=C(C3=O)C(=O)O)CC4CC4)F', 331.34, 0.28, 1, 6, 2, 74.57, 0.612, 'antibiotic',    'Fluoroquinolone antibiotique',                  2764,     'approved'),
('Doxycycline',     'CC1C2CC3C(C(=O)C(=C(C3=C(C2=C(C(=O)C1O)O)O)O)C(=O)N)N(C)C', 444.43, -0.20, 3, 7, 1, 123.23, 0.456, 'antibiotic', 'Tétracycline antibiotique', 54671203, 'approved'),
('Paclitaxel',      'CC1=C2C(C(=O)C3(C(CC4=CC(=O)C=CC43C)C1(C)C)OC(=O)C)CC(=O)OC2=O', 853.91, 3.92, 1, 14, 4, 206.27, 0.234, 'antineoplastic', 'Agent antimicrotubules', 36362, 'approved'),
('Doxorubicine',    'COC1=CC2=C(C=C1)C(=O)C3=C(O2)[C@H](O[C@@H]4C[C@@H](N)[C@H](O)[C@@H](C)O4)CC3=O', 543.52, 1.27, 3, 10, 4, 224.20, 0.345, 'antineoplastic', 'Anthracycline antibiotique', 31703, 'approved'),
('Dopamine',        'NCCC1=CC(=C(C=C1)O)O',                        153.18, -0.98, 3, 2,  2,  41.49, 0.789, 'neurotransmitter', 'Neurotransmetteur catécholamine',               681,      'research'),
('Sérotonine',      'NCCC1=CNC2=C1C=C(O)C=C2',                    176.22,  0.15, 2, 3,  2,  41.81, 0.734, 'neurotransmitter', 'Neurotransmetteur monoamine',                   5202,     'research'),
('GABA',            'NCC(CCC(=O)O)=O',                             103.10, -0.97, 2, 3,  3,  52.68, 0.823, 'neurotransmitter', 'Acide gamma-aminobutyrique',                    119,      'research'),
('Vitamine C',      'OC[C@@H](O)[C@H]1OC(=O)C(O)=C1O',            176.12, -1.77, 4, 6,  3, 107.99, 0.812, 'vitamin',          'Acide ascorbique',                              54670067, 'approved'),
('Vitamine D3',     'C[C@H](CCCC(C)C)[C@H]1CC[C@@H]2[C@@]1(CCC/C2=C\C=C3/C[C@@H](O)CCC3=C)C', 384.64, 6.91, 1, 1, 5, 20.23, 0.567, 'vitamin', 'Cholécalciférol', 5280795, 'approved'),
('Cortisol',        'C[C@]12CCC(=O)C=C1CC[C@@H]3[C@@H]2[C@H](O)C[C@]4(C)[C@H]3CC[C@@H]4O', 362.46, 1.61, 2, 3, 0, 74.60, 0.678, 'steroid', 'Hormone glucocorticoïde', 6258, 'approved'),
('Testostérone',    'C[C@]12CCC(=O)C=C1CC[C@@H]3[C@@H]2CC[C@]4(C)[C@H]3CCC4=O', 288.42, 2.99, 1, 2, 0, 40.47, 0.745, 'steroid', 'Hormone androgène', 6013, 'approved'),
('Dopamine-D3',     'CCC1=CC(=C(C=C1N)O)O',                        153.18, -1.10, 3, 2, 2, 41.49, 0.767, 'neurotransmitter', 'Agoniste récepteur D3', 681, 'research'),
('ATP',             'Nc1ncnc2n(cnc12)[C@@H]1O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]1O', 507.18, -1.67, 6, 13, 7, 207.58, 0.234, 'nucleotide', 'Adénosine triphosphate', 6508, 'research'),
('NAD+',            'NC(=O)c1ccc[n+](c1)[C@@H]1O[C@H](COP(=O)(O)OP(=O)(O)OC[C@H]2O[C@H]([C@H](O)[C@@H]2O)n2cnc3c(N)ncnc23)[C@@H](O)[C@H]1O', 663.43, -1.09, 5, 10, 6, 191.79, 0.312, 'cofactor', 'Nicotinamide adénine dinucléotide', 438368, 'research');

-- ============================================================
-- DOCKING JOBS (Celery — remplace docking_results PHP/sync)
-- ============================================================
CREATE TABLE docking_jobs (
    id             SERIAL PRIMARY KEY,
    user_id        INT NOT NULL REFERENCES users(id),
    status         job_status   NOT NULL DEFAULT 'pending',
    celery_task_id VARCHAR(255),
    protein_seq    TEXT,
    ligand_smiles  VARCHAR(1000),
    result_path    TEXT,
    progress       SMALLINT     NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    error          TEXT,
    status_message TEXT,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_docking_jobs_user    ON docking_jobs(user_id);
CREATE INDEX idx_docking_jobs_status  ON docking_jobs(status);
CREATE INDEX idx_docking_jobs_created ON docking_jobs(created_at DESC);

-- ============================================================
-- DOCKING RESULTS (résultats stockés — vue pour compatibilité)
-- ============================================================
CREATE TABLE docking_results (
    id               SERIAL PRIMARY KEY,
    analysis_id      INT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    protein_sequence TEXT         NOT NULL,
    ligand_smiles    VARCHAR(1000) NOT NULL,
    docking_score    NUMERIC(10,4),
    binding_energy   NUMERIC(10,4),
    effective_dg     NUMERIC(10,4),
    pose_data        JSONB,
    vina_log         TEXT,
    modeling_method  modeling_method NOT NULL DEFAULT 'ASP',
    status           docking_status  NOT NULL DEFAULT 'pending',
    error_message    TEXT,
    execution_time   NUMERIC(8,3),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_docking_results_analysis ON docking_results(analysis_id);
CREATE INDEX idx_docking_results_status   ON docking_results(status);
CREATE INDEX idx_docking_results_score    ON docking_results(docking_score);
CREATE INDEX idx_docking_results_created  ON docking_results(created_at DESC);

-- ============================================================
-- PROTEIN METADATA
-- ============================================================
CREATE TABLE protein_metadata (
    id                   SERIAL PRIMARY KEY,
    analysis_id          INT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    protein_sequence_hash VARCHAR(64) NOT NULL UNIQUE,
    sequence_length      INT          NOT NULL,
    organism_type        organism_type NOT NULL DEFAULT 'unknown',
    gene_name            VARCHAR(255),
    protein_name         VARCHAR(255),
    molecular_weight     NUMERIC(10,2),
    isoelectric_point    NUMERIC(5,2),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_protein_meta_analysis ON protein_metadata(analysis_id);
CREATE INDEX idx_protein_meta_hash     ON protein_metadata(protein_sequence_hash);

-- ============================================================
-- TEMP STRUCTURES (cache PDB temporaire)
-- ============================================================
CREATE TABLE temp_structures (
    id                   SERIAL PRIMARY KEY,
    protein_sequence_hash VARCHAR(64) NOT NULL,
    structure_pdb        TEXT        NOT NULL,
    modeling_method      modeling_method NOT NULL DEFAULT 'ASP',
    confidence_score     NUMERIC(5,4),
    expires_at           TIMESTAMPTZ NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (protein_sequence_hash, modeling_method)
);
CREATE INDEX idx_temp_structures_hash    ON temp_structures(protein_sequence_hash);
CREATE INDEX idx_temp_structures_expires ON temp_structures(expires_at);

-- ============================================================
-- HSA JOBS (Heuristic Stability Analysis — Celery)
-- Table name historique qsa_jobs conservé pour compatibilité
-- ============================================================
CREATE TABLE qsa_jobs (
    id             SERIAL PRIMARY KEY,
    user_id        INT NOT NULL REFERENCES users(id),
    protein_sequence TEXT NOT NULL,
    env_factors    JSONB NOT NULL DEFAULT '{}',
    status         VARCHAR(20) NOT NULL DEFAULT 'pending',
    progress       SMALLINT NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    results        JSONB NOT NULL DEFAULT '{}',
    error          TEXT,
    celery_task_id VARCHAR(255),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_qsa_jobs_user    ON qsa_jobs(user_id);
CREATE INDEX idx_qsa_jobs_status  ON qsa_jobs(status);
CREATE INDEX idx_qsa_jobs_created ON qsa_jobs(created_at DESC);

-- ============================================================
-- VUE : docking_results_view (compatibilité avec l'ancien code)
-- ============================================================
CREATE VIEW docking_results_view AS
SELECT
    dr.id,
    dr.analysis_id,
    a.name        AS analysis_name,
    a.user_id,
    dr.protein_sequence,
    dr.ligand_smiles,
    dr.docking_score,
    dr.binding_energy,
    dr.effective_dg,
    dr.pose_data,
    dr.modeling_method,
    dr.status,
    dr.execution_time,
    dr.created_at,
    pm.sequence_length,
    pm.organism_type,
    pm.gene_name,
    pm.protein_name,
    pm.molecular_weight
FROM docking_results dr
LEFT JOIN analyses       a  ON dr.analysis_id = a.id
LEFT JOIN protein_metadata pm ON dr.analysis_id = pm.analysis_id;

-- ============================================================
-- DONNÉES DE TEST
-- ============================================================
INSERT INTO users (username, email, password, status, is_admin) VALUES
('admin', 'admin@nexora.test', '$2b$12$PLACEHOLDER_BCRYPT_HASH_FOR_TESTING_ONLY', 'active', TRUE);


-- ============================================================
-- RESEARCH STUDIES — Sahel / variant-aware workflow
-- Data model is provenance-first; it does not make clinical claims.
-- ============================================================
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
    structure_status VARCHAR(50) NOT NULL DEFAULT 'not_generated',
    structure_sequence_identity NUMERIC(6,5),
    structure_sequence_coverage NUMERIC(6,5),
    structure_validation JSONB NOT NULL DEFAULT '{}',
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


CREATE TABLE IF NOT EXISTS research_structure_jobs (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    target_id INT NOT NULL REFERENCES research_targets(id) ON DELETE CASCADE,
    provider VARCHAR(40) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    celery_task_id VARCHAR(255),
    progress INT NOT NULL DEFAULT 0,
    error TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_research_structure_jobs_target ON research_structure_jobs(target_id);
CREATE INDEX IF NOT EXISTS idx_research_structure_jobs_study ON research_structure_jobs(study_id);
