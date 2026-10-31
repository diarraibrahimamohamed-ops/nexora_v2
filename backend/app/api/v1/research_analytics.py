"""Experimental table import and statistical analytics endpoints."""
from __future__ import annotations

import hashlib
import os
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.experimental import (
    ResearchAssayAnalysis, ResearchAssayAttachment, ResearchAssayEndpoint,
    ResearchAssayMeasurement, ResearchAssayReviewEvent,
)
from app.models.evidence import ResearchEvidence
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.experimental_analytics import (
    TabularImportError, parse_measurement_table, fit_dose_response,
)
from app.services.scientific_provenance import (
    build_analysis_manifest,
    sha256_json,
)
from app.api.v1.research_assays import (
    MAX_ATTACHMENT_BYTES, MeasurementCreate, _owned_assay,
    _assert_draft, _serialize_attachment,
)

router = APIRouter(prefix="/research", tags=["experimental analytics"])


class DoseResponseAnalysisRequest(BaseModel):
    response_name: str = Field(..., min_length=1, max_length=120)
    direction: Literal["increasing", "decreasing"]
    endpoint_name: Literal["IC50", "EC50", "GI50", "CC50"] = "IC50"
    timepoint_value: Optional[float] = Field(None, ge=0)
    timepoint_unit: Optional[str] = Field(None, max_length=40)
    bootstrap_iterations: int = Field(300, ge=100, le=1000)
    random_seed: int = Field(20261031, ge=0, le=2_147_483_647)

    @model_validator(mode="after")
    def validate_timepoint(self):
        if (self.timepoint_value is None) != (self.timepoint_unit is None):
            raise ValueError("Renseigner ensemble la valeur et l'unité du timepoint.")
        return self


def _parse_uploaded_table(filename: str, content_type: str, payload: bytes) -> dict:
    if len(payload) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, "Fichier trop volumineux (maximum 20 MB).")
    safe_name = os.path.basename((filename or "measurements.csv").replace("\\", "/"))[:255]
    extension = os.path.splitext(safe_name)[1].lower()
    accepted = {
        ".csv": {"text/csv", "application/csv", "application/vnd.ms-excel", "application/octet-stream"},
        ".tsv": {"text/tab-separated-values", "text/plain", "application/octet-stream"},
        ".txt": {"text/plain", "application/octet-stream"},
        ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"},
    }
    if extension not in accepted:
        raise HTTPException(415, "Format tabulaire non accepté. Utiliser CSV, TSV ou XLSX.")
    if (content_type or "application/octet-stream").lower() not in accepted[extension]:
        raise HTTPException(415, "Le type MIME ne correspond pas à l'extension du fichier.")
    try:
        parsed = parse_measurement_table(safe_name, payload)
    except TabularImportError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"filename": safe_name, "extension": extension, "parsed": parsed}


