import hashlib
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.hsa import HSAJob
from app.models.research import ResearchStructureJob, ResearchStudy, ResearchTarget
from app.models.rna_structure_job import RnaStructureJob
from app.models.scientific_workflow import ScientificWorkflowRun, ScientificWorkflowStep
from app.models.user import User
from app.services import scientific_workflow_adapters as adapters
from app.services.scientific_workflow_policy import TASK_CONTRACTS, WorkflowDefinition, WorkflowStepDefinition


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


def _patch_adapter_session(db, monkeypatch):
    factory = sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(adapters, "SessionLocal", factory)
    return factory


def test_phase1d_task_contracts_are_allowlisted():
    assert TASK_CONTRACTS["hsa.run"].queue_name == "qsa"
    assert TASK_CONTRACTS["qsa.run"].queue_name == "qsa"
    assert TASK_CONTRACTS["rna_structure.predict_secondary"].queue_name == "structure"
    assert TASK_CONTRACTS["research.resolve_structure"].queue_name == "structure"

    for name, queue in (
        ("hsa.run", "qsa"),
        ("qsa.run", "qsa"),
        ("rna_structure.predict_secondary", "structure"),
        ("research.resolve_structure", "structure"),
    ):
        definition = WorkflowDefinition(
            key=f"test.{name}",
            version="1.0.0",
            steps=(WorkflowStepDefinition("execute", name, queue),),
        )
        assert definition.steps[0].task_name == name


def test_hsa_and_qsa_workflow_submissions_are_persisted_and_idempotent(client, db):
    headers = _auth_headers(client, "phase1d_hsa_user", "phase1d-hsa@nexora.test")
    body = {
        "protein_sequence": "ACGTGCGTACGTACGT",
        "env_factors": {"temperature": 37.0, "ph": 7.4, "stress_factor": 1.0},
    }

    first = client.post(
        "/api/v1/scientific-workflows/hsa",
        headers={**headers, "Idempotency-Key": "phase1d-hsa-key"},
        json=body,
    )
    assert first.status_code == 202, first.text
    replay = client.post(
        "/api/v1/scientific-workflows/hsa",
        headers={**headers, "Idempotency-Key": "phase1d-hsa-key"},
        json=body,
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["workflow_run_id"] == first.json()["workflow_run_id"]
    assert replay.json()["idempotent_replay"] is True

    hsa_job_id = first.json()["hsa_job_id"]
    hsa_job = db.query(HSAJob).filter(HSAJob.id == hsa_job_id).one()
    assert hsa_job.status == "pending"
    hsa_run = db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == first.json()["workflow_run_id"]
    ).one()
    hsa_step = db.query(ScientificWorkflowStep).filter_by(workflow_run_id=hsa_run.id).one()
    assert hsa_run.workflow_key == "nexora.hsa-stability"
    assert hsa_step.task_name == "hsa.run"
    assert hsa_step.queue_name == "qsa"
    assert hsa_run.input_manifest["sequence_sha256"] == hashlib.sha256(
        body["protein_sequence"].encode("ascii")
    ).hexdigest()
    assert db.query(HSAJob).filter(HSAJob.user_id == hsa_job.user_id).count() == 1

    qsa = client.post(
        "/api/v1/scientific-workflows/qsa",
        headers=headers,
        json=body,
    )
    assert qsa.status_code == 202, qsa.text
    qsa_run = db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == qsa.json()["workflow_run_id"]
    ).one()
    qsa_step = db.query(ScientificWorkflowStep).filter_by(workflow_run_id=qsa_run.id).one()
    assert qsa_run.workflow_key == "nexora.qsa-compatibility"
    assert qsa_step.task_name == "qsa.run"
    assert qsa_step.queue_name == "qsa"


def test_hsa_workflow_rejects_non_nucleotide_sequence(client):
    headers = _auth_headers(client, "phase1d_hsa_invalid", "phase1d-hsa-invalid@nexora.test")
    response = client.post(
        "/api/v1/scientific-workflows/hsa",
        headers=headers,
        json={"protein_sequence": "MKTAYIAK", "env_factors": {}},
    )
    assert response.status_code == 422


