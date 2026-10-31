import json

import pytest

from app.config import settings
from app.main import app
from app.models.docking import DockingJob, JobStatus
from app.models.evidence import ResearchEvidence
from app.models.experimental import ResearchAssay, ResearchAssayAnalysis, ResearchAssayMeasurement
from app.models.research import ResearchDockingRun, ResearchStudy, ResearchTarget
from app.models.user import User


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


def test_docking_potency_comparison_requires_same_target_compounds_and_protocol(
    client, db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "RESULTS_DIR", str(tmp_path))
    token = _token(client, "rank_cmp_user", "rank-cmp@nexora.test")
    headers = {"Authorization": f"Bearer {token}"}
    owner = db.query(User).filter(User.email == "rank-cmp@nexora.test").one()

    study = ResearchStudy(
        user_id=owner.id,
        name="Vina potency validation",
        disease="research",
        pathogen="Test organism",
        country_focus="Mali",
        region_scope="Sahel",
        objective="Compare rank ordering in a curated same-target cohort.",
        hypothesis="Docking rank may be directionally associated with experimental potency.",
        status="analysis",
        metadata_json={},
    )
    db.add(study)
    db.flush()
    target = ResearchTarget(
        study_id=study.id,
        state="wt",
        protein_name="Shared target",
        protein_sequence="MKTAYIAKQRQISFVKSHFSRQ",
        structure_source="uploaded",
        structure_accession="P12345",
        structure_status="accepted_for_docking_review",
        provenance={},
    )
    db.add(target)
    db.flush()

    smiles = ["C", "CC", "CCC", "CCCC", "CCCCC"]
    assay_ids = []
    run_ids = []
    midpoint_values = [0.5, 1.0, 2.0, 4.0, 8.0]
    vina_scores = [-10.0, -9.0, -8.0, -7.0, -6.0]
    protocol = {
        "protocol_id": "GENERIC_SMILES_SCREENING_V1",
        "protocol_version": "research-screening-v1",
        "structure_sha256": "a" * 64,
        "target_accession": "P12345",
        "max_pockets": 1,
        "vina_seed": 42,
        "num_modes": 20,
        "energy_range_kcal_mol": 3,
        "vina_exhaustiveness_policy": "adaptive_by_engine",
        "vina_cpu_config": 1,
    }

    for index, (smiles_i, midpoint, vina_score) in enumerate(zip(smiles, midpoint_values, vina_scores), start=1):
        assay = ResearchAssay(
            study_id=study.id,
            target_id=target.id,
            created_by=owner.id,
            assay_type="dose_response",
            assay_name=f"Assay {index}",
            test_article=f"Compound {index}",
            canonical_smiles=smiles_i,
            source_kind="lab_generated",
            laboratory="Validation laboratory",
            method_name="Growth inhibition",
            method_version="SOP-4PL-1",
            protocol_reference="SOP-4PL-1",
            organism="Test organism",
            strain="3D7",
            conditions={"temperature_c": 37, "exposure_hours": 48},
            status="reviewed",
            review_decision="accepted_for_analysis",
            comparability_status="comparable",
            comparability_group="same-target-same-assay-v1",
        )
        db.add(assay)
        db.flush()
        analysis_result = {
            "model": {"endpoint_label": "IC50"},
            "fit": {"midpoint_concentration": midpoint, "concentration_unit": "uM"},
            "quality": {"status": "interpolated"},
            "confidence_interval": {"lower": midpoint * 0.8, "upper": midpoint * 1.2, "unit": "uM"},
        }
        statistical_analysis = ResearchAssayAnalysis(
            assay_id=assay.id,
            created_by=owner.id,
            analysis_type="dose_response_4pl",
            algorithm="four_parameter_logistic",
            algorithm_version="N3XORA_4PL_CLUSTER_BOOTSTRAP_V1",
            analysis_key=f"{index:064x}",
            input_sha256=f"{index + 10:064x}",
            output_sha256=f"{index + 20:064x}",
            parameters={"endpoint_name": "IC50"},
            result=analysis_result,
            status="completed",
        )
        db.add(statistical_analysis)
        docking_result = tmp_path / f"docking-{index}.json"
        docking_result.write_text(json.dumps({"vina_best_score": vina_score}), encoding="utf-8")
        job = DockingJob(
            user_id=owner.id,
            status=JobStatus.completed,
            protein_seq=target.protein_sequence,
            ligand_smiles=smiles_i,
            result_path=str(docking_result),
            progress=100,
        )
        db.add(job)
        db.flush()
        run = ResearchDockingRun(
            study_id=study.id,
            target_id=target.id,
            docking_job_id=job.id,
            ligand_smiles=smiles_i,
            reference_state="wt",
            engine="AutoDock Vina",
            parameters=dict(protocol),
            status="completed",
        )
        db.add(run)
        db.flush()
        assay_ids.append(assay.id)
        run_ids.append(run.id)

    db.commit()
    response = client.post(
        f"/api/v1/research/studies/{study.id}/assays/docking-potency-comparison",
        headers=headers,
        json={"assay_ids": assay_ids, "docking_run_ids": run_ids},
    )
    assert response.status_code == 201, response.text
    result = response.json()["analysis"]["result"]
    assert result["rank_comparison"]["rho"] == pytest.approx(1.0)
    assert result["rank_comparison"]["permutation_method"] == "exact_all_pairings"
    assert result["rank_comparison"]["n_compounds"] == 5
    assert len(result["pairs"]) == 5
    assert result["scientific_notice"].startswith("Association descriptive")

    evidence = db.query(ResearchEvidence).filter(
        ResearchEvidence.study_id == study.id,
        ResearchEvidence.object_type == "assay_analysis",
    ).one()
    assert evidence.evidence_status == "computational_hypothesis"
    assert evidence.evidence_grade == "D"
    assert evidence.input_sha256
    assert evidence.output_sha256


