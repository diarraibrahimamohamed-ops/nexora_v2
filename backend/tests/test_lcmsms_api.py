"""Regression tests for the botanical and LC-MS/MS evidence registry."""
from pathlib import Path

import pytest

from app.config import settings
from app.main import app
from app.models.evidence import ResearchEvidence
from app.models.lcmsms import (
    BotanicalSpecimen,
    LcmsAnalyticalBatch,
    LcmsCompoundAnnotation,
    LcmsDataArtifact,
    LcmsFeature,
    LcmsFeatureObservation,
    LcmsInjection,
    LcmsMs2Spectrum,
    LcmsProcessingRun,
    LcmsSpectralLibrary,
    PlantMaterialSample,
    ExtractionBatch,
    PlantFraction,
)
from app.models.research import ResearchStudy
from app.models.user import User
from app.services.auth_service import get_current_user, require_admin


def _user(db, username, *, admin=False):
    user = User(
        username=username,
        email=f"{username}@nexora.test",
        password="test-password-hash",
        is_admin=admin,
    )
    db.add(user)
    db.flush()
    return user


def _study(db, user):
    study = ResearchStudy(
        user_id=user.id,
        name="LC-MS/MS lineage test",
        disease="natural_products",
        pathogen="botanical_material",
        country_focus="Mali",
        region_scope="Sahel",
        metadata_json={},
    )
    db.add(study)
    db.flush()
    return study