def test_rna_workflow_submission_is_idempotent_and_requires_provider_key(client, db, monkeypatch):
    headers = _auth_headers(client, "phase1d_rna_user", "phase1d-rna@nexora.test")
    body = {"sequence": "ACGUACGUACGU"}
    missing = client.post(
        "/api/v1/scientific-workflows/rna-secondary-structure",
        headers=headers,
        json=body,
    )
    assert missing.status_code == 503

    monkeypatch.setattr(settings, "TAMARIND_API_KEY", "phase1d-test-key")
    first = client.post(
        "/api/v1/scientific-workflows/rna-secondary-structure",
        headers={**headers, "Idempotency-Key": "phase1d-rna-key"},
        json=body,
    )
    assert first.status_code == 202, first.text
    replay = client.post(
        "/api/v1/scientific-workflows/rna-secondary-structure",
        headers={**headers, "Idempotency-Key": "phase1d-rna-key"},
        json=body,
    )
    assert replay.status_code == 202
    assert replay.json()["workflow_run_id"] == first.json()["workflow_run_id"]
    job = db.query(RnaStructureJob).filter(
        RnaStructureJob.id == first.json()["rna_structure_job_id"]
    ).one()
    run = db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == first.json()["workflow_run_id"]
    ).one()
    step = db.query(ScientificWorkflowStep).filter_by(workflow_run_id=run.id).one()
    assert job.status == "pending"
    assert job.metadata_json["scientific_workflow_run_id"] == run.id
    assert step.task_name == "rna_structure.predict_secondary"
    assert step.args == [job.id]
    assert step.queue_name == "structure"


def test_research_structure_submission_respects_study_approval_gate(client, db):
    headers = _auth_headers(client, "phase1d_structure_user", "phase1d-structure@nexora.test")
    user = db.query(User).filter(User.username == "phase1d_structure_user").one()
    study = ResearchStudy(
        user_id=user.id,
        name="Phase 1D structure test",
        disease="malaria",
        pathogen="Plasmodium falciparum",
        country_focus="Mali",
        region_scope="Sahel",
        objective="Test controlled workflow submission",
        hypothesis="Structure resolver requires the approved analysis state",
        status="draft",
        metadata_json={},
    )
    db.add(study)
    db.flush()
    target = ResearchTarget(
        study_id=study.id,
        protein_sequence="MKTAYIAKQRQISFVKSHFSRQ",
        structure_source="sequence_resolved",
        structure_status="not_generated",
        provenance={},
        structure_validation={},
    )
    db.add(target)
    db.commit()

    blocked = client.post(
        f"/api/v1/scientific-workflows/research-structure/{study.id}/{target.id}",
        headers=headers,
        json={"provider": "auto_rcsb"},
    )
    assert blocked.status_code == 409

    study.status = "analysis"
    db.commit()
    accepted = client.post(
        f"/api/v1/scientific-workflows/research-structure/{study.id}/{target.id}",
        headers={**headers, "Idempotency-Key": "phase1d-structure-key"},
        json={"provider": "auto_rcsb"},
    )
    assert accepted.status_code == 202, accepted.text
    structure_job = db.query(ResearchStructureJob).filter(
        ResearchStructureJob.id == accepted.json()["structure_job_id"]
    ).one()
    run = db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == accepted.json()["workflow_run_id"]
    ).one()
    step = db.query(ScientificWorkflowStep).filter_by(workflow_run_id=run.id).one()
    assert structure_job.provider == "auto_rcsb"
    assert structure_job.metadata_json["scientific_workflow_run_id"] == run.id
    assert step.task_name == "research.resolve_structure"
    assert step.args == [structure_job.id]
    assert step.queue_name == "structure"


