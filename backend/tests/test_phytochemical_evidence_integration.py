"""Tests for the read-only, ownership-aware PhytoEvidence integration view."""
from app.models.experimental import (
    ResearchAssay,
    ResearchAssayAnalysis,
    ResearchAssayEndpoint,
    ResearchAssayMeasurement,
)
from app.models.lcmsms import (
    BotanicalSpecimen,
    LcmsAlignedFeature,
    LcmsAlignedFeatureMember,
    LcmsAnalyticalBatch,
    LcmsCompoundAnnotation,
    LcmsFeature,
    LcmsFeatureAlignmentRun,
    LcmsFeatureObservation,
    LcmsInjection,
    LcmsNormalizedFeatureQuantification,
    LcmsProcessingRun,
    LcmsQuantificationRun,
    PlantMaterialSample,
)
from app.models.phytochemistry import (
    PhytochemicalCandidate,
    PhytochemicalOccurrenceEvidence,
    TaxonResolution,
)
from app.models.research import ResearchStudy, ResearchTarget
from app.models.virtual_screening import ResearchScreeningCompound, ResearchScreeningRun
from app.models.scientific_workflow import ScientificWorkflowEvent, ScientificWorkflowRun
from app.models.user import User
from app.services.phytochemical_evidence_integration import (
    EvidenceIntegrationError,
    build_phytochemical_evidence_integration,
)
from app.main import app
from app.services.auth_service import get_current_user
import pytest


def _user(db, username: str, *, admin: bool = False) -> User:
    row = User(
        username=username,
        email=f"{username}@nexora.test",
        password="test-password-hash",
        is_admin=admin,
    )
    db.add(row)
    db.flush()
    return row


def _study(db, user: User, name: str) -> ResearchStudy:
    row = ResearchStudy(
        user_id=user.id,
        name=name,
        disease="natural_products",
        pathogen="botanical_material",
        country_focus="Mali",
        region_scope="Sahel",
        metadata_json={},
    )
    db.add(row)
    db.flush()
    return row


def _candidate(db) -> PhytochemicalCandidate:
    row = PhytochemicalCandidate(
        name_raw="Caffeine",
        canonical_name="Caffeine",
        library_source="MANUAL",
        source_row="integration-test",
        source_plant="Coffea arabica",
        accepted_taxon="Coffea arabica",
        taxon_identifier="WCVP:coffee-1",
        taxonomy_resolution_source="WCVP_VIA_GBIF_CHECKLISTBANK",
        taxonomy_cache_sha256="a" * 64,
        occurrence_status="VÉRIFIÉE",
        occurrence_source="https://doi.org/10.1234/integration",
        plant_part="graines",
        identity_status="CANDIDAT_IMPORTÉ",
    )
    db.add(row)
    db.flush()
    return row


def _accepted_resolution(db, candidate: PhytochemicalCandidate) -> TaxonResolution:
    row = TaxonResolution(
        candidate_id=candidate.id,
        requested_name="Coffea arabica",
        matched_name="Coffea arabica",
        matched_identifier="coffee-1",
        matched_status="ACCEPTED",
        matched_rank="SPECIES",
        accepted_name="Coffea arabica",
        accepted_identifier="coffee-1",
        accepted_status="ACCEPTED",
        accepted_rank="SPECIES",
        kingdom="Plantae",
        family="Rubiaceae",
        match_type="EXACT",
        confidence=100,
        provider="GBIF ChecklistBank Species Match API v2 / WCVP",
        checklist_key="f382f0ce-323a-4091-bb9f-add557f3a9a2",
        dataset_doi="10.15468/6h8ucr",
        dataset_version="WCVP v16 (2026-06-04)",
        source_mode="LIVE",
        source_url="https://api.gbif.org/v2/species/match",
        response_sha256="b" * 64,
        response_payload={"test_fixture": True},
        review_status="accepted",
        reviewed_by="curator@nexora.test",
        review_note="WCVP accepted usage reviewed independently.",
    )
    db.add(row)
    db.flush()
    return row


def _approved_workflow(db, owner: User, key: str, scope_type: str, scope_id: int) -> ScientificWorkflowRun:
    reviewer = _user(db, f"reviewer_{scope_type}_{scope_id}", admin=True)
    row = ScientificWorkflowRun(
        workflow_key=key,
        definition_version="1.0.0",
        owner_user_id=owner.id,
        scope_type=scope_type,
        scope_id=str(scope_id),
        state="succeeded",
        requires_quality_review=True,
        context={},
        definition_snapshot={},
        input_manifest={"input_sha256": "c" * 64},
        output_manifest={"output_sha256": "d" * 64},
    )
    db.add(row)
    db.flush()
    db.add(ScientificWorkflowEvent(
        workflow_run_id=row.id,
        actor_user_id=reviewer.id,
        event_type="review_decision",
        from_state="quality_review.pending",
        to_state="quality_review.accepted",
        action="approve",
        reason="Independent quality review accepted.",
        payload={},
    ))
    db.flush()
    return row


