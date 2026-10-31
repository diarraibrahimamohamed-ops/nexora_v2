"""Integration checks for transactional outbox adoption in generic async routes."""
from app.models.docking import DockingJob
from app.models.hsa import HSAJob
from app.models.rna_structure_job import RnaStructureJob
from app.models.sequence import Analysis
from app.models.workflow import ResearchTaskOutbox
from app.config import settings


def _authenticated_headers(client, username, email):
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


def test_large_sequence_analysis_uses_transactional_outbox(client, db):
    response = client.post("/api/v1/analysis/sequence", json={
        "sequence": "A" * 10001,
        "force_async": True,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["async"] is True
    assert payload["dispatch"] == "transactional_outbox"

    analysis = db.query(Analysis).filter(Analysis.id == payload["analysis_id"]).one()
    event = db.query(ResearchTaskOutbox).filter_by(event_key=payload["task_id"]).one()
    assert analysis.status.value == "pending"
    assert event.task_name == "analysis.run"
    assert event.queue_name == "analysis"
    assert event.args[0] == analysis.id
    assert analysis.data["outbox_event_key"] == event.event_key


def test_generic_docking_uses_transactional_outbox(client, db):
    headers = _authenticated_headers(client, "outbox_docking_user", "outbox-docking@nexora.test")
    response = client.post("/api/v1/docking/submit", headers=headers, json={
        "protein_sequence": "MKK",
        "ligand_smiles": "CCO",
        "max_pockets": 1,
    })
    assert response.status_code == 202
    payload = response.json()
    assert payload["dispatch"] == "transactional_outbox"

    job = db.query(DockingJob).filter(DockingJob.id == payload["job_id"]).one()
    event = db.query(ResearchTaskOutbox).filter_by(event_key=payload["task_id"]).one()
    assert job.status.value == "pending"
    assert job.celery_task_id == event.event_key
    assert event.task_name == "docking.run_vina"
    assert event.queue_name == "docking"
    assert event.args[0] == job.id


def test_hsa_submit_uses_transactional_outbox(client, db):
    headers = _authenticated_headers(client, "outbox_hsa_user", "outbox-hsa@nexora.test")
    response = client.post("/api/v1/hsa/submit", headers=headers, json={
        "protein_sequence": "MKK",
        "env_factors": {"temperature": 37},
    })
    assert response.status_code == 202
    payload = response.json()
    assert payload["dispatch"] == "transactional_outbox"

    job = db.query(HSAJob).filter(HSAJob.id == payload["job_id"]).one()
    event = db.query(ResearchTaskOutbox).filter_by(event_key=payload["task_id"]).one()
    assert job.celery_task_id == event.event_key
    assert event.task_name == "hsa.run"
    assert event.queue_name == "qsa"
    assert event.args[0] == job.id


def test_rna_structure_submit_uses_transactional_outbox(client, db, monkeypatch):
    monkeypatch.setattr(settings, "TAMARIND_API_KEY", "unit-test-key")
    headers = _authenticated_headers(client, "outbox_rna_user", "outbox-rna@nexora.test")
    response = client.post("/api/v1/rna-structure/submit", headers=headers, json={
        "sequence": "ACGUACGU",
    })
    assert response.status_code == 202
    payload = response.json()
    assert payload["dispatch"] == "transactional_outbox"

    job = db.query(RnaStructureJob).filter(RnaStructureJob.id == payload["job_id"]).one()
    event = db.query(ResearchTaskOutbox).filter_by(event_key=payload["task_id"]).one()
    assert job.celery_task_id == event.event_key
    assert job.metadata_json["outbox_event_key"] == event.event_key
    assert event.task_name == "rna_structure.predict_secondary"
    assert event.queue_name == "structure"
    assert event.args == [job.id]


def test_afribio_async_alignment_uses_transactional_outbox(client, db):
    response = client.post("/api/v1/analysis/afribio/align", json={
        "query": "A" * 1200,
        "reference": "A" * 1200,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "async"
    assert payload["dispatch"] == "transactional_outbox"
    event = db.query(ResearchTaskOutbox).filter_by(event_key=payload["outbox_event_key"]).one()
    assert event.task_name == "analysis.run_afribio_alignment"
    assert event.queue_name == "analysis"
    assert event.args[0] == payload["job_id"]


def test_afribio_async_phylogeny_uses_transactional_outbox(client, db):
    response = client.post("/api/v1/analysis/afribio/phylo", json={
        "force_async": True,
        "sequences": [
            {"name": "sample-a", "sequence": "ACGTACGT"},
            {"name": "sample-b", "sequence": "ACGTTCGT"},
        ],
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "async"
    assert payload["dispatch"] == "transactional_outbox"
    event = db.query(ResearchTaskOutbox).filter_by(event_key=payload["outbox_event_key"]).one()
    assert event.task_name == "analysis.run_afribio_phylo"
    assert event.queue_name == "analysis"
    assert event.args[0] == payload["job_id"]
