import json
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.docking import DockingJob, JobStatus
from app.models.scientific_workflow import (
    ScientificWorkflowRun,
    ScientificWorkflowStep,
)
from app.models.sequence import Analysis, AnalysisStatus
from app.services import scientific_workflow_adapters as adapters
from app.workers import analysis_tasks, scientific_workflow_dispatcher, scientific_workflow_tasks


def _auth_headers(client, username, email):
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
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _patch_session_factory(db, monkeypatch):
    factory = sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(scientific_workflow_dispatcher, "SessionLocal", factory)
    monkeypatch.setattr(scientific_workflow_tasks, "SessionLocal", factory)
    monkeypatch.setattr(adapters, "SessionLocal", factory)
    monkeypatch.setattr(analysis_tasks, "SessionLocal", factory)
    return factory


def _advance_once(db, run_id, monkeypatch):
    _patch_session_factory(db, monkeypatch)
    queued = scientific_workflow_dispatcher.dispatch_ready_workflows_once(
        max_runs=20, max_steps_per_run=10
    )
    db.expire_all()
    running = db.query(ScientificWorkflowStep).filter_by(
        workflow_run_id=run_id, state="running"
    ).order_by(ScientificWorkflowStep.id.asc()).all()
    return queued, running


def test_sequence_workflow_executes_real_analysis_worker_and_validates_persisted_output(
    client, db, monkeypatch
):
    headers = _auth_headers(
        client, "sequence_workflow_user", "sequence-workflow@nexora.test"
    )
    sequence = ("ACGT" * 300) + ("GATTACA" * 20)
    response = client.post(
        "/api/v1/scientific-workflows/sequence-analysis",
        headers=headers,
        json={"sequence": sequence, "name": "Workflow integration DNA"},
    )
    assert response.status_code == 202
    payload = response.json()
    run_id = payload["workflow_run_id"]
    analysis_id = payload["analysis_id"]

    queued, running = _advance_once(db, run_id, monkeypatch)
    assert queued == 1
    assert len(running) == 1
    first = running[0]
    assert first.step_key == "sequence_analysis"
    assert first.queue_name == "analysis"

    result = scientific_workflow_tasks.execute_claimed_step(
        first.id, first.lease_token, "workflow-test-worker"
    )
    assert result["status"] == "completed"
    db.expire_all()

    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).one()
    assert analysis.status == AnalysisStatus.completed
    assert (analysis.data or {}).get("async_sequence_result", {}).get("input_length") == len(sequence)

    queued, running = _advance_once(db, run_id, monkeypatch)
    assert queued == 1
    assert len(running) == 1
    second = running[0]
    assert second.step_key == "validate_sequence_output"
    assert second.queue_name == "workflow"

    result = scientific_workflow_tasks.execute_claimed_step(
        second.id, second.lease_token, "workflow-test-worker"
    )
    assert result["status"] == "completed"
    db.expire_all()

    run = db.query(ScientificWorkflowRun).filter(ScientificWorkflowRun.id == run_id).one()
    steps = db.query(ScientificWorkflowStep).filter_by(workflow_run_id=run_id).all()
    assert run.state == "succeeded"
    assert [step.state for step in steps] == ["succeeded", "succeeded"]
    assert steps[1].output["output_sha256"]

    status = client.get(f"/api/v1/scientific-workflows/{run_id}", headers=headers)
    assert status.status_code == 200
    assert status.json()["state"] == "succeeded"
    assert len(status.json()["steps"]) == 2


def test_docking_workflow_runs_input_gate_then_vina_adapter_then_artifact_validation(
    client, db, monkeypatch, tmp_path
):
    headers = _auth_headers(
        client, "docking_workflow_user", "docking-workflow@nexora.test"
    )
    monkeypatch.setattr(settings, "RESULTS_DIR", str(tmp_path))
    response = client.post(
        "/api/v1/scientific-workflows/docking",
        headers=headers,
        json={
            "protein_sequence": "MKTAYIAKQRQISFVKSHFSRQ",
            "ligand_smiles": "CCO",
            "max_pockets": 2,
        },
    )
    assert response.status_code == 202
    payload = response.json()
    run_id = payload["workflow_run_id"]
    job_id = payload["docking_job_id"]

    queued, running = _advance_once(db, run_id, monkeypatch)
    assert queued == 1 and len(running) == 1
    validation_step = running[0]
    assert validation_step.step_key == "validate_docking_input"
    validation = scientific_workflow_tasks.execute_claimed_step(
        validation_step.id, validation_step.lease_token, "workflow-test-worker"
    )
    assert validation["status"] == "completed"
    assert validation["output"]["protein_length"] > 0

    factory = _patch_session_factory(db, monkeypatch)

    def fake_vina_adapter(args, kwargs):
        assert args == [job_id, 2]
        result_path = Path(tmp_path) / f"job_{job_id}.json"
        result_path.write_text(json.dumps({
            "success": True,
            "num_poses": 2,
            "vina_best_score": -6.4,
            "docking_score": -6.4,
        }), encoding="utf-8")
        session = factory()
        try:
            job = session.query(DockingJob).filter(DockingJob.id == job_id).one()
            job.status = JobStatus.completed
            job.progress = 100
            job.result_path = str(result_path)
            session.commit()
        finally:
            session.close()
        return {"job_id": job_id, "status": "completed", "num_poses": 2}

    monkeypatch.setitem(adapters.ADAPTERS, "docking.run_vina", fake_vina_adapter)

    queued, running = _advance_once(db, run_id, monkeypatch)
    assert queued == 1 and len(running) == 1
    vina_step = running[0]
    assert vina_step.step_key == "vina_docking"
    assert vina_step.queue_name == "docking"
    vina = scientific_workflow_tasks.execute_claimed_step(
        vina_step.id, vina_step.lease_token, "workflow-test-worker"
    )
    assert vina["status"] == "completed"

    queued, running = _advance_once(db, run_id, monkeypatch)
    assert queued == 1 and len(running) == 1
    output_step = running[0]
    assert output_step.step_key == "validate_docking_output"
    checked = scientific_workflow_tasks.execute_claimed_step(
        output_step.id, output_step.lease_token, "workflow-test-worker"
    )
    assert checked["status"] == "completed"
    assert checked["output"]["vina_best_score"] == -6.4
    assert checked["output"]["result_sha256"]

    db.expire_all()
    run = db.query(ScientificWorkflowRun).filter(ScientificWorkflowRun.id == run_id).one()
    job = db.query(DockingJob).filter(DockingJob.id == job_id).one()
    assert run.state == "succeeded"
    assert job.status == JobStatus.completed