def _lcms_fixture(db, owner: User, study: ResearchStudy, candidate: PhytochemicalCandidate, *, accepted: bool = True):
    specimen = BotanicalSpecimen(
        study_id=study.id,
        specimen_code=f"BOT-{study.id}-001",
        reported_taxon="Coffea arabica",
        accepted_taxon="Coffea arabica",
        taxon_identifier="WCVP:coffee-1",
        taxonomy_source="WCVP via GBIF ChecklistBank",
        identification_status="verified",
        identification_method="Morphological examination",
        identifier_name="Independent botanist",
        voucher_code=f"HERB-{study.id}-01",
        herbarium_name="Herbarium de test",
        created_by=owner.id,
        curated_by=owner.id,
        curation_rationale="Voucher and accepted WCVP resolution reviewed.",
    )
    db.add(specimen)
    db.flush()
    sample = PlantMaterialSample(
        study_id=study.id,
        specimen_id=specimen.id,
        sample_code=f"SAMPLE-{study.id}-001",
        plant_part="graines",
        material_state="dried",
        initial_mass_g=25,
        moisture_percent=8,
        preservation_method="Air dried under documented conditions",
        created_by=owner.id,
    )
    db.add(sample)
    db.flush()
    batch = LcmsAnalyticalBatch(
        study_id=study.id,
        batch_code=f"LCMS-{study.id}-001",
        instrument_vendor="Test vendor",
        instrument_model="Test QTOF",
        acquisition_method="HPLC-QTOF-MS/MS",
        created_by=owner.id,
    )
    db.add(batch)
    db.flush()
    injection = LcmsInjection(
        study_id=study.id,
        batch_id=batch.id,
        injection_order=1,
        injection_type="sample",
        sample_label=sample.sample_code,
        plant_sample_id=sample.id,
        technical_replicate=1,
        created_by=owner.id,
    )
    db.add(injection)
    db.flush()
    processing = LcmsProcessingRun(
        study_id=study.id,
        batch_id=batch.id,
        software_name="OpenMS",
        software_version="3.2.0",
        workflow_name="feature_finding",
        workflow_version="1.0.0",
        input_sha256="e" * 64,
        output_sha256="f" * 64,
        status="completed",
        created_by=owner.id,
    )
    db.add(processing)
    db.flush()
    second_injection = LcmsInjection(
        study_id=study.id,
        batch_id=batch.id,
        injection_order=2,
        injection_type="sample",
        sample_label=sample.sample_code + "-technical-2",
        plant_sample_id=sample.id,
        technical_replicate=2,
        created_by=owner.id,
    )
    db.add(second_injection)
    db.flush()
    second_processing = LcmsProcessingRun(
        study_id=study.id,
        batch_id=batch.id,
        software_name="OpenMS",
        software_version="3.2.0",
        workflow_name="feature_finding",
        workflow_version="1.0.0",
        input_sha256="9" * 64,
        output_sha256="8" * 64,
        status="completed",
        created_by=owner.id,
    )
    db.add(second_processing)
    db.flush()
    feature = LcmsFeature(
        processing_run_id=processing.id,
        feature_key=f"feature-{study.id}-1",
        precursor_mz=195.0877,
        retention_time=180.0,
        retention_time_unit="second",
        polarity="positive",
        adduct="[M+H]+",
    )
    second_feature = LcmsFeature(
        processing_run_id=second_processing.id,
        feature_key=f"feature-{study.id}-2",
        precursor_mz=195.0878,
        retention_time=181.0,
        retention_time_unit="second",
        polarity="positive",
        adduct="[M+H]+",
    )
    db.add_all([feature, second_feature])
    db.flush()
    observation = LcmsFeatureObservation(
        feature_id=feature.id,
        injection_id=injection.id,
        observed_mz=195.0877,
        retention_time=180.0,
        peak_area=120000,
        intensity=120000,
        quality_flag="reviewed",
    )
    second_observation = LcmsFeatureObservation(
        feature_id=second_feature.id,
        injection_id=second_injection.id,
        observed_mz=195.0878,
        retention_time=181.0,
        peak_area=118000,
        intensity=118000,
        quality_flag="reviewed",
    )
    db.add_all([observation, second_observation])
    annotation = LcmsCompoundAnnotation(
        feature_id=feature.id,
        phytochem_candidate_id=candidate.id,
        annotation_method="authentic_standard",
        annotation_name="Caffeine",
        molecular_formula="C8H10N4O2",
        inchikey="RYYVLZVUVIJVGH-UHFFFAOYSA-N",
        msi_level=1,
        score_type="standard_match",
        score_value=1.0,
        evidence={"authentic_standard": True},
        status="accepted" if accepted else "candidate",
        review_rationale="Test annotation status.",
        created_by=owner.id,
        reviewed_by=owner.id if accepted else None,
    )
    db.add(annotation)
    db.flush()

    alignment = LcmsFeatureAlignmentRun(
        study_id=study.id,
        batch_id=batch.id,
        scientific_workflow_run_id=None,
        software_name="OpenMS",
        software_version="3.2.0",
        algorithm_name="feature_alignment",
        algorithm_version="1.0.0",
        source_processing_run_ids=[processing.id, second_processing.id],
        source_injection_ids=[injection.id, second_injection.id],
        input_sha256="1" * 64,
        output_sha256="2" * 64,
        status="completed",
        created_by=owner.id,
    )
    db.add(alignment)
    db.flush()
    alignment_workflow = _approved_workflow(
        db, owner, "nexora.lcmsms.feature-alignment",
        "lcms_feature_alignment_run", alignment.id,
    )
    alignment.scientific_workflow_run_id = alignment_workflow.id
    db.flush()
    aligned_feature = LcmsAlignedFeature(
        alignment_run_id=alignment.id,
        polarity="positive",
        consensus_mz=195.0877,
        aligned_retention_time=180.0,
        retention_time_unit="second",
        member_count=2,
        injection_count=2,
        alignment_status="aligned",
    )
    db.add(aligned_feature)
    db.flush()
    db.add(LcmsAlignedFeatureMember(
        alignment_run_id=alignment.id,
        aligned_feature_id=aligned_feature.id,
        processing_run_id=processing.id,
        injection_id=injection.id,
        feature_id=feature.id,
        source_observation_id=observation.id,
        original_mz=195.0877,
        original_retention_time=180.0,
        aligned_retention_time=180.0,
        retention_time_unit="second",
        raw_peak_area=120000,
        raw_intensity=120000,
    ))
    db.add(LcmsAlignedFeatureMember(
        alignment_run_id=alignment.id,
        aligned_feature_id=aligned_feature.id,
        processing_run_id=second_processing.id,
        injection_id=second_injection.id,
        feature_id=second_feature.id,
        source_observation_id=second_observation.id,
        original_mz=195.0878,
        original_retention_time=181.0,
        aligned_retention_time=180.5,
        retention_time_unit="second",
        raw_peak_area=118000,
        raw_intensity=118000,
    ))

    quant_run = LcmsQuantificationRun(
        study_id=study.id,
        batch_id=batch.id,
        alignment_run_id=alignment.id,
        scientific_workflow_run_id=None,
        method="qc_rlsc_loess",
        method_version="1.0.0",
        parameters={"minimum_qc_injections": 8},
        source_injection_ids=[injection.id, second_injection.id],
        input_sha256="3" * 64,
        output_sha256="4" * 64,
        status="completed",
        quantification_manifest={"method": "pooled_qc_robust_loess_log_intensity"},
        created_by=owner.id,
    )
    db.add(quant_run)
    db.flush()
    quant_workflow = _approved_workflow(
        db, owner, "nexora.lcmsms.qc-rlsc-quantification",
        "lcms_quantification_run", quant_run.id,
    )
    quant_run.scientific_workflow_run_id = quant_workflow.id
    db.flush()
    db.add_all([
        LcmsNormalizedFeatureQuantification(
            quantification_run_id=quant_run.id,
            alignment_run_id=alignment.id,
            aligned_feature_id=aligned_feature.id,
            injection_id=injection.id,
            processing_run_id=processing.id,
            feature_id=feature.id,
            qc_type="sample",
            raw_intensity=120000,
            corrected_intensity=105000,
            correction_factor=0.875,
            normalization_status="corrected",
            qc_rsd_before_pct=15.2,
            qc_rsd_after_pct=7.1,
            quality_metrics={"qc_observation_count": 8, "qc_span_ratio": 0.9},
        ),
        LcmsNormalizedFeatureQuantification(
            quantification_run_id=quant_run.id,
            alignment_run_id=alignment.id,
            aligned_feature_id=aligned_feature.id,
            injection_id=second_injection.id,
            processing_run_id=second_processing.id,
            feature_id=second_feature.id,
            qc_type="sample",
            raw_intensity=118000,
            corrected_intensity=103250,
            correction_factor=0.875,
            normalization_status="corrected",
            qc_rsd_before_pct=15.2,
            qc_rsd_after_pct=7.1,
            quality_metrics={"qc_observation_count": 8, "qc_span_ratio": 0.9},
        ),
    ])
    db.flush()
    return {
        "specimen": specimen,
        "sample": sample,
        "batch": batch,
        "injection": injection,
        "processing": processing,
        "second_processing": second_processing,
        "feature": feature,
        "second_feature": second_feature,
        "second_injection": second_injection,
        "annotation": annotation,
        "alignment": alignment,
        "quantification": quant_run,
    }




