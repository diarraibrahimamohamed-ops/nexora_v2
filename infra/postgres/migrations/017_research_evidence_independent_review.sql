-- Phase 1L-inspired research evidence governance: independent reviewer attribution.
BEGIN;

ALTER TABLE research_evidence
    ADD COLUMN IF NOT EXISTS review_status VARCHAR(24) NOT NULL DEFAULT 'pending_review';
ALTER TABLE research_evidence
    ADD COLUMN IF NOT EXISTS reviewed_by INT;
ALTER TABLE research_evidence
    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP;
ALTER TABLE research_evidence
    ADD COLUMN IF NOT EXISTS review_reason TEXT;

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_research_evidence_reviewed_by'
          AND conrelid = 'research_evidence'::regclass
    ) THEN
        ALTER TABLE research_evidence
            ADD CONSTRAINT fk_research_evidence_reviewed_by
            FOREIGN KEY (reviewed_by) REFERENCES users(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_research_evidence_review_status'
          AND conrelid = 'research_evidence'::regclass
    ) THEN
        ALTER TABLE research_evidence
            ADD CONSTRAINT ck_research_evidence_review_status
            CHECK (review_status IN ('pending_review', 'accepted', 'rejected'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_research_evidence_independent_review'
          AND conrelid = 'research_evidence'::regclass
    ) THEN
        ALTER TABLE research_evidence
            ADD CONSTRAINT ck_research_evidence_independent_review
            CHECK (created_by IS NULL OR reviewed_by IS NULL OR created_by <> reviewed_by);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_research_evidence_review_attribution'
          AND conrelid = 'research_evidence'::regclass
    ) THEN
        ALTER TABLE research_evidence
            ADD CONSTRAINT ck_research_evidence_review_attribution
            CHECK (
                (review_status = 'pending_review' AND reviewed_by IS NULL AND reviewed_at IS NULL)
                OR
                (review_status IN ('accepted', 'rejected') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
            );
    END IF;
END;
$migration$;

CREATE INDEX IF NOT EXISTS ix_research_evidence_review_status
    ON research_evidence(review_status);
CREATE INDEX IF NOT EXISTS ix_research_evidence_reviewed_by
    ON research_evidence(reviewed_by);

COMMIT;
