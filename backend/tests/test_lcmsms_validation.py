"""Deterministic LC-MS/MS batch QC validation and evidence snapshots."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services.lcmsms_validation import (
    LcmsValidationError,
    assess_lcms_batch_quality,
    build_validation_report_payload,
)


def observation(
    row_id,
    qc_type,
    metric_name,
    value,
    injection_id,
    *,
    unit="intensity",
    status="not_assessed",
    criteria=None,
):
    return SimpleNamespace(
        id=row_id,
        qc_type=qc_type,
        metric_name=metric_name,
        metric_value=value,
        metric_unit=unit,
        injection_id=injection_id,
        status=status,
        assessment_method="imported",
        acceptance_criteria=criteria or {},
    )


def test_pooled_qc_rsd_blank_ratio_and_method_specific_mass_accuracy():
    name = "feature:F-001:peak_area"
    rows = [
        observation(1, "pooled_qc", name, 100, 1),
        observation(2, "pooled_qc", name, 101, 2),
        observation(3, "pooled_qc", name, 99, 3),
        observation(4, "pooled_qc", name, 100, 4),
        observation(5, "solvent_blank", name, 20, 5),
        observation(6, "solvent_blank", name, 5, 6),
        observation(7, "mass_accuracy", "mass_error_ppm", -6.0, 4, unit="ppm"),
    ]
    report = assess_lcms_batch_quality(
        rows,
        injection_orders={1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6},
        criteria={
            "min_pooled_qc_injections": 3,
            "max_pooled_qc_rsd_pct": 30,
            "metric_limits": {
                name: {
                    "max_rsd_pct": 10,
                    "max_blank_to_pooled_qc_pct": 10,
                    "max_abs_drift_pct_per_10_injections": 20,
                },
                "mass_error_ppm": {"max_abs": 5},
            },
        },
    )

    repeatability = next(
        item for item in report["metrics"]
        if item["metric_type"] == "repeatability"
    )
    blank = next(
        item for item in report["metrics"]
        if item["metric_type"] == "blank_contamination"
    )
    mass_accuracy = next(
        item for item in report["metrics"]
        if item["metric_type"] == "configured_measurement"
    )
    assert repeatability["status"] == "pass"
    assert repeatability["n"] == 4
    assert repeatability["drift_status"] == "pass"
    assert blank["max_blank_to_pooled_qc_pct"] == 20.0
    assert blank["status"] == "fail"
    assert mass_accuracy["status"] == "fail"
    assert report["status"] == "fail"


def test_fewer_than_minimum_pooled_qc_injections_is_insufficient_data():
    rows = [
        observation(1, "pooled_qc", "feature:F-001:peak_area", 100, 1),
        observation(2, "pooled_qc", "feature:F-001:peak_area", 101, 2),
    ]
    report = assess_lcms_batch_quality(
        rows,
        injection_orders={1: 1, 2: 2},
        criteria={"min_pooled_qc_injections": 3, "max_pooled_qc_rsd_pct": 30},
    )
    assert report["status"] == "insufficient_data"
    assert report["metrics"][0]["status"] == "insufficient_data"


def test_duplicate_qc_measurements_from_one_injection_are_not_silently_averaged():
    rows = [
        observation(1, "pooled_qc", "feature:F-001:peak_area", 100, 1),
        observation(2, "pooled_qc", "feature:F-001:peak_area", 102, 1),
        observation(3, "pooled_qc", "feature:F-001:peak_area", 99, 2),
        observation(4, "pooled_qc", "feature:F-001:peak_area", 101, 3),
        observation(5, "pooled_qc", "feature:F-001:peak_area", 100, 4),
    ]
    report = assess_lcms_batch_quality(
        rows,
        injection_orders={1: 1, 2: 2, 3: 3, 4: 4},
        criteria={"min_pooled_qc_injections": 3, "max_pooled_qc_rsd_pct": 30},
    )
    metric = report["metrics"][0]
    assert metric["n"] == 3
    assert metric["duplicate_injection_ids"] == [1]
    assert metric["status"] == "pass"


def test_drift_threshold_is_not_assessed_when_fewer_than_three_orders_exist():
    name = "feature:F-001:peak_area"
    rows = [
        observation(1, "pooled_qc", name, 100, 1),
        observation(2, "pooled_qc", name, 101, 2),
        observation(3, "pooled_qc", name, 99, 3),
    ]
    report = assess_lcms_batch_quality(
        rows,
        injection_orders={1: 1, 2: 2},
        criteria={
            "min_pooled_qc_injections": 3,
            "max_pooled_qc_rsd_pct": 30,
            "metric_limits": {name: {"max_abs_drift_pct_per_10_injections": 1}},
        },
    )
    assert report["metrics"][0]["status"] == "insufficient_data"
    assert report["metrics"][0]["drift_status"] == "insufficient_data"


def test_invalid_metric_limits_are_rejected():
    with pytest.raises(LcmsValidationError):
        assess_lcms_batch_quality(
            [],
            injection_orders={},
            criteria={
                "min_pooled_qc_injections": 2,
                "max_pooled_qc_rsd_pct": 30,
            },
        )
    with pytest.raises(LcmsValidationError):
        assess_lcms_batch_quality(
            [],
            injection_orders={},
            criteria={
                "min_pooled_qc_injections": 3,
                "max_pooled_qc_rsd_pct": 30,
                "metric_limits": {"feature:F": {"not_a_real_limit": 4}},
            },
        )


def test_report_hash_is_stable_for_the_same_source_evidence():
    rows = [
        observation(1, "pooled_qc", "feature:F-001:peak_area", 100, 1),
        observation(2, "pooled_qc", "feature:F-001:peak_area", 101, 2),
        observation(3, "pooled_qc", "feature:F-001:peak_area", 99, 3),
    ]
    assessment = assess_lcms_batch_quality(
        rows,
        injection_orders={1: 1, 2: 2, 3: 3},
        criteria={"min_pooled_qc_injections": 3, "max_pooled_qc_rsd_pct": 30},
    )
    run = SimpleNamespace(
        id=17,
        input_sha256="a" * 64,
        output_sha256="b" * 64,
        status="completed",
    )
    args = {
        "assessment": assessment,
        "batch_id": 5,
        "study_id": 2,
        "processing_runs": [run],
        "observations": rows,
        "injection_orders": {1: 1, 2: 2, 3: 3},
    }
    first = build_validation_report_payload(**args, created_at=datetime(2026, 11, 1, 8, 0))
    replay = build_validation_report_payload(**args, created_at=datetime(2026, 11, 1, 9, 0))
    assert first["input_sha256"] == replay["input_sha256"]
    assert first["report_sha256"] == replay["report_sha256"]


def test_input_fingerprint_changes_when_a_qc_value_changes():
    rows = [
        observation(1, "pooled_qc", "feature:F-001:peak_area", 100, 1),
        observation(2, "pooled_qc", "feature:F-001:peak_area", 101, 2),
        observation(3, "pooled_qc", "feature:F-001:peak_area", 99, 3),
    ]
    criteria = {"min_pooled_qc_injections": 3, "max_pooled_qc_rsd_pct": 30}
    run = SimpleNamespace(id=17, input_sha256="a" * 64, output_sha256="b" * 64, status="completed")
    def fingerprint(rows):
        assessed = assess_lcms_batch_quality(
            rows, injection_orders={1: 1, 2: 2, 3: 3}, criteria=criteria
        )
        return build_validation_report_payload(
            assessed,
            batch_id=5,
            study_id=2,
            processing_runs=[run],
            observations=rows,
            injection_orders={1: 1, 2: 2, 3: 3},
            created_at=datetime(2026, 11, 1, 8, 0),
        )["input_sha256"]

    original = fingerprint(rows)
    changed = list(rows)
    changed[0] = observation(1, "pooled_qc", "feature:F-001:peak_area", 120, 1)
    assert original != fingerprint(changed)



def test_batch_validation_api_persists_replays_and_enforces_study_ownership(client, db):
    from app.main import app
    from app.models.lcmsms import (
        LcmsAnalyticalBatch,
        LcmsInjection,
        LcmsProcessingRun,
        LcmsQualityControlObservation,
    )
    from app.models.research import ResearchStudy
    from app.models.user import User
    from app.services.auth_service import get_current_user

    owner = User(
        username="lcms_validation_owner",
        email="lcms-validation-owner@nexora.test",
        password="test-password-hash",
        is_admin=False,
    )
    outsider = User(
        username="lcms_validation_outsider",
        email="lcms-validation-outsider@nexora.test",
        password="test-password-hash",
        is_admin=False,
    )
    db.add_all([owner, outsider])
    db.flush()
    study = ResearchStudy(
        user_id=owner.id,
        name="LC-MS validation API test",
        disease="natural_products",
        pathogen="botanical_material",
        country_focus="Mali",
        region_scope="Sahel",
        metadata_json={},
    )
    db.add(study)
    db.flush()
    batch = LcmsAnalyticalBatch(
        study_id=study.id,
        batch_code="VALIDATION-API-01",
        instrument_vendor="Synthetic",
        instrument_model="Synthetic HRMS",
        acquisition_method="Synthetic method",
        created_by=owner.id,
    )
    db.add(batch)
    db.flush()
    injections = []
    for order in range(1, 5):
        injection = LcmsInjection(
            study_id=study.id,
            batch_id=batch.id,
            injection_order=order,
            injection_type="pooled_qc",
            sample_label=f"POOL-QC-{order:02d}",
            created_by=owner.id,
        )
        db.add(injection)
        injections.append(injection)
    db.flush()
    run = LcmsProcessingRun(
        study_id=study.id,
        batch_id=batch.id,
        software_name="OpenMS",
        software_version="3.6.0",
        workflow_name="nexora.lcmsms.untargeted-metabolomics",
        workflow_version="1.0.0",
        parameters={"mzml_artifact_id": 1},
        input_sha256="a" * 64,
        output_sha256="b" * 64,
        status="completed",
        created_by=owner.id,
    )
    db.add(run)
    db.flush()
    for injection, value in zip(injections, [100.0, 101.0, 99.0, 100.0]):
        db.add(LcmsQualityControlObservation(
            study_id=study.id,
            batch_id=batch.id,
            injection_id=injection.id,
            qc_type="pooled_qc",
            metric_name="feature:F-001:peak_area",
            metric_value=value,
            metric_unit="intensity",
            status="not_assessed",
            assessment_method="imported",
            acceptance_criteria={},
        ))
    db.commit()

    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        url = f"/api/v1/lcmsms/batches/{batch.id}/validation-reports"
        request_body = {
            "min_pooled_qc_injections": 3,
            "max_pooled_qc_rsd_pct": 10,
            "metric_limits": {
                "feature:F-001:peak_area": {
                    "max_rsd_pct": 10,
                    "max_abs_drift_pct_per_10_injections": 20,
                }
            },
        }
        first = client.post(url, json=request_body)
        assert first.status_code == 201, first.text
        first_payload = first.json()["validation_report"]
        assert first_payload["status"] == "pass"
        assert first_payload["coverage"]["pooled_qc_injection_count"] == 4
        assert first_payload["report_sha256"]

        replay = client.post(url, json=request_body)
        assert replay.status_code == 201, replay.text
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["validation_report"]["id"] == first_payload["id"]

        listed = client.get(url)
        assert listed.status_code == 200
        assert len(listed.json()["validation_reports"]) == 1

        detail = client.get(
            f"/api/v1/lcmsms/validation-reports/{first_payload['id']}"
        )
        assert detail.status_code == 200
        assert detail.json()["validation_report"]["input_sha256"] == first_payload["input_sha256"]

        app.dependency_overrides[get_current_user] = lambda: outsider
        forbidden_list = client.get(url)
        forbidden_detail = client.get(
            f"/api/v1/lcmsms/validation-reports/{first_payload['id']}"
        )
        assert forbidden_list.status_code == 404
        assert forbidden_detail.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