def _assay_evidence_fixture(
    db,
    owner: User,
    study: ResearchStudy,
    candidate: PhytochemicalCandidate,
    *,
    assay_suffix: str,
    smiles: str = "Cn1c(=O)c2c(ncn2C)n(C)c1=O",
):
    target = ResearchTarget(
        study_id=study.id,
        state="wt",
        protein_name=f"Assay evidence target {assay_suffix}",
        protein_sequence="MKTAYIAKQRQISFVKSHFSRQ",
        structure_source="sequence_resolved",
    )
    db.add(target)
    db.flush()

    screening = ResearchScreeningRun(
        study_id=study.id,
        target_id=target.id,
        status="completed",
        engine="AutoDock Vina",
        protocol={"protocol_id": "test-protocol-1"},
        summary={"status": "completed"},
    )
    db.add(screening)
    db.flush()
    compound = ResearchScreeningCompound(
        screening_id=screening.id,
        input_index=0,
        canonical_smiles=smiles,
        descriptors={},
        qc={"status": "accepted"},
        status="accepted",
        phytochem_candidate_id=candidate.id,
        phytochem_candidate_ids=[candidate.id],
        docking_summary={"interpretation": "computational_only"},
    )
    db.add(compound)
    db.flush()

    curator = _user(db, f"assay_curator_{assay_suffix.replace('-', '_')}", admin=True)
    assay = ResearchAssay(
        study_id=study.id,
        target_id=target.id,
        screening_compound_id=compound.id,
        created_by=owner.id,
        reviewer_id=curator.id,
        assay_type="dose_response",
        assay_name=f"Growth inhibition assay {assay_suffix}",
        test_article=candidate.name_raw,
        compound_identifier=candidate.inchikey or "TEST-INCHIKEY",
        canonical_smiles=smiles,
        source_kind="lab_generated",
        source_reference=f"LAB-RECORD-{assay_suffix}",
        source_url="https://example.org/assay-record",
        laboratory=f"Laboratory {assay_suffix}",
        operator_name="Test operator",
        method_name="Validated colorimetric growth inhibition",
        method_version="SOP-1.0",
        protocol_reference="SOP-ASSAY-001",
        organism="Plasmodium falciparum",
        strain="3D7",
        conditions={"temperature_c": 37, "exposure_hours": 48},
        status="reviewed",
        review_decision="accepted_for_analysis",
        review_notes="Administrative dossier review completed.",
        comparability_status="comparable",
        comparability_group="pf3d7-growth-inhibition-sop-001",
        comparability_rationale="Same test system, method and declared conditions.",
    )
    db.add(assay)
    db.flush()

    for replicate in ("bio-1", "bio-2", "bio-3"):
        for concentration in (1, 3, 10, 30, 100):
            response = 100.0 / (1.0 + concentration / 15.0)
            db.add(ResearchAssayMeasurement(
                assay_id=assay.id,
                sample_role="test_sample",
                sample_label=candidate.name_raw,
                replicate_id=replicate,
                replicate_kind="biological",
                concentration_value=concentration,
                concentration_unit="uM",
                response_name="parasite_viability",
                response_value=response,
                response_unit="% viability",
                qualifier="exact",
                record_key=f"{assay_suffix}-{replicate}-{concentration}",
                metadata_json={"source": "synthetic test fixture"},
            ))

    db.add(ResearchAssayEndpoint(
        assay_id=assay.id,
        endpoint_name="IC50",
        value=15,
        unit="uM",
        qualifier="exact",
        origin="derived_from_raw_data",
        method="four_parameter_logistic",
        method_version="N3XORA_4PL_CLUSTER_BOOTSTRAP_V1",
        confidence_low=10,
        confidence_high=22,
        biological_replicates=3,
        source_reference="LAB-RECORD-" + assay_suffix,
        notes="Relative 4PL midpoint with biological replicate bootstrap interval.",
    ))
    db.add(ResearchAssayAnalysis(
        assay_id=assay.id,
        created_by=owner.id,
        analysis_type="dose_response_4pl",
        algorithm="four_parameter_logistic",
        algorithm_version="N3XORA_4PL_CLUSTER_BOOTSTRAP_V1",
        analysis_key=(f"{assay_suffix:0<64}" if len(assay_suffix) < 64 else assay_suffix[:64]),
        output_sha256="b" * 64,
        status="completed",
        parameters={
            "response_name": "parasite_viability",
            "response_unit": "% viability",
            "concentration_unit": "uM",
            "response_direction": "decreasing",
            "endpoint_name": "IC50",
        },
        result={
            "model": {
                "endpoint_label": "IC50",
                "endpoint_semantics": "Relative midpoint halfway between fitted asymptotes.",
            },
            "fit": {
                "midpoint_concentration": 15.0,
                "concentration_unit": "uM",
                "hill_slope": 1.1,
                "r_squared": 0.98,
                "rmse": 1.2,
            },
            "confidence_interval": {
                "level": 0.95,
                "lower": 10.0,
                "upper": 22.0,
                "unit": "uM",
                "method": "biological_replicate_cluster_bootstrap_percentile",
                "convergence_rate": 0.97,
            },
            "quality": {"status": "interpolated", "warnings": []},
            "sample": {
                "n_biological_replicates": 3,
                "n_concentrations": 5,
                "n_selected_measurement_rows": 15,
                "n_technical_measurements_used": 0,
            },
            "scientific_notice": "Exploratory fitting of declared experimental measurements.",
        },
        input_sha256="a" * 64,
    ))
    db.flush()
    return {
        "assay": assay,
        "screening_compound": compound,
        "screening_run": screening,
        "target": target,
    }


