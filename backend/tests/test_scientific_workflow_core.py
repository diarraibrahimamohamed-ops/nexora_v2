from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.scientific_workflow import (
    ScientificWorkflowEvent,
    ScientificWorkflowRun,
    ScientificWorkflowStep,
)
from app.models.user import User
from app.services.scientific_workflow_engine import (
    WorkflowConflict,
    claim_ready_step,
    complete_step,
    create_workflow_run,
    fail_step,
    reclaim_expired_leases,
    renew_step_lease,
    transition_workflow_run,
)
from app.services.scientific_workflow_policy import (
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowStepDefinition,
    allowed_actions,
    parent_state,
    resolve_transition,
    validate_dag,
)


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(autoflush=False, autocommit=False, bind=engine)


def _definition(requires_review=True):
    return WorkflowDefinition(
        key="test.sequence-docking",
        version="1.0.0",
        description="Test DAG with one dependency and one downstream docking task.",
        requires_quality_review=requires_review,
        steps=(
            WorkflowStepDefinition(
                key="sequence_qc",
                task_name="analysis.run",
                queue_name="analysis",
                max_attempts=2,
            ),
            WorkflowStepDefinition(
                key="docking",
                task_name="docking.run_vina",
                queue_name="docking",
                depends_on=("sequence_qc",),
                max_attempts=2,
            ),
        ),
    )


def _owner(db):
    user = User(
        username="workflow_owner",
        email="workflow-owner@nexora.test",
        password="hash",
        status="active",
        is_admin=False,
    )
    db.add(user)
    db.flush()
    return user


def test_dag_returns_deterministic_topological_order_and_rejects_cycles():
    a = WorkflowStepDefinition("a", "analysis.run", "analysis")
    b = WorkflowStepDefinition("b", "docking.run_vina", "docking", depends_on=("a",))
    c = WorkflowStepDefinition("c", "hsa.run", "qsa")
    assert validate_dag((a, b, c)) == ["a", "b", "c"]

    with pytest.raises(ValueError, match="cycle"):
        WorkflowDefinition(
            key="cycle", version="1",
            steps=(
                WorkflowStepDefinition("a", "analysis.run", "analysis", depends_on=("b",)),
                WorkflowStepDefinition("b", "hsa.run", "qsa", depends_on=("a",)),
            ),
        )


def test_task_registry_rejects_unknown_task_and_wrong_queue():
    with pytest.raises(ValueError, match="not registered"):
        WorkflowStepDefinition("bad", "os.system", "analysis")
    with pytest.raises(ValueError, match="queue mismatch"):
        WorkflowStepDefinition("bad", "analysis.run", "docking")


def test_hfsm_uses_parent_states_and_rejects_invalid_transitions():
    assert parent_state("running.executing") == "running"
    assert parent_state("quality_review.pending") == "quality_review"
    assert resolve_transition("ready", "queue").to_state == "queued"
    assert not resolve_transition("succeeded", "retry_ready").allowed
    assert "cancel" in allowed_actions("running.executing")