def test_hsa_adapter_requires_successful_persisted_result(db, monkeypatch):
    factory = _patch_adapter_session(db, monkeypatch)
    user = db.query(User).filter(User.username == "phase1d_hsa_user").one()
    good = HSAJob(
        user_id=user.id,
        protein_sequence="ACGTACGT",
        env_factors={},
        status="completed",
        progress=100,
        results={
            "success": True,
            "method": "HSA",
            "formula_version": "HSA-2.0-transparent-index",
            "sequence_length": 8,
            "global_score": 75,
            "vulnerability_index": 25.0,
            "disclaimer": "Heuristic only.",
        },
    )
    bad = HSAJob(
        user_id=user.id,
        protein_sequence="ACGTACGT",
        env_factors={},
        status="completed",
        progress=100,
        results={"success": False, "error": "Invalid DNA sequence."},
    )
    db.add_all([good, bad])
    db.commit()
    monkeypatch.setattr(adapters, "_run_task_eager", lambda *a, **k: {"status": "completed"})

    result = adapters.run_hsa_analysis([good.id], {})
    assert result["status"] == "completed"
    assert result["formula_version"] == "HSA-2.0-transparent-index"
    assert len(result["result_sha256"]) == 64

    with pytest.raises(adapters.WorkflowAdapterError, match="HSA did not persist|Invalid DNA"):
        adapters.run_hsa_analysis([bad.id], {})


def test_rna_adapter_checks_artifact_containment_and_sha256(db, monkeypatch, tmp_path):
    factory = _patch_adapter_session(db, monkeypatch)
    user = db.query(User).filter(User.username == "phase1d_rna_user").one()
    root = tmp_path / "rna_secondary" / "job_7001"
    root.mkdir(parents=True)
    artifact = root / "secondary_structure.dbn"
    artifact.write_text(">test\n....\n", encoding="utf-8")
    job = RnaStructureJob(
        id=7001,
        user_id=user.id,
        sequence="ACGUACGU",
        status="completed",
        progress=100,
        artifact_path=str(artifact),
        metadata_json={"provider": "test"},
    )
    db.add(job)
    db.commit()
    monkeypatch.setattr(settings, "TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(adapters, "_run_task_eager", lambda *a, **k: {"success": True})

    result = adapters.run_rna_secondary_structure([job.id], {})
    assert result["status"] == "completed"
    assert result["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()

    job.artifact_path = str(tmp_path / "outside.dbn")
    db.commit()
    with pytest.raises(adapters.WorkflowAdapterError, match="outside its job-specific"):
        adapters.run_rna_secondary_structure([job.id], {})


def test_research_structure_adapter_requires_validated_contained_pdb(db, monkeypatch, tmp_path):
    factory = _patch_adapter_session(db, monkeypatch)
    user = db.query(User).filter(User.username == "phase1d_structure_user").one()
    study = db.query(ResearchStudy).filter(ResearchStudy.user_id == user.id).one()
    target = db.query(ResearchTarget).filter(ResearchTarget.study_id == study.id).one()
    root = tmp_path / "research_structures" / f"study_{study.id}" / f"target_{target.id}"
    root.mkdir(parents=True)
    structure_path = root / "resolved.pdb"
    structure_path.write_text("HEADER    TEST PDB\nEND\n", encoding="utf-8")
    target.structure_path = str(structure_path)
    target.structure_source = "experimental_pdb"
    target.structure_validation = {
        "status": "accepted_for_docking_review",
        "eligible_for_docking_review": True,
        "sequence_match": {"best_chain": {"identity": 1.0, "coverage": 1.0}},
    }
    job = ResearchStructureJob(
        study_id=study.id,
        target_id=target.id,
        provider="auto_rcsb",
        status="completed",
        progress=100,
        metadata_json={},
    )
    db.add(job)
    db.commit()
    monkeypatch.setattr(settings, "TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(adapters, "_run_task_eager", lambda *a, **k: {"success": True})

    result = adapters.run_research_structure_resolution([job.id], {})
    assert result["status"] == "validated"
    assert result["eligible_for_docking_review"] is True
    assert result["structure_sha256"] == hashlib.sha256(structure_path.read_bytes()).hexdigest()

    target.structure_validation = {"status": "sequence_mismatch", "eligible_for_docking_review": False}
    db.commit()
    with pytest.raises(adapters.WorkflowAdapterError, match="did not pass the sequence/coverage gate"):
        adapters.run_research_structure_resolution([job.id], {})
