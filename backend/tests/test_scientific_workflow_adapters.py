from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.docking import DockingJob
from app.models.hsa import HSAJob
from app.models.rna_structure_job import RnaStructureJob
from app.models.scientific_workflow import (
    ScientificWorkflowRun,
    ScientificWorkflowStep,
)
from app.models.sequence import Analysis
from app.models.user import User
from app.models.workflow import ResearchTaskOutbox
from app.services.scientific_workflow_engine import (
    begin_step_execution,
    claim_ready_step,
    create_workflow_run,
)
from app.services.scientific_workflow_policy import (
    WorkflowDefinition,
    WorkflowStepDefinition,
)
from app.services.scientific_workflow_presets import create_builtin_task_run


def _token(client, username, email):
    registered = client.post("/api/v1/auth/register", json={
        "username": username,
        "email": email,
        "password": "password123",
    })
    assert registered.status_code == 201
    login = client.post("/api/v1/auth/token", data={
        "username": email,
        "password": "password123",
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert login.status_code == 200
    return login.json()["access_token"]


def test_builtin_task_presets_reject_unregistered_tasks():
    with pytest.raises(ValueError, match="No built-in workflow"):
        create_builtin_task_run(
            None,
            task_name="os.system",
            args=["rm", "-rf", "/"],
            owner_user_id=1,
            scope_type="test",
            scope_id=1,
        )


def test_analysis_async_submission_creates_workflow_atomically(client, db):
    token = _token(client, "wf_analysis_user", "wf-analysis@nexora.test")
    response = client.post(
        "/api/v1/analysis/sequence",
        headers={"Authorization": f"Bearer {token}"},
        json={"sequence": "ATGCGT", "force_async": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["dispatch"] == "scientific_workflow"
    assert payload["workflow_run_id"]

    analysis = db.query(Analysis).filter(Analysis.id == payload["analysis_id"]).one()
    run = db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == payload["workflow_run_id"]
    ).one()
    step = db.query(ScientificWorkflowStep).filter_by(workflow_run_id=run.id).one()
    assert run.scope_type == "analysis"
    assert run.scope_id == str(analysis.id)
    assert analysis.data["workflow_run_id"] == run.id
    assert step.task_name == "analysis.run"
    assert step.args == [analysis.id, "ATGCGT"]


def test_docking_submission_creates_vina_workflow(client, db):
    token = _token(client, "wf_docking_user", "wf-docking@nexora.test")
    response = client.post(
        "/api/v1/docking/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={"protein_sequence": "MKTAYIAKQRQISFVK", "ligand_smiles": "CCO", "max_pockets": 2},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["dispatch"] == "scientific_workflow"

    job = db.query(DockingJob).filter(DockingJob.id == payload["job_id"]).one()
    run = db.query(ScientificWorkflowRun).filter_by(id=payload["workflow_run_id"]).one()
    step = db.query(ScientificWorkflowStep).filter_by(workflow_run_id=run.id).one()
    assert job.celery_task_id == f"workflow-run-{run.id}"
    assert run.scope_type == "docking_job"
    assert step.task_name == "docking.run_vina"
    assert step.queue_name == "docking"
    assert step.args[0] == job.id
    assert step.args[3] == 2


def test_hsa_and_legacy_qsa_routes_create_same_workflow_contract(client, db):
    token = _token(client, "wf_hsa_user", "wf-hsa@nexora.test")
    headers = {"Authorization": f"Bearer {token}"}
    hsa = client.post("/api/v1/hsa/submit", headers=headers, json={
        "protein_sequence": "MKTAYIAKQRQISFVK",
        "env_factors": {"temperature_c": 37},
    })
    qsa = client.post("/api/v1/qsa/submit", headers=headers, json={
        "protein_sequence": "MKTAYIAKQRQISFVK",
        "env_factors": {"temperature_c": 25},
    })
    assert hsa.status_code == 202
    assert qsa.status_code == 202
    assert hsa.json()["dispatch"] == qsa.json()["dispatch"] == "scientific_workflow"

    run = db.query(ScientificWorkflowRun).filter_by(id=hsa.json()["workflow_run_id"]).one()
    step = db.query(ScientificWorkflowStep).filter_by(workflow_run_id=run.id).one()
    job = db.query(HSAJob).filter_by(id=hsa.json()["job_id"]).one()
    assert run.scope_type == "hsa_job"
    assert step.task_name == "hsa.run"
    assert step.queue_name == "qsa"
    assert step.args[0] == job.id


def test_rna_structure_submission_creates_workflow_and_keeps_legacy_status(client, db, monkeypatch):
    monkeypatch.setattr(settings, "TAMARIND_API_KEY", "unit-test-key")
    token = _token(client, "wf_rna_user", "wf-rna@nexora.test")
    response = client.post(
        "/api/v1/rna-structure/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={"sequence": "ACGUACGU"},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["dispatch"] == "scientific_workflow"

    job = db.query(RnaStructureJob).filter_by(id=payload["job_id"]).one()
    run = db.query(ScientificWorkflowRun).filter_by(id=payload["workflow_run_id"]).one()
    step = db.query(ScientificWorkflowStep).filter_by(workflow_run_id=run.id).one()
    assert job.metadata_json["workflow_run_id"] == run.id
    assert step.task_name == "rna_structure.predict_secondary"
    assert step.args == [job.id]


def test_dispatcher_claim_and_outbox_event_are_one_atomic_operation(client, db, monkeypatch):
    import app.services.scientific_workflow_scheduler as scheduler

    user = db.query(User).filter_by(email="wf-analysis@nexora.test").one()
    definition = WorkflowDefinition(
        key="scheduler-test", version="1",
        steps=(WorkflowStepDefinition("execute", "analysis.run", "analysis"),),
    )
    run, _ = create_workflow_run(
        db, definition, owner_user_id=user.id,
        scope_type="analysis", scope_id="777",
        step_inputs={"execute": {"args": [777, "ATGC"]}},
    )
    db.commit()

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False)
    monkeypatch.setattr(scheduler, "SessionLocal", factory)
    dispatched = scheduler.dispatch_ready_workflow_steps(worker_id="dispatcher-test")
    assert dispatched == 1

    db.expire_all()
    step = db.query(ScientificWorkflowStep).filter_by(workflow_run_id=run.id).one()
    event = db.query(ResearchTaskOutbox).filter_by(task_name="scientific_workflow.execute_step").one()
    assert step.state == "running"
    assert step.lease_token
    assert event.queue_name == "workflows"
    assert event.args == [step.id, step.lease_token]


def test_executor_start_marker_prevents_duplicate_delivery_and_resets_after_reclaim(client, db):
    user = db.query(User).filter_by(email="wf-analysis@nexora.test").one()
    definition = WorkflowDefinition(
        key="executor-fence-test", version="1",
        steps=(WorkflowStepDefinition("execute", "analysis.run", "analysis", max_attempts=2),),
    )
    run, _ = create_workflow_run(db, definition, owner_user_id=user.id)
    start = datetime.utcnow() + timedelta(seconds=5)
    step = claim_ready_step(db, run.id, worker_id="dispatcher", lease_seconds=10, now=start)
    db.commit()

    started, replay = begin_step_execution(
        db, step.id, lease_token=step.lease_token, executor_id="worker-a", now=start
    )
    db.commit()
    assert replay is False
    assert started.executor_id == "worker-a"

    duplicate, replay = begin_step_execution(
        db, step.id, lease_token=step.lease_token, executor_id="worker-b",
        now=start + timedelta(seconds=1),
    )
    assert replay is True
    assert duplicate.executor_id == "worker-a"

    # Expiry invalidates the old executor claim; a new lease may start a new attempt.
    from app.services.scientific_workflow_engine import reclaim_expired_leases
    expired = start + timedelta(seconds=11)
    assert reclaim_expired_leases(db, run.id, now=expired) == 1
    db.commit()
    db.refresh(step)
    assert step.state == "retry_wait"
    assert step.execution_started_at is None
    assert step.executor_id is None


def test_scientific_workflow_status_endpoint_is_owner_scoped(client, db):
    token = _token(client, "wf_read_user", "wf-read@nexora.test")
    user = db.query(User).filter_by(email="wf-read@nexora.test").one()
    definition = WorkflowDefinition(
        key="read-test", version="1",
        steps=(WorkflowStepDefinition("execute", "analysis.run", "analysis"),),
    )
    run, _ = create_workflow_run(db, definition, owner_user_id=user.id)
    db.commit()
    response = client.get(
        f"/api/v1/scientific-workflows/{run.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["workflow"]["id"] == run.id
    assert response.json()["steps"][0]["key"] == "execute"