def test_workflow_creation_persists_definition_snapshot_and_is_idempotent():
    engine, factory = _session_factory()
    db = factory()
    owner = _owner(db)
    definition = _definition()

    run, replay = create_workflow_run(
        db, definition,
        owner_user_id=owner.id,
        scope_type="research_study",
        scope_id=51,
        context={"organism": "test"},
        input_manifest={"sequence_sha256": "a" * 64},
        step_inputs={"sequence_qc": {"args": [42], "kwargs": {"mode": "strict"}}},
        idempotency_key="same-study-workflow-1",
    )
    db.commit()
    assert replay is False
    assert run.state == "ready"
    assert run.scope_id == "51"
    assert run.definition_snapshot["topological_order"] == ["sequence_qc", "docking"]

    replayed, was_replay = create_workflow_run(
        db, definition,
        owner_user_id=owner.id,
        idempotency_key="same-study-workflow-1",
    )
    assert was_replay is True
    assert replayed.id == run.id
    assert db.query(ScientificWorkflowRun).count() == 1
    assert db.query(ScientificWorkflowStep).count() == 2
    assert db.query(ScientificWorkflowEvent).filter_by(
        workflow_run_id=run.id, event_type="run_created"
    ).count() == 1

    first = db.query(ScientificWorkflowStep).filter_by(
        workflow_run_id=run.id, step_key="sequence_qc"
    ).one()
    assert first.args == [42]
    assert first.kwargs == {"mode": "strict"}

    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_dag_claiming_waits_for_prerequisite_and_quality_review():
    engine, factory = _session_factory()
    db = factory()
    owner = _owner(db)
    run, _ = create_workflow_run(db, _definition(), owner_user_id=owner.id)
    db.commit()

    start = datetime.utcnow() + timedelta(seconds=5)
    first = claim_ready_step(
        db, run.id, worker_id="worker-a", now=start
    )
    assert first is not None and first.step_key == "sequence_qc"
    first_token = first.lease_token

    # The docking step is not claimable while its prerequisite is running.
    assert claim_ready_step(
        db, run.id, worker_id="worker-b", now=start + timedelta(seconds=1)
    ) is None

    complete_step(
        db, first.id, lease_token=first_token,
        output={"sequence_length": 250}, now=start + timedelta(seconds=2),
    )
    db.commit()

    second = claim_ready_step(
        db, run.id, worker_id="worker-b", now=start + timedelta(seconds=3)
    )
    assert second is not None and second.step_key == "docking"
    complete_step(
        db, second.id, lease_token=second.lease_token,
        output={"vina_best_score": -7.1}, now=start + timedelta(seconds=4),
    )
    db.commit()

    db.refresh(run)
    assert run.state == "quality_review.pending"
    run, replay = transition_workflow_run(
        db, run.id, action="approve", actor_user_id=owner.id,
        expected_state_version=run.state_version,
        idempotency_key="review-approved-1",
    )
    assert replay is False
    run, replay = transition_workflow_run(
        db, run.id, action="finalize_review", actor_user_id=owner.id,
        expected_state_version=run.state_version,
    )
    db.commit()
    assert run.state == "succeeded"
    assert run.completed_at is not None

    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_step_lease_renewal_and_fencing_token():
    engine, factory = _session_factory()
    db = factory()
    owner = _owner(db)
    definition = WorkflowDefinition(
        key="lease.test", version="1",
        steps=(WorkflowStepDefinition("only", "analysis.run", "analysis", max_attempts=2),),
        requires_quality_review=False,
    )
    run, _ = create_workflow_run(db, definition, owner_user_id=owner.id)
    start = datetime.utcnow() + timedelta(seconds=5)
    step = claim_ready_step(db, run.id, worker_id="worker-1", lease_seconds=30, now=start)
    original_token = step.lease_token

    with pytest.raises(WorkflowConflict, match="lease token"):
        complete_step(db, step.id, lease_token="stale-token", output={}, now=start + timedelta(seconds=1))

    renewed = renew_step_lease(
        db, step.id, lease_token=original_token, lease_seconds=60,
        now=start + timedelta(seconds=10),
    )
    assert renewed.lease_expires_at == start + timedelta(seconds=70)

    completed = complete_step(
        db, step.id, lease_token=original_token, output={"ok": True},
        now=start + timedelta(seconds=20),
    )
    db.commit()
    db.refresh(run)
    assert completed.state == "succeeded"
    assert run.state == "succeeded"

    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_expired_lease_retries_then_fails_after_attempt_limit():
    engine, factory = _session_factory()
    db = factory()
    owner = _owner(db)
    definition = WorkflowDefinition(
        key="lease.expiry", version="1",
        steps=(WorkflowStepDefinition("only", "analysis.run", "analysis", max_attempts=2),),
    )
    run, _ = create_workflow_run(db, definition, owner_user_id=owner.id)
    start = datetime.utcnow() + timedelta(seconds=5)

    first = claim_ready_step(
        db, run.id, worker_id="worker-1", lease_seconds=10, now=start
    )
    expired_at = start + timedelta(seconds=11)
    assert reclaim_expired_leases(db, run.id, now=expired_at) == 1
    db.commit()
    assert first.state == "retry_wait"
    assert first.lease_token is None

    retry_at = first.available_at + timedelta(seconds=1)
    second = claim_ready_step(
        db, run.id, worker_id="worker-2", lease_seconds=10, now=retry_at
    )
    assert second is not None
    assert second.attempts == 2

    assert reclaim_expired_leases(
        db, run.id, now=second.lease_expires_at + timedelta(seconds=1)
    ) == 1
    db.commit()
    db.refresh(run)
    assert second.state == "failed"
    assert run.state == "failed"
    assert run.failure_code == "STEP_LEASE_EXHAUSTED"

    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_retryable_step_failure_can_be_claimed_again():
    engine, factory = _session_factory()
    db = factory()
    owner = _owner(db)
    definition = WorkflowDefinition(
        key="retry.test", version="1",
        steps=(WorkflowStepDefinition("only", "analysis.run", "analysis", max_attempts=2),),
    )
    run, _ = create_workflow_run(db, definition, owner_user_id=owner.id)
    start = datetime.utcnow() + timedelta(seconds=5)
    first = claim_ready_step(db, run.id, worker_id="worker-1", now=start)
    failed = fail_step(
        db, first.id, lease_token=first.lease_token,
        error_code="TEMPORARY", error_message="temporary worker failure",
        retryable=True, now=start + timedelta(seconds=1),
    )
    assert failed.state == "retry_wait"

    second = claim_ready_step(
        db, run.id, worker_id="worker-2", now=failed.available_at + timedelta(seconds=1)
    )
    assert second.attempts == 2
    complete_step(db, second.id, lease_token=second.lease_token, output={"ok": True})
    db.commit()
    db.refresh(run)
    assert run.state == "quality_review.pending"

    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_transition_uses_state_version_and_event_idempotency():
    engine, factory = _session_factory()
    db = factory()
    owner = _owner(db)
    run, _ = create_workflow_run(db, _definition(), owner_user_id=owner.id)
    db.commit()

    run, replay = transition_workflow_run(
        db, run.id, action="queue", expected_state_version=0,
        idempotency_key="queue-run-1",
    )
    assert replay is False
    run, replay = transition_workflow_run(
        db, run.id, action="queue", expected_state_version=0,
        idempotency_key="queue-run-1",
    )
    assert replay is True

    with pytest.raises(WorkflowConflict, match="stale state version"):
        transition_workflow_run(
            db, run.id, action="worker_started", expected_state_version=0
        )

    db.rollback()
    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_block_and_resume_return_to_recorded_hfsm_leaf_state():
    engine, factory = _session_factory()
    db = factory()
    owner = _owner(db)
    run, _ = create_workflow_run(db, _definition(), owner_user_id=owner.id)

    run, _ = transition_workflow_run(db, run.id, action="queue")
    run, _ = transition_workflow_run(db, run.id, action="block", reason="Waiting for a reviewer.")
    assert run.state == "blocked"
    assert run.resume_state == "queued"

    run, _ = transition_workflow_run(db, run.id, action="resume", reason="Reviewer completed the check.")
    db.commit()
    assert run.state == "queued"
    assert run.resume_state is None

    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_workflow_definition_registry_rejects_duplicate_version():
    registry = WorkflowRegistry()
    definition = _definition()
    registry.register(definition)
    assert registry.get(definition.key, definition.version) is definition
    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition)



