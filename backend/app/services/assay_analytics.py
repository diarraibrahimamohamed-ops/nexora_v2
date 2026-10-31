"""Shared persistence service for review-gated experimental assay analyses.

The numerical 4PL implementation lives in assay_statistics.py. This service
preserves the accepted-assay gate, immutable analysis record, evidence event,
and input/output provenance for both synchronous and workflow execution.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.evidence import ResearchEvidence
from app.models.experimental import (
    ResearchAssay,
    ResearchAssayAnalysis,
    ResearchAssayMeasurement,
    ResearchAssayReviewEvent,
)
from app.models.research import ResearchStudy
from app.services.assay_statistics import (
    ALGORITHM_VERSION,
    AssayStatisticsError,
    fit_dose_response_4pl,
)


class AssayAnalysisNotReady(RuntimeError):
    """The dossier is not eligible for the requested analysis."""


def _as_float(value: Any):
    return float(value) if value is not None else None


def serialize_analysis(row: ResearchAssayAnalysis) -> dict:
    return {
        "id": row.id,
        "assay_id": row.assay_id,
        "analysis_type": row.analysis_type,
        "algorithm_version": row.algorithm_version,
        "analysis_key": row.analysis_key,
        "input_sha256": row.input_sha256,
        "output_sha256": row.output_sha256,
        "parameters": row.parameters or {},
        "result": row.result or {},
        "status": row.status,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def execute_dose_response_analysis(
    db: Session,
    *,
    study_id: int,
    assay_id: int,
    user_id: int,
    parameters: dict[str, Any],
) -> dict:
    """Run or replay a 4PL analysis after rechecking ownership and curator approval.

    Both the HTTP endpoint and the scientific-workflow adapter call this
    function. The statistical algorithm itself is deliberately unchanged.
    """
    study = db.query(ResearchStudy).filter(
        ResearchStudy.id == study_id,
        ResearchStudy.user_id == user_id,
    ).first()
    if study is None:
        raise AssayAnalysisNotReady("Étude introuvable.")

    assay = db.query(ResearchAssay).filter(
        ResearchAssay.id == assay_id,
        ResearchAssay.study_id == study_id,
    ).with_for_update().first()
    if assay is None:
        raise AssayAnalysisNotReady("Dossier expérimental introuvable.")
    if assay.status != "reviewed" or assay.review_decision != "accepted_for_analysis":
        raise AssayAnalysisNotReady(
            "La revue curatoriale doit avoir accepté le dossier pour analyse avant un ajustement statistique."
        )
    if assay.assay_type not in {
        "dose_response", "growth_inhibition", "cytotoxicity", "enzyme_activity", "binding"
    }:
        raise AssayAnalysisNotReady(
            "Le type d'essai ne relève pas d'un ajustement dose-réponse IC50/EC50."
        )

    measurements = db.query(ResearchAssayMeasurement).filter(
        ResearchAssayMeasurement.assay_id == assay_id
    ).order_by(ResearchAssayMeasurement.id.asc()).all()
    try:
        result = fit_dose_response_4pl(
            measurements,
            response_name=parameters["response_name"],
            response_unit=parameters["response_unit"],
            concentration_unit=parameters["concentration_unit"],
            response_direction=parameters["response_direction"],
            endpoint_name=parameters["endpoint_name"],
            sample_label=parameters.get("sample_label"),
            timepoint_value=parameters.get("timepoint_value"),
            timepoint_unit=parameters.get("timepoint_unit"),
            bootstrap_iterations=int(parameters.get("bootstrap_iterations", 1000)),
            random_seed=int(parameters.get("random_seed", 42)),
        )
    except AssayStatisticsError:
        raise

    selected_ids = set(result["sample"]["selected_measurement_ids"])
    selected_rows = [row for row in measurements if row.id in selected_ids]
    selected_payload = [{
        "id": row.id,
        "sample_role": row.sample_role,
        "sample_label": row.sample_label,
        "replicate_id": row.replicate_id,
        "replicate_kind": row.replicate_kind,
        "biological_sample_id": row.biological_sample_id,
        "concentration_value": _as_float(row.concentration_value),
        "concentration_unit": row.concentration_unit,
        "response_name": row.response_name,
        "response_value": _as_float(row.response_value),
        "response_unit": row.response_unit,
        "qualifier": row.qualifier,
        "timepoint_value": _as_float(row.timepoint_value),
        "timepoint_unit": row.timepoint_unit,
        "well_id": row.well_id,
        "record_key": row.record_key,
        "metadata": row.metadata_json or {},
    } for row in selected_rows]
    input_payload = {
        "assay_id": assay_id,
        "assay_manifest_sha256": (
            db.query(ResearchEvidence.input_sha256)
            .filter(
                ResearchEvidence.study_id == study_id,
                ResearchEvidence.object_type == "assay",
                ResearchEvidence.object_id == str(assay_id),
                ResearchEvidence.evidence_kind == "experimental_assay_submission",
            )
            .order_by(ResearchEvidence.created_at.desc())
            .scalar()
        ),
        "algorithm_version": ALGORITHM_VERSION,
        "parameters": parameters,
        "selected_measurements": selected_payload,
    }
    input_encoded = json.dumps(
        input_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    input_sha256 = hashlib.sha256(input_encoded).hexdigest()
    analysis_key = hashlib.sha256(
        f"{assay_id}:{ALGORITHM_VERSION}:{input_sha256}".encode("utf-8")
    ).hexdigest()

    existing = db.query(ResearchAssayAnalysis).filter(
        ResearchAssayAnalysis.analysis_key == analysis_key
    ).first()
    if existing and existing.status == "completed":
        return {
            "success": True,
            "idempotent_replay": True,
            "analysis": serialize_analysis(existing),
        }
    if existing and existing.status not in {"pending", "failed"}:
        raise AssayAnalysisNotReady(
            f"Analyse existante dans un état non exécutable: {existing.status}."
        )

    result["provenance"] = {
        "input_sha256": input_sha256,
        "measurement_ids": sorted(selected_ids),
        "assay_id": assay_id,
        "assay_manifest_sha256": input_payload["assay_manifest_sha256"],
    }
    output_encoded = json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    output_sha256 = hashlib.sha256(output_encoded).hexdigest()

    if existing:
        analysis = existing
        analysis.status = "completed"
        analysis.created_by = analysis.created_by or user_id
        analysis.parameters = parameters
        analysis.result = result
        analysis.input_sha256 = input_sha256
        analysis.output_sha256 = output_sha256
    else:
        analysis = ResearchAssayAnalysis(
            assay_id=assay_id,
            created_by=user_id,
            analysis_type="dose_response_4pl",
            algorithm="four_parameter_logistic",
            algorithm_version=ALGORITHM_VERSION,
            analysis_key=analysis_key,
            input_sha256=input_sha256,
            output_sha256=output_sha256,
            parameters=parameters,
            result=result,
            status="completed",
        )
        db.add(analysis)
        db.flush()
        db.add(ResearchAssayReviewEvent(
            assay_id=assay_id,
            actor_id=user_id,
            event_type="statistical_analysis_created",
            from_status="reviewed",
            to_status="reviewed",
            payload={
                "analysis_id": analysis.id,
                "analysis_type": "dose_response_4pl",
                "algorithm_version": ALGORITHM_VERSION,
                "input_sha256": input_sha256,
                "output_sha256": output_sha256,
                "quality_status": result["quality"]["status"],
                "endpoint_name": parameters["endpoint_name"],
                "midpoint": result["fit"]["midpoint_concentration"],
                "midpoint_unit": parameters["concentration_unit"],
            },
        ))
        db.add(ResearchEvidence(
            study_id=study_id,
            object_type="assay_analysis",
            object_id=str(analysis.id),
            evidence_kind="dose_response_4pl_analysis",
            evidence_status="computational_hypothesis",
            evidence_grade="D",
            claim=(
                f"Ajustement 4PL exploratoire de {assay.test_article}: "
                f"midpoint relatif {parameters['endpoint_name']}="
                f"{result['fit']['midpoint_concentration']:.6g} "
                f"{parameters['concentration_unit']}; statut qualité={result['quality']['status']}."
            ),
            organism=assay.organism or assay.cell_line,
            source_type="curated_experimental_assay",
            source_id=f"assay:{assay_id}",
            source_title=assay.assay_name,
            study_reference=assay.protocol_reference,
            method="Nonlinear least-squares 4PL with biological-replicate cluster bootstrap percentile CI",
            tool_name="N3XORA Assay Statistics",
            tool_version=ALGORITHM_VERSION,
            input_sha256=input_sha256,
            output_sha256=output_sha256,
            limitations=(
                "Analyse computationnelle secondaire des données expérimentales déclarées; "
                "le midpoint 4PL est relatif aux asymptotes ajustées, pas nécessairement au niveau absolu de 50 %. "
                "L'intervalle percentile bootstrap utilise les réplicats biologiques complets. "
                "Ce résultat ne constitue pas une réplication indépendante ni une validation clinique."
            ),
            metadata_json={
                "assay_id": assay_id,
                "analysis_id": analysis.id,
                "quality_status": result["quality"]["status"],
                "endpoint_name": parameters["endpoint_name"],
                "response_direction": parameters["response_direction"],
                "concentration_unit": parameters["concentration_unit"],
            },
            created_by=user_id,
        ))

    db.flush()
    db.refresh(analysis)
    return {
        "success": True,
        "idempotent_replay": False,
        "analysis": serialize_analysis(analysis),
    }
