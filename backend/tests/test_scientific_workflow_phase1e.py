from datetime import date

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.evidence import ResearchEvidence
from app.models.experimental import ResearchAssay, ResearchAssayAnalysis, ResearchAssayMeasurement
from app.models.research import ResearchStudy
from app.models.scientific_workflow import ScientificWorkflowRun, ScientificWorkflowStep
from app.models.user import User
from app.services import scientific_workflow_adapters
from app.workers import scientific_workflow_dispatcher, scientific_workflow_tasks
from app.services.scientific_workflow_policy import TASK_CONTRACTS


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


def _accepted_assay(db, user):
    study = ResearchStudy(
        user_id=user.id,
        name="Phase 1E 4PL workflow validation",
        disease="malaria",
        pathogen="Plasmodium falciparum",
        country_focus="Mali",
        region_scope="Sahel",
        objective="Evaluate a dose-response curve from biological replicate profiles.",
        hypothesis="The test article changes the declared growth-inhibition readout.",
        status="analysis",
        metadata_json={},
    )
    db.add(study)
    db.flush()
    assay = ResearchAssay(
        study_id=study.id,
        created_by=user.id,
        assay_type="dose_response",
        assay_name="Phase 1E robust 4PL assay",
        test_article="Compound Phase1E",
        source_kind="lab_generated",
        laboratory="N3XORA test laboratory",
        method_name="Growth-inhibition response",
        method_version="SOP-4PL-1",
        assay_date=date(2026, 10, 31),
        organism="Plasmodium falciparum",
        strain="3D7",
        conditions={"temperature_c": 37, "exposure_hours": 48},
        status="reviewed",
        review_decision="accepted_for_analysis",
        reviewer_id=user.id,
    )
    db.add(assay)
    db.flush()

    doses = (0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0)
    offsets = {"bio-1": -0.5, "bio-2": 0.0, "bio-3": 0.5}
    for replicate, offset in offsets.items():
        for dose in doses:
            response = 100.0 / (1.0 + (3.0 / dose) ** 1.2) + offset
            db.add(ResearchAssayMeasurement(
                assay_id=assay.id,
                sample_role="test_sample",
                sample_label="Compound Phase1E",
                replicate_id=replicate,
                replicate_kind="biological",
                concentration_value=dose,
                concentration_unit="uM",
                response_name="growth_inhibition",
                response_value=response,
                response_unit="% inhibition",
                qualifier="exact",
                metadata_json={},
            ))
    db.commit()
    return study, assay


def _patch_workflow_sessions(db, monkeypatch):
    factory = sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(scientific_workflow_dispatcher, "SessionLocal", factory)
    monkeypatch.setattr(scientific_workflow_tasks, "SessionLocal", factory)
    monkeypatch.setattr(scientific_workflow_adapters, "SessionLocal", factory)
    return factory


def test_4pl_task_is_allowlisted_on_dedicated_workflow_queue():
    contract = TASK_CONTRACTS["experimental.run_4pl"]
    assert contract.queue_name == "workflow"


def test_accepted_assay_4pl_runs_through_hfsm_and_replays_through_sync_service(
    client, db, monkeypatch
):
    headers = _auth_headers(
        client, "phase1e_4pl_owner", "phase1e-4pl-owner@nexora.test"
    )
    user = db.query(User).filter(User.username == "phase1e_4pl_owner").one()
    study, assay = _accepted_assay(db, user)
    body = {
        "response_name": "growth_inhibition",
        "response_unit": "% inhibition",
        "concentration_unit": "uM",
        "response_direction": "increasing",
        "endpoint_name": "IC50",
        "sample_label": "Compound Phase1E",
        "bootstrap_iterations": 500,
        "random_seed": 29,
    }
    endpoint = (
        f"/api/v1/research/studies/{study.id}/assays/{assay.id}/dose-response-workflow"
    )

    submitted = client.post(endpoint, headers=headers, json=body)
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["workflow_run_id"]
    assert submitted.json()["state"] == "ready"
    assert submitted.json()["measurement_count"] == 21

    replay = client.post(endpoint, headers=headers, json=body)
    assert replay.status_code == 202, replay.text
    assert replay.json()["workflow_run_id"] == run_id
    assert replay.json()["idempotent_replay"] is True
    assert db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == run_id
    ).count() == 1

    _patch_workflow_sessions(db, monkeypatch)
    queued = scientific_workflow_dispatcher.dispatch_ready_workflows_once(
        max_runs=20, max_steps_per_run=10
    )
    assert queued == 1
    db.expire_all()
    step = db.query(ScientificWorkflowStep).filter_by(
        workflow_run_id=run_id
    ).one()
    assert step.step_key == "dose_response_4pl"
    assert step.task_name == "experimental.run_4pl"
    assert step.queue_name == "workflow"
    assert step.args == [study.id, assay.id, user.id]
    assert step.kwargs["parameters"] == body

    execution = scientific_workflow_tasks.execute_claimed_step(
        step.id, step.lease_token, "phase1e-test-worker"
    )
    assert execution["status"] == "completed"
    assert execution["output"]["status"] == "completed"
    assert execution["output"]["algorithm_version"] == "N3XORA_4PL_CLUSTER_BOOTSTRAP_V1"
    assert len(execution["output"]["input_sha256"]) == 64
    assert len(execution["output"]["output_sha256"]) == 64

    db.expire_all()
    run = db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == run_id
    ).one()
    step = db.query(ScientificWorkflowStep).filter_by(
        workflow_run_id=run_id
    ).one()
    analysis = db.query(ResearchAssayAnalysis).filter(
        ResearchAssayAnalysis.assay_id == assay.id
    ).one()
    assert run.state == "succeeded"
    assert step.state == "succeeded"
    assert analysis.status == "completed"
    assert analysis.algorithm_version == "N3XORA_4PL_CLUSTER_BOOTSTRAP_V1"
    assert analysis.result["data_summary"]["complete_biological_replicates"] == 3
    assert analysis.result["confidence_interval"]["level"] == 0.95
    evidence = db.query(ResearchEvidence).filter(
        ResearchEvidence.object_type == "assay_analysis",
        ResearchEvidence.object_id == str(analysis.id),
    ).one()
    assert evidence.evidence_status == "computational_hypothesis"
    assert evidence.evidence_grade == "D"
    assert evidence.input_sha256 == analysis.input_sha256
    assert evidence.output_sha256 == analysis.output_sha256

    # The legacy synchronous route now calls the same service and returns the
    # already persisted immutable analysis rather than calculating a duplicate.
    sync = client.post(
        f"/api/v1/research/studies/{study.id}/assays/{assay.id}/dose-response-analysis",
        headers=headers,
        json=body,
    )
    assert sync.status_code == 201, sync.text
    assert sync.json()["idempotent_replay"] is True
    assert sync.json()["analysis"]["id"] == analysis.id
    assert db.query(ResearchAssayAnalysis).filter(
        ResearchAssayAnalysis.assay_id == assay.id
    ).count() == 1


