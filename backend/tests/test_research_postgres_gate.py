"""PostgreSQL-only concurrency gates for Research lifecycle and task outbox."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier, Lock

import pytest
from fastapi import HTTPException

from app.api.v1.research_workflow import WorkflowTransitionRequest, get_workflow, transition_workflow
from app.database import SessionLocal, engine
from app.models.research import ResearchStudy
from app.models.scientific_workflow import ScientificWorkflowEvent, ScientificWorkflowRun
from app.models.user import User, UserStatus
from app.models.workflow import ResearchTaskOutbox, ResearchWorkflowEvent
from app.services import task_outbox
from app.services.research_workflow_hfsm import ensure_research_lifecycle_run
from app.services.task_outbox import enqueue_task

pytestmark = pytest.mark.skipif(
    engine.dialect.name != "postgresql",
    reason="Requires PostgreSQL row-level locking; SQLite is not an equivalent substitute.",
)


def _seed_study(username: str, email: str, name: str, state: str = "draft") -> tuple[int, int]:
    db = SessionLocal()
    try:
        owner = User(
            username=username, email=email, password="test-only-password-hash",
            status=UserStatus.active, is_admin=False,
        )
        db.add(owner)
        db.flush()
        study = ResearchStudy(
            user_id=owner.id, name=name, disease="malaria",
            pathogen="Plasmodium falciparum", country_focus="Mali",
            region_scope="Sahel",
            objective="Vérifier la cohérence transactionnelle du workflow Research.",
            hypothesis="Les transitions concurrentes doivent être sérialisées par étude.",
            status=state, metadata_json={},
        )
        db.add(study)
        db.flush()
        owner_id, study_id = owner.id, study.id
        db.add(ResearchWorkflowEvent(
            study_id=study_id, actor_id=owner_id, from_state=None, to_state="draft",
            action="created", payload={"source": "postgres-concurrency-gate"},
        ))
        db.commit()
        return owner_id, study_id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def test_concurrent_legacy_backfill_creates_one_run_and_imports_history_once():
    owner_id, study_id = _seed_study(
        "pg_backfill_owner", "pg-backfill-owner@nexora.test",
        "PostgreSQL concurrent legacy backfill", "analysis",
    )
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        db.add_all([
            ResearchWorkflowEvent(
                study_id=study_id, actor_id=owner_id, from_state="draft",
                to_state="data_review", action="submit_for_review",
                payload={"legacy": "submitted"}, created_at=now + timedelta(seconds=1),
            ),
            ResearchWorkflowEvent(
                study_id=study_id, actor_id=owner_id, from_state="data_review",
                to_state="analysis", action="approve_data",
                reason="Legacy approval imported by the PostgreSQL gate.",
                payload={"legacy": "approved"}, created_at=now + timedelta(seconds=2),
            ),
        ])
        db.commit()
    finally:
        db.close()

    barrier = Barrier(2)

    def read_workflow(_):
        session = SessionLocal()
        try:
            actor = session.query(User).filter(User.id == owner_id).one()
            barrier.wait(timeout=15)
            result = get_workflow(study_id, session, actor)
            return result["scientific_workflow"]["run_id"], result["state"]
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(read_workflow, range(2)))
    assert results[0] == results[1]
    run_id, state = results[0]
    assert state == "analysis"

    verify = SessionLocal()
    try:
        runs = verify.query(ScientificWorkflowRun).filter(
            ScientificWorkflowRun.workflow_key == "nexora.research-study-lifecycle",
            ScientificWorkflowRun.scope_type == "research_study",
            ScientificWorkflowRun.scope_id == str(study_id),
        ).all()
        assert len(runs) == 1
        assert runs[0].id == run_id
        imported = verify.query(ScientificWorkflowEvent).filter(
            ScientificWorkflowEvent.workflow_run_id == run_id,
            ScientificWorkflowEvent.event_type == "legacy_transition_imported",
        ).all()
        assert len(imported) == 3
        assert len({item.idempotency_key for item in imported}) == 3
    finally:
        verify.close()


def test_concurrent_transitions_allow_exactly_one_state_change():
    owner_id, study_id = _seed_study(
        "pg_transition_owner", "pg-transition-owner@nexora.test",
        "PostgreSQL concurrent Research transition",
    )
    setup = SessionLocal()
    try:
        study = setup.query(ResearchStudy).filter(ResearchStudy.id == study_id).one()
        ensure_research_lifecycle_run(setup, study)
        setup.commit()
    finally:
        setup.close()

    barrier = Barrier(2)

    def submit(index: int):
        session = SessionLocal()
        try:
            actor = session.query(User).filter(User.id == owner_id).one()
            barrier.wait(timeout=15)
            result = transition_workflow(
                study_id, WorkflowTransitionRequest(action="submit_for_review"),
                session, actor, f"pg-concurrent-submit-{index}",
            )
            return ("accepted", result["state"])
        except HTTPException as exc:
            session.rollback()
            return ("rejected", exc.status_code)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, (1, 2)))
    assert sorted(item[0] for item in outcomes) == ["accepted", "rejected"]
    assert ("rejected", 409) in outcomes
    assert ("accepted", "data_review") in outcomes

    verify = SessionLocal()
    try:
        study = verify.query(ResearchStudy).filter(ResearchStudy.id == study_id).one()
        run = verify.query(ScientificWorkflowRun).filter(
            ScientificWorkflowRun.workflow_key == "nexora.research-study-lifecycle",
            ScientificWorkflowRun.scope_type == "research_study",
            ScientificWorkflowRun.scope_id == str(study_id),
        ).one()
        assert study.status == "data_review"
        assert run.state == "data_review"
        assert run.state_version == 1
        assert verify.query(ResearchWorkflowEvent).filter(
            ResearchWorkflowEvent.study_id == study_id,
            ResearchWorkflowEvent.action == "submit_for_review",
        ).count() == 1
        assert verify.query(ScientificWorkflowEvent).filter(
            ScientificWorkflowEvent.workflow_run_id == run.id,
            ScientificWorkflowEvent.event_type == "run_transition",
        ).count() == 1
    finally:
        verify.close()


def test_concurrent_outbox_dispatchers_do_not_publish_same_event_twice():
    db = SessionLocal()
    try:
        event = enqueue_task(
            db, task_name="analysis.run", args=[987654], queue="analysis",
        )
        event_key = event.event_key
        db.commit()
    finally:
        db.close()

    barrier = Barrier(2)
    sent: list[str] = []
    sent_lock = Lock()

    def dispatch():
        barrier.wait(timeout=15)

        def sender(_task_name, *, task_id, **_kwargs):
            with sent_lock:
                sent.append(task_id)

        return task_outbox.dispatch_once(limit=1, sender=sender)

    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda _: dispatch(), range(2)))
    assert sorted(counts) == [0, 1]
    assert sent == [event_key]

    verify = SessionLocal()
    try:
        stored = verify.query(ResearchTaskOutbox).filter(
            ResearchTaskOutbox.event_key == event_key,
        ).one()
        assert stored.status == "published"
        assert stored.attempts == 1
        assert stored.published_at is not None
    finally:
        verify.close()
