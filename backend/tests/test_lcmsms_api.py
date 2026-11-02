"""Regression tests for the botanical and LC-MS/MS evidence registry."""
from pathlib import Path

import pytest

from app.config import settings
from app.main import app
from app.models.evidence import ResearchEvidence
from app.models.scientific_workflow import ScientificWorkflowRun, ScientificWorkflowStep
from app.models.phytochemistry import TaxonResolution
from app.models.lcmsms import (
    BotanicalSpecimen,
    LcmsAnalyticalBatch,
    LcmsAlignedFeature,
    LcmsAlignedFeatureMember,
    LcmsCompoundAnnotation,
    LcmsDataArtifact,
    LcmsFeature,
    LcmsFeatureAlignmentRun,
    LcmsFeatureObservation,
    LcmsInjection,
    LcmsQuantificationRun,
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
from app.services.scientific_workflow_engine import transition_workflow_run


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

        # The nomenclatural proposal must be resolved and accepted before
        # the physical voucher review can set the specimen to verified.
        taxon_resolution = TaxonResolution(
            specimen_id=specimen_id,
            requested_name="Guiera senegalensis",
            matched_name="Guiera senegalensis J.F.Gmel.",
            matched_identifier="1234567",
            matched_status="ACCEPTED",
            matched_rank="SPECIES",
            accepted_name="Guiera senegalensis J.F.Gmel.",
            accepted_identifier="1234567",
            accepted_status="ACCEPTED",
            accepted_rank="SPECIES",
            kingdom="Plantae",
            family="Combretaceae",
            match_type="EXACT",
            confidence=100,
            provider="GBIF ChecklistBank Species Match API v2 / WCVP",
            checklist_key="f382f0ce-323a-4091-bb9f-add557f3a9a2",
            dataset_doi="10.15468/6h8ucr",
            dataset_version="WCVP v16 (2026-06-04)",
            source_mode="LIVE",
            source_url="https://api.gbif.org/v2/species/match",
            response_sha256="a" * 64,
            response_payload={"test_fixture": True},
            review_status="accepted",
            reviewed_by=admin.email,
            review_note="Accepted WCVP test fixture for physical specimen curation.",
        )
        db.add(taxon_resolution)
        db.commit()

        app.dependency_overrides[require_admin] = lambda: admin
        reviewed_specimen = client.post(
            f"/api/v1/lcmsms/specimens/{specimen_id}/review",
            json={
                "status": "verified",
                "accepted_taxon": "Guiera senegalensis J.F.Gmel.",
                "taxon_identifier": "WCVP:1234567",
                "taxonomy_source": "WCVP via GBIF ChecklistBank",
                "identification_method": "Morphological examination",
                "identifier_name": "Test botanist",
                "voucher_code": "HERB-TEST-01",
                "herbarium_name": "Herbarium de test",
                "source_reference": "https://example.org/botanical-voucher/01",
                "rationale": "Taxonomic determination reviewed against the voucher specimen and source record.",
            },
        )
        assert reviewed_specimen.status_code == 200, reviewed_specimen.text
        assert reviewed_specimen.json()["specimen"]["identification_status"] == "verified"
        app.dependency_overrides.pop(require_admin, None)

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
                "data_format": "msp",
                "citation": "Synthetic test fixture",
            },
        )
        assert library_response.status_code == 201, library_response.text
        library_id = library_response.json()["spectral_library"]["id"]
        msp = (
            b"Name: Synthetic flavonoid\n"
            b"PrecursorMZ: 301.1234\n"
            b"IonMode: Positive\n"
            b"Num Peaks: 3\n"
            b"50.0 100.0\n"
            b"100.0 60.0\n"
            b"150.0 30.0\n\n"
        )
        msp_upload = client.post(
            f"/api/v1/lcmsms/spectral-libraries/{library_id}/upload-msp",
            files={"file": ("synthetic-library.msp", msp, "text/plain")},
        )
        assert msp_upload.status_code == 201, msp_upload.text
        assert msp_upload.json()["spectral_library"]["reference_set_size_bytes"] == len(msp)
        assert len(msp_upload.json()["spectral_library"]["reference_set_sha256"]) == 64

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

        engine_parameters = {
            "mzml_artifact_id": artifact["id"],
            "spectral_library_id": library_id,
            "mass_error_ppm": 5.0,
            "noise_threshold_int": 100.0,
            "precursor_mass_tolerance_ppm": 10.0,
            "retention_time_tolerance_seconds": 5.0,
            "fragment_mz_tolerance_da": 0.02,
            "minimum_cosine_score": 0.7,
            "minimum_matched_peaks": 3,
            "fragment_storage_limit": 200,
            "max_annotations_per_feature": 3,
        }
        started_engine = client.post(
            f"/api/v1/lcmsms/studies/{study_id}/batches/{batch_id}/process-mzml",
            json=engine_parameters,
            headers={"Idempotency-Key": "phase1h-lcmsms-test-run"},
        )
        assert started_engine.status_code == 202, started_engine.text
        started_payload = started_engine.json()
        assert started_payload["workflow_state"] == "ready"
        assert started_payload["processing_run"]["status"] == "registered"
        assert started_payload["processing_run"]["scientific_workflow_run_id"] == started_payload["workflow_run_id"]
        workflow_step = db.query(ScientificWorkflowStep).filter_by(
            workflow_run_id=started_payload["workflow_run_id"]
        ).one()
        assert workflow_step.queue_name == "lcmsms"
        assert workflow_step.max_attempts == 2

        replay_engine = client.post(
            f"/api/v1/lcmsms/studies/{study_id}/batches/{batch_id}/process-mzml",
            json=engine_parameters,
            headers={"Idempotency-Key": "phase1h-lcmsms-test-run"},
        )
        assert replay_engine.status_code == 202, replay_engine.text
        assert replay_engine.json()["idempotent_replay"] is True
        assert replay_engine.json()["workflow_run_id"] == started_payload["workflow_run_id"]
        assert replay_engine.json()["processing_run"]["id"] == started_payload["processing_run"]["id"]

        conflicting_parameters = dict(engine_parameters)
        conflicting_parameters["noise_threshold_int"] = 101.0
        conflicting_replay = client.post(
            f"/api/v1/lcmsms/studies/{study_id}/batches/{batch_id}/process-mzml",
            json=conflicting_parameters,
            headers={"Idempotency-Key": "phase1h-lcmsms-test-run"},
        )
        assert conflicting_replay.status_code == 409, conflicting_replay.text

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
                "spectrum_type": "centroided",
                "fragments": [{"mz": 163.0601, "intensity": 1000}],
            },
        )
        assert ms2.status_code == 201, ms2.text

        evidence = {
            "authentic_standard": True,
            "same_method": True,
            "orthogonal_matches": ["retention_time", "ms2_spectrum"],
            "retention_time_match": True,
            "retention_time_abs_delta_seconds": 0.2,
            "retention_time_tolerance_seconds": 1.0,
            "ms2_match": True,
            "ms2_match_score": 0.93,
            "ms2_match_score_type": "cosine_similarity",
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

        app.dependency_overrides[require_admin] = lambda: admin
        reviewed = client.post(
            f"/api/v1/lcmsms/annotations/{annotation_id}/review",
            json={
                "status": "accepted",
                "review_rationale": "Authentic standard and two orthogonal properties reviewed.",
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["annotation"]["status"] == "accepted"
        app.dependency_overrides.pop(require_admin, None)
        evidence_row = db.query(ResearchEvidence).filter(
            ResearchEvidence.object_type == "lcms_compound_annotation",
            ResearchEvidence.object_id == str(annotation_id),
        ).one()
        assert evidence_row.evidence_grade == "D"
        assert evidence_row.evidence_status == "computational_hypothesis"
        assert evidence_row.metadata_json["annotation_status"] == "accepted"
        assert evidence_row.metadata_json["msi_level"] == 1

        read_checks = [
            client.get(f"/api/v1/lcmsms/studies/{study_id}/samples"),
            client.get(f"/api/v1/lcmsms/studies/{study_id}/extractions"),
            client.get(f"/api/v1/lcmsms/studies/{study_id}/fractions"),
            client.get(f"/api/v1/lcmsms/studies/{study_id}/analytical-batches"),
            client.get(f"/api/v1/lcmsms/batches/{batch_id}/injections"),
            client.get(f"/api/v1/lcmsms/batches/{batch_id}/artifacts"),
            client.get(f"/api/v1/lcmsms/batches/{batch_id}/quality-controls"),
            client.get(f"/api/v1/lcmsms/studies/{study_id}/processing-runs"),
            client.get(f"/api/v1/lcmsms/processing-runs/{processing['id']}/features"),
            client.get(f"/api/v1/lcmsms/features/{feature_id}/observations"),
            client.get(f"/api/v1/lcmsms/features/{feature_id}/ms2-spectra"),
            client.get(f"/api/v1/lcmsms/processing-runs/{started_payload['processing_run']['id']}/ms2-spectra?association_status=unmatched"),
            client.get(f"/api/v1/lcmsms/features/{feature_id}/annotations"),
        ]
        assert all(response.status_code == 200 for response in read_checks), [
            response.text for response in read_checks if response.status_code != 200
        ]

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

        duplicate_feature_import = client.post(
            f"/api/v1/lcmsms/processing-runs/{processing['id']}/features",
            json=[{
                "feature_key": "FT-0002",
                "precursor_mz": 302.123456,
                "retention_time": 126.0,
                "retention_time_unit": "second",
                "polarity": "positive",
            }],
        )
        assert duplicate_feature_import.status_code == 409

        owner_evidence = db.query(LcmsCompoundAnnotation).filter_by(id=annotation_id).one()
        assert owner_evidence.status == "accepted"
        assert db.query(LcmsDataArtifact).filter_by(id=artifact["id"]).count() == 1
        assert db.query(LcmsProcessingRun).filter_by(id=processing["id"]).one().status == "completed"

        # The test only exercises submission/idempotency; do not leave a ready
        # workflow in the shared CI database for dispatcher tests that follow.
        transition_workflow_run(
            db,
            started_payload["workflow_run_id"],
            action="cancel",
            actor_user_id=owner.id,
            reason="LC-MS/MS API test cleanup",
        )
        db.commit()

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



def _create_completed_alignment_sources(db, owner, study, *, batch_code="ALIGNMENT-API", count=3):
    batch = LcmsAnalyticalBatch(
        study_id=study.id,
        batch_code=batch_code,
        instrument_vendor="Synthetic",
        instrument_model="Synthetic HRMS",
        acquisition_method="Synthetic alignment API method",
        created_by=owner.id,
    )
    db.add(batch)
    db.flush()
    processing_ids = []
    injection_rows = []
    for order in range(1, count + 1):
        injection_type = "pooled_qc" if order in {1, count} else "sample"
        injection = LcmsInjection(
            study_id=study.id,
            batch_id=batch.id,
            injection_order=order,
            injection_type=injection_type,
            sample_label=f"{batch_code}-{order:02d}",
            created_by=owner.id,
        )
        db.add(injection)
        db.flush()
        run = LcmsProcessingRun(
            study_id=study.id,
            batch_id=batch.id,
            software_name="OpenMS",
            software_version="3.6.0",
            workflow_name="nexora.lcmsms.untargeted-metabolomics",
            workflow_version="1.0.0",
            parameters={"mzml_injection_id": injection.id},
            input_sha256=(f"{order:064x}")[-64:],
            output_sha256=(f"{order + 100:064x}")[-64:],
            status="completed",
            created_by=owner.id,
        )
        db.add(run)
        db.flush()
        for index in range(10):
            feature = LcmsFeature(
                processing_run_id=run.id,
                feature_key=f"openms:positive:{index}",
                precursor_mz=150.0 + index * 11.0,
                retention_time=40.0 + index * 30.0,
                retention_time_unit="second",
                polarity="positive",
                charge=1,
                adduct="[M+H]+",
                quality_flags=["synthetic_test"],
            )
            db.add(feature)
            db.flush()
            db.add(LcmsFeatureObservation(
                feature_id=feature.id,
                injection_id=injection.id,
                observed_mz=feature.precursor_mz,
                retention_time=feature.retention_time,
                intensity=1000.0 + index + order,
                peak_area=1000.0 + index + order,
                quality_flag="not_assessed",
                metadata_json={"source": "synthetic alignment API test"},
            ))
        processing_ids.append(int(run.id))
        injection_rows.append(injection)
    db.flush()
    return batch, processing_ids, injection_rows


def test_feature_alignment_api_creates_durable_workflow_replays_and_enforces_ownership(
    client, db
):
    owner = _user(db, "lcms_alignment_owner")
    outsider = _user(db, "lcms_alignment_outsider")
    study = _study(db, owner)
    batch, processing_ids, injections = _create_completed_alignment_sources(
        db, owner, study
    )
    db.commit()

    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        url = f"/api/v1/lcmsms/batches/{batch.id}/feature-alignments"
        request_body = {
            "processing_run_ids": processing_ids,
            "reference_injection_ids": {"positive": injections[0].id},
            "minimum_injections": 3,
            "minimum_anchors": 8,
            "minimum_anchor_rt_coverage": 0.5,
        }
        first = client.post(url, json=request_body)
        assert first.status_code == 202, first.text
        payload = first.json()
        alignment = payload["alignment_run"]
        assert alignment["status"] == "registered"
        assert alignment["input_sha256"]
        assert payload["workflow_run_id"]
        assert payload["workflow_state"] == "ready"

        replay = client.post(url, json=request_body)
        assert replay.status_code == 202, replay.text
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["alignment_run"]["id"] == alignment["id"]

        runs = db.query(LcmsFeatureAlignmentRun).filter_by(batch_id=batch.id).all()
        assert len(runs) == 1
        assert runs[0].scientific_workflow_run_id == payload["workflow_run_id"]

        app.dependency_overrides[get_current_user] = lambda: outsider
        forbidden_list = client.get(url)
        forbidden_detail = client.get(
            f"/api/v1/lcmsms/feature-alignments/{alignment['id']}"
        )
        assert forbidden_list.status_code == 404
        assert forbidden_detail.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_qc_rlsc_api_requires_completed_alignment_and_replays_quantification(
    client, db
):
    owner = _user(db, "lcms_quantification_owner")
    study = _study(db, owner)
    batch, processing_ids, injections = _create_completed_alignment_sources(
        db, owner, study, batch_code="QUANTIFICATION-API", count=9
    )
    alignment = LcmsFeatureAlignmentRun(
        study_id=study.id,
        batch_id=batch.id,
        software_name="Nexora deterministic alignment",
        software_version="1.0.0",
        algorithm_name="mutual_unique_mz_anchors_piecewise_linear",
        algorithm_version="1.0.0",
        parameters={"minimum_anchors": 8},
        source_processing_run_ids=processing_ids,
        source_injection_ids=[int(item.id) for item in injections],
        input_sha256="a" * 64,
        output_sha256="b" * 64,
        status="completed",
        alignment_manifest={"status": "completed", "output_sha256": "b" * 64},
        created_by=owner.id,
    )
    db.add(alignment)
    db.flush()

    alignment_workflow = ScientificWorkflowRun(
        workflow_key="nexora.lcmsms.feature-alignment",
        definition_version="1.0.0",
        owner_user_id=owner.id,
        scope_type="lcms_feature_alignment_run",
        scope_id=str(alignment.id),
        state="quality_review.pending",
        state_version=1,
        requires_quality_review=True,
        context={"study_id": study.id, "batch_id": batch.id, "alignment_run_id": alignment.id},
        definition_snapshot={},
        input_manifest={},
        output_manifest={},
    )
    db.add(alignment_workflow)
    db.flush()
    alignment.scientific_workflow_run_id = alignment_workflow.id

    aligned_feature = LcmsAlignedFeature(
        alignment_run_id=alignment.id,
        polarity="positive",
        consensus_mz=150.0,
        aligned_retention_time=40.0,
        retention_time_unit="second",
        member_count=9,
        injection_count=9,
        alignment_status="aligned",
        quality_metrics={"expected_injection_count": 9},
    )
    db.add(aligned_feature)
    db.flush()

    for processing_id, injection in zip(processing_ids, injections):
        source_feature = db.query(LcmsFeature).filter_by(
            processing_run_id=processing_id,
            feature_key="openms:positive:0",
        ).one()
        db.add(LcmsAlignedFeatureMember(
            alignment_run_id=alignment.id,
            aligned_feature_id=aligned_feature.id,
            processing_run_id=processing_id,
            injection_id=injection.id,
            feature_id=source_feature.id,
            original_mz=150.0,
            original_retention_time=40.0,
            aligned_retention_time=40.0,
            retention_time_unit="second",
            delta_retention_time_seconds=0.0,
            delta_mz_ppm=0.0,
            raw_intensity=1000.0 + injection.injection_order,
            charge=1,
            adduct="[M+H]+",
            metadata_json={"is_rt_aligned": True},
        ))
    db.commit()

    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        url = f"/api/v1/lcmsms/feature-alignments/{alignment.id}/quantifications"
        request_body = {
            "minimum_qc_injections": 8,
            "minimum_qc_span_ratio": 0.6,
            "loess_span_fraction": 0.75,
            "robust_iterations": 2,
            "minimum_correction_factor": 0.2,
            "maximum_correction_factor": 5.0,
            "maximum_qc_rsd_worsening_pct": 0.0,
            "no_correction_threshold_pct": 2.0,
        }
        blocked = client.post(url, json=request_body)
        assert blocked.status_code == 409, blocked.text
        assert "revue qualité" in blocked.json()["detail"]
        assert db.query(LcmsQuantificationRun).filter_by(alignment_run_id=alignment.id).count() == 0
        review, _ = transition_workflow_run(
            db, alignment_workflow.id,
            action="approve", actor_user_id=owner.id,
            reason="Revue qualité LC-MS/MS approuvée.",
            expected_state_version=alignment_workflow.state_version,
        )
        review, _ = transition_workflow_run(
            db, alignment_workflow.id,
            action="finalize_review", actor_user_id=owner.id,
            reason="Finalisation après revue qualité.",
            expected_state_version=review.state_version,
        )
        db.commit()
        first = client.post(url, json=request_body)
        assert first.status_code == 202, first.text
        payload = first.json()
        quantification = payload["quantification_run"]
        assert quantification["status"] == "registered"
        assert quantification["method"] == "qc_rlsc_loess"
        assert quantification["input_sha256"]
        assert payload["workflow_run_id"]

        replay = client.post(url, json=request_body)
        assert replay.status_code == 202, replay.text
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["quantification_run"]["id"] == quantification["id"]

        rows = db.query(LcmsQuantificationRun).filter_by(
            alignment_run_id=alignment.id
        ).all()
        assert len(rows) == 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)



def test_verified_botanical_specimen_requires_complete_identification_and_voucher_contract(client, db):
    owner = _user(db, "botanical_incomplete_owner")
    admin = _user(db, "botanical_incomplete_admin", admin=True)
    study = _study(db, owner)
    specimen = BotanicalSpecimen(
        study_id=study.id,
        specimen_code="BOT-MALI-INCOMPLETE",
        reported_taxon="Guiera senegalensis",
        created_by=owner.id,
    )
    db.add(specimen)
    db.commit()
    app.dependency_overrides[require_admin] = lambda: admin
    try:
        response = client.post(
            f"/api/v1/lcmsms/specimens/{specimen.id}/review",
            json={
                "status": "verified",
                "accepted_taxon": "Guiera senegalensis",
                "taxon_identifier": "WCVP:1234567",
                "taxonomy_source": "WCVP",
                "identifier_name": "Test botanist",
                "source_reference": "https://example.org/voucher",
                "rationale": "The specimen record needs its missing identification method and voucher details.",
            },
        )
        assert response.status_code == 422
        assert "méthode d'identification" in response.text
        assert "code voucher" in response.text
        assert "herbier" in response.text
        db.refresh(specimen)
        assert specimen.identification_status == "unverified"
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_botanical_specimen_creator_cannot_verify_own_specimen(client, db):
    curator = _user(db, "botanical_self_curator", admin=True)
    study = _study(db, curator)
    specimen = BotanicalSpecimen(
        study_id=study.id,
        specimen_code="BOT-MALI-SELF-REVIEW",
        reported_taxon="Guiera senegalensis",
        created_by=curator.id,
    )
    db.add(specimen)
    db.commit()
    app.dependency_overrides[require_admin] = lambda: curator
    try:
        response = client.post(
            f"/api/v1/lcmsms/specimens/{specimen.id}/review",
            json={
                "status": "verified",
                "accepted_taxon": "Guiera senegalensis",
                "taxon_identifier": "WCVP:1234567",
                "taxonomy_source": "WCVP",
                "identification_method": "Morphological examination",
                "identifier_name": "Test botanist",
                "voucher_code": "HERB-SELF-01",
                "herbarium_name": "Herbarium de test",
                "source_reference": "https://example.org/voucher",
                "rationale": "An independent botanist must review this collected specimen.",
            },
        )
        assert response.status_code == 403
        assert "indépendante" in response.text
        db.refresh(specimen)
        assert specimen.identification_status == "unverified"
    finally:
        app.dependency_overrides.pop(require_admin, None)



def test_specimen_cannot_be_verified_without_accepted_wcvp_resolution(client, db):
    owner = _user(db, "botanical_no_wcvp_owner")
    admin = _user(db, "botanical_no_wcvp_admin", admin=True)
    study = _study(db, owner)
    specimen = BotanicalSpecimen(
        study_id=study.id,
        specimen_code="BOT-MALI-NO-WCVP",
        reported_taxon="Guiera senegalensis",
        created_by=owner.id,
    )
    db.add(specimen)
    db.commit()
    app.dependency_overrides[require_admin] = lambda: admin
    try:
        response = client.post(
            f"/api/v1/lcmsms/specimens/{specimen.id}/review",
            json={
                "status": "verified",
                "accepted_taxon": "Guiera senegalensis",
                "taxon_identifier": "WCVP:1234567",
                "taxonomy_source": "WCVP",
                "identification_method": "Morphological examination",
                "identifier_name": "Test botanist",
                "voucher_code": "HERB-NO-WCVP-01",
                "herbarium_name": "Herbarium de test",
                "source_reference": "https://example.org/voucher",
                "rationale": "Physical identification must be linked to an accepted WCVP resolution.",
            },
        )
        assert response.status_code == 422
        assert "résolution WCVP acceptée" in response.text
        db.refresh(specimen)
        assert specimen.identification_status == "unverified"
    finally:
        app.dependency_overrides.pop(require_admin, None)