def test_4pl_workflow_requires_curator_acceptance(client, db):
    headers = _auth_headers(
        client, "phase1e_4pl_unreviewed", "phase1e-4pl-unreviewed@nexora.test"
    )
    user = db.query(User).filter(User.username == "phase1e_4pl_unreviewed").one()
    study = ResearchStudy(
        user_id=user.id,
        name="Unreviewed 4PL dossier",
        disease="malaria",
        pathogen="Plasmodium falciparum",
        country_focus="Mali",
        region_scope="Sahel",
        objective="Test review gate.",
        hypothesis="No execution before acceptance.",
        status="analysis",
        metadata_json={},
    )
    db.add(study)
    db.flush()
    assay = ResearchAssay(
        study_id=study.id,
        created_by=user.id,
        assay_type="dose_response",
        assay_name="Awaiting curator",
        test_article="Compound U",
        source_kind="lab_generated",
        method_name="Growth-inhibition response",
        status="submitted",
        review_decision=None,
    )
    db.add(assay)
    db.commit()

    response = client.post(
        f"/api/v1/research/studies/{study.id}/assays/{assay.id}/dose-response-workflow",
        headers=headers,
        json={
            "response_name": "growth_inhibition",
            "response_unit": "% inhibition",
            "concentration_unit": "uM",
            "response_direction": "increasing",
            "endpoint_name": "IC50",
            "bootstrap_iterations": 500,
        },
    )
    assert response.status_code == 409
    assert db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.scope_type == "research_assay",
        ScientificWorkflowRun.scope_id == str(assay.id),
    ).count() == 0

def test_4pl_worker_rechecks_curator_acceptance_after_submission(client, db, monkeypatch):
    headers = _auth_headers(
        client, "phase1e_4pl_revoked", "phase1e-4pl-revoked@nexora.test"
    )
    user = db.query(User).filter(User.username == "phase1e_4pl_revoked").one()
    study, assay = _accepted_assay(db, user)
    response = client.post(
        f"/api/v1/research/studies/{study.id}/assays/{assay.id}/dose-response-workflow",
        headers=headers,
        json={
            "response_name": "growth_inhibition",
            "response_unit": "% inhibition",
            "concentration_unit": "uM",
            "response_direction": "increasing",
            "endpoint_name": "IC50",
            "sample_label": "Compound Phase1E",
            "bootstrap_iterations": 500,
            "random_seed": 31,
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["workflow_run_id"]

    # Simulate the dossier being rejected or otherwise invalidated after the
    # request was queued but before a worker claims the persisted step.
    assay.review_decision = "rejected"
    db.commit()

    _patch_workflow_sessions(db, monkeypatch)
    queued = scientific_workflow_dispatcher.dispatch_ready_workflows_once(
        max_runs=20, max_steps_per_run=10
    )
    assert queued == 1
    db.expire_all()
    step = db.query(ScientificWorkflowStep).filter_by(
        workflow_run_id=run_id
    ).one()
    execution = scientific_workflow_tasks.execute_claimed_step(
        step.id, step.lease_token, "phase1e-revoked-worker"
    )
    assert execution["status"] == "failed"

    db.expire_all()
    run = db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == run_id
    ).one()
    assert run.state == "failed"
    assert db.query(ResearchAssayAnalysis).filter(
        ResearchAssayAnalysis.assay_id == assay.id
    ).count() == 0

