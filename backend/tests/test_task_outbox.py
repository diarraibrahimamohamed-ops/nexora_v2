from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.user import User
from app.models.research import ResearchStudy
from app.models.workflow import ResearchTaskOutbox
from app.services import task_outbox


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def test_enqueue_is_persisted_in_callers_transaction_and_dispatched(monkeypatch):
    engine, factory = _session_factory()
    monkeypatch.setattr(task_outbox, "SessionLocal", factory)
    db = factory()
    user = User(username="outbox_user", email="outbox@example.test", password="hash", status="active", is_admin=False)
    db.add(user)
    db.flush()
    study = ResearchStudy(user_id=user.id, name="Outbox test", disease="x", pathogen="y", country_focus="Mali", region_scope="Sahel", metadata_json={})
    db.add(study)
    db.flush()
    row = task_outbox.enqueue_task(db, task_name="docking.run_vina", args=[42], queue="docking")
    assert row.id is not None
    assert db.query(ResearchTaskOutbox).count() == 1
    db.commit()

    sent = []
    count = task_outbox.dispatch_once(sender=lambda *a, **kw: sent.append((a, kw)))
    assert count == 1
    assert len(sent) == 1
    assert sent[0][0] == ("docking.run_vina",)
    assert sent[0][1]["args"] == [42]
    assert sent[0][1]["task_id"] == row.event_key

    check = db.query(ResearchTaskOutbox).filter_by(event_key=row.event_key).one()
    assert check.status == "published"
    assert check.published_at is not None
    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_failed_publication_returns_to_pending_with_backoff(monkeypatch):
    engine, factory = _session_factory()
    monkeypatch.setattr(task_outbox, "SessionLocal", factory)
    db = factory()
    user = User(username="retry_user", email="retry@example.test", password="hash", status="active", is_admin=False)
    db.add(user)
    db.flush()
    study = ResearchStudy(user_id=user.id, name="Retry test", disease="x", pathogen="y", country_focus="Mali", region_scope="Sahel", metadata_json={})
    db.add(study)
    db.flush()
    row = task_outbox.enqueue_task(db, task_name="docking.run_vina", args=[99], queue="docking")
    db.commit()
    event_key = row.event_key

    def failing_sender(*args, **kwargs):
        raise ConnectionError("broker unavailable")

    assert task_outbox.dispatch_once(sender=failing_sender) == 0
    db.expire_all()
    check = db.query(ResearchTaskOutbox).filter_by(event_key=event_key).one()
    assert check.status == "pending"
    assert check.attempts == 1
    assert check.last_error == "ConnectionError"
    assert check.available_at > datetime.utcnow()

    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()
