from datetime import date

import pytest

from app.config import settings
from app.main import app
from app.models.experimental import ResearchAssayAnalysis, ResearchAssayEndpoint, ResearchAssayMeasurement
from app.models.evidence import ResearchEvidence
from app.models.user import User
from app.services.auth_service import require_admin


def _token(client, username, email):
    registered = client.post("/api/v1/auth/register", json={
        "username": username, "email": email, "password": "password123"
    })
    assert registered.status_code == 201
    login = client.post("/api/v1/auth/token", data={
        "username": email, "password": "password123"
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert login.status_code == 200
    return login.json()["access_token"]


def _approved_study(client, headers):
    response = client.post("/api/v1/research/studies", headers=headers, json={
        "name": "Validation statistique expérimentale",
        "disease": "malaria", "pathogen": "Plasmodium falciparum",
        "country_focus": "Mali", "region_scope": "Sahel",
        "objective": "Estimer une réponse concentration-effet à partir de réplicats biologiques.",
        "hypothesis": "Le composé modifie le readout de croissance.",
    })
    assert response.status_code == 200
    study_id = response.json()["study"]["id"]
    target = client.post(f"/api/v1/research/studies/{study_id}/targets", headers=headers, json={
        "state": "wt", "protein_name": "Analytics test target",
        "protein_sequence": "MKTAYIAKQRQISFVKSHFSRQ",
        "structure_source": "sequence_resolved",
    })
    assert target.status_code == 200
    for action in ("submit_for_review", "approve_data"):
        result = client.post(
            f"/api/v1/research/studies/{study_id}/workflow/transitions",
            headers=headers, json={"action": action},
        )
        assert result.status_code == 200
    return study_id, target.json()["target_id"]


def _dose_response_csv():
    lines = [
        "sample_role,sample_label,replicate_id,replicate_kind,concentration_value,"
        "concentration_unit,response_name,response_value,response_unit"
    ]
    doses = (0.03, 0.1, 0.3, 1, 3, 10, 30)
    offsets = {"bio-1": -0.5, "bio-2": 0.0, "bio-3": 0.5}
    for replicate, offset in offsets.items():
        for dose in doses:
            response = 100.0 / (1.0 + (3.0 / dose) ** 1.2) + offset
            lines.append(
                f"test_sample,Compound Z,{replicate},biological,{dose},uM,"
                f"growth_inhibition,{response},% inhibition"
            )
    lines.append("vehicle_control,Vehicle,control-1,biological,,,growth_inhibition,0,% inhibition")
    return ("\n".join(lines) + "\n").encode("utf-8")


def test_import_preview_confirmation_review_and_persisted_4pl_analysis(
    client, db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    token = _token(client, "analytics_owner", "analytics-owner@nexora.test")
    headers = {"Authorization": f"Bearer {token}"}
    study_id, target_id = _approved_study(client, headers)

    created = client.post(f"/api/v1/research/studies/{study_id}/assays", headers=headers, json={
        "assay_type": "dose_response",
        "assay_name": "Compound Z dose response",
        "test_article": "Compound Z",
        "target_id": target_id,
        "source_kind": "lab_generated",
        "laboratory": "N3XORA validation laboratory",
        "method_name": "Growth inhibition readout",
        "method_version": "SOP-4PL-1",
        "assay_date": date.today().isoformat(),
        "organism": "Plasmodium falciparum",
        "strain": "3D7",
        "conditions": {"temperature_c": 37, "exposure_hours": 48},
    })
    assert created.status_code == 201
    assay_id = created.json()["assay"]["id"]
    source = _dose_response_csv()

    preview = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/measurements/import-preview",
        headers=headers,
        files={"file": ("dose_response.csv", source, "text/csv")},
    )
    assert preview.status_code == 200
    preview_data = preview.json()
    assert preview_data["row_count"] == 22
    assert preview_data["error_count"] == 0
    assert len(preview_data["source_sha256"]) == 64
    assert db.query(ResearchAssayMeasurement).filter(
        ResearchAssayMeasurement.assay_id == assay_id
    ).count() == 0

    mismatch = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/measurements/import?expected_sha256=" + "0" * 64,
        headers=headers,
        files={"file": ("dose_response.csv", source, "text/csv")},
    )
    assert mismatch.status_code == 409
    assert db.query(ResearchAssayMeasurement).filter(
        ResearchAssayMeasurement.assay_id == assay_id
    ).count() == 0

    imported = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/measurements/import"
        f"?expected_sha256={preview_data['source_sha256']}",
        headers=headers,
        files={"file": ("dose_response.csv", source, "text/csv")},
    )
    assert imported.status_code == 201
    assert imported.json()["created_count"] == 22
    assert imported.json()["attachment"]["sha256"] == preview_data["source_sha256"]

    submitted = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/submit",
        headers=headers, json={"submission_note": "CSV original archivé; mesures importées après aperçu."},
    )
    assert submitted.status_code == 200
    assert submitted.json()["assay"]["status"] == "submitted"

    reviewer = User(
        username="analytics_reviewer", email="analytics-reviewer@nexora.test",
        password="not-used", status="active", is_admin=True,
    )
    db.add(reviewer)
    db.commit()
    app.dependency_overrides[require_admin] = lambda: reviewer
    try:
        reviewed = client.post(
            f"/api/v1/research/studies/{study_id}/assays/{assay_id}/review",
            json={
                "decision": "accepted_for_analysis",
                "comparability_status": "not_assessed",
                "rationale": "Dossier complet et mesures brutes traçables.",
            },
        )
    finally:
        app.dependency_overrides.pop(require_admin, None)
    assert reviewed.status_code == 200

    analysis_payload = {
        "response_name": "growth_inhibition",
        "direction": "increasing",
        "endpoint_name": "IC50",
        "bootstrap_iterations": 100,
        "random_seed": 17,
    }
    analysis_url = (
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/analyses/dose-response"
    )
    analysis = client.post(
        analysis_url,
        headers=headers,
        json=analysis_payload,
    )
    assert analysis.status_code == 201, analysis.text
    result = analysis.json()["analysis"]["result"]
    assert result["estimate"] == pytest.approx(3.0, rel=0.18)
    assert result["confidence_interval"]["status"] == "available"
    assert result["data_summary"]["complete_biological_replicates"] == 3
    assert len(result["input_sha256"]) == 64
    assert len(analysis.json()["analysis"]["output_sha256"]) == 64
    manifest = result["provenance"]["manifest"]
    assert manifest["schema"] == "nexora.scientific-provenance/v1"
    assert manifest["manifest_type"] == "scientific_analysis"
    assert manifest["algorithm"]["version"] == "1.0.0"
    assert len(manifest["manifest_sha256"]) == 64
    assert manifest["output"]["sha256"] == analysis.json()["analysis"]["output_sha256"]

    persisted = db.query(ResearchAssayAnalysis).filter(
        ResearchAssayAnalysis.assay_id == assay_id
    ).one()
    assert persisted.input_sha256 == result["input_sha256"]
    assert persisted.status == "completed"
    assert len(persisted.analysis_key) == 64
    assert len(persisted.output_sha256) == 64
    evidence = db.query(ResearchEvidence).filter(
        ResearchEvidence.object_type == "assay_analysis",
        ResearchEvidence.object_id == str(persisted.id),
    ).one()
    assert evidence.evidence_grade == "D"
    assert evidence.evidence_status == "computational_hypothesis"
    assert evidence.input_sha256 == persisted.input_sha256
    assert evidence.output_sha256 == persisted.output_sha256
    assert evidence.metadata_json["provenance_manifest_sha256"] == manifest["manifest_sha256"]
    endpoint = db.query(ResearchAssayEndpoint).filter(
        ResearchAssayEndpoint.assay_id == assay_id,
        ResearchAssayEndpoint.origin == "derived_from_raw_data",
    ).one()
    assert endpoint.endpoint_name == "IC50"
    assert float(endpoint.value) == pytest.approx(result["estimate"])
    assert float(endpoint.confidence_low) == pytest.approx(result["confidence_interval"]["lower"])
    assert float(endpoint.confidence_high) == pytest.approx(result["confidence_interval"]["upper"])

    history = client.get(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/analyses",
        headers=headers,
    )
    assert history.status_code == 200
    assert history.json()["analyses"][0]["id"] == persisted.id
    assert history.json()["analyses"][0]["provenance_manifest_sha256"] == manifest["manifest_sha256"]

    replay = client.post(analysis_url, headers=headers, json=analysis_payload)
    assert replay.status_code == 201, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["analysis_id"] == persisted.id
    assert db.query(ResearchAssayAnalysis).filter(
        ResearchAssayAnalysis.assay_id == assay_id
    ).count() == 1
    assert db.query(ResearchAssayEndpoint).filter(
        ResearchAssayEndpoint.assay_id == assay_id,
        ResearchAssayEndpoint.origin == "derived_from_raw_data",
    ).count() == 1


def test_4pl_analysis_is_blocked_until_curator_accepts_assay(client, db):
    token = _token(client, "analytics_gate", "analytics-gate@nexora.test")
    headers = {"Authorization": f"Bearer {token}"}
    study_id, target_id = _approved_study(client, headers)
    created = client.post(f"/api/v1/research/studies/{study_id}/assays", headers=headers, json={
        "assay_type": "dose_response", "assay_name": "Not reviewed",
        "test_article": "Compound Q", "target_id": target_id,
        "source_kind": "lab_generated", "laboratory": "Test lab",
        "method_name": "Growth", "assay_date": date.today().isoformat(),
        "organism": "Plasmodium falciparum", "conditions": {"temperature_c": 37},
    })
    assert created.status_code == 201
    assay_id = created.json()["assay"]["id"]
    response = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/analyses/dose-response",
        headers=headers,
        json={"response_name": "growth", "direction": "increasing"},
    )
    assert response.status_code == 409
