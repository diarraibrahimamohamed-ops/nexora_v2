"""Read-only integration of botanical, chemical and LC-MS/MS evidence.

This projection intentionally adds no duplicate scientific records. It connects
existing curation and analytical entities while preserving their independent
status, provenance and limitations.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.lcmsms import (
    BotanicalSpecimen,
    ExtractionBatch,
    LcmsAlignedFeatureMember,
    LcmsCompoundAnnotation,
    LcmsFeature,
    LcmsFeatureAlignmentRun,
    LcmsFeatureObservation,
    LcmsInjection,
    LcmsNormalizedFeatureQuantification,
    LcmsProcessingRun,
    LcmsQuantificationRun,
    PlantFraction,
    PlantMaterialSample,
)
from app.models.experimental import (
    ResearchAssay,
    ResearchAssayAnalysis,
    ResearchAssayAttachment,
    ResearchAssayEndpoint,
    ResearchAssayMeasurement,
)
from app.models.phytochemistry import (
    PhytochemicalCandidate,
    PhytochemicalOccurrenceEvidence,
    TaxonResolution,
)
from app.models.research import ResearchStudy
from app.models.virtual_screening import ResearchScreeningCompound, ResearchScreeningRun
from app.models.scientific_workflow import ScientificWorkflowEvent, ScientificWorkflowRun
from app.models.user import User, UserStatus
from app.services.phytochemistry_catalog import IDENTITY_VERIFIED, OCCURRENCE_VERIFIED


class EvidenceIntegrationError(ValueError):
    """An integrated evidence projection could not be built."""


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _is_study_visible(study_id: int, *, is_admin: bool, visible_study_ids: set[int]) -> bool:
    return is_admin or study_id in visible_study_ids


def _workflow_has_accepted_review(
    db: Session,
    workflow_run_id: int | None,
    *,
    expected_key: str,
    expected_scope_type: str,
    expected_scope_id: int,
    expected_owner_id: int | None,
) -> bool:
    if workflow_run_id is None or expected_owner_id is None:
        return False
    workflow = db.get(ScientificWorkflowRun, workflow_run_id)
    if (
        workflow is None
        or workflow.state != "succeeded"
        or workflow.requires_quality_review is not True
        or workflow.workflow_key != expected_key
        or workflow.scope_type != expected_scope_type
        or workflow.scope_id != str(expected_scope_id)
        or workflow.owner_user_id != expected_owner_id
    ):
        return False
    approval = db.query(ScientificWorkflowEvent.actor_user_id).filter(
        ScientificWorkflowEvent.workflow_run_id == workflow_run_id,
        ScientificWorkflowEvent.action == "approve",
        ScientificWorkflowEvent.to_state == "quality_review.accepted",
        ScientificWorkflowEvent.actor_user_id.isnot(None),
        ScientificWorkflowEvent.actor_user_id != expected_owner_id,
    ).order_by(ScientificWorkflowEvent.created_at.desc(), ScientificWorkflowEvent.id.desc()).first()
    if approval is None:
        return False
    reviewer = db.get(User, approval[0])
    return bool(
        reviewer is not None
        and reviewer.is_admin
        and reviewer.status == UserStatus.active
    )


def _material_snapshot(db: Session, injection: LcmsInjection) -> dict[str, Any] | None:
    """Return only non-geographic specimen/material provenance for one injection."""
    sample: PlantMaterialSample | None = None
    fraction: PlantFraction | None = None
    extraction: ExtractionBatch | None = None

    if injection.plant_sample_id is not None:
        sample = db.get(PlantMaterialSample, injection.plant_sample_id)
    elif injection.fraction_id is not None:
        fraction = db.get(PlantFraction, injection.fraction_id)
        if fraction is not None and fraction.study_id == injection.study_id:
            extraction = db.get(ExtractionBatch, fraction.extraction_batch_id)
            if extraction is not None and extraction.study_id == injection.study_id:
                sample = db.get(PlantMaterialSample, extraction.sample_id)

    if sample is None or sample.study_id != injection.study_id:
        return None
    specimen = db.get(BotanicalSpecimen, sample.specimen_id)
    if specimen is None or specimen.study_id != injection.study_id:
        return None

    return {
        "specimen": {
            "id": specimen.id,
            "specimen_code": specimen.specimen_code,
            "reported_taxon": specimen.reported_taxon,
            "accepted_taxon": specimen.accepted_taxon,
            "taxon_identifier": specimen.taxon_identifier,
            "identification_status": specimen.identification_status,
            "voucher_code": specimen.voucher_code,
            "herbarium_name": specimen.herbarium_name,
        },
        "sample": {
            "id": sample.id,
            "sample_code": sample.sample_code,
            "plant_part": sample.plant_part,
            "material_state": sample.material_state,
            "initial_mass_g": _number(sample.initial_mass_g),
            "moisture_percent": _number(sample.moisture_percent),
            "preservation_method": sample.preservation_method,
            "sample_status": sample.sample_status,
        },
        "extraction": None if extraction is None else {
            "id": extraction.id,
            "batch_code": extraction.batch_code,
            "extraction_method": extraction.extraction_method,
            "method_version": extraction.method_version,
            "solvent_system": extraction.solvent_system or [],
            "input_mass_g": _number(extraction.input_mass_g),
            "extract_mass_g": _number(extraction.extract_mass_g),
            "yield_percent": _number(extraction.yield_percent),
            "status": extraction.status,
        },
        "fraction": None if fraction is None else {
            "id": fraction.id,
            "fraction_code": fraction.fraction_code,
            "separation_method": fraction.separation_method,
            "method_version": fraction.method_version,
            "elution_system": fraction.elution_system or [],
            "recovered_mass_g": _number(fraction.recovered_mass_g),
            "yield_percent": _number(fraction.yield_percent),
            "status": fraction.status,
        },
    }


def _quantification_for_feature(
    db: Session,
    *,
    feature_id: int,
    processing_run_id: int,
    study_id: int,
    is_admin: bool,
    visible_study_ids: set[int],
    cell_limit: int,
) -> list[dict[str, Any]]:
    """Expose only approved, completed QC-RLSC sample-intensity cells."""
    member_rows = db.query(
        LcmsAlignedFeatureMember,
        LcmsFeatureAlignmentRun,
    ).join(
        LcmsFeature,
        LcmsFeature.id == LcmsAlignedFeatureMember.feature_id,
    ).join(
        LcmsFeatureAlignmentRun,
        LcmsFeatureAlignmentRun.id == LcmsAlignedFeatureMember.alignment_run_id,
    ).filter(
        LcmsAlignedFeatureMember.feature_id == feature_id,
        LcmsAlignedFeatureMember.processing_run_id == LcmsFeature.processing_run_id,
        LcmsFeature.processing_run_id == processing_run_id,
        LcmsFeatureAlignmentRun.study_id == study_id,
        LcmsFeatureAlignmentRun.status == "completed",
        LcmsFeatureAlignmentRun.output_sha256.isnot(None),
    ).order_by(
        LcmsFeatureAlignmentRun.id.desc(),
        LcmsAlignedFeatureMember.aligned_feature_id.asc(),
    ).all()

    results: list[dict[str, Any]] = []
    seen_runs: set[tuple[int, int]] = set()
    for member, alignment in member_rows:
        if not _is_study_visible(
            alignment.study_id, is_admin=is_admin, visible_study_ids=visible_study_ids
        ):
            continue
        if (
            member.processing_run_id not in set(alignment.source_processing_run_ids or [])
            or member.injection_id not in set(alignment.source_injection_ids or [])
        ):
            continue
        if not _workflow_has_accepted_review(
            db,
            alignment.scientific_workflow_run_id,
            expected_key="nexora.lcmsms.feature-alignment",
            expected_scope_type="lcms_feature_alignment_run",
            expected_scope_id=alignment.id,
            expected_owner_id=alignment.created_by,
        ):
            continue

        quant_runs = db.query(LcmsQuantificationRun).filter(
            LcmsQuantificationRun.alignment_run_id == alignment.id,
            LcmsQuantificationRun.status == "completed",
            LcmsQuantificationRun.output_sha256.isnot(None),
        ).order_by(LcmsQuantificationRun.id.desc()).all()
        for quant_run in quant_runs:
            if quant_run.study_id != alignment.study_id or quant_run.batch_id != alignment.batch_id:
                continue
            if not _workflow_has_accepted_review(
                db,
                quant_run.scientific_workflow_run_id,
                expected_key="nexora.lcmsms.qc-rlsc-quantification",
                expected_scope_type="lcms_quantification_run",
                expected_scope_id=quant_run.id,
                expected_owner_id=quant_run.created_by,
            ):
                continue
            run_key = (quant_run.id, member.aligned_feature_id)
            if run_key in seen_runs:
                continue
            seen_runs.add(run_key)

            cells = db.query(
                LcmsNormalizedFeatureQuantification,
                LcmsInjection,
            ).join(
                LcmsInjection,
                LcmsInjection.id == LcmsNormalizedFeatureQuantification.injection_id,
            ).filter(
                LcmsNormalizedFeatureQuantification.quantification_run_id == quant_run.id,
                LcmsNormalizedFeatureQuantification.aligned_feature_id == member.aligned_feature_id,
                LcmsInjection.study_id == alignment.study_id,
                LcmsInjection.injection_type == "sample",
            ).order_by(
                LcmsInjection.injection_order.asc(),
                LcmsInjection.id.asc(),
            ).limit(cell_limit + 1).all()
            cells_truncated = len(cells) > cell_limit
            cells = cells[:cell_limit]

            sample_cells = []
            for cell, injection in cells:
                member_for_cell = db.query(LcmsAlignedFeatureMember.id).filter(
                    LcmsAlignedFeatureMember.alignment_run_id == alignment.id,
                    LcmsAlignedFeatureMember.aligned_feature_id == member.aligned_feature_id,
                    LcmsAlignedFeatureMember.injection_id == injection.id,
                    LcmsAlignedFeatureMember.processing_run_id == cell.processing_run_id,
                    LcmsAlignedFeatureMember.feature_id == cell.feature_id,
                ).first()
                if member_for_cell is None:
                    continue
                if injection.plant_sample_id is None and injection.fraction_id is None:
                    continue
                if not _is_study_visible(
                    injection.study_id, is_admin=is_admin, visible_study_ids=visible_study_ids
                ):
                    continue
                if injection.id not in set(quant_run.source_injection_ids or []):
                    continue
                material = _material_snapshot(db, injection)
                sample_cells.append({
                    "injection_id": injection.id,
                    "injection_order": injection.injection_order,
                    "technical_replicate": injection.technical_replicate,
                    "material": material,
                    "raw_intensity": _number(cell.raw_intensity),
                    "corrected_intensity": _number(cell.corrected_intensity),
                    "correction_factor": _number(cell.correction_factor),
                    "normalization_status": cell.normalization_status,
                    "qc_rsd_before_pct": _number(cell.qc_rsd_before_pct),
                    "qc_rsd_after_pct": _number(cell.qc_rsd_after_pct),
                    "quality_metrics": cell.quality_metrics or {},
                })
            if sample_cells:
                results.append({
                    "quantification_run": {
                        "id": quant_run.id,
                        "method": quant_run.method,
                        "method_version": quant_run.method_version,
                        "status": quant_run.status,
                        "input_sha256": quant_run.input_sha256,
                        "output_sha256": quant_run.output_sha256,
                        "completed_at": quant_run.completed_at.isoformat() if quant_run.completed_at else None,
                        "workflow_run_id": quant_run.scientific_workflow_run_id,
                    },
                    "alignment": {
                        "id": alignment.id,
                        "software": alignment.software_name,
                        "software_version": alignment.software_version,
                        "algorithm": alignment.algorithm_name,
                        "algorithm_version": alignment.algorithm_version,
                        "output_sha256": alignment.output_sha256,
                    },
                    "aligned_feature_id": member.aligned_feature_id,
                    "signal_unit": "instrument_intensity",
                    "cells": sample_cells,
                    "cells_limit": cell_limit,
                    "cells_truncated": cells_truncated,
                })
    return results




def _assay_analysis_snapshot(row: ResearchAssayAnalysis) -> dict[str, Any]:
    result = row.result or {}
    fit = result.get("fit") or {}
    interval = result.get("confidence_interval") or {}
    quality = result.get("quality") or {}
    sample = result.get("sample") or {}
    model = result.get("model") or {}
    return {
        "id": row.id,
        "analysis_type": row.analysis_type,
        "algorithm": row.algorithm,
        "algorithm_version": row.algorithm_version,
        "status": row.status,
        "input_sha256": row.input_sha256,
        "output_sha256": row.output_sha256,
        "endpoint_label": model.get("endpoint_label"),
        "endpoint_semantics": model.get("endpoint_semantics"),
        "midpoint_concentration": _number(fit.get("midpoint_concentration")),
        "concentration_unit": fit.get("concentration_unit") or interval.get("unit"),
        "hill_slope": _number(fit.get("hill_slope")),
        "r_squared": _number(fit.get("r_squared")),
        "rmse": _number(fit.get("rmse")),
        "confidence_interval_95": {
            "lower": _number(interval.get("lower")),
            "upper": _number(interval.get("upper")),
            "unit": interval.get("unit"),
            "method": interval.get("method"),
            "convergence_rate": _number(interval.get("convergence_rate")),
        } if interval else None,
        "quality": {
            "status": quality.get("status"),
            "warnings": quality.get("warnings") or [],
        },
        "sample": {
            "biological_replicates": sample.get("n_biological_replicates"),
            "concentrations": sample.get("n_concentrations"),
            "selected_measurement_rows": sample.get("n_selected_measurement_rows"),
            "technical_measurements_used": sample.get("n_technical_measurements_used"),
        },
        "scientific_notice": result.get("scientific_notice"),
        "evidence_grade": "D",
        "evidence_status": "computational_hypothesis",
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _assay_evidence_snapshot(
    db: Session,
    *,
    candidate: PhytochemicalCandidate,
    user_id: int,
    is_admin: bool,
    visible_study_ids: set[int],
    assay_limit: int,
) -> dict[str, Any]:
    query = db.query(
        ResearchAssay,
        ResearchScreeningCompound,
        ResearchScreeningRun,
        ResearchStudy,
    ).join(
        ResearchScreeningCompound,
        ResearchScreeningCompound.id == ResearchAssay.screening_compound_id,
    ).join(
        ResearchScreeningRun,
        ResearchScreeningRun.id == ResearchScreeningCompound.screening_id,
    ).join(
        ResearchStudy,
        ResearchStudy.id == ResearchAssay.study_id,
    ).filter(
        ResearchScreeningCompound.phytochem_candidate_id == candidate.id,
        ResearchAssay.study_id == ResearchScreeningRun.study_id,
    )
    if not is_admin:
        if visible_study_ids:
            query = query.filter(ResearchAssay.study_id.in_(visible_study_ids))
        else:
            query = query.filter(ResearchAssay.study_id == -1)

    total = query.count()
    rows = query.order_by(
        ResearchAssay.created_at.desc(), ResearchAssay.id.desc()
    ).limit(assay_limit + 1).all()
    truncated = len(rows) > assay_limit
    rows = rows[:assay_limit]
    snapshots = []

    for assay, screening_compound, screening_run, study in rows:
        endpoints = db.query(ResearchAssayEndpoint).filter(
            ResearchAssayEndpoint.assay_id == assay.id
        ).order_by(
            ResearchAssayEndpoint.created_at.desc(),
            ResearchAssayEndpoint.id.desc(),
        ).limit(100).all()
        analyses = db.query(ResearchAssayAnalysis).filter(
            ResearchAssayAnalysis.assay_id == assay.id
        ).order_by(
            ResearchAssayAnalysis.created_at.desc(),
            ResearchAssayAnalysis.id.desc(),
        ).limit(20).all()
        attachments = db.query(ResearchAssayAttachment).filter(
            ResearchAssayAttachment.assay_id == assay.id
        ).order_by(
            ResearchAssayAttachment.created_at.desc(),
            ResearchAssayAttachment.id.desc(),
        ).limit(100).all()

        measurement_query = db.query(ResearchAssayMeasurement).filter(
            ResearchAssayMeasurement.assay_id == assay.id
        )
        measurement_count = measurement_query.count()
        test_measurement_query = measurement_query.filter(
            ResearchAssayMeasurement.sample_role == "test_sample"
        )
        biological_replicates = {
            str(value[0]) for value in test_measurement_query.with_entities(
                ResearchAssayMeasurement.replicate_id
            ).filter(
                ResearchAssayMeasurement.replicate_kind == "biological"
            ).distinct().all()
        }
        technical_measurement_count = test_measurement_query.filter(
            ResearchAssayMeasurement.replicate_kind == "technical"
        ).count()
        concentration_levels = {
            str(value[0]) for value in test_measurement_query.with_entities(
                ResearchAssayMeasurement.concentration_value
            ).filter(
                ResearchAssayMeasurement.concentration_value.isnot(None)
            ).distinct().all()
        }
        response_definitions = test_measurement_query.with_entities(
            ResearchAssayMeasurement.response_name,
            ResearchAssayMeasurement.response_unit,
        ).distinct().limit(30).all()

        endpoint_snapshots = [{
            "id": endpoint.id,
            "name": endpoint.endpoint_name,
            "value": _number(endpoint.value),
            "unit": endpoint.unit,
            "qualifier": endpoint.qualifier,
            "origin": endpoint.origin,
            "method": endpoint.method,
            "method_version": endpoint.method_version,
            "confidence_interval_95": {
                "lower": _number(endpoint.confidence_low),
                "upper": _number(endpoint.confidence_high),
                "unit": endpoint.unit,
            } if endpoint.confidence_low is not None or endpoint.confidence_high is not None else None,
            "biological_replicates": endpoint.biological_replicates,
            "source_reference": endpoint.source_reference,
            "notes": endpoint.notes,
            "created_at": endpoint.created_at.isoformat() if endpoint.created_at else None,
        } for endpoint in endpoints]

        analysis_snapshots = [_assay_analysis_snapshot(row) for row in analyses]
        accepted_for_analysis = (
            assay.status == "reviewed"
            and assay.review_decision == "accepted_for_analysis"
        )
        completed_4pl = [
            item for item in analysis_snapshots
            if item["analysis_type"] == "dose_response_4pl" and item["status"] == "completed"
        ]
        structure_match = None
        if candidate.smiles_canonical and screening_compound.canonical_smiles:
            structure_match = candidate.smiles_canonical == screening_compound.canonical_smiles

        snapshots.append({
            "assay": {
                "id": assay.id,
                "name": assay.assay_name,
                "type": assay.assay_type,
                "test_article": assay.test_article,
                "compound_identifier": assay.compound_identifier,
                "canonical_smiles": assay.canonical_smiles,
                "source_kind": assay.source_kind,
                "source_reference": assay.source_reference,
                "source_url": assay.source_url,
                "laboratory": assay.laboratory,
                "operator_name": assay.operator_name,
                "method": assay.method_name,
                "method_version": assay.method_version,
                "protocol_reference": assay.protocol_reference,
                "assay_date": assay.assay_date.isoformat() if assay.assay_date else None,
                "organism": assay.organism,
                "strain": assay.strain,
                "cell_line": assay.cell_line,
                "conditions": assay.conditions or {},
                "status": assay.status,
                "review_decision": assay.review_decision,
                "review_notes": assay.review_notes,
                "reviewed_at": assay.reviewed_at.isoformat() if assay.reviewed_at else None,
                "comparability_status": assay.comparability_status,
                "comparability_group": assay.comparability_group,
                "comparability_rationale": assay.comparability_rationale,
                "study_id": study.id,
                "study_name": study.name,
            },
            "candidate_link": {
                "screening_compound_id": screening_compound.id,
                "screening_run_id": screening_run.id,
                "screening_run_status": screening_run.status,
                "screening_compound_status": screening_compound.status,
                "phytochem_candidate_id": screening_compound.phytochem_candidate_id,
                "canonical_smiles_match": structure_match,
            },
            "measurements": {
                "total_rows": measurement_count,
                "test_sample_rows": test_measurement_query.count(),
                "biological_replicate_count": len(biological_replicates),
                "technical_measurement_rows": technical_measurement_count,
                "distinct_concentration_count": len(concentration_levels),
                "response_definitions": [
                    {"name": name, "unit": unit} for name, unit in response_definitions
                ],
            },
            "endpoints": endpoint_snapshots,
            "analyses": analysis_snapshots,
            "analysis_count": len(analysis_snapshots),
            "analyses_may_be_truncated": len(analyses) == 20,
            "attachments": [{
                "id": attachment.id,
                "name": attachment.original_name,
                "role": attachment.role,
                "content_type": attachment.content_type,
                "size_bytes": attachment.size_bytes,
                "sha256": attachment.sha256,
            } for attachment in attachments],
            "attachment_count": len(attachments),
            "accepted_for_analysis": accepted_for_analysis,
            "completed_4pl_analysis_present": bool(completed_4pl),
            "interpretation": (
                "curator_accepted_assay_with_derived_4pl_analysis"
                if accepted_for_analysis and completed_4pl
                else "curator_accepted_assay_without_4pl_analysis"
                if accepted_for_analysis
                else "reported_assay_record_not_accepted_for_analysis"
            ),
            "scientific_boundary": (
                "Administrative acceptance establishes dossier completeness for analysis, not independent "
                "biological replication, pharmacological efficacy, selectivity, safety or clinical relevance. "
                "A 4PL midpoint is relative to fitted asymptotes and must be interpreted with its confidence "
                "interval, quality warnings, biological replicate count and tested concentration range."
            ),
        })

    accepted_count = sum(1 for item in snapshots if item["accepted_for_analysis"])
    analysis_count = sum(1 for item in snapshots if item["completed_4pl_analysis_present"])
    return {
        "total": total,
        "count": len(snapshots),
        "accepted_for_analysis_count": accepted_count,
        "completed_4pl_analysis_count": analysis_count,
        "assays_may_be_truncated": truncated,
        "assays": snapshots,
    }

def build_phytochemical_evidence_integration(
    db: Session,
    *,
    candidate_id: int,
    user_id: int,
    is_admin: bool = False,
    annotation_limit: int = 50,
    quantification_cell_limit: int = 500,
    assay_limit: int = 50,
) -> dict[str, Any]:
    """Build a read-only, ownership-aware candidate evidence projection."""
    if not 1 <= annotation_limit <= 100:
        raise EvidenceIntegrationError("annotation_limit doit être compris entre 1 et 100.")
    if not 1 <= quantification_cell_limit <= 1000:
        raise EvidenceIntegrationError("quantification_cell_limit doit être compris entre 1 et 1000.")
    if not 1 <= assay_limit <= 100:
        raise EvidenceIntegrationError("assay_limit doit être compris entre 1 et 100.")
    candidate = db.get(PhytochemicalCandidate, candidate_id)
    if candidate is None:
        raise EvidenceIntegrationError("Candidat phytochimique introuvable.")

    visible_study_ids = set()
    if not is_admin:
        visible_study_ids = {
            int(row[0]) for row in db.query(ResearchStudy.id).filter(
                ResearchStudy.user_id == user_id
            ).all()
        }

    taxon_query = db.query(TaxonResolution).filter(
        TaxonResolution.candidate_id == candidate_id
    )
    taxon_count = taxon_query.count()
    accepted_taxon_count = taxon_query.filter(TaxonResolution.review_status == "accepted").count()
    taxon_rows = taxon_query.order_by(
        TaxonResolution.created_at.desc(), TaxonResolution.id.desc()
    ).limit(501).all()
    taxon_resolutions_truncated = len(taxon_rows) > 500
    taxon_rows = taxon_rows[:500]
    taxon_resolutions = [{
        "id": row.id,
        "requested_name": row.requested_name,
        "matched_name": row.matched_name,
        "matched_identifier": row.matched_identifier,
        "matched_status": row.matched_status,
        "matched_rank": row.matched_rank,
        "accepted_name": row.accepted_name,
        "accepted_identifier": row.accepted_identifier,
        "accepted_status": row.accepted_status,
        "accepted_rank": row.accepted_rank,
        "provider": row.provider,
        "checklist_key": row.checklist_key,
        "dataset_doi": row.dataset_doi,
        "dataset_version": row.dataset_version,
        "source_mode": row.source_mode,
        "source_url": row.source_url,
        "response_sha256": row.response_sha256,
        "review_status": row.review_status,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_note": row.review_note,
    } for row in taxon_rows]

    evidence_query = db.query(PhytochemicalOccurrenceEvidence).filter(
        PhytochemicalOccurrenceEvidence.candidate_id == candidate_id
    )
    if not is_admin:
        if visible_study_ids:
            evidence_query = evidence_query.outerjoin(
                BotanicalSpecimen,
                BotanicalSpecimen.id == PhytochemicalOccurrenceEvidence.specimen_id,
            ).filter(
                or_(
                    PhytochemicalOccurrenceEvidence.specimen_id.is_(None),
                    BotanicalSpecimen.study_id.in_(visible_study_ids),
                )
            )
        else:
            evidence_query = evidence_query.filter(
                PhytochemicalOccurrenceEvidence.specimen_id.is_(None)
            )
    occurrence_count = evidence_query.count()
    accepted_occurrence_count = evidence_query.filter(
        PhytochemicalOccurrenceEvidence.review_status == "accepted"
    ).count()
    pending_occurrence_count = evidence_query.filter(
        PhytochemicalOccurrenceEvidence.review_status == "pending_review"
    ).count()
    occurrence_rows = evidence_query.order_by(
        PhytochemicalOccurrenceEvidence.created_at.desc(),
        PhytochemicalOccurrenceEvidence.id.desc(),
    ).limit(501).all()
    occurrence_claims_truncated = len(occurrence_rows) > 500
    occurrence_rows = occurrence_rows[:500]
    occurrence_claims = []
    for row in occurrence_rows:
        if row.specimen_id is not None:
            specimen = db.get(BotanicalSpecimen, row.specimen_id)
            if specimen is None or not _is_study_visible(
                specimen.study_id, is_admin=is_admin, visible_study_ids=visible_study_ids
            ):
                continue
        occurrence_claims.append({
            "id": row.id,
            "evidence_type": row.evidence_type,
            "plant_part": row.plant_part,
            "source_reference": row.source_reference,
            "source_title": row.source_title,
            "source_locator": row.source_locator,
            "evidence_statement": row.evidence_statement,
            "analytical_method": row.analytical_method,
            "measurement_value": _number(row.measurement_value),
            "measurement_unit": row.measurement_unit,
            "evidence_sha256": row.evidence_sha256,
            "review_status": row.review_status,
            "specimen_id": row.specimen_id,
            "sample_id": row.sample_id,
            "extraction_batch_id": row.extraction_batch_id,
            "fraction_id": row.fraction_id,
            "lcms_annotation_id": row.lcms_annotation_id,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            "review_note": row.review_note,
        })

    annotation_query = db.query(LcmsCompoundAnnotation).join(
        LcmsFeature,
        LcmsFeature.id == LcmsCompoundAnnotation.feature_id,
    ).join(
        LcmsProcessingRun,
        LcmsProcessingRun.id == LcmsFeature.processing_run_id,
    ).filter(
        LcmsCompoundAnnotation.phytochem_candidate_id == candidate_id
    )
    if not is_admin:
        if not visible_study_ids:
            annotation_query = annotation_query.filter(LcmsProcessingRun.study_id == -1)
        else:
            annotation_query = annotation_query.filter(
                LcmsProcessingRun.study_id.in_(visible_study_ids)
            )
    annotation_total = annotation_query.count()
    accepted_msi1_count = annotation_query.filter(
        LcmsCompoundAnnotation.status == "accepted",
        LcmsCompoundAnnotation.msi_level == 1,
    ).count()
    annotation_rows = annotation_query.order_by(
        LcmsCompoundAnnotation.created_at.desc(),
        LcmsCompoundAnnotation.id.desc(),
    ).limit(annotation_limit + 1).all()
    annotations_truncated = len(annotation_rows) > annotation_limit
    annotations = annotation_rows[:annotation_limit]

    integrated_annotations = []
    quantification_available = False
    for annotation in annotations:
        feature = db.get(LcmsFeature, annotation.feature_id)
        processing_run = db.get(LcmsProcessingRun, feature.processing_run_id) if feature else None
        if feature is None or processing_run is None:
            continue

        observation_query = db.query(
            LcmsFeatureObservation,
            LcmsInjection,
        ).join(
            LcmsInjection,
            LcmsInjection.id == LcmsFeatureObservation.injection_id,
        ).filter(
            LcmsFeatureObservation.feature_id == feature.id,
            LcmsInjection.study_id == processing_run.study_id,
        ).order_by(
            LcmsInjection.injection_order.asc(),
            LcmsInjection.id.asc(),
        )
        observations = []
        observation_rows = observation_query.limit(1001).all()
        observations_truncated = len(observation_rows) > 1000
        for observation, injection in observation_rows[:1000]:
            if not _is_study_visible(
                injection.study_id, is_admin=is_admin, visible_study_ids=visible_study_ids
            ):
                continue
            material = _material_snapshot(db, injection)
            if material is None:
                continue
            observations.append({
                "observation_id": observation.id,
                "injection_id": injection.id,
                "injection_order": injection.injection_order,
                "injection_type": injection.injection_type,
                "technical_replicate": injection.technical_replicate,
                "observed_mz": _number(observation.observed_mz),
                "retention_time": _number(observation.retention_time),
                "peak_area": _number(observation.peak_area),
                "peak_height": _number(observation.peak_height),
                "intensity": _number(observation.intensity),
                "signal_to_noise": _number(observation.signal_to_noise),
                "quality_flag": observation.quality_flag,
                "material": material,
            })

        accepted_msi1 = annotation.status == "accepted" and annotation.msi_level == 1
        quantification = _quantification_for_feature(
            db,
            feature_id=feature.id,
            processing_run_id=processing_run.id,
            study_id=processing_run.study_id,
            is_admin=is_admin,
            visible_study_ids=visible_study_ids,
            cell_limit=quantification_cell_limit,
        )
        quantification_available = quantification_available or bool(quantification)

        integrated_annotations.append({
            "annotation": {
                "id": annotation.id,
                "status": annotation.status,
                "annotation_name": annotation.annotation_name,
                "annotation_method": annotation.annotation_method,
                "molecular_formula": annotation.molecular_formula,
                "inchikey": annotation.inchikey,
                "msi_level": annotation.msi_level,
                "score_type": annotation.score_type,
                "score_value": _number(annotation.score_value),
                "review_rationale": annotation.review_rationale,
                "reviewed_at": annotation.reviewed_at.isoformat() if annotation.reviewed_at else None,
            },
            "interpretation": (
                "accepted_msi_level_1_annotation"
                if accepted_msi1
                else "candidate_feature_signal_only"
            ),
            "feature": {
                "id": feature.id,
                "feature_key": feature.feature_key,
                "precursor_mz": _number(feature.precursor_mz),
                "retention_time": _number(feature.retention_time),
                "retention_time_unit": feature.retention_time_unit,
                "polarity": feature.polarity,
                "charge": feature.charge,
                "adduct": feature.adduct,
            },
            "processing": {
                "id": processing_run.id,
                "software_name": processing_run.software_name,
                "software_version": processing_run.software_version,
                "workflow_name": processing_run.workflow_name,
                "workflow_version": processing_run.workflow_version,
                "status": processing_run.status,
                "input_sha256": processing_run.input_sha256,
                "output_sha256": processing_run.output_sha256,
                "completed_at": processing_run.completed_at.isoformat() if processing_run.completed_at else None,
            },
            "observations": observations,
            "observations_truncated": observations_truncated,
            "qc_rlsc_quantification": quantification,
        })

    bioactivity_assays = _assay_evidence_snapshot(
        db,
        candidate=candidate,
        user_id=user_id,
        is_admin=is_admin,
        visible_study_ids=visible_study_ids,
        assay_limit=assay_limit,
    )

    return {
        "success": True,
        "view": "phytochemical_evidence_integration",
        "view_version": "1.1.0",
        "candidate": {
            "id": candidate.id,
            "name_raw": candidate.name_raw,
            "canonical_name": candidate.canonical_name,
            "synonyms": candidate.synonyms or [],
            "pubchem_cid": candidate.pubchem_cid,
            "inchikey": candidate.inchikey,
            "formula": candidate.formula,
            "smiles_canonical": candidate.smiles_canonical,
            "chemical_class": candidate.chemical_class,
            "library_source": candidate.library_source,
            "source_license": candidate.source_license,
            "identity_source": candidate.identity_source,
            "identity_reference_sha256": candidate.identity_reference_sha256,
            "identity_status": candidate.identity_status,
            "identity_verified": candidate.identity_status == IDENTITY_VERIFIED,
        },
        "taxonomy": {
            "accepted_taxon": candidate.accepted_taxon,
            "taxon_identifier": candidate.taxon_identifier,
            "resolution_source": candidate.taxonomy_resolution_source,
            "resolution_cache_sha256": candidate.taxonomy_cache_sha256,
            "accepted_resolution_count": accepted_taxon_count,
            "resolution_total": taxon_count,
            "resolutions_truncated": taxon_resolutions_truncated,
            "resolutions": taxon_resolutions,
        },
        "occurrence": {
            "status": candidate.occurrence_status,
            "verified": candidate.occurrence_status == OCCURRENCE_VERIFIED,
            "source": candidate.occurrence_source,
            "plant_part": candidate.plant_part,
            "accepted_evidence_count": accepted_occurrence_count,
            "pending_evidence_count": pending_occurrence_count,
            "claim_total": occurrence_count,
            "claims_truncated": occurrence_claims_truncated,
            "claims": occurrence_claims,
        },
        "experimental_bioactivity": bioactivity_assays,
        "reported_bioactivity": {
            "target_organism": candidate.target_organism,
            "target_accession": candidate.target_accession,
            "assay_type": candidate.assay_type,
            "endpoint_value": _number(candidate.endpoint_value),
            "endpoint_unit": candidate.endpoint_unit,
            "study_reference": candidate.study_reference,
            "evidence_grade": candidate.evidence_grade,
            "curator_review_note": candidate.review_note,
            "reviewed_at": candidate.reviewed_at.isoformat() if candidate.reviewed_at else None,
            "interpretation": "reported candidate-level assay metadata; not independently validated pharmacological efficacy or safety by this projection",
        },
        "analytical": {
            "annotation_count": len(integrated_annotations),
            "annotation_total": annotation_total,
            "accepted_msi1_annotation_count": accepted_msi1_count,
            "qc_rlsc_quantification_available": quantification_available,
            "qc_rlsc_quantification_checked_annotation_count": len(integrated_annotations),
            "qc_rlsc_quantification_may_be_truncated": annotations_truncated,
            "annotations": integrated_annotations,
            "signal_semantics": "Raw and QC-RLSC corrected instrument intensity per sample injection where available; not an absolute concentration.",
        },
        "interpretation_gates": {
            "chemical_identity_verified": candidate.identity_status == IDENTITY_VERIFIED,
            "accepted_wcvp_name_present": bool(candidate.accepted_taxon and candidate.taxon_identifier),
            "accepted_occurrence_claim_present": accepted_occurrence_count > 0,
            "accepted_msi1_annotation_present": accepted_msi1_count > 0,
            "qc_rlsc_signal_available": quantification_available,
            "structured_assay_present": bioactivity_assays["total"] > 0,
            "curator_accepted_assay_present": bioactivity_assays["accepted_for_analysis_count"] > 0,
            "derived_4pl_analysis_present": bioactivity_assays["completed_4pl_analysis_count"] > 0,
            "ready_for_pharmacological_or_clinical_claim": False,
        },
        "limitations": [
            "Taxonomic nomenclature does not identify a physical specimen or prove compound occurrence.",
            "A candidate or LC-MS/MS annotation status is not pharmacological activity evidence.",
            "An accepted assay dossier is an administrative completeness decision, not proof of independent biological validation.",
            "4PL midpoints and bootstrap intervals describe the selected measured dataset; they do not establish clinical potency or therapeutic efficacy.",
            "QC-RLSC values are instrument intensities, not absolute concentrations such as mg/g.",
            "Non-detections, insufficient QC, and out-of-range injections remain distinct; no zero imputation or LOESS extrapolation is performed.",
            "Biological and technical replicates are not pooled into an unqualified mean in this view.",
            "No therapeutic efficacy, safety, or clinical suitability is inferred.",
        ],
        "pagination": {
            "annotation_limit": annotation_limit,
            "assay_limit": assay_limit,
            "assay_count": bioactivity_assays["count"],
            "assay_total": bioactivity_assays["total"],
            "assays_may_be_truncated": bioactivity_assays["assays_may_be_truncated"],
            "annotation_count": len(integrated_annotations),
            "annotation_total": annotation_total,
            "annotations_may_be_truncated": annotations_truncated,
        },
    }
