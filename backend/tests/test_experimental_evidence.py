from datetime import date

import pytest

from app.config import settings
from app.main import app
from app.models.evidence import ResearchEvidence
from app.models.user import User
from app.services.auth_service import require_admin


def _access_token(client, username, email):
    registered = client.post("/api/v1/auth/register", json={
        "username": username, "email": email, "password": "password123"
    })
    assert registered.status_code == 201
    login = client.post("/api/v1/auth/token", data={
        "username": email, "password": "password123"
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert login.status_code == 200
    return login.json()["access_token"]


def _create_analysis_study(client, headers):
    response = client.post("/api/v1/research/studies", headers=headers, json={
        "name": "Étude essais expérimentaux",
        "disease": "malaria",
        "pathogen": "Plasmodium falciparum",
        "country_focus": "Mali",
        "region_scope": "Sahel",
        "objective": "Décrire une réponse biologique mesurée.",
        "hypothesis": "Le composé testé peut modifier le readout expérimental.",
    })
    assert response.status_code == 200
    study_id = response.json()["study"]["id"]

    target = client.post(f"/api/v1/research/studies/{study_id}/targets", headers=headers, json={
        "state": "wt",
        "protein_name": "Assay test target",
        "protein_sequence": "MKTAYIAKQRQISFVKSHFSRQ",
        "structure_source": "sequence_resolved",
    })
    assert target.status_code == 200
    target_id = target.json()["target_id"]

    submitted = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/transitions",
        headers=headers,
        json={"action": "submit_for_review"},
    )
    assert submitted.status_code == 200
    approved = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/transitions",
        headers=headers,
        json={"action": "approve_data"},
    )
    assert approved.status_code == 200
    return study_id, target_id


