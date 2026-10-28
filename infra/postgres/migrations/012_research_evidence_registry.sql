-- Traceable scientific evidence records for N3XORA-Sahel studies.
BEGIN;
CREATE TABLE IF NOT EXISTS research_evidence (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    object_type VARCHAR(60) NOT NULL,
    object_id VARCHAR(160),
    evidence_kind VARCHAR(80) NOT NULL,
    evidence_status VARCHAR(40) NOT NULL DEFAULT 'computational_hypothesis',
    evidence_grade VARCHAR(1) NOT NULL DEFAULT 'D',
    claim TEXT NOT NULL,
    organism VARCHAR(255),
    region_context VARCHAR(255),
    source_type VARCHAR(60),
    source_id VARCHAR(255),
    source_title VARCHAR(500),
    source_url TEXT,
    study_reference TEXT,
    method VARCHAR(255),
    tool_name VARCHAR(255),
    tool_version VARCHAR(120),
    reference_set_version VARCHAR(255),
    input_sha256 VARCHAR(64),
    output_sha256 VARCHAR(64),
    limitations TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_research_evidence_study ON research_evidence(study_id);
CREATE INDEX IF NOT EXISTS ix_research_evidence_object_type ON research_evidence(object_type);
CREATE INDEX IF NOT EXISTS ix_research_evidence_object_id ON research_evidence(object_id);
CREATE INDEX IF NOT EXISTS ix_research_evidence_status ON research_evidence(evidence_status);
CREATE INDEX IF NOT EXISTS ix_research_evidence_grade ON research_evidence(evidence_grade);
CREATE INDEX IF NOT EXISTS ix_research_evidence_created_by ON research_evidence(created_by);
CREATE INDEX IF NOT EXISTS ix_research_evidence_study_object ON research_evidence(study_id, object_type, object_id);
CREATE INDEX IF NOT EXISTS ix_research_evidence_study_status ON research_evidence(study_id, evidence_status);
COMMIT;