def test_duplicate_dependency_is_rejected():
    with pytest.raises(ValueError, match="duplicate dependency"):
        WorkflowDefinition(
            key="duplicate-edge", version="1",
            steps=(
                WorkflowStepDefinition("a", "analysis.run", "analysis"),
                WorkflowStepDefinition("b", "hsa.run", "qsa", depends_on=("a", "a")),
            ),
        )


def test_late_success_from_parallel_step_does_not_overwrite_failed_run():
    engine, factory = _session_factory()
    db = factory()
    owner = _owner(db)
    definition = WorkflowDefinition(
        key="parallel.failure", version="1",
        requires_quality_review=False,
        steps=(
            WorkflowStepDefinition("first", "analysis.run", "analysis", max_attempts=1),
            WorkflowStepDefinition("second", "hsa.run", "qsa", max_attempts=1),
        ),
    )
    run, _ = create_workflow_run(db, definition, owner_user_id=owner.id)
    now = datetime.utcnow() + timedelta(seconds=5)
    first = claim_ready_step(db, run.id, worker_id="worker-1", now=now)
    second = claim_ready_step(db, run.id, worker_id="worker-2", now=now + timedelta(seconds=1))
    assert first and second and first.id != second.id

    fail_step(
        db, first.id, lease_token=first.lease_token,
        error_code="PERMANENT", error_message="non-retryable error",
        retryable=False, now=now + timedelta(seconds=2),
    )
    assert run.state == "failed"

    complete_step(
        db, second.id, lease_token=second.lease_token,
        output={"late_result": True}, now=now + timedelta(seconds=3),
    )
    db.commit()
    db.refresh(run)
    assert run.state == "failed"

    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()