def test_experimental_assay_submit_review_and_immutable_measurements(client, db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    owner_token = _access_token(client, "assay_owner", "assay-owner@nexora.test")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    study_id, target_id = _create_analysis_study(client, owner_headers)

    created = client.post(f"/api/v1/research/studies/{study_id}/assays", headers=owner_headers, json={
        "assay_type": "dose_response",
        "assay_name": "Dose-response cellular inhibition",
        "test_article": "Compound X",
        "target_id": target_id,
        "source_kind": "lab_generated",
        "laboratory": "Laboratoire de microbiologie appliquée",
        "operator_name": "Research operator",
        "method_name": "Colorimetric growth inhibition",
        "method_version": "SOP-1.2",
        "protocol_reference": "SOP-ASSAY-001",
        "assay_date": date.today().isoformat(),
        "organism": "Plasmodium falciparum",
        "strain": "3D7",
        "conditions": {"temperature_c": 37, "atmosphere": "controlled", "exposure_hours": 48},
    })
    assert created.status_code == 201
    assay_id = created.json()["assay"]["id"]

    rows = []
    for concentration in (1.0, 10.0, 100.0):
        for replicate in ("bio-1", "bio-2"):
            rows.append({
                "sample_role": "test_sample",
                "sample_label": "Compound X",
                "replicate_id": replicate,
                "replicate_kind": "biological",
                "concentration_value": concentration,
                "concentration_unit": "nM",
                "response_name": "parasite_growth_inhibition",
                "response_value": 100.0 - concentration / (concentration + 15.0) * 100.0,
                "response_unit": "% inhibition",
            })
    rows.extend([
        {
            "sample_role": "vehicle_control", "sample_label": "Vehicle",
            "replicate_id": "control-1", "replicate_kind": "biological",
            "response_name": "parasite_growth_inhibition", "response_value": 0.0,
            "response_unit": "% inhibition",
        },
        {
            "sample_role": "positive_control", "sample_label": "Reference control",
            "replicate_id": "control-2", "replicate_kind": "biological",
            "response_name": "parasite_growth_inhibition", "response_value": 95.0,
            "response_unit": "% inhibition",
        },
    ])
    measures = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/measurements",
        headers=owner_headers,
        json={"measurements": rows},
    )
    assert measures.status_code == 201
    assert measures.json()["created_count"] == 8

    endpoints = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/endpoints",
        headers=owner_headers,
        json={"endpoints": [{
            "endpoint_name": "IC50",
            "value": 15.0,
            "unit": "nM",
            "origin": "derived_from_raw_data",
            "method": "Four-parameter logistic fit",
            "method_version": "fit-pipeline-1.0",
            "confidence_low": 12.0,
            "confidence_high": 18.0,
            "biological_replicates": 2,
            "notes": "Valeur fournie par l'opérateur; Nexora ne recalcule pas cette IC50.",
        }]},
    )
    assert endpoints.status_code == 201

    attachment = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/attachments?role=raw_data",
        headers=owner_headers,
        files={"file": ("assay_raw.csv", b"concentration,response\n1,95\n10,60\n100,5\n", "text/csv")},
    )
    assert attachment.status_code == 201
    attachment_data = attachment.json()["attachment"]
    assert len(attachment_data["sha256"]) == 64
    assert attachment_data["size_bytes"] > 0

    submitted = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/submit",
        headers=owner_headers,
        json={"submission_note": "Données brutes et protocole archivés."},
    )
    assert submitted.status_code == 200
    assert submitted.json()["assay"]["status"] == "submitted"
    manifest_sha256 = submitted.json()["manifest_sha256"]
    assert len(manifest_sha256) == 64

    evidence = db.query(ResearchEvidence).filter(
        ResearchEvidence.study_id == study_id,
        ResearchEvidence.object_type == "assay",
        ResearchEvidence.object_id == str(assay_id),
    ).one()
    assert evidence.evidence_status == "observation"
    assert evidence.evidence_grade == "D"
    assert evidence.input_sha256 == manifest_sha256

    immutable = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/measurements",
        headers=owner_headers,
        json={"measurements": [rows[0]]},
    )
    assert immutable.status_code == 409

    download_url = attachment_data["download_url"]
    downloaded = client.get(download_url, headers=owner_headers)
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"concentration,response")

    reviewer = User(
        username="assay_reviewer",
        email="assay-reviewer@nexora.test",
        password="not-used-in-this-test",
        status="active",
        is_admin=True,
    )
    db.add(reviewer)
    db.commit()
    app.dependency_overrides[require_admin] = lambda: reviewer
    try:
        pending = client.get("/api/v1/research/assays/pending-review")
        assert pending.status_code == 200
        assert any(item["id"] == assay_id for item in pending.json()["assays"])
        review_data = client.get(f"/api/v1/research/assays/{assay_id}/review-data")
        assert review_data.status_code == 200
        assert len(review_data.json()["assay"]["measurements"]) == 8

        reviewed = client.post(
            f"/api/v1/research/studies/{study_id}/assays/{assay_id}/review",
            json={
                "decision": "accepted_for_analysis",
                "comparability_status": "comparable",
                "comparability_group": "pf3d7-growth-inhibition-sop-001",
                "rationale": "Méthode, souche, conditions et endpoint documentés.",
            },
        )

        different = client.post(f"/api/v1/research/studies/{study_id}/assays", headers=owner_headers, json={
            "assay_type": "dose_response",
            "assay_name": "Second assay with different method",
            "test_article": "Compound X",
            "target_id": target_id,
            "source_kind": "literature_reported",
            "source_reference": "PMID:999999",
            "method_name": "Different fluorescence assay",
            "organism": "Plasmodium falciparum",
            "strain": "3D7",
            "conditions": {"temperature_c": 37, "atmosphere": "controlled", "exposure_hours": 48},
        })
        assert different.status_code == 201
        different_id = different.json()["assay"]["id"]
        endpoint = client.post(
            f"/api/v1/research/studies/{study_id}/assays/{different_id}/endpoints",
            headers=owner_headers,
            json={"endpoints": [{
                "endpoint_name": "IC50", "value": 16, "unit": "nM",
                "origin": "literature_reported", "source_reference": "PMID:999999",
            }]},
        )
        assert endpoint.status_code == 201
        assert client.post(
            f"/api/v1/research/studies/{study_id}/assays/{different_id}/submit",
            headers=owner_headers,
            json={},
        ).status_code == 200
        incomparable = client.post(
            f"/api/v1/research/studies/{study_id}/assays/{different_id}/review",
            json={
                "decision": "accepted_for_analysis",
                "comparability_status": "comparable",
                "comparability_group": "pf3d7-growth-inhibition-sop-001",
                "rationale": "Demande de comparaison avec un protocole différent.",
            },
        )
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert incomparable.status_code == 409
    assert reviewed.status_code == 200
    assert reviewed.json()["assay"]["status"] == "reviewed"
    assert reviewed.json()["assay"]["review_decision"] == "accepted_for_analysis"
    assert reviewed.json()["assay"]["comparability_status"] == "comparable"
    db.refresh(evidence)
    assert evidence.evidence_status == "observation"
    assert evidence.evidence_grade == "D"


