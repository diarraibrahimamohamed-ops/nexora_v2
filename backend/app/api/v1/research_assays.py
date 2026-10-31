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

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.docking import DockingJob
from app.models.evidence import ResearchEvidence
from app.models.experimental import (
    ResearchAssay, ResearchAssayAnalysis, ResearchAssayAttachment, ResearchAssayEndpoint,
    ResearchAssayMeasurement, ResearchAssayReviewEvent,
)
from app.models.research import ResearchStudy, ResearchTarget, ResearchVariant, ResearchDockingRun
from app.models.user import User
from app.models.virtual_screening import ResearchScreeningCompound, ResearchScreeningRun
from app.services.auth_service import get_current_user, require_admin
from app.services.assay_statistics import (
    COMPARISON_VERSION, AssayStatisticsError, spearman_rank_comparison,
)
from app.services.assay_analytics import (
    AssayAnalysisNotReady, execute_dose_response_analysis,
)
from app.models.scientific_workflow import ScientificWorkflowRun
from app.services.scientific_workflow_engine import InvalidWorkflow, create_workflow_run
from app.services.scientific_workflow_policy import WorkflowDefinition, WorkflowStepDefinition

router = APIRouter(prefix="/research", tags=["experimental evidence"])

_DOSE_RESPONSE_4PL_DEFINITION = WorkflowDefinition(
    key="nexora.experimental-dose-response-4pl",
    version="1.0.0",
    description=(
        "Curator-approved experimental dose-response analysis using the existing "
        "biological-replicate 4PL and cluster-bootstrap service."
    ),
    requires_quality_review=False,
    steps=(
        WorkflowStepDefinition(
            key="dose_response_4pl",
            task_name="experimental.run_4pl",
            queue_name="workflow",
            max_attempts=1,
        ),
    ),
)

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
    biological_sample_id: Optional[str] = Field(None, max_length=80)
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
        if self.replicate_kind == "technical" and not (self.biological_sample_id or "").strip():
            raise ValueError("Un réplicat technique doit identifier son biological_sample_id parent.")
        if self.replicate_kind != "technical" and self.biological_sample_id is not None:
            raise ValueError("biological_sample_id ne s'applique qu'aux réplicats techniques.")
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


class DoseResponseAnalysisRequest(BaseModel):
    response_name: str = Field(..., min_length=1, max_length=120)
    response_unit: str = Field(..., min_length=1, max_length=60)
    concentration_unit: str = Field(..., min_length=1, max_length=40)
    response_direction: str = Field(..., pattern="^(increasing|decreasing)$")
    endpoint_name: str = Field(..., pattern="^(IC50|EC50)$")
    sample_label: Optional[str] = Field(None, max_length=255)
    timepoint_value: Optional[float] = Field(None, ge=0)
    timepoint_unit: Optional[str] = Field(None, max_length=40)
    bootstrap_iterations: int = Field(1000, ge=500, le=2000)
    random_seed: int = Field(42, ge=0, le=2147483647)

    @model_validator(mode="after")
    def validate_timepoint(self):
        if (self.timepoint_value is None) != (self.timepoint_unit is None):
            raise ValueError("Le temps et son unité doivent être renseignés ensemble.")
        if self.timepoint_value is not None and not math.isfinite(self.timepoint_value):
            raise ValueError("Le temps doit être une valeur finie.")
        return self


class DockingAssayComparisonRequest(BaseModel):
    assay_ids: list[int] = Field(..., min_length=5, max_length=100)
    docking_run_ids: list[int] = Field(..., min_length=5, max_length=100)

    @model_validator(mode="after")
    def validate_pairs(self):
        if len(self.assay_ids) != len(self.docking_run_ids):
            raise ValueError("Chaque essai doit être associé à exactement un run de docking.")
        if len(set(self.assay_ids)) != len(self.assay_ids):
            raise ValueError("Les identifiants d'essais doivent être uniques.")
        if len(set(self.docking_run_ids)) != len(self.docking_run_ids):
            raise ValueError("Les identifiants de docking doivent être uniques.")
        return self


