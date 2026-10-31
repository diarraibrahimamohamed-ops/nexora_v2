-- Research workflow FSM audit trail and transactional Celery outbox.
-- Additive migration; existing research_studies.status values remain valid.
BEGIN;

CREATE TABLE IF NOT EXISTS research_workflow_events (
    id SERIAL PRIMARY KEY,
    study_id INT NOT NULL REFERENCES research_studies(id) ON DELETE CASCADE,
    actor_id INT REFERENCES users(id) ON DELETE SET NULL,
    from_state VARCHAR(40),
    to_state VARCHAR(40) NOT NULL,
    action VARCHAR(60) NOT NULL,
    reason TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_research_workflow_event_idempotency UNIQUE (study_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_research_workflow_event_study_time
    ON research_workflow_events(study_id, created_at);
CREATE INDEX IF NOT EXISTS ix_research_workflow_event_actor
    ON research_workflow_events(actor_id);

CREATE TABLE IF NOT EXISTS research_task_outbox (
    id SERIAL PRIMARY KEY,
    event_key VARCHAR(64) NOT NULL UNIQUE,
    task_name VARCHAR(160) NOT NULL,
    queue_name VARCHAR(80) NOT NULL,
    args JSONB NOT NULL DEFAULT '[]'::jsonb,
    kwargs JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts INT NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    last_error VARCHAR(120),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_research_task_outbox_due
    ON research_task_outbox(status, available_at);
CREATE INDEX IF NOT EXISTS ix_research_task_outbox_locked
    ON research_task_outbox(status, locked_at);

COMMIT;