def test_experimental_assay_submission_requires_controls_and_dose_response_replicates(client):
    token = _access_token(client, "assay_guard", "assay-guard@nexora.test")
    headers = {"Authorization": f"Bearer {token}"}
    study_id, target_id = _create_analysis_study(client, headers)
    created = client.post(f"/api/v1/research/studies/{study_id}/assays", headers=headers, json={
        "assay_type": "dose_response",
        "assay_name": "Dose response missing controls",
        "test_article": "Compound Y",
        "target_id": target_id,
        "source_kind": "lab_generated",
        "laboratory": "Lab testing",
        "method_name": "Growth inhibition",
        "assay_date": date.today().isoformat(),
        "organism": "Plasmodium falciparum",
        "conditions": {"temperature_c": 37},
    })
    assert created.status_code == 201
    assay_id = created.json()["assay"]["id"]
    measures = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/measurements",
        headers=headers,
        json={"measurements": [
            {
                "sample_role": "test_sample", "sample_label": "Compound Y",
                "replicate_id": "bio-1", "concentration_value": 1, "concentration_unit": "uM",
                "response_name": "growth", "response_value": 90, "response_unit": "%",
            },
            {
                "sample_role": "test_sample", "sample_label": "Compound Y",
                "replicate_id": "bio-1", "concentration_value": 10, "concentration_unit": "uM",
                "response_name": "growth", "response_value": 60, "response_unit": "%",
            },
            {
                "sample_role": "test_sample", "sample_label": "Compound Y",
                "replicate_id": "bio-1", "concentration_value": 100, "concentration_unit": "uM",
                "response_name": "growth", "response_value": 10, "response_unit": "%",
            },
        ]},
    )
    assert measures.status_code == 201
    submitted = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/submit",
        headers=headers,
        json={},
    )
    assert submitted.status_code == 409
    blockers = submitted.json()["detail"]["blockers"]
    assert any("contrôle" in item.lower() for item in blockers)
    assert any("réplicat" in item.lower() for item in blockers)


def test_experimental_assay_does_not_allow_cross_study_target_reference(client):
    token = _access_token(client, "assay_scope", "assay-scope@nexora.test")
    headers = {"Authorization": f"Bearer {token}"}
    study_a, target_a = _create_analysis_study(client, headers)
    study_b, target_b = _create_analysis_study(client, headers)
    response = client.post(f"/api/v1/research/studies/{study_a}/assays", headers=headers, json={
        "assay_type": "binding",
        "assay_name": "Cross-study target test",
        "test_article": "Protein ligand",
        "target_id": target_b,
        "source_kind": "lab_generated",
        "laboratory": "Lab",
        "method_name": "Binding assay",
        "assay_date": date.today().isoformat(),
        "organism": "Test organism",
        "conditions": {"buffer": "PBS"},
    })
    assert response.status_code == 422
