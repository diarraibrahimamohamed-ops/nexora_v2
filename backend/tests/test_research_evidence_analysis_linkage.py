"""Verified links from asynchronous sequence analyses into ResearchEvidence."""

import hashlib

from app.models.evidence import ResearchEvidence
from app.models.research import ResearchStudy
from app.models.sequence import Analysis, AnalysisStatus, Sequence
from app.services.scientific_provenance import (
    build_analysis_manifest,
    seal_manifest,
    sha256_json,
)


def _owner_and_study(client):
    registered = client.post("/api/v1/auth/register", json={
        "username": "evidence_link_owner",
        "email": "evidence-link-owner@nexora.test",
        "password": "password123",
    })
    assert registered.status_code == 201
    login = client.post(
        "/api/v1/auth/token",
        data={"username": "evidence-link-owner@nexora.test", "password": "password123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    study_response = client.post(
        "/api/v1/research/studies",
        headers=headers,
        json={
            "name": "Verified sequence evidence",
            "objective": "Link a reproducible sequence analysis to a research study.",
            "hypothesis": "Sequence composition can be characterized computationally.",
        },
    )
    assert study_response.status_code == 200
    return headers, study_response.json()["study"]["id"]


def _completed_analysis(db, user_id):
    sequence_text = "ATGCGTACGTAGCTAGCTAGCTAG"
    input_sha256 = hashlib.sha256(sequence_text.encode("ascii")).hexdigest()
    analysis = Analysis(
        user_id=user_id,
        name="sequence-evidence-fixture",
        type="DNA",
        status=AnalysisStatus.completed,
        data={},
    )
    db.add(analysis)
    db.flush()
    source = Sequence(
        analysis_id=analysis.id,
        header="primary_input",
        sequence=sequence_text,
        length=len(sequence_text),
    )
    db.add(source)
    db.flush()

    input_manifest = seal_manifest(
        {
            "analysis_id": analysis.id,
            "input_sequence": {
                "object_type": "sequences",
                "object_id": str(source.id),
                "header": source.header,
                "length": source.length,
                "sha256": input_sha256,
                "alphabet": "DNA/IUPAC-ACGTN",
            },
        },
        manifest_type="scientific_analysis_input",
    )
    result = {"length": len(sequence_text), "gc_content": 50.0, "mode": "async_server"}
    output_sha256 = sha256_json(result)
    provenance = build_analysis_manifest(
        algorithm="N3XORA_SEQUENCE_CHUNK_ANALYSIS",
        algorithm_version="N3XORA_SEQUENCE_CHUNK_ANALYSIS_V2",
        inputs=[{
            "role": "primary_sequence",
            "object_type": "sequences",
            "object_id": str(source.id),
            "sha256": input_sha256,
            "length": source.length,
            "alphabet": "DNA/IUPAC-ACGTN",
        }],
        parameters={"chunk_size": 20000},
        output_sha256=output_sha256,
        interpretation_limit="Composition and translation only; no clinical inference.",
    )
    analysis.data = {
        "mode": "async_server",
        "input_sequence_sha256": input_sha256,
        "scientific_input_manifest": input_manifest,
        "scientific_provenance": provenance,
        "scientific_output_sha256": output_sha256,
        "async_sequence_result": result,
    }
    db.commit()
    db.refresh(analysis)
    return analysis


def test_completed_analysis_creates_grade_d_evidence_and_is_idempotent(client, db):
    headers, study_id = _owner_and_study(client)
    user = db.query(ResearchStudy).filter_by(id=study_id).one().user
    analysis = _completed_analysis(db, user.id)

    endpoint = f"/api/v1/research/studies/{study_id}/evidence/from-analysis/{analysis.id}"
    created = client.post(endpoint, headers=headers)
    assert created.status_code == 201
    payload = created.json()
    evidence = payload["evidence"]
    assert payload["idempotent_replay"] is False
    assert evidence["object_type"] == "sequence"
    assert evidence["object_id"] == str(analysis.id)
    assert evidence["evidence_kind"] == "nexora_sequence_analysis"
    assert evidence["evidence_status"] == "computational_hypothesis"
    assert evidence["evidence_grade"] == "D"
    assert evidence["review_status"] == "pending_review"
    assert evidence["qualified_evidence_status"] == "unreviewed_claim"
    assert evidence["input_sha256"] == analysis.data["input_sequence_sha256"]
    assert evidence["output_sha256"] == analysis.data["scientific_output_sha256"]
    assert evidence["metadata"]["integrity_at_link_time"] == {
        "input": "verified",
        "provenance": "verified",
        "result": "verified",
    }
    assert "pas une validation" in evidence["claim"]

    replay = client.post(endpoint, headers=headers)
    assert replay.status_code == 201
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["evidence"]["id"] == evidence["id"]
    assert db.query(ResearchEvidence).filter_by(
        study_id=study_id, evidence_kind="nexora_sequence_analysis"
    ).count() == 1


def test_analysis_evidence_link_rejects_tampered_result(client, db):
    headers, study_id = _owner_and_study(client)
    user = db.query(ResearchStudy).filter_by(id=study_id).one().user
    analysis = _completed_analysis(db, user.id)
    data = dict(analysis.data)
    data["async_sequence_result"] = {**data["async_sequence_result"], "length": 999}
    analysis.data = data
    db.commit()

    response = client.post(
        f"/api/v1/research/studies/{study_id}/evidence/from-analysis/{analysis.id}",
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["integrity"]["result"] == "invalid"
    assert db.query(ResearchEvidence).filter_by(study_id=study_id).count() == 0


def test_analysis_evidence_link_rejects_tampered_input_manifest(client, db):
    headers, study_id = _owner_and_study(client)
    user = db.query(ResearchStudy).filter_by(id=study_id).one().user
    analysis = _completed_analysis(db, user.id)
    data = dict(analysis.data)
    manifest = dict(data["scientific_input_manifest"])
    manifest["manifest_sha256"] = "0" * 64
    data["scientific_input_manifest"] = manifest
    analysis.data = data
    db.commit()

    response = client.post(
        f"/api/v1/research/studies/{study_id}/evidence/from-analysis/{analysis.id}",
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["integrity"]["input"] == "invalid"
    assert db.query(ResearchEvidence).filter_by(study_id=study_id).count() == 0
