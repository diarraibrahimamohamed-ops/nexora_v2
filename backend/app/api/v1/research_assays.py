"""API for structured experimental evidence and curator review."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.docking import DockingJob
from app.models.evidence import ResearchEvidence
from app.models.experimental import (
    ResearchAssay, ResearchAssayAttachment, ResearchAssayEndpoint,
    ResearchAssayMeasurement, ResearchAssayReviewEvent,
)
from app.models.research import ResearchStudy, ResearchTarget, ResearchVariant
from app.models.user import User
from app.models.virtual_screening import ResearchScreeningCompound, ResearchScreeningRun
from app.services.auth_service import get_current_user, require_admin

router = APIRouter(prefix="/research", tags=["experimental evidence"])

ASSAY_TYPES = {
    "dose_response", "susceptibility", "growth_inhibition", "binding",
    "cytotoxicity", "enzyme_activity", "other",
}
SAMPLE_ROLES = {
    "test_sample", "positive_control", "negative_control", "vehicle_control",
    "blank", "reference_standard", "untreated_control",
}
CONTROL_ROLES = SAMPLE_ROLES - {"test_sample"}
ENDPOINT_NAMES = {
    "IC50", "EC50", "MIC", "MBC", "CC50", "GI50", "IC90", "EC90",
    "KD", "KI", "OTHER",
}
ALLOWED_FILE_TYPES = {
    ".csv": {"text/csv", "application/csv", "application/vnd.ms-excel", "application/octet-stream"},
    ".tsv": {"text/tab-separated-values", "text/plain", "application/octet-stream"},
    ".txt": {"text/plain", "application/octet-stream"},
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".png": {"image/png", "application/octet-stream"},
    ".jpg": {"image/jpeg", "application/octet-stream"},
    ".jpeg": {"image/jpeg", "application/octet-stream"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"},
}
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def _owned_study(db: Session, study_id: int, user: User) -> ResearchStudy:
    study = db.query(ResearchStudy).filter(
        ResearchStudy.id == study_id,
        ResearchStudy.user_id == user.id,
    ).first()
    if study is None:
        raise HTTPException(404, "Étude introuvable.")
    return study


def _owned_assay(db: Session, study_id: int, assay_id: int, user: User, *, lock: bool = False) -> ResearchAssay:
    query = (
        db.query(ResearchAssay)
        .join(ResearchStudy, ResearchAssay.study_id == ResearchStudy.id)
        .filter(
            ResearchAssay.id == assay_id,
            ResearchAssay.study_id == study_id,
            ResearchStudy.user_id == user.id,
        )
    )
    if lock:
        query = query.with_for_update()
    assay = query.first()
    if assay is None:
        raise HTTPException(404, "Essai expérimental introuvable.")
    return assay


def _assert_draft(assay: ResearchAssay) -> None:
    if assay.status != "draft":
        raise HTTPException(
            409,
            detail={
                "message": "Le dossier expérimental est verrouillé après soumission.",
                "status": assay.status,
            },
        )


def _as_float(value):
    return float(value) if value is not None else None


def _serialize_measurement(row: ResearchAssayMeasurement) -> dict:
    return {
        "id": row.id,
        "sample_role": row.sample_role,
        "sample_label": row.sample_label,
        "replicate_id": row.replicate_id,
        "replicate_kind": row.replicate_kind,
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
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_endpoint(row: ResearchAssayEndpoint) -> dict:
    return {
        "id": row.id,
        "endpoint_name": row.endpoint_name,
        "value": _as_float(row.value),
        "unit": row.unit,
        "qualifier": row.qualifier,
        "origin": row.origin,
        "method": row.method,
        "method_version": row.method_version,
        "confidence_low": _as_float(row.confidence_low),
        "confidence_high": _as_float(row.confidence_high),
        "biological_replicates": row.biological_replicates,
        "source_reference": row.source_reference,
        "notes": row.notes,
        "metadata": row.metadata_json or {},
    }


def _serialize_attachment(row: ResearchAssayAttachment) -> dict:
    return {
        "id": row.id,
        "original_name": row.original_name,
        "role": row.role,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "download_url": f"/api/v1/research/studies/{row.assay.study_id}/assays/{row.assay_id}/attachments/{row.id}",
    }


def _serialize_assay(assay: ResearchAssay, *, include_data: bool = False) -> dict:
    data = {
        "id": assay.id,
        "study_id": assay.study_id,
        "target_id": assay.target_id,
        "variant_id": assay.variant_id,
        "screening_compound_id": assay.screening_compound_id,
        "assay_type": assay.assay_type,
        "assay_name": assay.assay_name,
        "test_article": assay.test_article,
        "compound_identifier": assay.compound_identifier,
        "canonical_smiles": assay.canonical_smiles,
        "source_kind": assay.source_kind,
        "source_reference": assay.source_reference,
        "source_url": assay.source_url,
        "laboratory": assay.laboratory,
        "operator_name": assay.operator_name,
        "method_name": assay.method_name,
        "method_version": assay.method_version,
        "protocol_reference": assay.protocol_reference,
        "assay_date": assay.assay_date.isoformat() if assay.assay_date else None,
        "organism": assay.organism,
        "strain": assay.strain,
        "cell_line": assay.cell_line,
        "conditions": assay.conditions or {},
        "notes": assay.notes,
        "status": assay.status,
        "review_decision": assay.review_decision,
        "review_notes": assay.review_notes,
        "reviewer_id": assay.reviewer_id,
        "submitted_at": assay.submitted_at.isoformat() if assay.submitted_at else None,
        "reviewed_at": assay.reviewed_at.isoformat() if assay.reviewed_at else None,
        "comparability_status": assay.comparability_status,
        "comparability_group": assay.comparability_group,
        "comparability_rationale": assay.comparability_rationale,
        "created_by": assay.created_by,
        "created_at": assay.created_at.isoformat() if assay.created_at else None,
        "updated_at": assay.updated_at.isoformat() if assay.updated_at else None,
        "measurement_count": len(assay.measurements),
        "endpoint_count": len(assay.endpoints),
        "attachment_count": len(assay.attachments),
    }
    if include_data:
        data["measurements"] = [_serialize_measurement(x) for x in assay.measurements]
        data["endpoints"] = [_serialize_endpoint(x) for x in assay.endpoints]
        data["attachments"] = [_serialize_attachment(x) for x in assay.attachments]
        data["events"] = [{
            "id": x.id,
            "actor_id": x.actor_id,
            "event_type": x.event_type,
            "from_status": x.from_status,
            "to_status": x.to_status,
            "decision": x.decision,
            "reason": x.reason,
            "payload": x.payload or {},
            "created_at": x.created_at.isoformat() if x.created_at else None,
        } for x in assay.events]
    data["interpretation_notice"] = (
        "La revue administrative examine la complétude et la traçabilité du dossier. "
        "Elle ne constitue pas une réplication indépendante ni une validation biologique automatique."
    )
    return data


class AssayCreate(BaseModel):
    assay_type: str = Field(..., min_length=3, max_length=40)
    assay_name: str = Field(..., min_length=3, max_length=255)
    test_article: str = Field(..., min_length=2, max_length=255)
    target_id: Optional[int] = Field(None, ge=1)
    variant_id: Optional[int] = Field(None, ge=1)
    screening_compound_id: Optional[int] = Field(None, ge=1)
    compound_identifier: Optional[str] = Field(None, max_length=255)
    canonical_smiles: Optional[str] = Field(None, max_length=1000)
    source_kind: str = Field("lab_generated", pattern="^(lab_generated|literature_reported|public_dataset)$")
    source_reference: Optional[str] = Field(None, max_length=2000)
    source_url: Optional[str] = Field(None, max_length=2000)
    laboratory: Optional[str] = Field(None, max_length=255)
    operator_name: Optional[str] = Field(None, max_length=255)
    method_name: str = Field(..., min_length=2, max_length=255)
    method_version: Optional[str] = Field(None, max_length=120)
    protocol_reference: Optional[str] = Field(None, max_length=2000)
    assay_date: Optional[date] = None
    organism: Optional[str] = Field(None, max_length=255)
    strain: Optional[str] = Field(None, max_length=255)
    cell_line: Optional[str] = Field(None, max_length=255)
    conditions: dict = Field(default_factory=dict)
    notes: Optional[str] = Field(None, max_length=6000)

    @field_validator("assay_type")
    @classmethod
    def valid_assay_type(cls, value):
        if value not in ASSAY_TYPES:
            raise ValueError("Type d'essai inconnu.")
        return value

    @field_validator("canonical_smiles")
    @classmethod
    def clean_smiles(cls, value):
        return value.strip() if value else value

    @model_validator(mode="after")
    def validate_context(self):
        if not (self.organism or self.cell_line):
            raise ValueError("Renseigner l'organisme ou la lignée cellulaire.")
        if self.source_kind == "lab_generated":
            if not self.laboratory:
                raise ValueError("Un essai généré au laboratoire doit identifier le laboratoire.")
            if not self.assay_date:
                raise ValueError("La date de l'essai est obligatoire pour les données de laboratoire.")
        if self.source_kind in {"literature_reported", "public_dataset"} and not (self.source_reference or self.source_url):
            raise ValueError("Une référence ou une URL de source est obligatoire pour ces données.")
        return self


class MeasurementCreate(BaseModel):
    sample_role: str = Field(..., min_length=3, max_length=30)
    sample_label: str = Field(..., min_length=1, max_length=255)
    replicate_id: str = Field(..., min_length=1, max_length=80)
    replicate_kind: str = Field("biological", pattern="^(biological|technical|independent_run)$")
    concentration_value: Optional[float] = Field(None, ge=0)
    concentration_unit: Optional[str] = Field(None, max_length=40)
    response_name: str = Field(..., min_length=1, max_length=120)
    response_value: float
    response_unit: str = Field(..., min_length=1, max_length=60)
    qualifier: str = Field("exact", pattern="^(exact|below|below_or_equal|above|above_or_equal)$")
    timepoint_value: Optional[float] = Field(None, ge=0)
    timepoint_unit: Optional[str] = Field(None, max_length=40)
    well_id: Optional[str] = Field(None, max_length=40)
    record_key: Optional[str] = Field(None, max_length=120)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_measurement(self):
        if self.sample_role not in SAMPLE_ROLES:
            raise ValueError("Rôle d'échantillon inconnu.")
        if not math.isfinite(self.response_value):
            raise ValueError("La réponse doit être une valeur numérique finie.")
        if self.concentration_value is not None and not math.isfinite(self.concentration_value):
            raise ValueError("La concentration doit être une valeur numérique finie.")
        if self.timepoint_value is not None and not math.isfinite(self.timepoint_value):
            raise ValueError("Le temps de mesure doit être une valeur numérique finie.")
        if self.concentration_value is not None and not self.concentration_unit:
            raise ValueError("Toute concentration doit déclarer son unité.")
        if self.concentration_unit and self.concentration_value is None:
            raise ValueError("Une unité de concentration sans valeur est interdite.")
        if self.timepoint_value is not None and not self.timepoint_unit:
            raise ValueError("Tout temps de mesure doit déclarer son unité.")
        if self.timepoint_unit and self.timepoint_value is None:
            raise ValueError("Une unité de temps sans valeur est interdite.")
        return self


class MeasurementsCreate(BaseModel):
    measurements: list[MeasurementCreate] = Field(..., min_length=1, max_length=5000)


class EndpointCreate(BaseModel):
    endpoint_name: str = Field(..., min_length=2, max_length=40)
    value: float = Field(..., gt=0)
    unit: str = Field(..., min_length=1, max_length=40)
    qualifier: str = Field("exact", pattern="^(exact|below|below_or_equal|above|above_or_equal)$")
    origin: str = Field("measured_reported", pattern="^(measured_reported|literature_reported|derived_from_raw_data)$")
    method: Optional[str] = Field(None, max_length=255)
    method_version: Optional[str] = Field(None, max_length=120)
    confidence_low: Optional[float] = Field(None, gt=0)
    confidence_high: Optional[float] = Field(None, gt=0)
    biological_replicates: Optional[int] = Field(None, ge=1, le=10000)
    source_reference: Optional[str] = Field(None, max_length=2000)
    notes: Optional[str] = Field(None, max_length=4000)
    metadata: dict = Field(default_factory=dict)

    @field_validator("endpoint_name")
    @classmethod
    def valid_endpoint(cls, value):
        normalized = value.upper()
        if normalized not in ENDPOINT_NAMES:
            raise ValueError("Endpoint inconnu; utiliser IC50, EC50, MIC, MBC, CC50, GI50, IC90, EC90, KD, KI ou OTHER.")
        return normalized

    @model_validator(mode="after")
    def validate_endpoint(self):
        if not math.isfinite(self.value):
            raise ValueError("La valeur d'endpoint doit être finie.")
        if self.confidence_low is not None and not math.isfinite(self.confidence_low):
            raise ValueError("La borne basse doit être finie.")
        if self.confidence_high is not None and not math.isfinite(self.confidence_high):
            raise ValueError("La borne haute doit être finie.")
        if self.confidence_low is not None and self.confidence_high is not None and self.confidence_low > self.confidence_high:
            raise ValueError("L'intervalle de confiance est inversé.")
        if (self.confidence_low is None) != (self.confidence_high is None):
            raise ValueError("Renseigner les deux bornes de l'intervalle de confiance.")
        if self.confidence_low is not None and self.confidence_low > self.value:
            raise ValueError("La valeur de l'endpoint est inférieure à la borne basse déclarée.")
        if self.confidence_high is not None and self.confidence_high < self.value:
            raise ValueError("La valeur de l'endpoint dépasse la borne haute déclarée.")
        if self.origin == "derived_from_raw_data" and not (self.method and self.method_version):
            raise ValueError("Un endpoint dérivé doit déclarer la méthode et sa version.")
        if self.origin == "literature_reported" and not self.source_reference:
            raise ValueError("Un endpoint de littérature exige sa référence.")
        return self


class EndpointsCreate(BaseModel):
    endpoints: list[EndpointCreate] = Field(..., min_length=1, max_length=100)


class AssaySubmit(BaseModel):
    submission_note: Optional[str] = Field(None, max_length=4000)


class AssayReview(BaseModel):
    decision: str = Field(..., pattern="^(accepted_for_analysis|rejected)$")
    comparability_status: str = Field("not_assessed", pattern="^(not_assessed|comparable|not_comparable)$")
    comparability_group: Optional[str] = Field(None, max_length=120)
    rationale: str = Field(..., min_length=5, max_length=4000)


def _submission_blockers(db: Session, assay: ResearchAssay) -> list[str]:
    blockers = []
    measurements = db.query(ResearchAssayMeasurement).filter(
        ResearchAssayMeasurement.assay_id == assay.id
    ).all()
    endpoints = db.query(ResearchAssayEndpoint).filter(
        ResearchAssayEndpoint.assay_id == assay.id
    ).all()
    if not measurements and not endpoints:
        blockers.append("Ajouter au moins une mesure brute ou un endpoint rapporté.")
    if assay.source_kind == "lab_generated" and not measurements:
        raw_files = [x for x in db.query(ResearchAssayAttachment).filter(
            ResearchAssayAttachment.assay_id == assay.id
        ).all() if x.role == "raw_data"]
        if not raw_files:
            blockers.append("Un endpoint de laboratoire sans lignes de mesures exige une pièce jointe de données brutes.")
    sample_rows = [x for x in measurements if x.sample_role == "test_sample"]
    control_rows = [x for x in measurements if x.sample_role in CONTROL_ROLES]
    if measurements and not sample_rows:
        blockers.append("Aucune mesure de l'échantillon testé n'est enregistrée.")
    if measurements and assay.source_kind in {"lab_generated", "public_dataset"} and not control_rows:
        blockers.append("Ajouter au moins un contrôle positif, négatif, véhicule, blanc ou standard de référence.")
    if assay.assay_type in {"dose_response", "susceptibility"} and sample_rows:
        missing = [x.id for x in sample_rows if x.concentration_value is None or not x.concentration_unit]
        if missing:
            blockers.append("Chaque mesure de concentration doit déclarer une valeur et son unité.")
    if assay.assay_type == "dose_response" and sample_rows:
        concentrations = {
            (str(x.concentration_value), x.concentration_unit)
            for x in sample_rows if x.concentration_value is not None
        }
        biological_replicates_by_concentration: dict[tuple[str, str], set[str]] = {}
        for row in sample_rows:
            if row.concentration_value is None or not row.concentration_unit:
                continue
            key = (str(row.concentration_value), row.concentration_unit)
            if row.replicate_kind == "biological":
                biological_replicates_by_concentration.setdefault(key, set()).add(row.replicate_id)
        if len(concentrations) < 3:
            blockers.append("Une courbe dose-réponse doit contenir au moins trois concentrations distinctes.")
        under_replicated = [
            f"{value} {unit}"
            for (value, unit), replicates in biological_replicates_by_concentration.items()
            if len(replicates) < 2
        ]
        if under_replicated:
            blockers.append(
                "Chaque concentration doit avoir au moins deux réplicats biologiques distincts; "
                "points insuffisamment répliqués: " + ", ".join(under_replicated[:10]) + "."
            )
    if assay.source_kind == "lab_generated" and not assay.laboratory:
        blockers.append("Le laboratoire source doit être renseigné.")
    return blockers


def _assay_manifest(assay: ResearchAssay, measurements, endpoints, attachments) -> str:
    payload = {
        "assay_id": assay.id,
        "study_id": assay.study_id,
        "target_id": assay.target_id,
        "variant_id": assay.variant_id,
        "screening_compound_id": assay.screening_compound_id,
        "assay_type": assay.assay_type,
        "assay_name": assay.assay_name,
        "test_article": assay.test_article,
        "compound_identifier": assay.compound_identifier,
        "canonical_smiles": assay.canonical_smiles,
        "source_kind": assay.source_kind,
        "source_reference": assay.source_reference,
        "source_url": assay.source_url,
        "laboratory": assay.laboratory,
        "operator_name": assay.operator_name,
        "method_name": assay.method_name,
        "method_version": assay.method_version,
        "protocol_reference": assay.protocol_reference,
        "assay_date": assay.assay_date.isoformat() if assay.assay_date else None,
        "organism": assay.organism,
        "strain": assay.strain,
        "cell_line": assay.cell_line,
        "conditions": assay.conditions or {},
        "notes": assay.notes,
        "measurements": [{
            "role": x.sample_role, "label": x.sample_label, "replicate": x.replicate_id,
            "replicate_kind": x.replicate_kind,
            "concentration": str(x.concentration_value) if x.concentration_value is not None else None,
            "concentration_unit": x.concentration_unit, "response": str(x.response_value),
            "response_name": x.response_name, "response_unit": x.response_unit,
            "qualifier": x.qualifier, "timepoint": str(x.timepoint_value) if x.timepoint_value is not None else None,
            "timepoint_unit": x.timepoint_unit, "well_id": x.well_id,
            "record_key": x.record_key, "metadata": x.metadata_json or {},
        } for x in measurements],
        "endpoints": [{
            "name": x.endpoint_name, "value": str(x.value), "unit": x.unit,
            "qualifier": x.qualifier, "origin": x.origin, "method": x.method,
            "method_version": x.method_version,
            "confidence_low": str(x.confidence_low) if x.confidence_low is not None else None,
            "confidence_high": str(x.confidence_high) if x.confidence_high is not None else None,
            "biological_replicates": x.biological_replicates,
            "source_reference": x.source_reference, "notes": x.notes,
            "metadata": x.metadata_json or {},
        } for x in endpoints],
        "attachments": [{
            "sha256": x.sha256, "size": x.size_bytes, "role": x.role,
            "original_name": x.original_name, "content_type": x.content_type,
        } for x in attachments],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@router.post("/studies/{study_id}/assays", status_code=201)
def create_assay(
    study_id: int,
    body: AssayCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    study = _owned_study(db, study_id, user)
    if (study.status or "draft") not in {"analysis", "results_review", "completed"}:
        raise HTTPException(409, "L'étude doit être approuvée pour analyse avant l'enregistrement d'un essai expérimental.")

    target = None
    effective_target_id = body.target_id
    effective_variant_id = body.variant_id
    effective_smiles = body.canonical_smiles
    if body.target_id is not None:
        target = db.query(ResearchTarget).filter(
            ResearchTarget.id == body.target_id, ResearchTarget.study_id == study_id
        ).first()
        if target is None:
            raise HTTPException(422, "La cible n'appartient pas à cette étude.")
    if body.variant_id is not None:
        variant = db.query(ResearchVariant).filter(
            ResearchVariant.id == body.variant_id, ResearchVariant.study_id == study_id
        ).first()
        if variant is None:
            raise HTTPException(422, "Le variant n'appartient pas à cette étude.")
    if target is not None and target.state == "variant":
        if target.variant_id is None:
            raise HTTPException(409, "La cible variante n'est pas liée à un variant de cette étude.")
        if effective_variant_id not in {None, target.variant_id}:
            raise HTTPException(422, "La cible variante et le variant déclaré ne correspondent pas.")
        effective_variant_id = target.variant_id
    elif target is not None and effective_variant_id is not None:
        raise HTTPException(422, "Un variant ne peut pas être associé à une cible WT.")
    screening_run = None
    compound = None
    if body.screening_compound_id is not None:
        pair = (
            db.query(ResearchScreeningCompound, ResearchScreeningRun)
            .join(ResearchScreeningRun, ResearchScreeningCompound.screening_id == ResearchScreeningRun.id)
            .filter(
                ResearchScreeningCompound.id == body.screening_compound_id,
                ResearchScreeningRun.study_id == study_id,
            ).first()
        )
        if pair is None:
            raise HTTPException(422, "Le composé de screening n'appartient pas à cette étude.")
        compound, screening_run = pair
        if compound.status not in {"accepted", "completed"}:
            raise HTTPException(409, "Le composé doit avoir passé le contrôle qualité du screening.")
        if effective_target_id is not None and effective_target_id != screening_run.target_id:
            raise HTTPException(422, "La cible déclarée ne correspond pas à la cible du screening.")
        effective_target_id = screening_run.target_id
        if effective_smiles and effective_smiles != compound.canonical_smiles:
            raise HTTPException(422, "Le SMILES déclaré ne correspond pas au composé du screening.")
        effective_smiles = compound.canonical_smiles
        if target is None:
            target = db.query(ResearchTarget).filter(
                ResearchTarget.id == effective_target_id,
                ResearchTarget.study_id == study_id,
            ).first()
    if not (body.screening_compound_id or body.compound_identifier or effective_smiles or body.test_article):
        raise HTTPException(422, "Identifier l'article testé.")
    assay_payload = body.model_dump()
    assay_payload["target_id"] = effective_target_id
    assay_payload["variant_id"] = effective_variant_id
    assay_payload["canonical_smiles"] = effective_smiles
    assay = ResearchAssay(
        study_id=study_id,
        created_by=user.id,
        **assay_payload,
    )
    db.add(assay)
    db.flush()
    db.add(ResearchAssayReviewEvent(
        assay_id=assay.id, actor_id=user.id, event_type="created",
        from_status=None, to_status="draft",
        payload={"policy_version": "experimental-assay-v1"},
    ))
    db.commit()
    db.refresh(assay)
    return {"success": True, "assay": _serialize_assay(assay)}


@router.get("/studies/{study_id}/assays")
def list_assays(
    study_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_study(db, study_id, user)
    rows = db.query(ResearchAssay).filter(
        ResearchAssay.study_id == study_id
    ).order_by(ResearchAssay.created_at.desc()).all()
    return {"success": True, "assays": [_serialize_assay(x) for x in rows]}


@router.get("/studies/{study_id}/assays/{assay_id}")
def get_assay(
    study_id: int,
    assay_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assay = _owned_assay(db, study_id, assay_id, user)
    return {"success": True, "assay": _serialize_assay(assay, include_data=True)}


@router.post("/studies/{study_id}/assays/{assay_id}/measurements", status_code=201)
def add_measurements(
    study_id: int,
    assay_id: int,
    body: MeasurementsCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assay = _owned_assay(db, study_id, assay_id, user, lock=True)
    _assert_draft(assay)
    existing_keys = {
        x[0] for x in db.query(ResearchAssayMeasurement.record_key).filter(
            ResearchAssayMeasurement.assay_id == assay_id,
            ResearchAssayMeasurement.record_key.isnot(None),
        ).all()
    }
    rows = []
    seen = set()
    for item in body.measurements:
        if item.record_key and (item.record_key in existing_keys or item.record_key in seen):
            raise HTTPException(409, f"record_key déjà utilisé: {item.record_key}")
        seen.add(item.record_key) if item.record_key else None
        if item.sample_role == "test_sample" and assay.assay_type in {"dose_response", "susceptibility"}:
            if item.concentration_value is None or item.concentration_value <= 0:
                raise HTTPException(422, "Les mesures testées de type dose-réponse/susceptibilité exigent une concentration strictement positive.")
        rows.append(ResearchAssayMeasurement(
            assay_id=assay_id,
            sample_role=item.sample_role,
            sample_label=item.sample_label,
            replicate_id=item.replicate_id,
            replicate_kind=item.replicate_kind,
            concentration_value=item.concentration_value,
            concentration_unit=item.concentration_unit,
            response_name=item.response_name,
            response_value=item.response_value,
            response_unit=item.response_unit,
            qualifier=item.qualifier,
            timepoint_value=item.timepoint_value,
            timepoint_unit=item.timepoint_unit,
            well_id=item.well_id,
            record_key=item.record_key,
            metadata_json=item.metadata,
        ))
    db.add_all(rows)
    db.flush()
    db.add(ResearchAssayReviewEvent(
        assay_id=assay_id,
        actor_id=user.id,
        event_type="measurements_added",
        from_status="draft",
        to_status="draft",
        payload={
            "measurement_ids": [row.id for row in rows],
            "created_count": len(rows),
            "sample_roles": sorted({row.sample_role for row in rows}),
            "replicate_kinds": sorted({row.replicate_kind for row in rows}),
        },
    ))
    db.commit()
    return {"success": True, "created_count": len(rows)}


@router.post("/studies/{study_id}/assays/{assay_id}/endpoints", status_code=201)
def add_endpoints(
    study_id: int,
    assay_id: int,
    body: EndpointsCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assay = _owned_assay(db, study_id, assay_id, user, lock=True)
    _assert_draft(assay)
    rows = []
    for item in body.endpoints:
        rows.append(ResearchAssayEndpoint(
            assay_id=assay_id,
            endpoint_name=item.endpoint_name,
            value=item.value,
            unit=item.unit,
            qualifier=item.qualifier,
            origin=item.origin,
            method=item.method,
            method_version=item.method_version,
            confidence_low=item.confidence_low,
            confidence_high=item.confidence_high,
            biological_replicates=item.biological_replicates,
            source_reference=item.source_reference,
            notes=item.notes,
            metadata_json=item.metadata,
        ))
    db.add_all(rows)
    db.flush()
    db.add(ResearchAssayReviewEvent(
        assay_id=assay_id,
        actor_id=user.id,
        event_type="endpoints_added",
        from_status="draft",
        to_status="draft",
        payload={
            "endpoint_ids": [row.id for row in rows],
            "created_count": len(rows),
            "endpoints": [{"name": row.endpoint_name, "unit": row.unit, "origin": row.origin} for row in rows],
        },
    ))
    db.commit()
    return {"success": True, "created_count": len(rows)}


@router.post("/studies/{study_id}/assays/{assay_id}/attachments", status_code=201)
async def upload_assay_attachment(
    study_id: int,
    assay_id: int,
    file: UploadFile = File(...),
    role: str = "raw_data",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assay = _owned_assay(db, study_id, assay_id, user, lock=True)
    _assert_draft(assay)
    if role not in {"raw_data", "protocol", "lab_report", "image", "other"}:
        raise HTTPException(422, "Rôle de pièce jointe inconnu.")
    original_name = os.path.basename((file.filename or "evidence_file").replace("\\", "/"))[:255]
    extension = os.path.splitext(original_name)[1].lower()
    if extension not in ALLOWED_FILE_TYPES:
        raise HTTPException(415, "Format non accepté. Extensions autorisées: CSV, TSV, TXT, PDF, PNG, JPG et XLSX.")
    mime = (file.content_type or "application/octet-stream").lower()
    if mime not in ALLOWED_FILE_TYPES[extension]:
        raise HTTPException(415, "Le type MIME ne correspond pas au format de fichier attendu.")

    root = os.path.realpath(settings.UPLOAD_DIR)
    folder = os.path.join(root, "experimental_evidence", f"study_{study_id}", f"assay_{assay_id}")
    os.makedirs(folder, mode=0o750, exist_ok=True)
    stored_name = uuid.uuid4().hex + extension
    destination = os.path.realpath(os.path.join(folder, stored_name))
    if os.path.commonpath([destination, root]) != root:
        raise HTTPException(400, "Chemin de stockage invalide.")

    digest = hashlib.sha256()
    size = 0
    try:
        with open(destination, "xb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_ATTACHMENT_BYTES:
                    raise HTTPException(413, "Pièce jointe trop volumineuse (maximum 20 MB).")
                digest.update(chunk)
                handle.write(chunk)
    except Exception:
        try:
            os.remove(destination)
        except OSError:
            pass
        raise
    finally:
        await file.close()

    attachment = ResearchAssayAttachment(
        assay_id=assay_id,
        uploaded_by=user.id,
        original_name=original_name,
        stored_name=stored_name,
        storage_path=destination,
        role=role,
        content_type=mime,
        size_bytes=size,
        sha256=digest.hexdigest(),
    )
    db.add(attachment)
    try:
        db.flush()
        db.add(ResearchAssayReviewEvent(
            assay_id=assay_id,
            actor_id=user.id,
            event_type="attachment_uploaded",
            from_status="draft",
            to_status="draft",
            payload={
                "attachment_id": attachment.id,
                "original_name": original_name,
                "role": role,
                "size_bytes": size,
                "sha256": digest.hexdigest(),
            },
        ))
        db.commit()
        db.refresh(attachment)
    except Exception:
        db.rollback()
        try:
            os.remove(destination)
        except OSError:
            pass
        raise
    return {"success": True, "attachment": _serialize_attachment(attachment)}


@router.get("/studies/{study_id}/assays/{assay_id}/attachments/{attachment_id}")
def download_assay_attachment(
    study_id: int,
    assay_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assay = db.query(ResearchAssay).join(
        ResearchStudy, ResearchAssay.study_id == ResearchStudy.id
    ).filter(
        ResearchAssay.id == assay_id,
        ResearchAssay.study_id == study_id,
    ).first()
    if assay is None:
        raise HTTPException(404, "Essai expérimental introuvable.")
    if assay.study.user_id != user.id and not user.is_admin:
        raise HTTPException(404, "Essai expérimental introuvable.")
    attachment = db.query(ResearchAssayAttachment).filter(
        ResearchAssayAttachment.id == attachment_id,
        ResearchAssayAttachment.assay_id == assay_id,
    ).first()
    if attachment is None:
        raise HTTPException(404, "Pièce jointe introuvable.")
    root = os.path.realpath(settings.UPLOAD_DIR)
    path = os.path.realpath(attachment.storage_path)
    if os.path.commonpath([path, root]) != root or not os.path.isfile(path):
        raise HTTPException(404, "Fichier de preuve introuvable.")
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != attachment.sha256:
        raise HTTPException(409, "L'empreinte du fichier ne correspond plus au manifeste enregistré.")
    return FileResponse(path, media_type=attachment.content_type or "application/octet-stream", filename=attachment.original_name)


@router.post("/studies/{study_id}/assays/{assay_id}/submit")
def submit_assay(
    study_id: int,
    assay_id: int,
    body: AssaySubmit,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assay = _owned_assay(db, study_id, assay_id, user, lock=True)
    _assert_draft(assay)
    blockers = _submission_blockers(db, assay)
    if blockers:
        raise HTTPException(409, detail={"message": "Dossier expérimental incomplet.", "blockers": blockers})

    measurements = db.query(ResearchAssayMeasurement).filter(ResearchAssayMeasurement.assay_id == assay_id).order_by(ResearchAssayMeasurement.id.asc()).all()
    endpoints = db.query(ResearchAssayEndpoint).filter(ResearchAssayEndpoint.assay_id == assay_id).order_by(ResearchAssayEndpoint.id.asc()).all()
    attachments = db.query(ResearchAssayAttachment).filter(ResearchAssayAttachment.assay_id == assay_id).order_by(ResearchAssayAttachment.id.asc()).all()
    manifest_sha256 = _assay_manifest(assay, measurements, endpoints, attachments)

    previous = assay.status
    assay.status = "submitted"
    assay.submitted_at = datetime.utcnow()
    db.add(ResearchAssayReviewEvent(
        assay_id=assay_id, actor_id=user.id, event_type="submitted",
        from_status=previous, to_status="submitted",
        reason=body.submission_note,
        payload={
            "manifest_sha256": manifest_sha256,
            "measurement_count": len(measurements),
            "endpoint_count": len(endpoints),
            "attachment_count": len(attachments),
            "attachment_sha256": [x.sha256 for x in attachments],
            "policy_version": "experimental-assay-v1",
        },
    ))
    # The general evidence registry records this as an observation (grade D).
    # A reviewer accepting the dossier never upgrades it to validated biology.
    db.add(ResearchEvidence(
        study_id=study_id,
        object_type="assay",
        object_id=str(assay_id),
        evidence_kind="experimental_assay_submission",
        evidence_status="observation",
        evidence_grade="D",
        claim=f"Jeu de données expérimental soumis: {assay.assay_name} ({assay.assay_type}); non validé indépendamment.",
        organism=assay.organism or assay.cell_line,
        source_type=assay.source_kind,
        source_id=assay.source_reference,
        source_title=assay.assay_name,
        source_url=assay.source_url,
        study_reference=assay.protocol_reference,
        method=assay.method_name,
        input_sha256=manifest_sha256,
        limitations="Soumission expérimentale en attente de revue. L'acceptation administrative ne prouve pas l'effet biologique ni sa reproductibilité.",
        metadata_json={
            "assay_id": assay_id,
            "measurement_count": len(measurements),
            "endpoint_count": len(endpoints),
            "attachment_sha256": [x.sha256 for x in attachments],
            "status_at_creation": "observation",
        },
        created_by=user.id,
    ))
    db.commit()
    db.refresh(assay)
    return {"success": True, "assay": _serialize_assay(assay), "manifest_sha256": manifest_sha256}


@router.get("/assays/pending-review")
def list_assays_pending_review(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    reviewer: User = Depends(require_admin),
):
    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    query = db.query(ResearchAssay).filter(ResearchAssay.status == "submitted")
    total = query.count()
    rows = query.order_by(ResearchAssay.submitted_at.asc()).offset(skip).limit(limit).all()
    return {
        "success": True,
        "total": total,
        "assays": [_serialize_assay(row, include_data=False) for row in rows],
        "review_policy": "La revue porte sur la complétude et la traçabilité; elle ne certifie pas indépendamment les résultats biologiques.",
    }


@router.get("/assays/{assay_id}/review-data")
def get_assay_for_review(
    assay_id: int,
    db: Session = Depends(get_db),
    reviewer: User = Depends(require_admin),
):
    assay = db.query(ResearchAssay).filter(ResearchAssay.id == assay_id).first()
    if assay is None:
        raise HTTPException(404, "Essai expérimental introuvable.")
    return {"success": True, "assay": _serialize_assay(assay, include_data=True)}


@router.post("/studies/{study_id}/assays/{assay_id}/review")
def review_assay(
    study_id: int,
    assay_id: int,
    body: AssayReview,
    db: Session = Depends(get_db),
    reviewer: User = Depends(require_admin),
):
    assay = db.query(ResearchAssay).filter(
        ResearchAssay.id == assay_id,
        ResearchAssay.study_id == study_id,
    ).with_for_update().first()
    if assay is None:
        raise HTTPException(404, "Essai expérimental introuvable.")
    if assay.status != "submitted":
        raise HTTPException(409, "Seul un dossier soumis peut être revu.")
    if body.decision == "rejected" and body.comparability_status != "not_assessed":
        raise HTTPException(422, "Un dossier rejeté ne peut pas être déclaré comparable.")
    if body.comparability_status == "comparable":
        endpoint_count = db.query(ResearchAssayEndpoint).filter(ResearchAssayEndpoint.assay_id == assay_id).count()
        if endpoint_count < 1:
            raise HTTPException(409, "Une comparabilité ne peut pas être déclarée sans endpoint explicite.")
        if not body.comparability_group:
            raise HTTPException(422, "Une comparabilité exige un groupe de comparaison explicite.")
        if not assay.method_name or not (assay.organism or assay.cell_line) or not assay.conditions:
            raise HTTPException(409, "Méthode, système biologique et conditions doivent être documentés avant toute déclaration de comparabilité.")
        existing_group = db.query(ResearchAssay).filter(
            ResearchAssay.comparability_group == body.comparability_group,
            ResearchAssay.comparability_status == "comparable",
            ResearchAssay.status == "reviewed",
        ).all()
        endpoint_signature = {
            (x.endpoint_name, x.unit)
            for x in db.query(ResearchAssayEndpoint).filter(ResearchAssayEndpoint.assay_id == assay_id).all()
        }
        for peer in existing_group:
            peer_signature = {
                (x.endpoint_name, x.unit)
                for x in db.query(ResearchAssayEndpoint).filter(ResearchAssayEndpoint.assay_id == peer.id).all()
            }
            same_context = (
                peer.assay_type == assay.assay_type
                and peer.method_name == assay.method_name
                and peer.organism == assay.organism
                and peer.strain == assay.strain
                and peer.cell_line == assay.cell_line
                and (peer.conditions or {}) == (assay.conditions or {})
            )
            if not same_context or peer_signature != endpoint_signature:
                raise HTTPException(
                    409,
                    detail={
                        "message": "Ce dossier n'est pas strictement comparable au groupe existant.",
                        "group": body.comparability_group,
                        "required": "Même type d'essai, méthode, système biologique, souche/lignée, conditions et signatures endpoint+unité.",
                    },
                )
    if body.comparability_status == "not_comparable" and not body.rationale.strip():
        raise HTTPException(422, "Justifier pourquoi les dossiers ne sont pas comparables.")

    previous = assay.status
    assay.status = "rejected" if body.decision == "rejected" else "reviewed"
    assay.review_decision = body.decision
    assay.review_notes = body.rationale.strip()
    assay.reviewer_id = reviewer.id
    assay.reviewed_at = datetime.utcnow()
    assay.comparability_status = body.comparability_status if body.decision != "rejected" else "not_assessed"
    assay.comparability_group = body.comparability_group if body.comparability_status == "comparable" else None
    assay.comparability_rationale = body.rationale.strip() if body.comparability_status != "not_assessed" else None
    db.add(ResearchAssayReviewEvent(
        assay_id=assay_id, actor_id=reviewer.id, event_type="reviewed",
        from_status=previous, to_status=assay.status, decision=body.decision,
        reason=body.rationale.strip(),
        payload={
            "comparability_status": assay.comparability_status,
            "comparability_group": assay.comparability_group,
            "policy_version": "experimental-assay-v1",
            "scope": "administrative completeness and provenance review; not independent biological replication",
        },
    ))
    db.commit()
    db.refresh(assay)
    return {"success": True, "assay": _serialize_assay(assay)}


@router.get("/studies/{study_id}/assays/{assay_id}/review-history")
def assay_review_history(
    study_id: int,
    assay_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assay = _owned_assay(db, study_id, assay_id, user)
    rows = db.query(ResearchAssayReviewEvent).filter(
        ResearchAssayReviewEvent.assay_id == assay_id
    ).order_by(ResearchAssayReviewEvent.created_at.asc()).all()
    return {"success": True, "events": [{
        "id": x.id, "actor_id": x.actor_id, "event_type": x.event_type,
        "from_status": x.from_status, "to_status": x.to_status,
        "decision": x.decision, "reason": x.reason, "payload": x.payload or {},
        "created_at": x.created_at.isoformat() if x.created_at else None,
    } for x in rows]}