def test_docking_potency_comparison_rejects_mixed_structure_fingerprints(
    client, db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "RESULTS_DIR", str(tmp_path))
    token = _token(client, "rank_cmp_bad", "rank-cmp-bad@nexora.test")
    headers = {"Authorization": f"Bearer {token}"}
    owner = db.query(User).filter(User.email == "rank-cmp-bad@nexora.test").one()
    study = ResearchStudy(
        user_id=owner.id, name="Invalid protocol cohort",
        disease="research", pathogen="Test organism",
        country_focus="Mali", region_scope="Sahel",
        status="analysis", metadata_json={},
    )
    db.add(study)
    db.flush()
    target = ResearchTarget(
        study_id=study.id, state="wt", protein_name="Target",
        protein_sequence="MKTAYIAKQRQISFVKSHFSRQ",
        structure_status="accepted_for_docking_review",
    )
    db.add(target)
    db.flush()

    assay_ids, run_ids = [], []
    for index in range(5):
        smiles = ["C", "CC", "CCC", "CCCC", "CCCCC"][index]
        assay = ResearchAssay(
            study_id=study.id, target_id=target.id, created_by=owner.id,
            assay_type="dose_response", assay_name=f"Assay {index}",
            test_article=f"Compound {index}", canonical_smiles=smiles,
            source_kind="lab_generated", laboratory="Lab",
            method_name="Growth inhibition", method_version="1",
            protocol_reference="SOP-1", organism="Test organism",
            strain="3D7", conditions={"temp": 37},
            status="reviewed", review_decision="accepted_for_analysis",
            comparability_status="comparable", comparability_group="same",
        )
        db.add(assay)
        db.flush()
        db.add(ResearchAssayAnalysis(
            assay_id=assay.id, created_by=owner.id,
            analysis_type="dose_response_4pl", algorithm="four_parameter_logistic", algorithm_version="N3XORA_4PL_CLUSTER_BOOTSTRAP_V1",
            analysis_key=f"{index + 30:064x}", input_sha256=f"{index + 40:064x}",
            output_sha256=f"{index + 50:064x}", parameters={},
            result={"model": {"endpoint_label": "IC50"},
                    "fit": {"midpoint_concentration": index + 1, "concentration_unit": "uM"},
                    "quality": {"status": "interpolated"}},
            status="completed",
        ))
        result_path = tmp_path / f"invalid-protocol-{index}.json"
        result_path.write_text(json.dumps({"vina_best_score": -8 + index}), encoding="utf-8")
        job = DockingJob(
            user_id=owner.id, status=JobStatus.completed,
            protein_seq=target.protein_sequence, ligand_smiles=smiles,
            result_path=str(result_path), progress=100,
        )
        db.add(job)
        db.flush()
        params = {
            "protocol_id": "GENERIC_SMILES_SCREENING_V1",
            "protocol_version": "research-screening-v1",
            "structure_sha256": ("b" if index == 4 else "a") * 64,
            "target_accession": "P12345",
            "max_pockets": 1, "vina_seed": 42, "num_modes": 20,
            "energy_range_kcal_mol": 3,
            "vina_exhaustiveness_policy": "adaptive_by_engine",
            "vina_cpu_config": 1,
        }
        run = ResearchDockingRun(
            study_id=study.id, target_id=target.id, docking_job_id=job.id,
            ligand_smiles=smiles, reference_state="wt",
            engine="AutoDock Vina", parameters=params, status="completed",
        )
        db.add(run)
        db.flush()
        assay_ids.append(assay.id)
        run_ids.append(run.id)

    db.commit()
    response = client.post(
        f"/api/v1/research/studies/{study.id}/assays/docking-potency-comparison",
        headers=headers,
        json={"assay_ids": assay_ids, "docking_run_ids": run_ids},
    )
    assert response.status_code == 409
    assert "même protocole" in response.json()["detail"]



