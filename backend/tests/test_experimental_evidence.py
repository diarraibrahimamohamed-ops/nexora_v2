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

    outsider_token = _access_token(client, "assay_outsider", "assay-outsider@nexora.test")
    outsider = client.get(download_url, headers={"Authorization": f"Bearer {outsider_token}"})
    assert outsider.status_code == 404

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
    full_dossier = client.get(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}",
        headers=owner_headers,
    )
    assert full_dossier.status_code == 200
    event_types = {event["event_type"] for event in full_dossier.json()["assay"]["events"]}
    assert {"created", "measurements_added", "endpoints_added", "attachment_uploaded", "submitted", "reviewed"} <= event_types
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


def test_4pl_api_persists_reproducible_analysis_and_replays_idempotently(client, db):
    token = _access_token(client, "assay_4pl_user", "assay-4pl@nexora.test")
    headers = {"Authorization": f"Bearer {token}"}
    study_id, target_id = _create_analysis_study(client, headers)
    created = client.post(f"/api/v1/research/studies/{study_id}/assays", headers=headers, json={
        "assay_type": "dose_response",
        "assay_name": "4PL synthetic API validation",
        "test_article": "Compound 4PL",
        "target_id": target_id,
        "source_kind": "lab_generated",
        "laboratory": "Laboratoire de test statistique",
        "operator_name": "CI",
        "method_name": "Growth inhibition readout",
        "method_version": "SOP-4PL-1",
        "protocol_reference": "SOP-4PL-1",
        "assay_date": date.today().isoformat(),
        "organism": "Plasmodium falciparum",
        "strain": "3D7",
        "conditions": {"temperature_c": 37, "exposure_hours": 48},
    })
    assert created.status_code == 201
    assay_id = created.json()["assay"]["id"]

    concentrations = [0.01, 0.1, 0.3, 1.0, 3.0, 10.0, 100.0]
    rows = []
    for replicate_id, shift in (("bio-A", -0.6), ("bio-B", 0.0), ("bio-C", 0.6)):
        for concentration in concentrations:
            response = 1.5 + (98.0 - 1.5) / (1.0 + (concentration / 3.2) ** 1.15) + shift
            rows.append({
                "sample_role": "test_sample",
                "sample_label": "Compound 4PL",
                "replicate_id": replicate_id,
                "replicate_kind": "biological",
                "concentration_value": concentration,
                "concentration_unit": "uM",
                "response_name": "growth_inhibition",
                "response_value": response,
                "response_unit": "% inhibition",
                "timepoint_value": 48,
                "timepoint_unit": "h",
            })
            for well_index, offset in enumerate((-0.2, 0.2), start=1):
                rows.append({
                    "sample_role": "test_sample",
                    "sample_label": "Compound 4PL",
                    "replicate_id": f"well-{replicate_id}-{concentration}-{well_index}",
                    "replicate_kind": "technical",
                    "biological_sample_id": replicate_id,
                    "concentration_value": concentration,
                    "concentration_unit": "uM",
                    "response_name": "growth_inhibition",
                    "response_value": response + offset,
                    "response_unit": "% inhibition",
                    "timepoint_value": 48,
                    "timepoint_unit": "h",
                })
    rows.extend([
        {
            "sample_role": "vehicle_control", "sample_label": "Vehicle",
            "replicate_id": "vehicle-1", "replicate_kind": "biological",
            "response_name": "growth_inhibition", "response_value": 0,
            "response_unit": "% inhibition", "timepoint_value": 48, "timepoint_unit": "h",
        },
        {
            "sample_role": "positive_control", "sample_label": "Positive control",
            "replicate_id": "positive-1", "replicate_kind": "biological",
            "response_name": "growth_inhibition", "response_value": 98,
            "response_unit": "% inhibition", "timepoint_value": 48, "timepoint_unit": "h",
        },
    ])
    measured = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/measurements",
        headers=headers,
        json={"measurements": rows},
    )
    assert measured.status_code == 201
    assert measured.json()["created_count"] == len(rows)

    # A technical well without a matching biological parent must be rejected.
    invalid_parent = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/measurements",
        headers=headers,
        json={"measurements": [{
            "sample_role": "test_sample",
            "sample_label": "Compound 4PL",
            "replicate_id": "orphan-well",
            "replicate_kind": "technical",
            "biological_sample_id": "does-not-exist",
            "concentration_value": 1,
            "concentration_unit": "uM",
            "response_name": "growth_inhibition",
            "response_value": 50,
            "response_unit": "% inhibition",
            "timepoint_value": 48,
            "timepoint_unit": "h",
        }]},
    )
    assert invalid_parent.status_code == 422

    submitted = client.post(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/submit",
        headers=headers,
        json={},
    )
    assert submitted.status_code == 200

    reviewer = User(
        username="assay_4pl_reviewer",
        email="assay-4pl-reviewer@nexora.test",
        password="not-used-in-this-test",
        status="active",
        is_admin=True,
    )
    db.add(reviewer)
    db.commit()
    app.dependency_overrides[require_admin] = lambda: reviewer
    try:
        review = client.post(
            f"/api/v1/research/studies/{study_id}/assays/{assay_id}/review",
            json={
                "decision": "accepted_for_analysis",
                "comparability_status": "not_assessed",
                "rationale": "Dossier synthétique cohérent pour tester le pipeline statistique.",
            },
        )
    finally:
        app.dependency_overrides.pop(require_admin, None)
    assert review.status_code == 200

    payload = {
        "response_name": "growth_inhibition",
        "response_unit": "% inhibition",
        "concentration_unit": "uM",
        "response_direction": "decreasing",
        "endpoint_name": "IC50",
        "sample_label": "Compound 4PL",
        "timepoint_value": 48,
        "timepoint_unit": "h",
        "bootstrap_iterations": 500,
        "random_seed": 123,
    }
    endpoint = f"/api/v1/research/studies/{study_id}/assays/{assay_id}/dose-response-analysis"
    calculated = client.post(endpoint, headers=headers, json=payload)
    assert calculated.status_code == 201, calculated.text
    analysis = calculated.json()["analysis"]
    assert analysis["analysis_type"] == "dose_response_4pl"
    assert analysis["input_sha256"] and analysis["output_sha256"]
    assert analysis["result"]["fit"]["midpoint_concentration"] == pytest.approx(3.2, rel=0.12)
    assert analysis["result"]["sample"]["n_biological_replicates"] == 3
    assert analysis["result"]["sample"]["n_technical_measurements_used"] == 42
    assert analysis["result"]["quality"]["status"] == "interpolated"

    replay = client.post(endpoint, headers=headers, json=payload)
    assert replay.status_code == 201
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["analysis"]["id"] == analysis["id"]

    listed = client.get(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}/analyses",
        headers=headers,
    )
    assert listed.status_code == 200
    assert len(listed.json()["analyses"]) == 1

    dossier = client.get(
        f"/api/v1/research/studies/{study_id}/assays/{assay_id}",
        headers=headers,
    )
    assert dossier.status_code == 200
    assert dossier.json()["assay"]["measurement_count"] == len(rows)
    evidence = db.query(ResearchEvidence).filter(
        ResearchEvidence.study_id == study_id,
        ResearchEvidence.object_type == "assay_analysis",
        ResearchEvidence.object_id == str(analysis["id"]),
    ).one()
    assert evidence.evidence_status == "computational_hypothesis"
    assert evidence.evidence_grade == "D"
    assert evidence.input_sha256 == analysis["input_sha256"]
    assert evidence.output_sha256 == analysis["output_sha256"]