def test_integration_is_read_only_and_keeps_evidence_domains_separate(db):
    owner = _user(db, "integration_owner")
    study = _study(db, owner, "Integration study")
    candidate = _candidate(db)
    resolution = _accepted_resolution(db, candidate)
    db.add(PhytochemicalOccurrenceEvidence(
        candidate_id=candidate.id,
        taxon_resolution_id=resolution.id,
        evidence_type="literature_report",
        plant_part="graines",
        source_reference="https://doi.org/10.1234/integration",
        evidence_statement="A primary source reports caffeine in Coffea arabica seeds.",
        evidence_sha256="5" * 64,
        review_status="accepted",
    ))
    db.flush()
    before = db.query(PhytochemicalOccurrenceEvidence).count()

    result = build_phytochemical_evidence_integration(
        db, candidate_id=candidate.id, user_id=owner.id
    )

    assert result["candidate"]["identity_verified"] is False
    assert result["taxonomy"]["accepted_taxon"] == "Coffea arabica"
    assert result["occurrence"]["accepted_evidence_count"] == 1
    assert result["interpretation_gates"]["ready_for_pharmacological_or_clinical_claim"] is False
    assert "absolute concentration" in " ".join(result["limitations"])
    assert db.query(PhytochemicalOccurrenceEvidence).count() == before
    assert db.query(LcmsCompoundAnnotation).count() == 0