@router.post("/studies/{study_id}/assays/{assay_id}/measurements/import-preview")
async def preview_assay_measurement_import(
    study_id: int,
    assay_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assay = _owned_assay(db, study_id, assay_id, user)
    _assert_draft(assay)
    payload = await file.read(MAX_ATTACHMENT_BYTES + 1)
    try:
        item = _parse_uploaded_table(file.filename or "", file.content_type or "", payload)
        parsed = item["parsed"]
        return {
            "success": True, "source_filename": item["filename"],
            "source_sha256": parsed["source_sha256"],
            "row_count": parsed["row_count"], "error_count": parsed["error_count"],
            "errors": parsed["errors"], "preview": parsed["preview"],
            "columns": parsed["columns"], "requires_confirmation": True,
            "message": "Aucune mesure n'a été enregistrée. Confirmer avec la même empreinte SHA-256.",
        }
    finally:
        await file.close()


@router.post("/studies/{study_id}/assays/{assay_id}/measurements/import", status_code=201)
async def import_assay_measurements(
    study_id: int,
    assay_id: int,
    expected_sha256: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assay = _owned_assay(db, study_id, assay_id, user, lock=True)
    _assert_draft(assay)
    payload = await file.read(MAX_ATTACHMENT_BYTES + 1)
    destination = None
    try:
        item = _parse_uploaded_table(file.filename or "", file.content_type or "", payload)
        parsed = item["parsed"]
        if parsed["source_sha256"] != expected_sha256:
            raise HTTPException(409, "Le fichier ne correspond plus à l'aperçu confirmé. Refaire l'aperçu.")
        if parsed["error_count"]:
            raise HTTPException(422, detail={
                "message": "Le fichier contient des lignes invalides; aucune mesure n'a été importée.",
                "error_count": parsed["error_count"], "errors": parsed["errors"],
            })
        if not parsed["measurements"]:
            raise HTTPException(422, "Aucune mesure importable dans le fichier.")

        existing = {
            value[0] for value in db.query(ResearchAssayMeasurement.record_key).filter(
                ResearchAssayMeasurement.assay_id == assay_id,
                ResearchAssayMeasurement.record_key.isnot(None),
            ).all()
        }
        seen, rows = set(), []
        for raw in parsed["measurements"]:
            if raw["sample_role"] == "test_sample" and assay.assay_type in {"dose_response", "susceptibility"}:
                if raw["concentration_value"] is None or raw["concentration_value"] <= 0:
                    raise HTTPException(422, "Les mesures test_sample de type dose-réponse/susceptibilité exigent une concentration strictement positive.")
            if not raw["record_key"]:
                row_no = raw["metadata"].get("source_row", len(rows) + 2)
                raw["record_key"] = f"import:{parsed['source_sha256'][:32]}:{row_no}"
            if raw["record_key"] in existing or raw["record_key"] in seen:
                raise HTTPException(409, f"record_key déjà utilisé: {raw['record_key']}")
            seen.add(raw["record_key"])
            validated = MeasurementCreate(**raw)
            rows.append(ResearchAssayMeasurement(
                assay_id=assay_id, sample_role=validated.sample_role,
                sample_label=validated.sample_label, replicate_id=validated.replicate_id,
                replicate_kind=validated.replicate_kind,
                biological_sample_id=validated.biological_sample_id,
                concentration_value=validated.concentration_value,
                concentration_unit=validated.concentration_unit,
                response_name=validated.response_name,
                response_value=validated.response_value, response_unit=validated.response_unit,
                qualifier=validated.qualifier, timepoint_value=validated.timepoint_value,
                timepoint_unit=validated.timepoint_unit, well_id=validated.well_id,
                record_key=validated.record_key, metadata_json=validated.metadata,
            ))

        root = os.path.realpath(settings.UPLOAD_DIR)
        folder = os.path.join(root, "experimental_evidence", f"study_{study_id}", f"assay_{assay_id}")
        os.makedirs(folder, mode=0o750, exist_ok=True)
        stored_name = uuid.uuid4().hex + item["extension"]
        destination = os.path.realpath(os.path.join(folder, stored_name))
        if os.path.commonpath([destination, root]) != root:
            raise HTTPException(400, "Chemin de stockage invalide.")
        with open(destination, "xb") as handle:
            handle.write(payload)

        attachment = ResearchAssayAttachment(
            assay_id=assay_id, uploaded_by=user.id, original_name=item["filename"],
            stored_name=stored_name, storage_path=destination, role="raw_data",
            content_type=file.content_type or "application/octet-stream",
            size_bytes=len(payload), sha256=parsed["source_sha256"],
        )
        db.add_all(rows)
        db.add(attachment)
        db.flush()
        db.add(ResearchAssayReviewEvent(
            assay_id=assay_id, actor_id=user.id, event_type="measurements_imported",
            from_status="draft", to_status="draft",
            payload={
                "created_count": len(rows), "source_filename": item["filename"],
                "source_sha256": parsed["source_sha256"],
                "measurement_ids": [row.id for row in rows],
                "source_format": item["extension"].lstrip("."),
            },
        ))
        db.commit()
        return {
            "success": True, "created_count": len(rows),
            "source_sha256": parsed["source_sha256"],
            "attachment": _serialize_attachment(attachment),
            "message": "Mesures importées et fichier source archivé.",
        }
    except HTTPException:
        db.rollback()
        if destination and os.path.exists(destination):
            try:
                os.remove(destination)
            except OSError:
                pass
        raise
    except Exception:
        db.rollback()
        if destination and os.path.exists(destination):
            try:
                os.remove(destination)
            except OSError:
                pass
        raise
    finally:
        await file.close()


@router.post("/studies/{study_id}/assays/{assay_id}/analyses/dose-response", status_code=201)
def create_dose_response_analysis(
    study_id: int,
    assay_id: int,
    body: DoseResponseAnalysisRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Legacy 4PL contract with additive provenance and deterministic replay.

    Its request/response fields, statistical engine and bootstrap limits remain
    unchanged. The shared provenance envelope is added around the stored result.
    """
    assay = _owned_assay(db, study_id, assay_id, user, lock=True)
    if assay.status != "reviewed" or assay.review_decision != "accepted_for_analysis":
        raise HTTPException(409, "Le dossier doit être accepté pour analyse par un curateur avant tout ajustement statistique.")
    measurements = db.query(ResearchAssayMeasurement).filter(
        ResearchAssayMeasurement.assay_id == assay_id
    ).order_by(ResearchAssayMeasurement.id.asc()).all()
    try:
        result = fit_dose_response(
            measurements, response_name=body.response_name, direction=body.direction,
            endpoint_name=body.endpoint_name, timepoint_value=body.timepoint_value,
            timepoint_unit=body.timepoint_unit,
            bootstrap_iterations=body.bootstrap_iterations, random_seed=body.random_seed,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    parameters = body.model_dump()
    assay_manifest_sha256 = (
        db.query(ResearchEvidence.input_sha256)
        .filter(
            ResearchEvidence.study_id == study_id,
            ResearchEvidence.object_type == "assay",
            ResearchEvidence.object_id == str(assay_id),
            ResearchEvidence.evidence_kind == "experimental_assay_submission",
        )
        .order_by(ResearchEvidence.created_at.desc())
        .scalar()
    )
    # Keep the historical engine input hash (selected biological observations)
    # intact. The analysis key additionally binds parameters and algorithm version.
    analysis_key = sha256_json({
        "assay_id": assay_id,
        "algorithm": result["algorithm"],
        "algorithm_version": result["algorithm_version"],
        "input_sha256": result["input_sha256"],
        "parameters": parameters,
    })
    existing = db.query(ResearchAssayAnalysis).filter(
        ResearchAssayAnalysis.analysis_key == analysis_key
    ).first()
    if existing and existing.status == "completed":
        return {
            "success": True,
            "analysis_id": existing.id,
            "idempotent_replay": True,
            "analysis": {
                "id": existing.id,
                "algorithm": existing.algorithm,
                "algorithm_version": existing.algorithm_version,
                "parameters": existing.parameters,
                "result": existing.result,
                "input_sha256": existing.input_sha256,
                "output_sha256": existing.output_sha256,
                "analysis_key": existing.analysis_key,
                "created_at": existing.created_at.isoformat() if existing.created_at else None,
            },
        }

    output_sha256 = sha256_json(result)
    manifest_inputs = [{
        "role": "selected_biological_measurements",
        "object_type": "research_assay_measurements",
        "object_id": f"assay:{assay_id}",
        "sha256": result["input_sha256"],
        "record_count": result["fit_statistics"]["observations_used"],
        "biological_replicates": result["data_summary"]["complete_biological_replicates"],
    }]
    if assay_manifest_sha256:
        manifest_inputs.append({
            "role": "curator_submitted_assay_manifest",
            "object_type": "research_assay_submission",
            "object_id": str(assay_id),
            "sha256": assay_manifest_sha256,
        })
    interpretation_limit = result.get("interpretation_limit")
    manifest = build_analysis_manifest(
        algorithm=result["algorithm"],
        algorithm_version=result["algorithm_version"],
        inputs=manifest_inputs,
        parameters=parameters,
        output_sha256=output_sha256,
        execution={
            "mode": "synchronous",
            "route": "/research/studies/{study_id}/assays/{assay_id}/analyses/dose-response",
            "engine_module": "experimental_analytics",
        },
        interpretation_limit=interpretation_limit,
    )
    result["provenance"] = {
        "manifest": manifest,
        "manifest_sha256": manifest["manifest_sha256"],
    }

    analysis = ResearchAssayAnalysis(
        assay_id=assay_id,
        created_by=user.id,
        analysis_type="dose_response_4pl",
        algorithm=result["algorithm"],
        algorithm_version=result["algorithm_version"],
        analysis_key=analysis_key,
        output_sha256=output_sha256,
        status="completed",
        parameters=parameters,
        result=result,
        input_sha256=result["input_sha256"],
    )
    db.add(analysis)
    db.flush()
    ci = result["confidence_interval"]
    db.add(ResearchAssayEndpoint(
        assay_id=assay_id, endpoint_name=result["endpoint_name"],
        value=result["estimate"], unit=result["concentration_unit"],
        qualifier="exact", origin="derived_from_raw_data",
        method="Four-parameter logistic fit (4PL); biological-replicate cluster bootstrap",
        method_version=result["algorithm_version"],
        confidence_low=ci.get("lower") if ci.get("status") == "available" else None,
        confidence_high=ci.get("upper") if ci.get("status") == "available" else None,
        biological_replicates=result["data_summary"]["complete_biological_replicates"],
        notes=f"Calcul Nexora; analyse #{analysis.id}. Résultat conditionnel au modèle et au protocole.",
        metadata_json={
            "analysis_id": analysis.id,
            "input_sha256": analysis.input_sha256,
            "output_sha256": output_sha256,
            "provenance_manifest_sha256": manifest["manifest_sha256"],
            "confidence_interval_status": ci.get("status"),
            "warnings": result["warnings"],
        },
    ))
    db.add(ResearchAssayReviewEvent(
        assay_id=assay_id, actor_id=user.id, event_type="dose_response_analysis_completed",
        from_status="reviewed", to_status="reviewed",
        payload={
            "analysis_id": analysis.id, "algorithm": result["algorithm"],
            "algorithm_version": result["algorithm_version"],
            "analysis_key": analysis_key,
            "input_sha256": result["input_sha256"],
            "output_sha256": output_sha256,
            "provenance_manifest_sha256": manifest["manifest_sha256"],
            "endpoint_name": result["endpoint_name"], "endpoint_estimate": result["estimate"],
            "confidence_interval_status": ci.get("status"),
        },
    ))
    db.add(ResearchEvidence(
        study_id=study_id,
        object_type="assay_analysis",
        object_id=str(analysis.id),
        evidence_kind="dose_response_4pl_legacy_analysis",
        evidence_status="computational_hypothesis",
        evidence_grade="D",
        claim=(
            f"Ajustement 4PL historique exploratoire de {assay.test_article}: "
            f"{result['endpoint_name']}={result['estimate']:.6g} "
            f"{result['concentration_unit']}; IC={ci.get('status')}."
        ),
        organism=assay.organism or assay.cell_line,
        source_type="curated_experimental_assay",
        source_id=f"assay:{assay_id}",
        source_title=assay.assay_name,
        study_reference=assay.protocol_reference,
        method="Legacy four-parameter logistic fit with biological-replicate cluster bootstrap",
        tool_name="N3XORA Experimental Analytics",
        tool_version=result["algorithm_version"],
        input_sha256=analysis.input_sha256,
        output_sha256=output_sha256,
        limitations=interpretation_limit,
        metadata_json={
            "analysis_id": analysis.id,
            "analysis_key": analysis_key,
            "engine_module": "experimental_analytics",
            "provenance_manifest": manifest,
            "provenance_manifest_sha256": manifest["manifest_sha256"],
        },
        created_by=user.id,
    ))
    db.commit()
    db.refresh(analysis)
    return {
        "success": True,
        "analysis_id": analysis.id,
        "idempotent_replay": False,
        "analysis": {
            "id": analysis.id, "algorithm": analysis.algorithm,
            "algorithm_version": analysis.algorithm_version, "parameters": analysis.parameters,
            "result": analysis.result, "input_sha256": analysis.input_sha256,
            "output_sha256": analysis.output_sha256, "analysis_key": analysis.analysis_key,
            "created_at": analysis.created_at.isoformat() if analysis.created_at else None,
        },
    }


@router.get("/studies/{study_id}/assays/{assay_id}/analyses")
def list_assay_analyses(
    study_id: int,
    assay_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_assay(db, study_id, assay_id, user)
    rows = db.query(ResearchAssayAnalysis).filter(
        ResearchAssayAnalysis.assay_id == assay_id
    ).order_by(ResearchAssayAnalysis.created_at.desc()).all()
    return {
        "success": True,
        "analyses": [{
            "id": row.id, "analysis_type": row.analysis_type,
            "algorithm": row.algorithm, "algorithm_version": row.algorithm_version,
            "analysis_key": row.analysis_key, "output_sha256": row.output_sha256,
            "provenance_manifest_sha256": (
                ((row.result or {}).get("provenance") or {}).get("manifest_sha256")
            ),
            "status": row.status,
            "parameters": row.parameters, "result": row.result,
            "input_sha256": row.input_sha256,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows],
    }
