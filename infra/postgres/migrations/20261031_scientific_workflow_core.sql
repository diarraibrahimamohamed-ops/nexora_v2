-- Generic scientific workflow runs, DAG steps, leases and HFSM audit events.
-- Additive schema: existing ResearchStudy FSM and historical jobs are untouched.
BEGIN;

CREATE TABLE IF NOT EXISTS scientific_workflow_runs (
    id SERIAL PRIMARY KEY,
    workflow_key VARCHAR(120) NOT NULL,
    definition_version VARCHAR(40) NOT NULL,
    owner_user_id INT REFERENCES users(id) ON DELETE SET NULL,
    scope_type VARCHAR(60),
    scope_id VARCHAR(120),
    state VARCHAR(40) NOT NULL DEFAULT 'created',
    resume_state VARCHAR(40),
    state_version INT NOT NULL DEFAULT 0,
    requires_quality_review BOOLEAN NOT NULL DEFAULT TRUE,
    context JSONB NOT NULL DEFAULT '{}'::jsonb,
    definition_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    input_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key VARCHAR(128),
    failure_code VARCHAR(120),
    failure_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    CONSTRAINT uq_scientific_workflow_run_owner_idempotency
        UNIQUE (owner_user_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_runs_workflow_key
    ON scientific_workflow_runs(workflow_key);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_runs_owner_user_id
    ON scientific_workflow_runs(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_runs_state
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_runs_scope_type ON scientific_workflow_runs(scope_type);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_runs_scope_id ON scientific_workflow_runs(scope_id);
    ON scientific_workflow_runs(state);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_run_scope
    ON scientific_workflow_runs(scope_type, scope_id);

CREATE TABLE IF NOT EXISTS scientific_workflow_steps (
    id SERIAL PRIMARY KEY,
    workflow_run_id INT NOT NULL REFERENCES scientific_workflow_runs(id) ON DELETE CASCADE,
    step_key VARCHAR(120) NOT NULL,
    task_name VARCHAR(160) NOT NULL,
    queue_name VARCHAR(80) NOT NULL,
    state VARCHAR(20) NOT NULL DEFAULT 'pending',
    args JSONB NOT NULL DEFAULT '[]'::jsonb,
    kwargs JSONB NOT NULL DEFAULT '{}'::jsonb,
    max_attempts INT NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    attempts INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_owner VARCHAR(160),
    lease_token VARCHAR(64),
    lease_expires_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    execution_started_at TIMESTAMPTZ,
    executor_id VARCHAR(160),
    output JSONB,
    error_code VARCHAR(120),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    CONSTRAINT uq_scientific_workflow_step_key UNIQUE (workflow_run_id, step_key)
);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_steps_workflow_run_id
    ON scientific_workflow_steps(workflow_run_id);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_steps_state
    ON scientific_workflow_steps(state);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_step_due
    ON scientific_workflow_steps(workflow_run_id, state, available_at);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_step_lease_token
    ON scientific_workflow_steps(lease_token);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_steps_lease_expires_at
    ON scientific_workflow_steps(lease_expires_at);

CREATE TABLE IF NOT EXISTS scientific_workflow_step_dependencies (
    id SERIAL PRIMARY KEY,
    workflow_run_id INT NOT NULL REFERENCES scientific_workflow_runs(id) ON DELETE CASCADE,
    step_id INT NOT NULL REFERENCES scientific_workflow_steps(id) ON DELETE CASCADE,
    depends_on_step_id INT NOT NULL REFERENCES scientific_workflow_steps(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_scientific_workflow_step_dependency
        UNIQUE (workflow_run_id, step_id, depends_on_step_id),
    CONSTRAINT ck_scientific_workflow_no_self_dependency CHECK (step_id <> depends_on_step_id)
);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_step_dependencies_workflow_run_id
    ON scientific_workflow_step_dependencies(workflow_run_id);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_step_dependencies_step_id
    ON scientific_workflow_step_dependencies(step_id);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_step_dependencies_depends_on_step_id
    ON scientific_workflow_step_dependencies(depends_on_step_id);

CREATE TABLE IF NOT EXISTS scientific_workflow_events (
    id SERIAL PRIMARY KEY,
    workflow_run_id INT NOT NULL REFERENCES scientific_workflow_runs(id) ON DELETE CASCADE,
    step_id INT REFERENCES scientific_workflow_steps(id) ON DELETE SET NULL,
    actor_user_id INT REFERENCES users(id) ON DELETE SET NULL,
    event_type VARCHAR(60) NOT NULL,
    from_state VARCHAR(40),
    to_state VARCHAR(40),
    action VARCHAR(60),
    reason TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_scientific_workflow_event_idempotency
        UNIQUE (workflow_run_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_events_workflow_run_id
    ON scientific_workflow_events(workflow_run_id);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_events_step_id
    ON scientific_workflow_events(step_id);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_events_actor_user_id
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_events_created_at ON scientific_workflow_events(created_at);
    ON scientific_workflow_events(actor_user_id);
CREATE INDEX IF NOT EXISTS ix_scientific_workflow_event_run_time
    ON scientific_workflow_events(workflow_run_id, created_at);

ALTER TABLE scientific_workflow_steps
    ADD COLUMN IF NOT EXISTS execution_started_at TIMESTAMPTZ;
ALTER TABLE scientific_workflow_steps
    ADD COLUMN IF NOT EXISTS executor_id VARCHAR(160);

COMMIT;