def test_integration_includes_only_owner_visible_analytical_records(db):
    owner = _user(db, "integration_data_owner")
    other = _user(db, "integration_other_owner")
    own_study = _study(db, owner, "Owned analytical study")
    other_study = _study(db, other, "Private analytical study")
    candidate = _candidate(db)
    own = _lcms_fixture(db, owner, own_study, candidate)
    private = _lcms_fixture(db, other, other_study, candidate)
    db.commit()

    own_view = build_phytochemical_evidence_integration(
        db, candidate_id=candidate.id, user_id=owner.id, is_admin=False
    )
    admin_view = build_phytochemical_evidence_integration(
        db, candidate_id=candidate.id, user_id=owner.id, is_admin=True
    )

    assert own_view["analytical"]["annotation_count"] == 1
    assert own_view["analytical"]["annotations"][0]["annotation"]["id"] == own["annotation"].id
    assert own_view["analytical"]["annotations"][0]["observations"][0]["material"]["specimen"]["specimen_code"] == own["specimen"].specimen_code
    assert admin_view["analytical"]["annotation_count"] == 2
    assert private["annotation"].id not in {
        row["annotation"]["id"] for row in own_view["analytical"]["annotations"]
    }


def test_qc_rlsc_signal_requires_both_alignment_and_quantification_review(db):
    owner = _user(db, "integration_qc_owner")
    study = _study(db, owner, "QC integration study")
    candidate = _candidate(db)
    fixture = _lcms_fixture(db, owner, study, candidate)
    db.commit()

    result = build_phytochemical_evidence_integration(
        db, candidate_id=candidate.id, user_id=owner.id
    )
    annotation = result["analytical"]["annotations"][0]
    assert annotation["annotation"]["status"] == "accepted"
    assert annotation["annotation"]["msi_level"] == 1
    assert annotation["interpretation"] == "accepted_msi_level_1_annotation"
    assert len(annotation["qc_rlsc_quantification"]) == 1
    cells = annotation["qc_rlsc_quantification"][0]["cells"]
    assert [cell["injection_id"] for cell in cells] == [
        fixture["injection"].id, fixture["second_injection"].id
    ]
    assert [cell["technical_replicate"] for cell in cells] == [1, 2]
    assert [cell["corrected_intensity"] for cell in cells] == [105000.0, 103250.0]
    assert all(cell["normalization_status"] == "corrected" for cell in cells)
    assert all(cell["material"]["sample"]["sample_code"] == fixture["sample"].sample_code for cell in cells)
    assert all("latitude" not in cell["material"]["specimen"] for cell in cells)
    assert all("longitude" not in cell["material"]["specimen"] for cell in cells)
    assert result["analytical"]["signal_semantics"].find("absolute concentration") >= 0