def test_advanced_4pl_endpoint_persists_biological_bootstrap_and_history(client, db):
    token = _token(client, "advanced_4pl_user", "advanced-4pl@nexora.test")
    headers = {"Authorization": f"Bearer {token}"}
    owner = db.query(User).filter(User.email == "advanced-4pl@nexora.test").one()

    study = ResearchStudy(
        user_id=owner.id,
        name="Biological replicate 4PL API validation",
        disease="research",
        pathogen="Test organism",
        country_focus="Mali",
        region_scope="Sahel",
        objective="Validate the advanced 4PL route end-to-end.",
        hypothesis="Replicate-aware analysis recovers the concentration midpoint.",
        status="analysis",
        metadata_json={},
    )
    db.add(study)
    db.flush()
    target = ResearchTarget(
        study_id=study.id,
        state="wt",
        protein_name="4PL API test target",
        protein_sequence="MKTAYIAKQRQISFVKSHFSRQ",
        structure_source="sequence_resolved",
    )
    db.add(target)
    db.flush()
    assay = ResearchAssay(
        study_id=study.id,
        target_id=target.id,
        created_by=owner.id,
        assay_type="dose_response",
        assay_name="Synthetic 4PL API assay",
        test_article="Compound 4PL",
        canonical_smiles="CCO",
        source_kind="lab_generated",
        laboratory="N3XORA test laboratory",
        method_name="Synthetic growth inhibition",
        method_version="4PL-API-TEST-1",
        protocol_reference="SOP-4PL-API-1",
        assay_date=__import__("datetime").date.today(),
        organism="Test organism",
        strain="Test strain",
        conditions={"temperature_c": 37, "exposure_hours": 48},
        status="reviewed",
        review_decision="accepted_for_analysis",
    )
    db.add(assay)
    db.flush()

    concentrations = (0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0)
    offsets = {"bio-1": -0.4, "bio-2": 0.0, "bio-3": 0.4, "bio-4": 0.2}
    for replicate_id, offset in offsets.items():
        for concentration in concentrations:
            response_value = 100.0 / (1.0 + (3.0 / concentration) ** 1.25) + offset
            db.add(ResearchAssayMeasurement(
                assay_id=assay.id,
                sample_role="test_sample",
                sample_label="Compound 4PL",
                replicate_id=replicate_id,
                replicate_kind="biological",
                concentration_value=concentration,
                concentration_unit="uM",
                response_name="growth_inhibition",
                response_value=response_value,
                response_unit="% inhibition",
                qualifier="exact",
                metadata_json={},
            ))
            db.add(ResearchAssayMeasurement(
                assay_id=assay.id,
                sample_role="test_sample",
                sample_label="Compound 4PL",
                replicate_id=f"{replicate_id}-technical",
                replicate_kind="technical",
                biological_sample_id=replicate_id,
                concentration_value=concentration,
                concentration_unit="uM",
                response_name="growth_inhibition",
                response_value=response_value + 0.1,
                response_unit="% inhibition",
                qualifier="exact",
                metadata_json={},
            ))
    db.commit()

    response = client.post(
        f"/api/v1/research/studies/{study.id}/assays/{assay.id}/dose-response-analysis",
        headers=headers,
        json={
            "response_name": "growth_inhibition",
            "response_unit": "% inhibition",
            "concentration_unit": "uM",
            "response_direction": "increasing",
            "endpoint_name": "IC50",
            "sample_label": "Compound 4PL",
            "bootstrap_iterations": 500,
            "random_seed": 42,
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    result = payload["analysis"]["result"]
    assert result["model"]["endpoint_label"] == "IC50"
    assert result["fit"]["midpoint_concentration"] == pytest.approx(3.0, rel=0.25)
    assert result["confidence_interval"]["iterations_converged"] >= 400
    assert result["sample"]["n_biological_replicates"] == 4
    assert result["sample"]["n_technical_measurements_used"] == 28

    history = client.get(
        f"/api/v1/research/studies/{study.id}/assays/{assay.id}/analyses",
        headers=headers,
    )
    assert history.status_code == 200
    stored = history.json()["analyses"][0]
    assert stored["analysis_key"]
    assert stored["output_sha256"]
    assert stored["status"] == "completed"

    detail = client.get(
        f"/api/v1/research/studies/{study.id}/assays/{assay.id}/analyses/{payload['analysis']['id']}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["analysis"]["result"]["sample"]["n_biological_replicates"] == 4
