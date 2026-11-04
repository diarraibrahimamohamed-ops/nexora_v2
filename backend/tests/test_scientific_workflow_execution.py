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
    # The dispatcher scans the whole test database, which is session-scoped.
    # Count only steps claimed for this workflow; unrelated pending fixtures
    # must not make this workflow's integration test order-dependent.
    scientific_workflow_dispatcher.dispatch_ready_workflows_once(
        max_runs=20, max_steps_per_run=10
    )
    db.expire_all()
    running = db.query(ScientificWorkflowStep).filter_by(
        workflow_run_id=run_id, state="running"
    ).order_by(ScientificWorkflowStep.id.asc()).all()
    return len(running), running


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
    assert response.status_code == 202, response.text
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
    assert response.status_code == 202, response.text
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



def test_afribio_alignment_workflow_definition_is_allowlisted_and_asynchronous():
    from app.api.v1.scientific_workflows import _SEQUENCE_ALIGNMENT_DEFINITION
    from app.services.scientific_workflow_policy import TASK_CONTRACTS

    steps = _SEQUENCE_ALIGNMENT_DEFINITION.steps
    assert [step.task_name for step in steps] == [
        "analysis.run_afribio_alignment",
        "workflow.validate_sequence_alignment",
    ]
    assert all(step.queue_name in {"analysis", "workflow"} for step in steps)
    assert TASK_CONTRACTS["workflow.validate_sequence_alignment"].queue_name == "workflow"



def test_afribio_alignment_workflow_executes_and_validates_persisted_provenance(
    client, db, monkeypatch
):
    headers = _auth_headers(
        client, "afribio_workflow_user", "afribio-workflow@nexora.test"
    )
    response = client.post(
        "/api/v1/scientific-workflows/sequence-alignment",
        headers=headers,
        json={
            "query": "AAAACCCCGGGGTTTT",
            "reference": "AAAATTTTCCCCGGGGTTTT",
            "sequence_type": "DNA",
            "max_alignment_cells": 5_000_000,
        },
    )
    assert response.status_code == 202, response.text
    payload = response.json()
    run_id = payload["workflow_run_id"]
    analysis_id = payload["analysis_id"]

    queued, running = _advance_once(db, run_id, monkeypatch)
    assert queued == 1 and len(running) == 1
    alignment_step = running[0]
    assert alignment_step.step_key == "afribio_alignment"
    assert alignment_step.queue_name == "analysis"
    assert alignment_step.args == [analysis_id]
    assert alignment_step.kwargs["options"]["maxAlignmentCells"] == 5_000_000

    execution = scientific_workflow_tasks.execute_claimed_step(
        alignment_step.id, alignment_step.lease_token, "afribio-workflow-test-worker"
    )
    assert execution["status"] == "completed"
    db.expire_all()

    from app.models.sequence import Sequence as BioSequence
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).one()
    assert analysis.status == AnalysisStatus.completed
    assert sorted(record.header for record in db.query(BioSequence).filter_by(analysis_id=analysis_id).all()) == [
        "query", "reference"
    ]
    data = analysis.data or {}
    assert data["scientific_provenance"]["manifest_type"] == "scientific_analysis"
    assert len(data["scientific_output_sha256"]) == 64
    assert len(data["scientific_provenance"]["manifest_sha256"]) == 64
    assert data["scientific_provenance"]["execution"]["mode"] == "celery_scientific_workflow"

    queued, running = _advance_once(db, run_id, monkeypatch)
    assert queued == 1 and len(running) == 1
    validate_step = running[0]
    assert validate_step.step_key == "validate_alignment_output"
    assert validate_step.queue_name == "workflow"
    validated = scientific_workflow_tasks.execute_claimed_step(
        validate_step.id, validate_step.lease_token, "afribio-workflow-test-worker"
    )
    assert validated["status"] == "completed"
    assert validated["output"]["status"] == "validated"

    db.expire_all()
    run = db.query(ScientificWorkflowRun).filter_by(id=run_id).one()
    assert run.state == "succeeded"


def test_sequence_route_uses_outbox_and_identifier_only_celery_payload(client, db):
    import hashlib

    from app.api.v1.analysis import BROWSER_SEQUENCE_THRESHOLD, SEQUENCE_ASYNC_THRESHOLD
    from app.models.sequence import Sequence as BioSequence
    from app.models.workflow import ResearchTaskOutbox

    assert SEQUENCE_ASYNC_THRESHOLD == 50_000
    assert BROWSER_SEQUENCE_THRESHOLD == 100_000
    sequence = "ACGT" * 12_501
    assert len(sequence) > BROWSER_SEQUENCE_THRESHOLD

    response = client.post(
        "/api/v1/analysis/sequence",
        json={"sequence": sequence},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["async"] is True
    assert payload["dispatch"] == "transactional_outbox"
    assert payload["threshold"] == 50_000

    analysis = db.query(Analysis).filter(Analysis.id == payload["analysis_id"]).one()
    record = db.query(BioSequence).filter_by(analysis_id=analysis.id).one()
    event = db.query(ResearchTaskOutbox).filter_by(event_key=payload["task_id"]).one()
    assert record.sequence == sequence
    assert record.length == len(sequence)
    assert hashlib.sha256(record.sequence.encode("ascii")).hexdigest() == analysis.data["input_sequence_sha256"]
    assert event.task_name == "analysis.run"
    assert event.queue_name == "analysis"
    assert event.args == [analysis.id]
    assert event.kwargs == {}
    assert sequence not in repr(event.args) + repr(event.kwargs)

    status = client.get(f"/api/v1/analysis/sequence/{analysis.id}/status")
    assert status.status_code == 200
    assert status.json()["input_sequence_sha256"] == analysis.data["input_sequence_sha256"]
    assert status.json()["scientific_input_manifest"]["manifest_type"] == "scientific_analysis_input"


def test_sequence_route_force_async_bypasses_50k_threshold(client, db):
    from app.models.sequence import Analysis, Sequence as BioSequence
    from app.models.workflow import ResearchTaskOutbox

    response = client.post(
        "/api/v1/analysis/sequence",
        json={"sequence": "ACGT" * 25, "force_async": True},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["async"] is True
    assert payload["threshold"] == 50_000
    analysis = db.query(Analysis).filter(Analysis.id == payload["analysis_id"]).one()
    sequence = db.query(BioSequence).filter_by(analysis_id=analysis.id).one()
    event = db.query(ResearchTaskOutbox).filter_by(event_key=payload["task_id"]).one()
    assert sequence.length == 100
    assert event.args == [analysis.id]