def _serialize_statistical_analysis(row: ResearchAssayAnalysis) -> dict:
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
    # Re-check target/variant consistency after resolving the target through
    # an associated screening run as well as through an explicit target_id.
    if target is not None and target.state == "variant":
        if target.variant_id is None or effective_variant_id not in {None, target.variant_id}:
            raise HTTPException(422, "Le variant ne correspond pas à la cible d'essai.")
        effective_variant_id = target.variant_id
    elif target is not None and effective_variant_id is not None:
        raise HTTPException(422, "Un variant ne peut pas être associé à une cible WT.")
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
            biological_sample_id=item.biological_sample_id,
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
                and peer.method_version == assay.method_version
                and peer.protocol_reference == assay.protocol_reference
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


@router.post("/studies/{study_id}/assays/{assay_id}/dose-response-analysis", status_code=201)
def create_dose_response_analysis(
    study_id: int,
    assay_id: int,
    body: DoseResponseAnalysisRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Synchronous compatibility endpoint using the shared 4PL service."""
    _owned_study(db, study_id, user)
    _owned_assay(db, study_id, assay_id, user, lock=True)
    try:
        response = execute_dose_response_analysis(
            db,
            study_id=study_id,
            assay_id=assay_id,
            user_id=user.id,
            parameters=body.model_dump(),
        )
        db.commit()
        return response
    except AssayAnalysisNotReady as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AssayStatisticsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Ajustement dose-réponse refusé par les contrôles scientifiques.",
                "reason": str(exc),
            },
        ) from exc
    except Exception:
        db.rollback()
        raise


def _dose_response_snapshot(
    db: Session, assay_id: int, parameters: dict
) -> dict:
    """Build a compact deterministic manifest for request idempotency."""
    rows = db.query(ResearchAssayMeasurement).filter(
        ResearchAssayMeasurement.assay_id == assay_id
    ).order_by(ResearchAssayMeasurement.id.asc()).all()
    payload = [{
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
        "record_key": row.record_key,
        "metadata": row.metadata_json or {},
    } for row in rows]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    parameters_encoded = json.dumps(
        parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return {
        "measurement_count": len(payload),
        "measurement_snapshot_sha256": hashlib.sha256(encoded).hexdigest(),
        "parameter_sha256": hashlib.sha256(parameters_encoded).hexdigest(),
    }


@router.post(
    "/studies/{study_id}/assays/{assay_id}/dose-response-workflow",
    status_code=202,
)
def submit_dose_response_workflow(
    study_id: int,
    assay_id: int,
    body: DoseResponseAnalysisRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Submit a curator-approved 4PL analysis through the durable HFSM/DAG core."""
    _owned_study(db, study_id, user)
    assay = _owned_assay(db, study_id, assay_id, user, lock=True)
    if assay.status != "reviewed" or assay.review_decision != "accepted_for_analysis":
        raise HTTPException(
            status_code=409,
            detail="La revue curatoriale doit accepter le dossier avant la soumission du workflow 4PL.",
        )
    if assay.assay_type not in {
        "dose_response", "growth_inhibition", "cytotoxicity", "enzyme_activity", "binding"
    }:
        raise HTTPException(
            status_code=409,
            detail="Le type d'essai ne relève pas d'un ajustement dose-réponse IC50/EC50.",
        )

    parameters = body.model_dump()
    snapshot = _dose_response_snapshot(db, assay_id, parameters)
    if idempotency_key is not None:
        supplied = idempotency_key.strip()
        if not supplied or len(supplied) > 128:
            raise HTTPException(
                status_code=422,
                detail="Idempotency-Key doit contenir entre 1 et 128 caractères.",
            )
        idempotency_material = "client:" + supplied
    else:
        idempotency_material = (
            "snapshot:"
            + snapshot["measurement_snapshot_sha256"]
            + ":"
            + snapshot["parameter_sha256"]
        )
    internal_key = "4pl:" + hashlib.sha256(
        f"{study_id}:{assay_id}:{user.id}:{idempotency_material}".encode("utf-8")
    ).hexdigest()

    try:
        run, replay = create_workflow_run(
            db,
            _DOSE_RESPONSE_4PL_DEFINITION,
            owner_user_id=user.id,
            scope_type="research_assay",
            scope_id=assay_id,
            context={
                "study_id": study_id,
                "assay_id": assay_id,
                "analysis": "dose_response_4pl",
                "algorithm": "four_parameter_logistic",
            },
            input_manifest={
                **snapshot,
                "algorithm_version": "N3XORA_4PL_CLUSTER_BOOTSTRAP_V1",
            },
            step_inputs={
                "dose_response_4pl": {
                    "args": [study_id, assay_id, user.id],
                    "kwargs": {"parameters": parameters},
                }
            },
            idempotency_key=internal_key,
        )
        db.commit()
        db.refresh(run)
        return {
            "success": True,
            "workflow_run_id": run.id,
            "workflow_key": run.workflow_key,
            "definition_version": run.definition_version,
            "state": run.state,
            "state_version": run.state_version,
            "study_id": study_id,
            "assay_id": assay_id,
            "measurement_count": snapshot["measurement_count"],
            "idempotent_replay": replay,
            "dispatch": "scientific_workflow_dispatcher",
        }
    except (InvalidWorkflow, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.post("/studies/{study_id}/assays/docking-potency-comparison", status_code=201)
def compare_docking_with_experimental_potency(
    study_id: int,
    body: DockingAssayComparisonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Exploratory same-target Spearman comparison for curated compounds.

    Each assay and docking run is explicitly paired by the caller. The API
    verifies the target, ligand identity, completed raw result, and exact
    docking protocol/structure fingerprint before computing the rank statistic.
    """
    _owned_study(db, study_id, user)
    from rdkit import Chem

    assays = db.query(ResearchAssay).filter(
        ResearchAssay.study_id == study_id,
        ResearchAssay.id.in_(body.assay_ids),
    ).all()
    assay_by_id = {row.id: row for row in assays}
    if len(assay_by_id) != len(body.assay_ids):
        raise HTTPException(404, "Un ou plusieurs essais n'appartiennent pas à cette étude.")
    ordered_assays = [assay_by_id[item_id] for item_id in body.assay_ids]

    groups = {row.comparability_group for row in ordered_assays}
    if len(groups) != 1 or None in groups:
        raise HTTPException(409, "Tous les essais doivent appartenir au même groupe de comparabilité curatoriale.")
    if any(row.status != "reviewed" or row.review_decision != "accepted_for_analysis" or row.comparability_status != "comparable" for row in ordered_assays):
        raise HTTPException(409, "Chaque essai doit être accepté pour analyse et marqué comparable.")
    target_ids = {row.target_id for row in ordered_assays}
    if len(target_ids) != 1 or None in target_ids:
        raise HTTPException(409, "Tous les essais doivent viser exactement la même cible biologique.")
    if any(row.canonical_smiles is None and row.screening_compound is None for row in ordered_assays):
        raise HTTPException(409, "Chaque essai doit être associé à un SMILES vérifié ou à un composé de screening.")
    assay_contexts = {
        (
            row.assay_type,
            row.method_name,
            row.method_version,
            row.protocol_reference,
            row.organism,
            row.strain,
            row.cell_line,
            json.dumps(row.conditions or {}, sort_keys=True, separators=(",", ":")),
        )
        for row in ordered_assays
    }
    if len(assay_contexts) != 1:
        raise HTTPException(409, "Les dossiers ne partagent pas exactement le type d'essai, la méthode, sa version, le protocole, le système biologique et les conditions.")

    endpoint_names = set()
    concentration_units = set()
    response_names = set()
    response_units = set()
    response_directions = set()
    analysis_by_assay = {}
    for assay in ordered_assays:
        analysis = db.query(ResearchAssayAnalysis).filter(
            ResearchAssayAnalysis.assay_id == assay.id,
            ResearchAssayAnalysis.analysis_type == "dose_response_4pl",
            ResearchAssayAnalysis.status == "completed",
        ).order_by(ResearchAssayAnalysis.created_at.desc()).first()
        if analysis is None:
            raise HTTPException(409, f"Aucun ajustement 4PL terminé pour l'essai #{assay.id}.")
        analysis_result = analysis.result or {}
        fit = analysis_result.get("fit", {})
        sample = analysis_result.get("sample", {})
        model = analysis_result.get("model", {})
        expected_label = str(assay.test_article or "").strip().casefold()
        analyzed_label = str(sample.get("sample_label") or "").strip().casefold()
        if not analyzed_label or analyzed_label != expected_label:
            raise HTTPException(409, f"L'ajustement 4PL de l'essai #{assay.id} ne porte pas sur le test_article déclaré.")
        response_names.add(sample.get("response_name"))
        response_units.add(sample.get("response_unit"))
        response_directions.add(model.get("response_direction"))
        if analysis_result.get("quality", {}).get("status") != "interpolated":
            raise HTTPException(409, f"L'ajustement 4PL de l'essai #{assay.id} nécessite une revue et ne peut pas entrer dans une comparaison de rang.")
        endpoint_names.add((analysis.result or {}).get("model", {}).get("endpoint_label"))
        concentration_units.add(fit.get("concentration_unit"))
        midpoint = fit.get("midpoint_concentration")
        if not isinstance(midpoint, (int, float)) or not math.isfinite(float(midpoint)) or midpoint <= 0:
            raise HTTPException(409, f"Midpoint invalide pour l'essai #{assay.id}.")
        analysis_by_assay[assay.id] = analysis
    if len(endpoint_names) != 1 or None in endpoint_names:
        raise HTTPException(409, "Les endpoints expérimentaux doivent être identiques (IC50 ou EC50).")
    if len(concentration_units) != 1 or None in concentration_units:
        raise HTTPException(409, "Les unités de concentration expérimentale doivent être strictement identiques.")
    if len(response_names) != 1 or None in response_names:
        raise HTTPException(409, "Les essais doivent utiliser exactement le même nom de readout.")
    if len(response_units) != 1 or None in response_units:
        raise HTTPException(409, "Les essais doivent utiliser exactement la même unité de réponse.")
    if len(response_directions) != 1 or None in response_directions:
        raise HTTPException(409, "Les analyses 4PL doivent partager le même sens de réponse.")

    docking_runs = db.query(ResearchDockingRun).filter(
        ResearchDockingRun.study_id == study_id,
        ResearchDockingRun.id.in_(body.docking_run_ids),
    ).all()
    run_by_id = {row.id: row for row in docking_runs}
    if len(run_by_id) != len(body.docking_run_ids):
        raise HTTPException(404, "Un ou plusieurs runs de docking n'appartiennent pas à cette étude.")

    results_dir = os.path.realpath(settings.RESULTS_DIR)
    pair_rows = []
    protocol_fingerprint = None
    seen_smiles = set()
    vina_scores = []
    log10_midpoints = []

    protocol_keys = (
        "protocol_id", "protocol_version", "structure_sha256", "target_accession",
        "max_pockets", "vina_seed", "num_modes", "energy_range_kcal_mol",
        "vina_exhaustiveness_policy",
    )
    for assay, run_id in zip(ordered_assays, body.docking_run_ids):
        run = run_by_id[run_id]
        if run.target_id != assay.target_id or run.engine != "AutoDock Vina":
            raise HTTPException(409, f"Le run de docking #{run_id} ne correspond pas à la cible ou au moteur de l'essai #{assay.id}.")
        assay_smiles = assay.canonical_smiles or assay.screening_compound.canonical_smiles
        molecule = Chem.MolFromSmiles(assay_smiles or "")
        if molecule is None:
            raise HTTPException(422, f"SMILES invalide ou absent pour l'essai #{assay.id}.")
        canonical_assay_smiles = Chem.MolToSmiles(molecule, canonical=True)
        run_molecule = Chem.MolFromSmiles(run.ligand_smiles or "")
        if run_molecule is None or Chem.MolToSmiles(run_molecule, canonical=True) != canonical_assay_smiles:
            raise HTTPException(409, f"Le ligand du docking #{run_id} ne correspond pas au composé de l'essai #{assay.id}.")
        if canonical_assay_smiles in seen_smiles:
            raise HTTPException(409, "La comparaison doit contenir au moins cinq composés distincts; un composé ne peut apparaître qu'une fois.")
        seen_smiles.add(canonical_assay_smiles)

        params = run.parameters or {}
        protocol = {key: params.get(key) for key in protocol_keys}
        if not protocol.get("protocol_id") or not protocol.get("protocol_version") or not protocol.get("structure_sha256"):
            raise HTTPException(409, f"Le run #{run_id} ne possède pas un manifeste de protocole et une empreinte de structure suffisants.")
        if protocol_fingerprint is None:
            protocol_fingerprint = protocol
        elif protocol != protocol_fingerprint:
            raise HTTPException(409, "Les runs de docking ne partagent pas exactement le même protocole et la même structure cible.")
        job = db.query(DockingJob).filter(DockingJob.id == run.docking_job_id).first()
        if job is None:
            raise HTTPException(404, f"Job de docking absent pour le run #{run_id}.")
        job_status = job.status.value if hasattr(job.status, "value") else str(job.status)
        if job_status != "completed" or not job.result_path:
            raise HTTPException(409, f"Le docking #{run_id} n'est pas terminé avec un résultat brut disponible.")
        result_path = os.path.realpath(job.result_path)
        if os.path.commonpath([result_path, results_dir]) != results_dir or not os.path.isfile(result_path):
            raise HTTPException(409, f"Le fichier de résultat du docking #{run_id} est absent ou hors du répertoire de résultats autorisé.")
        try:
            with open(result_path, encoding="utf-8") as handle:
                docking_result = json.load(handle)
        except (OSError, json.JSONDecodeError):
            raise HTTPException(409, f"Le résultat JSON du docking #{run_id} est illisible.")
        vina_score = docking_result.get("vina_best_score", docking_result.get("docking_score"))
        if not isinstance(vina_score, (float, int)) or not math.isfinite(float(vina_score)):
            raise HTTPException(409, f"Le résultat du docking #{run_id} ne contient pas de score Vina fini.")

        analysis = analysis_by_assay[assay.id]
        fit = analysis.result["fit"]
        midpoint = float(fit["midpoint_concentration"])
        vina_scores.append(float(vina_score))
        log10_midpoints.append(math.log10(midpoint))
        digest = hashlib.sha256()
        with open(result_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        pair_rows.append({
            "assay_id": assay.id,
            "assay_analysis_id": analysis.id,
            "compound": assay.test_article,
            "canonical_smiles": canonical_assay_smiles,
            "docking_run_id": run.id,
            "docking_result_sha256": digest.hexdigest(),
            "vina_best_score": float(vina_score),
            "experimental_endpoint": next(iter(endpoint_names)),
            "response_name": next(iter(response_names)),
            "response_unit": next(iter(response_units)),
            "response_direction": next(iter(response_directions)),
            "experimental_midpoint": midpoint,
            "experimental_unit": fit["concentration_unit"],
            "experimental_midpoint_ci95": (analysis.result.get("confidence_interval") or {}),
        })

    try:
        rank_report = spearman_rank_comparison(vina_scores, log10_midpoints, random_seed=42)
    except AssayStatisticsError as exc:
        raise HTTPException(422, detail={"message": "Comparaison statistique impossible.", "reason": str(exc)})

    parameters = {
        "assay_ids": body.assay_ids,
        "docking_run_ids": body.docking_run_ids,
        "comparability_group": next(iter(groups)),
        "target_id": next(iter(target_ids)),
        "endpoint_name": next(iter(endpoint_names)),
        "concentration_unit": next(iter(concentration_units)),
        "response_name": next(iter(response_names)),
        "response_unit": next(iter(response_units)),
        "response_direction": next(iter(response_directions)),
        "docking_protocol": protocol_fingerprint,
        "algorithm_version": COMPARISON_VERSION,
    }
    input_payload = {"parameters": parameters, "pairs": pair_rows}
    input_sha256 = hashlib.sha256(
        json.dumps(input_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    analysis_key = hashlib.sha256(
        f"{study_id}:{COMPARISON_VERSION}:{input_sha256}".encode("utf-8")
    ).hexdigest()
    existing = db.query(ResearchAssayAnalysis).filter(
        ResearchAssayAnalysis.analysis_key == analysis_key
    ).first()
    if existing:
        return {"success": True, "idempotent_replay": True, "analysis": _serialize_statistical_analysis(existing)}

    result = {
        "analysis_type": "vina_experimental_potency_rank_comparison",
        "algorithm_version": COMPARISON_VERSION,
        "rank_comparison": rank_report,
        "pairs": pair_rows,
        "scope": {
            "target_id": next(iter(target_ids)),
            "comparability_group": next(iter(groups)),
            "endpoint_name": next(iter(endpoint_names)),
            "concentration_unit": next(iter(concentration_units)),
            "response_name": next(iter(response_names)),
            "response_unit": next(iter(response_units)),
            "response_direction": next(iter(response_directions)),
            "docking_protocol": protocol_fingerprint,
        },
        "scientific_notice": (
            "Association descriptive sur les composés et protocoles explicitement appariés. "
            "Le score Vina n'est ni une IC50, ni une énergie libre expérimentale, ni un Kd. "
            "Une corrélation de rang ne démontre pas une relation causale ou une performance prédictive généralisable."
        ),
        "provenance": {
            "input_sha256": input_sha256,
            "assay_ids": body.assay_ids,
            "docking_run_ids": body.docking_run_ids,
        },
    }
    output_sha256 = hashlib.sha256(
        json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    anchor_assay = ordered_assays[0]
    analysis = ResearchAssayAnalysis(
        assay_id=anchor_assay.id,
        created_by=user.id,
        analysis_type="vina_experimental_potency_rank_comparison",
        algorithm="spearman_rank_correlation",
        algorithm_version=COMPARISON_VERSION,
        analysis_key=analysis_key,
        input_sha256=input_sha256,
        output_sha256=output_sha256,
        parameters=parameters,
        result=result,
        status="completed",
    )
    db.add(analysis)
    db.flush()
    db.add(ResearchEvidence(
        study_id=study_id,
        object_type="assay_analysis",
        object_id=str(analysis.id),
        evidence_kind="vina_experimental_potency_rank_comparison",
        evidence_status="computational_hypothesis",
        evidence_grade="D",
        claim=(
            f"Comparaison exploratoire Vina–{next(iter(endpoint_names))} sur {len(pair_rows)} composés: "
            f"rho de Spearman={rank_report['rho']:.4f}, p permutation bilatérale={rank_report['permutation_p_value_two_sided']:.4g}."
        ),
        source_type="curated_experimental_assays_and_docking_runs",
        source_id=",".join(f"assay:{x}" for x in body.assay_ids),
        method="Spearman rank correlation with permutation test and paired-compound bootstrap interval",
        tool_name="N3XORA Assay Statistics",
        tool_version=COMPARISON_VERSION,
        input_sha256=input_sha256,
        output_sha256=output_sha256,
        limitations=result["scientific_notice"],
        metadata_json={
            "analysis_id": analysis.id,
            "assay_ids": body.assay_ids,
            "docking_run_ids": body.docking_run_ids,
            "n_compounds": len(pair_rows),
        },
        created_by=user.id,
    ))
    db.add(ResearchAssayReviewEvent(
        assay_id=anchor_assay.id,
        actor_id=user.id,
        event_type="docking_potency_comparison_created",
        from_status="reviewed",
        to_status="reviewed",
        payload={
            "analysis_id": analysis.id,
            "analysis_type": "vina_experimental_potency_rank_comparison",
            "input_sha256": input_sha256,
            "output_sha256": output_sha256,
            "assay_ids": body.assay_ids,
            "docking_run_ids": body.docking_run_ids,
        },
    ))
    db.commit()
    db.refresh(analysis)
    return {"success": True, "idempotent_replay": False, "analysis": _serialize_statistical_analysis(analysis)}


@router.get("/studies/{study_id}/assays/{assay_id}/analyses/{analysis_id}")
def get_assay_statistical_analysis(
    study_id: int,
    assay_id: int,
    analysis_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_assay(db, study_id, assay_id, user)
    row = db.query(ResearchAssayAnalysis).filter(
        ResearchAssayAnalysis.id == analysis_id,
        ResearchAssayAnalysis.assay_id == assay_id,
    ).first()
    if row is None:
        raise HTTPException(404, "Analyse statistique introuvable.")
    return {"success": True, "analysis": _serialize_statistical_analysis(row)}