def test_lcmsms_lineage_artifact_processing_and_msi_annotation(client, db, tmp_path, monkeypatch):
    owner = _user(db, "lcmsms_owner")
    admin = _user(db, "lcmsms_admin", admin=True)
    study = _study(db, owner)
    study_id = study.id
    db.commit()

    monkeypatch.setattr(settings, "LCMS_STORAGE_DIR", str(tmp_path / "lcmsms"))
    monkeypatch.setattr(settings, "LCMS_MAX_ARTIFACT_BYTES", 2 * 1024 * 1024)
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        specimen_response = client.post(
            f"/api/v1/lcmsms/studies/{study_id}/specimens",
            json={
                "specimen_code": "BOT-MALI-0001",
                "reported_taxon": "Guiera senegalensis",
                "country": "Mali",
                "admin_region": "Sikasso",
                "locality": "Test site",
                "latitude": 11.31,
                "longitude": -5.67,
                "coordinate_access": "restricted",
                "collection_date": "2026-02-10",
                "voucher_code": "HERB-TEST-01",
                "herbarium_name": "Herbarium de test",
            },
        )
        assert specimen_response.status_code == 201, specimen_response.text
        specimen_id = specimen_response.json()["specimen"]["id"]

        listed = client.get(f"/api/v1/lcmsms/studies/{study_id}/specimens")
        assert listed.status_code == 200
        specimen_public = listed.json()["specimens"][0]
        assert specimen_public["latitude"] is None
        assert specimen_public["longitude"] is None

        sample_response = client.post(
            f"/api/v1/lcmsms/studies/{study_id}/samples",
            json={
                "specimen_id": specimen_id,
                "sample_code": "SAMPLE-0001",
                "plant_part": "leaf",
                "material_state": "dried",
                "initial_mass_g": 100,
                "moisture_percent": 8.5,
                "preservation_method": "Drying under documented conditions",
            },
        )
        assert sample_response.status_code == 201, sample_response.text
        sample_id = sample_response.json()["sample"]["id"]

        extraction_response = client.post(
            f"/api/v1/lcmsms/studies/{study_id}/extractions",
            json={
                "sample_id": sample_id,
                "batch_code": "EXT-0001",
                "extraction_method": "Maceration",
                "solvent_system": [{"solvent": "ethanol", "percentage": 70}],
                "input_mass_g": 100,
                "extract_mass_g": 105,
            },
        )
        assert extraction_response.status_code == 201, extraction_response.text
        extraction = extraction_response.json()["extraction"]
        assert extraction["yield_percent"] == pytest.approx(105.0)
        assert any("supérieur à 100 %" in item for item in extraction_response.json()["warnings"])
        extraction_id = extraction["id"]

        fraction_response = client.post(
            f"/api/v1/lcmsms/extractions/{extraction_id}/fractions",
            json={
                "fraction_code": "F-HEX-01",
                "fraction_order": 1,
                "separation_method": "Liquid-liquid partition",
                "elution_system": [{"phase": "hexane"}],
                "recovered_mass_g": 10,
            },
        )
        assert fraction_response.status_code == 201, fraction_response.text
        fraction_id = fraction_response.json()["fraction"]["id"]

        batch_response = client.post(
            f"/api/v1/lcmsms/studies/{study_id}/analytical-batches",
            json={
                "batch_code": "LCMS-BATCH-0001",
                "instrument_vendor": "Test vendor",
                "instrument_model": "Test QTOF",
                "mass_analyzer": "QTOF",
                "ion_source": "ESI",
                "chromatography_column": "C18 test column",
                "mobile_phase_a": "Water + documented additive",
                "mobile_phase_b": "Acetonitrile + documented additive",
                "gradient_program": [{"time_min": 0, "percent_b": 5}],
                "flow_rate": 0.3,
                "flow_rate_unit": "mL/min",
                "injection_volume": 2,
                "injection_volume_unit": "uL",
                "acquisition_method": "DDA test method",
                "acquisition_method_version": "1.0",
                "polarity_modes": ["positive", "negative"],
                "qc_policy": {"source": "pilot_protocol_v1"},
            },
        )
        assert batch_response.status_code == 201, batch_response.text
        batch_id = batch_response.json()["analytical_batch"]["id"]

        sample_injection_response = client.post(
            f"/api/v1/lcmsms/batches/{batch_id}/injections",
            json={
                "injection_order": 1,
                "injection_type": "sample",
                "sample_label": "SAMPLE-0001 / F-HEX-01",
                "fraction_id": fraction_id,
                "technical_replicate": 1,
            },
        )
        assert sample_injection_response.status_code == 201, sample_injection_response.text
        sample_injection_id = sample_injection_response.json()["injection"]["id"]

        standard_injection_response = client.post(
            f"/api/v1/lcmsms/batches/{batch_id}/injections",
            json={
                "injection_order": 2,
                "injection_type": "reference_standard",
                "sample_label": "Authentic reference standard",
            },
        )
        assert standard_injection_response.status_code == 201, standard_injection_response.text
        standard_injection_id = standard_injection_response.json()["injection"]["id"]

        mzml = b'<?xml version="1.0"?><mzML><run id="test"/></mzML>'
        upload = client.post(
            f"/api/v1/lcmsms/batches/{batch_id}/injections/{sample_injection_id}/artifacts?artifact_role=raw_data",
            files={"file": ("sample.mzML", mzml, "application/xml")},
        )
        assert upload.status_code == 201, upload.text
        artifact = upload.json()["artifact"]
        assert artifact["size_bytes"] == len(mzml)
        assert len(artifact["sha256"]) == 64
        assert artifact["validation_status"] == "checksum_verified"

        download = client.get(f"/api/v1/lcmsms/artifacts/{artifact['id']}/download")
        assert download.status_code == 200
        assert download.content == mzml
        assert download.headers["x-artifact-sha256"] == artifact["sha256"]

        qc = client.post(
            f"/api/v1/lcmsms/batches/{batch_id}/quality-controls",
            json={
                "injection_id": sample_injection_id,
                "qc_type": "pooled_qc",
                "metric_name": "retention_time_drift",
                "metric_value": 0.03,
                "metric_unit": "min",
                "status": "not_assessed",
                "acceptance_criteria": {"defined_by": "pilot protocol"},
            },
        )
        assert qc.status_code == 201, qc.text

        app.dependency_overrides[require_admin] = lambda: admin
        library_response = client.post(
            "/api/v1/lcmsms/spectral-libraries",
            json={
                "name": "Test spectral library",
                "version": "2026.1",
                "source_license": "Test-only license; not redistributed",
                "data_format": "mzML",
                "reference_set_sha256": "a" * 64,
                "citation": "Synthetic test fixture",
            },
        )
        assert library_response.status_code == 201, library_response.text
        library_id = library_response.json()["spectral_library"]["id"]
        review_library = client.post(
            f"/api/v1/lcmsms/spectral-libraries/{library_id}/review",
            json={
                "status": "reviewed",
                "rationale": "Metadata and source license checked for test fixture.",
            },
        )
        assert review_library.status_code == 200, review_library.text
        assert review_library.json()["spectral_library"]["curation_status"] == "reviewed"
        app.dependency_overrides.pop(require_admin, None)

        processing_response = client.post(
            f"/api/v1/lcmsms/studies/{study_id}/batches/{batch_id}/processing-runs",
            json={
                "spectral_library_id": library_id,
                "software_name": "External feature finder",
                "software_version": "1.0",
                "workflow_name": "feature detection import",
                "workflow_version": "1.0",
                "artifact_ids": [artifact["id"]],
                "parameters": {"alignment_tolerance_ppm": 5},
            },
        )
        assert processing_response.status_code == 201, processing_response.text
        processing = processing_response.json()["processing_run"]
        assert processing["status"] == "registered"
        assert processing["provenance_manifest"]["input_manifest"]["manifest_type"] == "lcms_processing_input"

        feature_response = client.post(
            f"/api/v1/lcmsms/processing-runs/{processing['id']}/features",
            json=[{
                "feature_key": "FT-0001",
                "precursor_mz": 301.123456,
                "retention_time": 125.0,
                "retention_time_unit": "second",
                "polarity": "positive",
                "charge": 1,
                "adduct": "[M+H]+",
            }],
        )
        assert feature_response.status_code == 201, feature_response.text
        assert len(feature_response.json()["output_sha256"]) == 64
        assert feature_response.json()["output_manifest"]["manifest_type"] == "lcms_processing_output"
        feature_id = feature_response.json()["features"][0]["id"]

        for injection_id in (sample_injection_id, standard_injection_id):
            observation = client.post(
                f"/api/v1/lcmsms/features/{feature_id}/observations",
                json={
                    "injection_id": injection_id,
                    "observed_mz": 301.1234,
                    "retention_time": 125.0,
                    "peak_area": 1500.0,
                    "intensity": 800.0,
                    "signal_to_noise": 30.0,
                    "quality_flag": "reviewed",
                },
            )
            assert observation.status_code == 201, observation.text

        ms2 = client.post(
            f"/api/v1/lcmsms/features/{feature_id}/ms2-spectra",
            json={
                "injection_id": standard_injection_id,
                "scan_identifier": "scan=1001",
                "precursor_mz": 301.1234,
                "collision_energy": 20,
                "collision_energy_unit": "eV",
                "polarity": "positive",
                "fragments": [{"mz": 163.0601, "intensity": 1000}],
            },
        )
        assert ms2.status_code == 201, ms2.text

        evidence = {
            "authentic_standard": True,
            "same_method": True,
            "orthogonal_matches": ["retention_time", "ms2_spectrum"],
            "retention_time_match": True,
            "ms2_match": True,
        }
        annotation = client.post(
            f"/api/v1/lcmsms/features/{feature_id}/annotations",
            json={
                "reference_standard_injection_id": standard_injection_id,
                "annotation_method": "authentic_standard",
                "annotation_name": "Synthetic standard",
                "molecular_formula": "C15H16O5",
                "inchikey": "ABCDEFGHIJKLMNOP-QRSTUVWXYY-Z",
                "msi_level": 1,
                "score_type": "curator_documented",
                "score_value": 1.0,
                "evidence": evidence,
            },
        )
        assert annotation.status_code == 201, annotation.text
        annotation_id = annotation.json()["annotation"]["id"]

        reviewed = client.post(
            f"/api/v1/lcmsms/annotations/{annotation_id}/review",
            json={
                "status": "accepted",
                "review_rationale": "Authentic standard and two orthogonal properties reviewed.",
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["annotation"]["status"] == "accepted"

        overview = client.get(f"/api/v1/lcmsms/studies/{study_id}/overview")
        assert overview.status_code == 200
        assert overview.json()["counts"]["specimens"] == 1
        assert overview.json()["counts"]["plant_samples"] == 1
        assert overview.json()["counts"]["injections"] == 2
        assert overview.json()["feature_count"] == 1
        assert overview.json()["annotation_count"] == 1

        invalid_msi1 = client.post(
            f"/api/v1/lcmsms/features/{feature_id}/annotations",
            json={
                "reference_standard_injection_id": standard_injection_id,
                "annotation_method": "authentic_standard",
                "msi_level": 1,
                "evidence": {
                    "authentic_standard": True,
                    "same_method": True,
                    "orthogonal_matches": ["retention_time"],
                    "retention_time_match": True,
                },
            },
        )
        assert invalid_msi1.status_code == 422

        owner_evidence = db.query(LcmsCompoundAnnotation).filter_by(id=annotation_id).one()
        assert owner_evidence.status == "accepted"
        assert db.query(LcmsDataArtifact).filter_by(id=artifact["id"]).count() == 1
        assert db.query(LcmsProcessingRun).filter_by(id=processing["id"]).one().status == "completed"

        outsider = _user(db, "lcmsms_outsider")
        db.commit()
        app.dependency_overrides[get_current_user] = lambda: outsider
        forbidden = client.get(f"/api/v1/lcmsms/studies/{study_id}/specimens")
        assert forbidden.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(require_admin, None)


def test_lcmsms_upload_rejects_directory_extension(client, db):
    owner = _user(db, "lcmsms_directory_user")
    study = _study(db, owner)
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        batch_response = client.post(
            f"/api/v1/lcmsms/studies/{study.id}/analytical-batches",
            json={
                "batch_code": "BATCH-D",
                "instrument_vendor": "Test",
                "instrument_model": "Test",
                "acquisition_method": "Test",
            },
        )
        assert batch_response.status_code == 201
        batch_id = batch_response.json()["analytical_batch"]["id"]
        injection = client.post(
            f"/api/v1/lcmsms/batches/{batch_id}/injections",
            json={
                "injection_order": 1,
                "injection_type": "other",
                "sample_label": "folder placeholder",
            },
        )
        assert injection.status_code == 201
        response = client.post(
            f"/api/v1/lcmsms/batches/{batch_id}/injections/{injection.json()['injection']['id']}/artifacts?artifact_role=raw_data",
            files={"file": ("vendor.D", b"not a directory archive", "application/octet-stream")},
        )
        assert response.status_code == 415
    finally:
        app.dependency_overrides.pop(get_current_user, None)