def test_candidate_annotation_is_not_promoted_to_accepted_msi1_evidence(db):
    owner = _user(db, "integration_candidate_owner")
    study = _study(db, owner, "Candidate annotation study")
    candidate = _candidate(db)
    fixture = _lcms_fixture(db, owner, study, candidate, accepted=False)
    db.commit()

    result = build_phytochemical_evidence_integration(
        db, candidate_id=candidate.id, user_id=owner.id
    )
    annotation = result["analytical"]["annotations"][0]
    assert annotation["annotation"]["status"] == "candidate"
    assert annotation["interpretation"] == "candidate_feature_signal_only"
    assert result["analytical"]["accepted_msi1_annotation_count"] == 0
    assert result["interpretation_gates"]["accepted_msi1_annotation_present"] is False


def test_missing_candidate_returns_controlled_error(db):
    with pytest.raises(EvidenceIntegrationError, match="introuvable"):
        build_phytochemical_evidence_integration(
            db, candidate_id=999999, user_id=1
        )



def test_authenticated_api_exposes_integrated_view_without_mutating_records(client, db):
    owner = _user(db, "integration_api_owner")
    candidate = _candidate(db)
    _accepted_resolution(db, candidate)
    db.commit()
    before = db.query(PhytochemicalCandidate).filter(
        PhytochemicalCandidate.id == candidate.id
    ).one().updated_at
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        response = client.get(
            f"/api/v1/phytochem/{candidate.id}/evidence-integration"
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["view"] == "phytochemical_evidence_integration"
        assert payload["candidate"]["id"] == candidate.id
        assert payload["taxonomy"]["accepted_taxon"] == "Coffea arabica"
        assert payload["analytical"]["annotation_count"] == 0
        db.refresh(candidate)
        assert candidate.updated_at == before
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_integration_api_rejects_invalid_pagination(client, db):
    owner = _user(db, "integration_api_limit_owner")
    candidate = _candidate(db)
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        response = client.get(
            f"/api/v1/phytochem/{candidate.id}/evidence-integration?annotation_limit=1000"
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)



def test_qc_rlsc_signal_is_withheld_without_accepted_quality_review(db):
    owner = _user(db, "integration_qc_pending_owner")
    study = _study(db, owner, "QC pending review study")
    candidate = _candidate(db)
    fixture = _lcms_fixture(db, owner, study, candidate)
    quant_workflow_id = fixture["quantification"].scientific_workflow_run_id
    event = db.query(ScientificWorkflowEvent).filter(
        ScientificWorkflowEvent.workflow_run_id == quant_workflow_id,
        ScientificWorkflowEvent.action == "approve",
    ).one()
    event.action = "reject"
    event.to_state = "quality_review.rejected"
    db.commit()

    result = build_phytochemical_evidence_integration(
        db, candidate_id=candidate.id, user_id=owner.id
    )
    annotation = result["analytical"]["annotations"][0]
    assert annotation["qc_rlsc_quantification"] == []
    assert result["analytical"]["qc_rlsc_quantification_available"] is False
    assert result["interpretation_gates"]["qc_rlsc_signal_available"] is False


def test_qc_rlsc_signal_is_withheld_when_workflow_owner_self_approves(db):
    owner = _user(db, "integration_qc_self_approval_owner")
    study = _study(db, owner, "QC self approval study")
    candidate = _candidate(db)
    fixture = _lcms_fixture(db, owner, study, candidate)
    workflow_ids = [
        fixture["alignment"].scientific_workflow_run_id,
        fixture["quantification"].scientific_workflow_run_id,
    ]
    for workflow_id in workflow_ids:
        event = db.query(ScientificWorkflowEvent).filter(
            ScientificWorkflowEvent.workflow_run_id == workflow_id,
            ScientificWorkflowEvent.action == "approve",
        ).one()
        event.actor_user_id = owner.id
    db.commit()

    result = build_phytochemical_evidence_integration(
        db, candidate_id=candidate.id, user_id=owner.id
    )
    annotation = result["analytical"]["annotations"][0]
    assert annotation["qc_rlsc_quantification"] == []
    assert result["analytical"]["qc_rlsc_quantification_available"] is False
    assert result["interpretation_gates"]["qc_rlsc_signal_available"] is False


def test_quality_review_requires_admin_other_than_workflow_owner(client, db):
    owner = _user(db, "workflow_api_owner", admin=True)
    non_admin = _user(db, "workflow_api_non_admin")
    reviewer = _user(db, "workflow_api_reviewer", admin=True)
    run = ScientificWorkflowRun(
        workflow_key="nexora.lcmsms.qc-rlsc-quantification",
        definition_version="1.0.0",
        owner_user_id=owner.id,
        scope_type="lcms_quantification_run",
        scope_id="123",
        state="quality_review.pending",
        state_version=0,
        requires_quality_review=True,
        context={},
        definition_snapshot={},
        input_manifest={"input_sha256": "a" * 64},
        output_manifest={"output_sha256": "b" * 64},
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # A regular authenticated user cannot perform an institutional quality review.
    app.dependency_overrides[get_current_user] = lambda: non_admin
    try:
        non_admin_response = client.post(
            f"/api/v1/scientific-workflows/{run.id}/actions",
            json={"action": "approve"},
        )
        assert non_admin_response.status_code == 403, non_admin_response.text
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    # Even an administrator cannot approve their own workflow.
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        owner_response = client.post(
            f"/api/v1/scientific-workflows/{run.id}/actions",
            json={"action": "approve"},
        )
        assert owner_response.status_code == 403, owner_response.text
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    app.dependency_overrides[get_current_user] = lambda: reviewer
    try:
        reviewer_response = client.post(
            f"/api/v1/scientific-workflows/{run.id}/actions",
            json={"action": "approve"},
        )
        assert reviewer_response.status_code == 200, reviewer_response.text
        assert reviewer_response.json()["state"] == "succeeded"
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    approve_event = db.query(ScientificWorkflowEvent).filter(
        ScientificWorkflowEvent.workflow_run_id == run.id,
        ScientificWorkflowEvent.action == "approve",
    ).one()
    assert approve_event.actor_user_id == reviewer.id
    assert approve_event.actor_user_id != run.owner_user_id


def test_qc_rlsc_signal_is_withheld_when_approval_actor_is_not_admin(db):
    owner = _user(db, "integration_qc_non_admin_reviewer_owner")
    study = _study(db, owner, "QC non-admin reviewer study")
    candidate = _candidate(db)
    fixture = _lcms_fixture(db, owner, study, candidate)
    workflow_id = fixture["quantification"].scientific_workflow_run_id
    event = db.query(ScientificWorkflowEvent).filter(
        ScientificWorkflowEvent.workflow_run_id == workflow_id,
        ScientificWorkflowEvent.action == "approve",
    ).one()
    reviewer = db.get(User, event.actor_user_id)
    assert reviewer is not None and reviewer.is_admin is True
    reviewer.is_admin = False
    db.commit()

    result = build_phytochemical_evidence_integration(
        db, candidate_id=candidate.id, user_id=owner.id
    )
    annotation = result["analytical"]["annotations"][0]
    assert annotation["qc_rlsc_quantification"] == []
    assert result["analytical"]["qc_rlsc_quantification_available"] is False


def test_qc_rlsc_signal_is_withheld_when_approval_admin_is_inactive(db):
    owner = _user(db, "integration_qc_inactive_reviewer_owner")
    study = _study(db, owner, "QC inactive reviewer study")
    candidate = _candidate(db)
    fixture = _lcms_fixture(db, owner, study, candidate)
    workflow_id = fixture["quantification"].scientific_workflow_run_id
    event = db.query(ScientificWorkflowEvent).filter(
        ScientificWorkflowEvent.workflow_run_id == workflow_id,
        ScientificWorkflowEvent.action == "approve",
    ).one()
    reviewer = db.get(User, event.actor_user_id)
    assert reviewer is not None and reviewer.is_admin is True
    reviewer.status = "inactive"
    db.commit()

    result = build_phytochemical_evidence_integration(
        db, candidate_id=candidate.id, user_id=owner.id
    )
    annotation = result["analytical"]["annotations"][0]
    assert annotation["qc_rlsc_quantification"] == []
    assert result["analytical"]["qc_rlsc_quantification_available"] is False


def test_integration_counts_all_visible_annotations_when_result_is_paginated(db):
    owner = _user(db, "integration_pagination_owner")
    admin = _user(db, "integration_pagination_admin", admin=True)
    first_study = _study(db, owner, "Pagination study one")
    second_study = _study(db, owner, "Pagination study two")
    candidate = _candidate(db)
    _lcms_fixture(db, owner, first_study, candidate)
    _lcms_fixture(db, owner, second_study, candidate)
    db.commit()

    result = build_phytochemical_evidence_integration(
        db,
        candidate_id=candidate.id,
        user_id=admin.id,
        is_admin=True,
        annotation_limit=1,
    )

    assert result["analytical"]["annotation_count"] == 1
    assert result["analytical"]["annotation_total"] == 2
    assert result["analytical"]["accepted_msi1_annotation_count"] == 2
    assert result["analytical"]["qc_rlsc_quantification_checked_annotation_count"] == 1
    assert result["analytical"]["qc_rlsc_quantification_may_be_truncated"] is True
    assert result["pagination"]["annotations_may_be_truncated"] is True


def test_integration_projects_curated_assays_and_4pl_uncertainty_with_owner_scope(db):
    owner = _user(db, "integration_assay_owner")
    other_owner = _user(db, "integration_assay_private_owner")
    own_study = _study(db, owner, "Owned assay evidence study")
    private_study = _study(db, other_owner, "Private assay evidence study")
    candidate = _candidate(db)
    candidate.smiles_canonical = "Cn1c(=O)c2c(ncn2C)n(C)c1=O"
    own = _assay_evidence_fixture(
        db, owner, own_study, candidate, assay_suffix="own-assay", smiles="Cn1c(=O)c2c(ncn2C)n(C)c1=O"
    )
    private = _assay_evidence_fixture(
        db, other_owner, private_study, candidate, assay_suffix="private-assay", smiles="CCN"
    )
    db.commit()

    own_view = build_phytochemical_evidence_integration(
        db, candidate_id=candidate.id, user_id=owner.id
    )
    admin_view = build_phytochemical_evidence_integration(
        db, candidate_id=candidate.id, user_id=owner.id, is_admin=True
    )

    bioactivity = own_view["experimental_bioactivity"]
    assert bioactivity["total"] == 1
    assert bioactivity["accepted_for_analysis_count"] == 1
    assert bioactivity["completed_4pl_analysis_count"] == 1
    assert len(bioactivity["assays"]) == 1

    assay = bioactivity["assays"][0]
    assert assay["assay"]["id"] == own["assay"].id
    assert assay["assay"]["study_id"] == own_study.id
    assert assay["candidate_link"]["phytochem_candidate_id"] == candidate.id
    assert assay["candidate_link"]["canonical_smiles_match"] is True
    assert assay["accepted_for_analysis"] is True
    assert assay["measurements"]["total_rows"] == 15
    assert assay["measurements"]["biological_replicate_count"] == 3
    assert assay["measurements"]["distinct_concentration_count"] == 5
    assert assay["endpoints"][0]["origin"] == "derived_from_raw_data"
    assert assay["analyses"][0]["endpoint_label"] == "IC50"
    assert assay["analyses"][0]["midpoint_concentration"] == 15.0
    assert assay["analyses"][0]["confidence_interval_95"]["lower"] == 10.0
    assert assay["analyses"][0]["confidence_interval_95"]["upper"] == 22.0
    assert assay["analyses"][0]["quality"]["status"] == "interpolated"
    assert assay["analyses"][0]["evidence_grade"] == "D"
    assert "not independent" in assay["scientific_boundary"]

    assert admin_view["experimental_bioactivity"]["total"] == 2
    private_rows = admin_view["experimental_bioactivity"]["assays"]
    private_link = next(x for x in private_rows if x["assay"]["id"] == private["assay"].id)
    assert private_link["candidate_link"]["canonical_smiles_match"] is False
    assert private["assay"].id not in {
        x["assay"]["id"] for x in bioactivity["assays"]
    }


def test_integration_assay_view_is_empty_when_no_screening_compound_links_candidate(db):
    owner = _user(db, "integration_unlinked_assay_owner")
    study = _study(db, owner, "Unlinked assay study")
    candidate = _candidate(db)
    target = ResearchTarget(
        study_id=study.id,
        protein_name="Unlinked assay target",
        protein_sequence="MKTAYIAKQRQISFVKSHFSRQ",
    )
    db.add(target)
    db.flush()
    assay = ResearchAssay(
        study_id=study.id,
        target_id=target.id,
        created_by=owner.id,
        assay_type="dose_response",
        assay_name="Not linked to phyto candidate",
        test_article=candidate.name_raw,
        source_kind="literature_reported",
        source_reference="PMID:123456",
        method_name="Literature method",
        status="reviewed",
        review_decision="accepted_for_analysis",
    )
    db.add(assay)
    db.commit()

    result = build_phytochemical_evidence_integration(
        db, candidate_id=candidate.id, user_id=owner.id
    )
    assert result["experimental_bioactivity"]["total"] == 0
    assert result["interpretation_gates"]["structured_assay_present"] is False
    assert result["interpretation_gates"]["derived_4pl_analysis_present"] is False


def test_integration_api_rejects_invalid_assay_pagination(client, db):
    owner = _user(db, "integration_api_assay_limit_owner")
    candidate = _candidate(db)
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        response = client.get(
            f"/api/v1/phytochem/{candidate.id}/evidence-integration?assay_limit=101"
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)
