"""LC-MS/MS experimental lineage API.

This module registers experimental material, analytical acquisitions, external
artifacts and imported processing results. It does not run peak picking, spectral
matching or metabolite identification algorithms.
"""
from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.evidence import ResearchEvidence
from app.models.lcmsms import (
    BotanicalSpecimen,
    ExtractionBatch,
    LcmsAnalyticalBatch,
    LcmsCompoundAnnotation,
    LcmsDataArtifact,
    LcmsFeature,
    LcmsFeatureObservation,
    LcmsInjection,
    LcmsMs2Spectrum,
    LcmsProcessingRun,
    LcmsQualityControlObservation,
    LcmsSpectralLibrary,
    PlantFraction,
    PlantMaterialSample,
)
from app.models.phytochemistry import PhytochemicalCandidate
from app.models.research import ResearchStudy
from app.models.user import User
from app.services.auth_service import get_current_user, require_admin
from app.services.scientific_provenance import seal_manifest, sha256_json

router = APIRouter(prefix="/lcmsms", tags=["LC-MS/MS experimental evidence"])

ALLOWED_ARTIFACTS = {
    ".raw": "vendor_raw",
    ".wiff": "vendor_raw",
    ".wiff2": "vendor_raw",
    ".mzml": "mzml",
    ".mzxml": "mzxml",
    ".imzml": "imzml",
    ".cdf": "netcdf",
    ".mgf": "mgf",
    ".msp": "msp",
    ".mztab": "mztab_m",
    ".csv": "csv",
    ".tsv": "tsv",
    ".json": "json",
    ".zip": "vendor_archive",
}
ARTIFACT_ROLES = {
    "raw_data", "converted_data", "feature_table", "spectra",
    "quantification", "qc_report", "annotation_table", "other",
}
INJECTION_TYPES = {
    "sample", "pooled_qc", "solvent_blank", "extraction_blank",
    "reference_standard", "system_suitability", "calibration", "other",
}
POLARITIES = {"positive", "negative", "unknown"}
QC_TYPES = {
    "pooled_qc", "solvent_blank", "extraction_blank", "internal_standard",
    "system_suitability", "carryover", "mass_accuracy", "retention_time_drift",
    "calibration", "other",
}


def _owned_study(db: Session, study_id: int, user: User) -> ResearchStudy:
    study = db.query(ResearchStudy).filter(
        ResearchStudy.id == study_id,
        ResearchStudy.user_id == user.id,
    ).first()
    if study is None:
        raise HTTPException(404, "Étude introuvable.")
    return study


def _owned_row(db: Session, model, row_id: int, study_id: int, user: User):
    _owned_study(db, study_id, user)
    row = db.query(model).filter(
        model.id == row_id,
        model.study_id == study_id,
    ).first()
    if row is None:
        raise HTTPException(404, "Enregistrement LC-MS/MS introuvable.")
    return row


def _json_object(value: dict | list, label: str) -> dict | list:
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"{label} doit contenir uniquement des valeurs JSON finies.") from exc
    return value


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _yield_percent(output_mass: float | None, input_mass: float | None) -> float | None:
    if output_mass is None or input_mass is None:
        return None
    if input_mass <= 0:
        raise HTTPException(422, "La masse initiale doit être strictement positive.")
    # More than 100% can occur from residual solvent, water or measurement error.
    # Preserve the observation; the caller must review the warning.
    return round((output_mass / input_mass) * 100.0, 4)


class BotanicalSpecimenCreate(BaseModel):
    specimen_code: str = Field(..., min_length=1, max_length=120)
    reported_taxon: str = Field(..., min_length=2, max_length=255)
    accepted_taxon: Optional[str] = Field(None, max_length=255)
    taxon_identifier: Optional[str] = Field(None, max_length=120)
    taxonomy_source: Optional[str] = Field(None, max_length=255)
    identification_method: Optional[str] = Field(None, max_length=255)
    identifier_name: Optional[str] = Field(None, max_length=255)
    identified_at: Optional[date] = None
    voucher_code: Optional[str] = Field(None, max_length=120)
    herbarium_name: Optional[str] = Field(None, max_length=255)
    collector_name: Optional[str] = Field(None, max_length=255)
    collection_date: Optional[date] = None
    country: Optional[str] = Field(None, max_length=120)
    admin_region: Optional[str] = Field(None, max_length=160)
    locality: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90, allow_inf_nan=False)
    longitude: Optional[float] = Field(None, ge=-180, le=180, allow_inf_nan=False)
    coordinate_uncertainty_m: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    coordinate_access: Literal["restricted", "coarse", "public"] = "restricted"
    field_notes: Optional[str] = None
    evidence_references: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_coordinates(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Latitude et longitude doivent être renseignées ensemble.")
        return self


class BotanicalSpecimenReview(BaseModel):
    status: Literal["provisional", "verified", "rejected"]
    accepted_taxon: Optional[str] = Field(None, min_length=2, max_length=255)
    taxon_identifier: Optional[str] = Field(None, max_length=120)
    taxonomy_source: Optional[str] = Field(None, max_length=255)
    identification_method: Optional[str] = Field(None, max_length=255)
    identifier_name: Optional[str] = Field(None, max_length=255)
    voucher_code: Optional[str] = Field(None, max_length=120)
    herbarium_name: Optional[str] = Field(None, max_length=255)
    source_reference: str = Field(..., min_length=5, max_length=2000)
    rationale: str = Field(..., min_length=10, max_length=4000)

    @model_validator(mode="after")
    def verified_requires_traceable_taxonomy(self):
        if self.status == "verified":
            if not self.accepted_taxon or not self.taxonomy_source or not self.identifier_name:
                raise ValueError("Une identification vérifiée exige taxon accepté, source taxonomique et identificateur.")
        return self


class PlantSampleCreate(BaseModel):
    specimen_id: int = Field(..., ge=1)
    sample_code: str = Field(..., min_length=1, max_length=120)
    plant_part: str = Field(..., min_length=1, max_length=120)
    material_state: Literal["fresh", "dried", "frozen", "powdered", "other"] = "fresh"
    collection_date: Optional[datetime] = None
    collector_name: Optional[str] = Field(None, max_length=255)
    initial_mass_g: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    moisture_percent: Optional[float] = Field(None, ge=0, le=100, allow_inf_nan=False)
    preservation_method: Optional[str] = Field(None, max_length=255)
    storage_conditions: dict[str, Any] = Field(default_factory=dict)
    storage_location: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractionBatchCreate(BaseModel):
    sample_id: int = Field(..., ge=1)
    batch_code: str = Field(..., min_length=1, max_length=120)
    extraction_method: str = Field(..., min_length=2, max_length=160)
    method_version: Optional[str] = Field(None, max_length=120)
    protocol_reference: Optional[str] = None
    solvent_system: list[dict[str, Any]] = Field(default_factory=list)
    solid_liquid_ratio: Optional[str] = Field(None, max_length=80)
    input_mass_g: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    extract_mass_g: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    temperature_c: Optional[float] = Field(None, allow_inf_nan=False)
    duration_minutes: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    ph_value: Optional[float] = Field(None, ge=0, le=14, allow_inf_nan=False)
    operator_name: Optional[str] = Field(None, max_length=255)
    performed_at: Optional[datetime] = None
    notes: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class FractionCreate(BaseModel):
    fraction_code: str = Field(..., min_length=1, max_length=120)
    fraction_order: Optional[int] = Field(None, ge=1)
    separation_method: str = Field(..., min_length=2, max_length=160)
    method_version: Optional[str] = Field(None, max_length=120)
    elution_system: list[dict[str, Any]] = Field(default_factory=list)
    collected_volume_ml: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    recovered_mass_g: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    notes: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class AnalyticalBatchCreate(BaseModel):
    batch_code: str = Field(..., min_length=1, max_length=120)
    instrument_vendor: str = Field(..., min_length=1, max_length=120)
    instrument_model: str = Field(..., min_length=1, max_length=160)
    mass_analyzer: Optional[str] = Field(None, max_length=120)
    ion_source: Optional[str] = Field(None, max_length=120)
    chromatography_column: Optional[str] = Field(None, max_length=255)
    mobile_phase_a: Optional[str] = None
    mobile_phase_b: Optional[str] = None
    gradient_program: list[dict[str, Any]] = Field(default_factory=list)
    flow_rate: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    flow_rate_unit: Optional[str] = Field(None, max_length=40)
    column_temperature_c: Optional[float] = Field(None, allow_inf_nan=False)
    injection_volume: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    injection_volume_unit: Optional[str] = Field(None, max_length=40)
    acquisition_method: str = Field(..., min_length=2, max_length=255)
    acquisition_method_version: Optional[str] = Field(None, max_length=120)
    polarity_modes: list[Literal["positive", "negative"]] = Field(default_factory=list)
    operator_name: Optional[str] = Field(None, max_length=255)
    acquired_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    qc_policy: dict[str, Any] = Field(default_factory=dict)
    notes: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class InjectionCreate(BaseModel):
    injection_order: int = Field(..., ge=1)
    injection_type: str = Field(..., min_length=2, max_length=40)
    sample_label: str = Field(..., min_length=1, max_length=255)
    plant_sample_id: Optional[int] = Field(None, ge=1)
    fraction_id: Optional[int] = Field(None, ge=1)
    technical_replicate: Optional[int] = Field(None, ge=1)
    injection_volume: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    injection_volume_unit: Optional[str] = Field(None, max_length=40)
    acquisition_reference: Optional[str] = Field(None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_material_source(self):
        if self.injection_type not in INJECTION_TYPES:
            raise ValueError("Type d'injection non pris en charge.")
        if self.plant_sample_id is not None and self.fraction_id is not None:
            raise ValueError("Une injection ne peut référencer simultanément un échantillon et une fraction.")
        if self.injection_type == "sample" and self.plant_sample_id is None and self.fraction_id is None:
            raise ValueError("Une injection de type sample doit référencer un échantillon ou une fraction.")
        return self


class ArtifactUploadForm(BaseModel):
    artifact_role: str = Field(..., min_length=2, max_length=40)
    parent_artifact_id: Optional[int] = Field(None, ge=1)
    conversion_tool: Optional[str] = Field(None, max_length=160)
    conversion_version: Optional[str] = Field(None, max_length=120)
    conversion_parameters: dict[str, Any] = Field(default_factory=dict)


class QualityControlCreate(BaseModel):
    injection_id: Optional[int] = Field(None, ge=1)
    qc_type: str = Field(..., min_length=2, max_length=40)
    metric_name: str = Field(..., min_length=1, max_length=120)
    metric_value: Optional[float] = Field(None, allow_inf_nan=False)
    metric_unit: Optional[str] = Field(None, max_length=60)
    status: Literal["not_assessed", "pass", "warning", "fail"] = "not_assessed"
    assessment_method: Literal["manual", "calculated", "imported"] = "manual"
    acceptance_criteria: dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None


class SpectralLibraryCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    version: str = Field(..., min_length=1, max_length=120)
    source_url: Optional[str] = None
    source_license: str = Field(..., min_length=3, max_length=255)
    data_format: Optional[str] = Field(None, max_length=40)
    reference_set_sha256: Optional[str] = Field(None, pattern="^[a-fA-F0-9]{64}$")
    citation: Optional[str] = None
    coverage_notes: Optional[str] = None


class LibraryReview(BaseModel):
    status: Literal["reviewed", "rejected"]
    rationale: str = Field(..., min_length=10, max_length=4000)


class ProcessingRunCreate(BaseModel):
    spectral_library_id: Optional[int] = Field(None, ge=1)
    software_name: str = Field(..., min_length=2, max_length=160)
    software_version: str = Field(..., min_length=1, max_length=120)
    workflow_name: str = Field(..., min_length=2, max_length=160)
    workflow_version: Optional[str] = Field(None, max_length=120)
    artifact_ids: list[int] = Field(..., min_length=1, max_length=500)
    parameters: dict[str, Any] = Field(default_factory=dict)


class FeatureCreate(BaseModel):
    feature_key: str = Field(..., min_length=1, max_length=120)
    precursor_mz: float = Field(..., gt=0, allow_inf_nan=False)
    retention_time: float = Field(..., ge=0, allow_inf_nan=False)
    retention_time_unit: Literal["second", "minute"] = "second"
    polarity: Literal["positive", "negative", "unknown"]
    charge: Optional[int] = Field(None, ge=1, le=20)
    adduct: Optional[str] = Field(None, max_length=80)
    molecular_formula_candidate: Optional[str] = Field(None, max_length=160)
    isotope_group: Optional[str] = Field(None, max_length=120)
    quality_flags: list[str] = Field(default_factory=list)


class FeatureObservationCreate(BaseModel):
    injection_id: int = Field(..., ge=1)
    observed_mz: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    retention_time: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    peak_area: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    peak_height: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    intensity: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    signal_to_noise: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    quality_flag: str = Field("not_assessed", max_length=40)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Ms2SpectrumCreate(BaseModel):
    injection_id: int = Field(..., ge=1)
    artifact_id: Optional[int] = Field(None, ge=1)
    scan_identifier: Optional[str] = Field(None, max_length=120)
    precursor_mz: float = Field(..., gt=0, allow_inf_nan=False)
    isolation_window: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    collision_energy: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    collision_energy_unit: Optional[str] = Field(None, max_length=40)
    polarity: Literal["positive", "negative", "unknown"]
    charge: Optional[int] = Field(None, ge=1, le=20)
    fragments: list[dict[str, Any]] = Field(default_factory=list)
    acquired_at: Optional[datetime] = None


class CompoundAnnotationCreate(BaseModel):
    phytochem_candidate_id: Optional[int] = Field(None, ge=1)
    spectral_library_id: Optional[int] = Field(None, ge=1)
    reference_standard_injection_id: Optional[int] = Field(None, ge=1)
    annotation_method: Literal["library_match", "authentic_standard", "in_silico", "formula_prediction", "manual"]
    annotation_name: Optional[str] = Field(None, max_length=255)
    molecular_formula: Optional[str] = Field(None, max_length=160)
    inchikey: Optional[str] = Field(None, max_length=64)
    smiles: Optional[str] = None
    msi_level: int = Field(..., ge=1, le=4)
    score_type: Optional[str] = Field(None, max_length=80)
    score_value: Optional[float] = Field(None, allow_inf_nan=False)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_level_one(self):
        if self.msi_level == 1:
            if self.reference_standard_injection_id is None:
                raise ValueError("MSI niveau 1 exige une injection de standard authentique.")
            if self.evidence.get("authentic_standard") is not True or self.evidence.get("same_method") is not True:
                raise ValueError("MSI niveau 1 exige un standard authentique analysé par la même méthode.")
            orthogonal = self.evidence.get("orthogonal_matches")
            allowed = {"accurate_mass", "retention_time", "ms2_spectrum"}
            if (
                not isinstance(orthogonal, list)
                or any(not isinstance(name, str) for name in orthogonal)
                or len(set(orthogonal)) < 2
                or not set(orthogonal).issubset(allowed)
            ):
                raise ValueError("MSI niveau 1 exige au moins deux propriétés orthogonales documentables par Nexora : masse exacte, temps de rétention ou spectre MS2.")
            evidence_keys = {
                "accurate_mass": "accurate_mass_match",
                "retention_time": "retention_time_match",
                "ms2_spectrum": "ms2_match",
            }
            if any(self.evidence.get(evidence_keys[name]) is not True for name in orthogonal):
                raise ValueError("Chaque propriété orthogonale annoncée doit être soutenue par une concordance documentée.")
            numeric_pairs = {
                "accurate_mass": ("accurate_mass_abs_error_ppm", "accurate_mass_tolerance_ppm"),
                "retention_time": ("retention_time_abs_delta_seconds", "retention_time_tolerance_seconds"),
            }
            for property_name in orthogonal:
                if property_name in numeric_pairs:
                    observed_key, tolerance_key = numeric_pairs[property_name]
                    for key in (observed_key, tolerance_key):
                        value = self.evidence.get(key)
                        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                            raise ValueError(f"MSI niveau 1 exige une valeur numérique finie pour {key}.")
                    if float(self.evidence[tolerance_key]) <= 0 or float(self.evidence[observed_key]) < 0:
                        raise ValueError(f"Les valeurs {observed_key}/{tolerance_key} doivent respecter leurs domaines positifs.")
                    if float(self.evidence[observed_key]) > float(self.evidence[tolerance_key]):
                        raise ValueError(f"La concordance {property_name} est hors de la tolérance déclarée.")
                elif property_name == "ms2_spectrum":
                    value = self.evidence.get("ms2_match_score")
                    score_type = self.evidence.get("ms2_match_score_type")
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                        raise ValueError("MSI niveau 1 exige un score MS2 numérique fini.")
                    if not isinstance(score_type, str) or not score_type.strip():
                        raise ValueError("MSI niveau 1 exige le type de score MS2.")
        if self.annotation_method == "library_match" and self.spectral_library_id is None:
            raise ValueError("Une annotation par bibliothèque doit référencer une bibliothèque spectrale.")
        return self


class AnnotationReview(BaseModel):
    status: Literal["accepted", "rejected"]
    review_rationale: str = Field(..., min_length=10, max_length=4000)


@router.post("/studies/{study_id}/specimens", status_code=201)
def create_botanical_specimen(
    study_id: int,
    body: BotanicalSpecimenCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_study(db, study_id, user)
    _json_object(body.metadata, "metadata")
    _json_object(body.evidence_references, "evidence_references")
    row = BotanicalSpecimen(
        study_id=study_id,
        specimen_code=body.specimen_code.strip(),
        reported_taxon=body.reported_taxon.strip(),
        accepted_taxon=body.accepted_taxon.strip() if body.accepted_taxon else None,
        taxon_identifier=body.taxon_identifier,
        taxonomy_source=body.taxonomy_source,
        identification_status="unverified",
        identification_method=body.identification_method,
        identifier_name=body.identifier_name,
        identified_at=body.identified_at,
        voucher_code=body.voucher_code,
        herbarium_name=body.herbarium_name,
        collector_name=body.collector_name,
        collection_date=body.collection_date,
        country=body.country,
        admin_region=body.admin_region,
        locality=body.locality,
        latitude=body.latitude,
        longitude=body.longitude,
        coordinate_uncertainty_m=body.coordinate_uncertainty_m,
        coordinate_access=body.coordinate_access,
        field_notes=body.field_notes,
        evidence_references=body.evidence_references,
        metadata_json=body.metadata,
        created_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "specimen": _specimen_dict(row)}


def _specimen_dict(row: BotanicalSpecimen) -> dict:
    return {
        "id": row.id, "study_id": row.study_id, "specimen_code": row.specimen_code,
        "reported_taxon": row.reported_taxon, "accepted_taxon": row.accepted_taxon,
        "taxon_identifier": row.taxon_identifier, "identification_status": row.identification_status,
        "voucher_code": row.voucher_code, "herbarium_name": row.herbarium_name,
        "collection_date": row.collection_date.isoformat() if row.collection_date else None,
        "country": row.country, "admin_region": row.admin_region, "locality": row.locality,
        "latitude": (
            float(row.latitude) if row.latitude is not None and row.coordinate_access == "public"
            else round(float(row.latitude), 2) if row.latitude is not None and row.coordinate_access == "coarse"
            else None
        ),
        "longitude": (
            float(row.longitude) if row.longitude is not None and row.coordinate_access == "public"
            else round(float(row.longitude), 2) if row.longitude is not None and row.coordinate_access == "coarse"
            else None
        ),
        "coordinate_uncertainty_m": float(row.coordinate_uncertainty_m) if row.coordinate_uncertainty_m is not None else None,
        "coordinate_access": row.coordinate_access, "metadata": row.metadata_json or {},
        "curation_rationale": row.curation_rationale,
        "curated_by": row.curated_by,
        "curated_at": row.curated_at.isoformat() if row.curated_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("/specimens/{specimen_id}/review")
def review_botanical_specimen(
    specimen_id: int,
    body: BotanicalSpecimenReview,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    row = db.query(BotanicalSpecimen).filter(
        BotanicalSpecimen.id == specimen_id
    ).first()
    if row is None:
        raise HTTPException(404, "Spécimen botanique introuvable.")
    references = list(row.evidence_references or [])
    references.append({
        "kind": "botanical_taxonomic_curation",
        "status": body.status,
        "source_reference": body.source_reference.strip(),
        "rationale": body.rationale.strip(),
        "curator_id": admin.id,
        "reviewed_at": datetime.utcnow().isoformat(),
    })
    row.identification_status = body.status
    row.accepted_taxon = body.accepted_taxon.strip() if body.accepted_taxon else row.accepted_taxon
    row.taxon_identifier = body.taxon_identifier or row.taxon_identifier
    row.taxonomy_source = body.taxonomy_source or row.taxonomy_source
    row.identification_method = body.identification_method or row.identification_method
    row.identifier_name = body.identifier_name or row.identifier_name
    row.voucher_code = body.voucher_code or row.voucher_code
    row.herbarium_name = body.herbarium_name or row.herbarium_name
    row.curation_rationale = body.rationale.strip()
    row.curated_by = admin.id
    row.curated_at = datetime.utcnow()
    row.evidence_references = references
    db.commit()
    db.refresh(row)
    return {"success": True, "specimen": _specimen_dict(row)}


@router.get("/studies/{study_id}/specimens")
def list_botanical_specimens(
    study_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_study(db, study_id, user)
    rows = db.query(BotanicalSpecimen).filter(
        BotanicalSpecimen.study_id == study_id
    ).order_by(BotanicalSpecimen.id.asc()).all()
    return {"success": True, "specimens": [_specimen_dict(row) for row in rows]}


@router.post("/studies/{study_id}/samples", status_code=201)
def create_plant_sample(
    study_id: int,
    body: PlantSampleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_study(db, study_id, user)
    specimen = _owned_row(db, BotanicalSpecimen, body.specimen_id, study_id, user)
    row = PlantMaterialSample(
        study_id=study_id, specimen_id=specimen.id, sample_code=body.sample_code.strip(),
        plant_part=body.plant_part.strip(), material_state=body.material_state,
        collection_date=body.collection_date.date() if body.collection_date else None,
        collector_name=body.collector_name, initial_mass_g=body.initial_mass_g,
        moisture_percent=body.moisture_percent, preservation_method=body.preservation_method,
        storage_conditions=_json_object(body.storage_conditions, "storage_conditions"),
        storage_location=body.storage_location, notes=body.notes,
        metadata_json=_json_object(body.metadata, "metadata"), created_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "sample": _sample_dict(row)}


def _sample_dict(row: PlantMaterialSample) -> dict:
    return {
        "id": row.id, "study_id": row.study_id, "specimen_id": row.specimen_id,
        "sample_code": row.sample_code, "plant_part": row.plant_part,
        "material_state": row.material_state,
        "initial_mass_g": float(row.initial_mass_g) if row.initial_mass_g is not None else None,
        "moisture_percent": float(row.moisture_percent) if row.moisture_percent is not None else None,
        "preservation_method": row.preservation_method, "storage_conditions": row.storage_conditions or {},
def _sample_dict(row: PlantMaterialSample) -> dict:
    return {
        "id": row.id, "study_id": row.study_id, "specimen_id": row.specimen_id,
        "sample_code": row.sample_code, "plant_part": row.plant_part,
        "material_state": row.material_state,
        "initial_mass_g": float(row.initial_mass_g) if row.initial_mass_g is not None else None,
        "moisture_percent": float(row.moisture_percent) if row.moisture_percent is not None else None,
        "preservation_method": row.preservation_method, "storage_conditions": row.storage_conditions or {},
        "sample_status": row.sample_status, "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/studies/{study_id}/samples")
def list_plant_samples(
    study_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_study(db, study_id, user)
    rows = db.query(PlantMaterialSample).filter(
        PlantMaterialSample.study_id == study_id
    ).order_by(PlantMaterialSample.id.asc()).offset(skip).limit(limit).all()
    return {"success": True, "samples": [_sample_dict(row) for row in rows]}


@router.post("/studies/{study_id}/extractions", status_code=201)
def create_extraction_batch(
    study_id: int,
    body: ExtractionBatchCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_study(db, study_id, user)
    sample = _owned_row(db, PlantMaterialSample, body.sample_id, study_id, user)
    input_mass = body.input_mass_g or (float(sample.initial_mass_g) if sample.initial_mass_g is not None else None)
    yield_percent = _yield_percent(body.extract_mass_g, input_mass)
    warnings = []
    if yield_percent is not None and yield_percent > 100:
        warnings.append("Rendement supérieur à 100 % : vérifier humidité, solvant résiduel, masses et unités.")
    parameters = _json_object(body.parameters, "parameters")
    parameters = {**parameters, "warnings": warnings, "yield_denominator_mass_g": input_mass}
    row = ExtractionBatch(
        study_id=study_id, sample_id=sample.id, batch_code=body.batch_code.strip(),
        extraction_method=body.extraction_method.strip(), method_version=body.method_version,
        protocol_reference=body.protocol_reference,
        solvent_system=_json_object(body.solvent_system, "solvent_system"),
        solid_liquid_ratio=body.solid_liquid_ratio, input_mass_g=input_mass,
        extract_mass_g=body.extract_mass_g, yield_percent=yield_percent,
        temperature_c=body.temperature_c, duration_minutes=body.duration_minutes,
        ph_value=body.ph_value, operator_name=body.operator_name, performed_at=body.performed_at,
        notes=body.notes, parameters=parameters, created_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "extraction": _extraction_dict(row), "warnings": warnings}


def _extraction_dict(row: ExtractionBatch) -> dict:
    return {
        "id": row.id, "study_id": row.study_id, "sample_id": row.sample_id,
        "batch_code": row.batch_code, "extraction_method": row.extraction_method,
        "method_version": row.method_version, "solvent_system": row.solvent_system or [],
        "input_mass_g": float(row.input_mass_g) if row.input_mass_g is not None else None,
        "extract_mass_g": float(row.extract_mass_g) if row.extract_mass_g is not None else None,
        "yield_percent": float(row.yield_percent) if row.yield_percent is not None else None,
        "status": row.status, "parameters": row.parameters or {},
    }


@router.get("/studies/{study_id}/extractions")
def list_extraction_batches(
    study_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_study(db, study_id, user)
    rows = db.query(ExtractionBatch).filter(
        ExtractionBatch.study_id == study_id
    ).order_by(ExtractionBatch.id.asc()).offset(skip).limit(limit).all()
    return {"success": True, "extractions": [_extraction_dict(row) for row in rows]}


@router.post("/extractions/{extraction_id}/fractions", status_code=201)
def create_fraction(
    extraction_id: int,
    body: FractionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    extraction = db.query(ExtractionBatch).filter(
        ExtractionBatch.id == extraction_id
    ).first()
    if extraction is None:
        raise HTTPException(404, "Lot d'extraction introuvable.")
    _owned_study(db, extraction.study_id, user)
    yield_percent = _yield_percent(
        body.recovered_mass_g,
        float(extraction.extract_mass_g) if extraction.extract_mass_g is not None else None,
    )
    warnings = []
    if yield_percent is not None and yield_percent > 100:
        warnings.append("Rendement de fraction supérieur à 100 % : vérifier masse, humidité et solvant résiduel.")
    row = PlantFraction(
        study_id=extraction.study_id, extraction_batch_id=extraction.id,
        fraction_code=body.fraction_code.strip(), fraction_order=body.fraction_order,
        separation_method=body.separation_method.strip(), method_version=body.method_version,
        elution_system=_json_object(body.elution_system, "elution_system"),
        collected_volume_ml=body.collected_volume_ml, recovered_mass_g=body.recovered_mass_g,
        yield_percent=yield_percent, notes=body.notes,
        parameters={**_json_object(body.parameters, "parameters"), "warnings": warnings},
        created_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "success": True,
        "fraction": {
            "id": row.id, "study_id": row.study_id, "extraction_batch_id": row.extraction_batch_id,
            "fraction_code": row.fraction_code, "separation_method": row.separation_method,
            "recovered_mass_g": float(row.recovered_mass_g) if row.recovered_mass_g is not None else None,
            "yield_percent": float(row.yield_percent) if row.yield_percent is not None else None,
        },
        "warnings": warnings,
    }


def _fraction_dict(row: PlantFraction) -> dict:
    return {
        "id": row.id, "study_id": row.study_id, "extraction_batch_id": row.extraction_batch_id,
        "fraction_code": row.fraction_code, "fraction_order": row.fraction_order,
        "separation_method": row.separation_method, "method_version": row.method_version,
        "elution_system": row.elution_system or [],
        "collected_volume_ml": float(row.collected_volume_ml) if row.collected_volume_ml is not None else None,
        "recovered_mass_g": float(row.recovered_mass_g) if row.recovered_mass_g is not None else None,
        "yield_percent": float(row.yield_percent) if row.yield_percent is not None else None,
        "parameters": row.parameters or {}, "status": row.status,
    }


@router.get("/studies/{study_id}/fractions")
def list_plant_fractions(
    study_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_study(db, study_id, user)
    rows = db.query(PlantFraction).filter(
        PlantFraction.study_id == study_id
    ).order_by(PlantFraction.id.asc()).offset(skip).limit(limit).all()
    return {"success": True, "fractions": [_fraction_dict(row) for row in rows]}


@router.post("/studies/{study_id}/analytical-batches", status_code=201)
def create_analytical_batch(
    study_id: int,
    body: AnalyticalBatchCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_study(db, study_id, user)
    row = LcmsAnalyticalBatch(
        study_id=study_id, batch_code=body.batch_code.strip(),
        instrument_vendor=body.instrument_vendor.strip(), instrument_model=body.instrument_model.strip(),
        mass_analyzer=body.mass_analyzer, ion_source=body.ion_source,
        chromatography_column=body.chromatography_column, mobile_phase_a=body.mobile_phase_a,
        mobile_phase_b=body.mobile_phase_b, gradient_program=_json_object(body.gradient_program, "gradient_program"),
        flow_rate=body.flow_rate, flow_rate_unit=body.flow_rate_unit,
        column_temperature_c=body.column_temperature_c, injection_volume=body.injection_volume,
        injection_volume_unit=body.injection_volume_unit, acquisition_method=body.acquisition_method.strip(),
        acquisition_method_version=body.acquisition_method_version, polarity_modes=body.polarity_modes,
        operator_name=body.operator_name, acquired_at=body.acquired_at, completed_at=body.completed_at,
        qc_policy=_json_object(body.qc_policy, "qc_policy"), notes=body.notes,
        metadata_json=_json_object(body.metadata, "metadata"), created_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "analytical_batch": _batch_dict(row)}


def _batch_dict(row: LcmsAnalyticalBatch) -> dict:
    return {
        "id": row.id, "study_id": row.study_id, "batch_code": row.batch_code,
        "instrument_vendor": row.instrument_vendor, "instrument_model": row.instrument_model,
        "mass_analyzer": row.mass_analyzer, "ion_source": row.ion_source,
        "acquisition_method": row.acquisition_method, "acquisition_method_version": row.acquisition_method_version,
        "polarity_modes": row.polarity_modes or [], "qc_policy": row.qc_policy or {},
        "status": row.status, "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/studies/{study_id}/analytical-batches")
def list_analytical_batches(
    study_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_study(db, study_id, user)
    rows = db.query(LcmsAnalyticalBatch).filter(
        LcmsAnalyticalBatch.study_id == study_id
    ).order_by(LcmsAnalyticalBatch.id.asc()).offset(skip).limit(limit).all()
    return {"success": True, "analytical_batches": [_batch_dict(row) for row in rows]}


@router.post("/batches/{batch_id}/injections", status_code=201)
def create_injection(
    batch_id: int,
    body: InjectionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    batch = db.query(LcmsAnalyticalBatch).filter(LcmsAnalyticalBatch.id == batch_id).first()
    if batch is None:
        raise HTTPException(404, "Lot analytique introuvable.")
    _owned_study(db, batch.study_id, user)
    if body.plant_sample_id is not None:
        _owned_row(db, PlantMaterialSample, body.plant_sample_id, batch.study_id, user)
    if body.fraction_id is not None:
        _owned_row(db, PlantFraction, body.fraction_id, batch.study_id, user)
    row = LcmsInjection(
        study_id=batch.study_id, batch_id=batch.id, injection_order=body.injection_order,
        injection_type=body.injection_type, sample_label=body.sample_label.strip(),
        plant_sample_id=body.plant_sample_id, fraction_id=body.fraction_id,
        technical_replicate=body.technical_replicate, injection_volume=body.injection_volume,
        injection_volume_unit=body.injection_volume_unit, acquisition_reference=body.acquisition_reference,
        metadata_json=_json_object(body.metadata, "metadata"), created_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "injection": _injection_dict(row)}


def _injection_dict(row: LcmsInjection) -> dict:
    return {
        "id": row.id, "study_id": row.study_id, "batch_id": row.batch_id,
        "injection_order": row.injection_order, "injection_type": row.injection_type,
        "sample_label": row.sample_label, "plant_sample_id": row.plant_sample_id,
        "fraction_id": row.fraction_id, "technical_replicate": row.technical_replicate,
        "status": row.status,
    }


@router.get("/batches/{batch_id}/injections")
def list_batch_injections(
    batch_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    batch = db.query(LcmsAnalyticalBatch).filter(LcmsAnalyticalBatch.id == batch_id).first()
    if batch is None:
        raise HTTPException(404, "Lot analytique introuvable.")
    _owned_study(db, batch.study_id, user)
    rows = db.query(LcmsInjection).filter(
        LcmsInjection.batch_id == batch_id
    ).order_by(LcmsInjection.injection_order.asc()).offset(skip).limit(limit).all()
    return {"success": True, "injections": [_injection_dict(row) for row in rows]}


@router.post("/batches/{batch_id}/injections/{injection_id}/artifacts", status_code=201)
def upload_data_artifact(
    batch_id: int,
    injection_id: int,
    artifact_role: str,
    file: UploadFile = File(...),
    parent_artifact_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    batch = db.query(LcmsAnalyticalBatch).filter(LcmsAnalyticalBatch.id == batch_id).first()
    if batch is None:
        raise HTTPException(404, "Lot analytique introuvable.")
    _owned_study(db, batch.study_id, user)
    injection = db.query(LcmsInjection).filter(
        LcmsInjection.id == injection_id,
        LcmsInjection.batch_id == batch_id,
        LcmsInjection.study_id == batch.study_id,
    ).first()
    if injection is None:
        raise HTTPException(404, "Injection introuvable dans ce lot.")
    if artifact_role not in ARTIFACT_ROLES:
        raise HTTPException(422, "Rôle d'artefact non pris en charge.")
    raw_filename = (file.filename or "").replace("\\", "/")
    filename = Path(raw_filename).name
    if (
        not filename or filename in {".", ".."} or len(filename) > 255
        or any(ord(char) < 32 for char in filename)
    ):
        raise HTTPException(422, "Nom de fichier invalide.")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_ARTIFACTS:
        raise HTTPException(415, "Extension de fichier non prise en charge pour le registre LC-MS/MS.")
    parent = None
    if parent_artifact_id is not None:
        parent = db.query(LcmsDataArtifact).filter(
            LcmsDataArtifact.id == parent_artifact_id,
            LcmsDataArtifact.study_id == batch.study_id,
        ).first()
        if parent is None:
            raise HTTPException(404, "Artefact parent introuvable dans cette étude.")
        if parent.injection_id != injection_id:
            raise HTTPException(422, "L'artefact parent doit appartenir à la même injection.")
    root = Path(settings.LCMS_STORAGE_DIR).resolve()
    temporary_path = None
    digest = hashlib.sha256()
    size = 0
    try:
        root.mkdir(parents=True, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(prefix="lcms-upload-", suffix=".part", dir=str(root))
        with os.fdopen(fd, "wb") as destination:
            while True:
                block = file.file.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > settings.LCMS_MAX_ARTIFACT_BYTES:
                    raise HTTPException(413, "Artefact LC-MS/MS supérieur à la limite configurée.")
                digest.update(block)
                destination.write(block)
            destination.flush()
            os.fsync(destination.fileno())
        if size == 0:
            raise HTTPException(400, "Le fichier fourni est vide.")
        sha = digest.hexdigest()
        relative = Path(sha[:2]) / (sha + suffix)
        target = (root / relative).resolve()
        if root not in target.parents:
            raise HTTPException(400, "Chemin d'artefact invalide.")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing_hash = _sha256_path(target)
            if existing_hash != sha:
                raise HTTPException(409, "Collision d'empreinte détectée dans le stockage d'artefacts.")
            os.unlink(temporary_path)
            temporary_path = None
        else:
            os.replace(temporary_path, target)
            temporary_path = None
        row = LcmsDataArtifact(
            study_id=batch.study_id, injection_id=injection_id,
            parent_artifact_id=parent.id if parent else None,
            artifact_role=artifact_role, data_format=ALLOWED_ARTIFACTS[suffix],
            original_filename=filename, storage_path=relative.as_posix(),
            content_type=file.content_type or mimetypes.guess_type(filename)[0],
            size_bytes=size, sha256=sha, conversion_tool=None, conversion_version=None,
            conversion_parameters={}, validation_status="checksum_verified", uploaded_by=user.id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {"success": True, "artifact": _artifact_dict(row),
                "notice": "Empreinte SHA-256 vérifiée ; le contenu interne du format n'a pas été interprété."}
    except HTTPException:
        db.rollback()
        raise
    except OSError as exc:
        db.rollback()
        raise HTTPException(500, "Échec du stockage externe de l'artefact.") from exc
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
        file.file.close()


@router.get("/batches/{batch_id}/artifacts")
def list_batch_artifacts(
    batch_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    batch = db.query(LcmsAnalyticalBatch).filter(LcmsAnalyticalBatch.id == batch_id).first()
    if batch is None:
        raise HTTPException(404, "Lot analytique introuvable.")
    _owned_study(db, batch.study_id, user)
    injection_ids = db.query(LcmsInjection.id).filter(LcmsInjection.batch_id == batch_id)
    rows = db.query(LcmsDataArtifact).filter(
        LcmsDataArtifact.study_id == batch.study_id,
        LcmsDataArtifact.injection_id.in_(injection_ids),
    ).order_by(LcmsDataArtifact.id.asc()).offset(skip).limit(limit).all()
    return {"success": True, "artifacts": [_artifact_dict(row) for row in rows]}


def _artifact_dict(row: LcmsDataArtifact) -> dict:
    return {
        "id": row.id, "study_id": row.study_id, "injection_id": row.injection_id,
        "parent_artifact_id": row.parent_artifact_id, "artifact_role": row.artifact_role,
        "data_format": row.data_format, "original_filename": row.original_filename,
        "size_bytes": row.size_bytes, "sha256": row.sha256,
        "validation_status": row.validation_status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/artifacts/{artifact_id}/download")
def download_data_artifact(
    artifact_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.query(LcmsDataArtifact).filter(LcmsDataArtifact.id == artifact_id).first()
    if row is None:
        raise HTTPException(404, "Artefact LC-MS/MS introuvable.")
    _owned_study(db, row.study_id, user)
    root = Path(settings.LCMS_STORAGE_DIR).resolve()
    relative = Path(row.storage_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(409, "Chemin de stockage invalide.")
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "Fichier source introuvable dans le stockage externe.")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if actual != row.sha256:
        raise HTTPException(409, "L'empreinte SHA-256 du fichier ne correspond plus au registre.")
    return FileResponse(
        str(path), filename=row.original_filename,
        media_type=row.content_type or "application/octet-stream",
        headers={"X-Artifact-SHA256": actual},
    )


@router.post("/batches/{batch_id}/quality-controls", status_code=201)
def create_quality_control(
    batch_id: int,
    body: QualityControlCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    batch = db.query(LcmsAnalyticalBatch).filter(LcmsAnalyticalBatch.id == batch_id).first()
    if batch is None:
        raise HTTPException(404, "Lot analytique introuvable.")
    _owned_study(db, batch.study_id, user)
    if body.qc_type not in QC_TYPES:
        raise HTTPException(422, "Type de contrôle qualité non pris en charge.")
    if body.injection_id is not None:
        injection = db.query(LcmsInjection).filter(
            LcmsInjection.id == body.injection_id,
            LcmsInjection.batch_id == batch_id,
        ).first()
        if injection is None:
            raise HTTPException(422, "L'injection de contrôle n'appartient pas au lot.")
    row = LcmsQualityControlObservation(
        study_id=batch.study_id, batch_id=batch_id, injection_id=body.injection_id,
        qc_type=body.qc_type, metric_name=body.metric_name.strip(),
        metric_value=body.metric_value, metric_unit=body.metric_unit,
        status=body.status, assessment_method=body.assessment_method,
        acceptance_criteria=_json_object(body.acceptance_criteria, "acceptance_criteria"),
        rationale=body.rationale, assessed_by=user.id,
        assessed_at=datetime.utcnow() if body.status != "not_assessed" else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "quality_control": {
        "id": row.id, "batch_id": row.batch_id, "injection_id": row.injection_id,
        "qc_type": row.qc_type, "metric_name": row.metric_name,
        "metric_value": float(row.metric_value) if row.metric_value is not None else None,
        "metric_unit": row.metric_unit, "status": row.status,
        "acceptance_criteria": row.acceptance_criteria or {},
        "rationale": row.rationale,
    }}


@router.get("/batches/{batch_id}/quality-controls")
def list_batch_quality_controls(
    batch_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    batch = db.query(LcmsAnalyticalBatch).filter(LcmsAnalyticalBatch.id == batch_id).first()
    if batch is None:
        raise HTTPException(404, "Lot analytique introuvable.")
    _owned_study(db, batch.study_id, user)
    rows = db.query(LcmsQualityControlObservation).filter(
        LcmsQualityControlObservation.batch_id == batch_id
    ).order_by(LcmsQualityControlObservation.id.asc()).offset(skip).limit(limit).all()
    return {"success": True, "quality_controls": [{
        "id": row.id, "batch_id": row.batch_id, "injection_id": row.injection_id,
        "qc_type": row.qc_type, "metric_name": row.metric_name,
        "metric_value": float(row.metric_value) if row.metric_value is not None else None,
        "metric_unit": row.metric_unit, "status": row.status,
        "assessment_method": row.assessment_method,
        "acceptance_criteria": row.acceptance_criteria or {},
        "rationale": row.rationale,
        "assessed_by": row.assessed_by,
        "assessed_at": row.assessed_at.isoformat() if row.assessed_at else None,
    } for row in rows]}


@router.post("/spectral-libraries", status_code=201)
def register_spectral_library(
    body: SpectralLibraryCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    row = LcmsSpectralLibrary(
        name=body.name.strip(), version=body.version.strip(),
        source_url=body.source_url, source_license=body.source_license.strip(),
        data_format=body.data_format,
        reference_set_sha256=body.reference_set_sha256.lower() if body.reference_set_sha256 else None,
        citation=body.citation, coverage_notes=body.coverage_notes,
        curation_status="registered", registered_by=admin.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "spectral_library": _library_dict(row)}


def _library_dict(row: LcmsSpectralLibrary) -> dict:
    return {
        "id": row.id, "name": row.name, "version": row.version,
        "source_url": row.source_url, "source_license": row.source_license,
        "data_format": row.data_format, "reference_set_sha256": row.reference_set_sha256,
        "curation_status": row.curation_status,
        "curation_rationale": row.curation_rationale,
        "curated_by": row.curated_by,
        "curated_at": row.curated_at.isoformat() if row.curated_at else None,
    }


@router.post("/spectral-libraries/{library_id}/review")
def review_spectral_library(
    library_id: int,
    body: LibraryReview,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    row = db.query(LcmsSpectralLibrary).filter(LcmsSpectralLibrary.id == library_id).first()
    if row is None:
        raise HTTPException(404, "Bibliothèque spectrale introuvable.")
    if body.status == "reviewed" and not row.reference_set_sha256:
        raise HTTPException(422, "Une bibliothèque ne peut être revue sans SHA-256 du jeu de référence.")
    if body.status == "reviewed" and not (row.citation or row.source_url):
        raise HTTPException(422, "Une bibliothèque revue doit conserver une citation ou une source publique.")
    row.curation_status = body.status
    row.curation_rationale = body.rationale.strip()
    row.curated_by = admin.id
    row.curated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"success": True, "spectral_library": _library_dict(row)}


@router.post("/studies/{study_id}/batches/{batch_id}/processing-runs", status_code=201)
def register_processing_run(
    study_id: int,
    batch_id: int,
    body: ProcessingRunCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    batch = _owned_row(db, LcmsAnalyticalBatch, batch_id, study_id, user)
    artifact_ids = sorted(set(body.artifact_ids))
    if len(artifact_ids) != len(body.artifact_ids):
        raise HTTPException(422, "La liste des artefacts contient des identifiants dupliqués.")
    artifacts = db.query(LcmsDataArtifact).filter(
        LcmsDataArtifact.study_id == study_id,
        LcmsDataArtifact.injection_id.in_(
            db.query(LcmsInjection.id).filter(LcmsInjection.batch_id == batch.id)
        ),
        LcmsDataArtifact.id.in_(artifact_ids),
    ).all()
    if len(artifacts) != len(artifact_ids):
        raise HTTPException(422, "Tous les artefacts d'entrée doivent appartenir au lot analytique.")
    library = None
    if body.spectral_library_id is not None:
        library = db.query(LcmsSpectralLibrary).filter(
            LcmsSpectralLibrary.id == body.spectral_library_id
        ).first()
        if library is None or library.curation_status != "reviewed":
            raise HTTPException(409, "La bibliothèque spectrale doit exister et avoir été revue par un curateur.")
    inputs = [{
        "artifact_id": row.id, "role": row.artifact_role,
        "format": row.data_format, "sha256": row.sha256, "size_bytes": row.size_bytes,
    } for row in sorted(artifacts, key=lambda x: x.id)]
    parameters = _json_object(body.parameters, "parameters")
    input_payload = {
        "study_id": study_id, "batch_id": batch.id, "inputs": inputs,
        "software_name": body.software_name, "software_version": body.software_version,
        "workflow_name": body.workflow_name, "workflow_version": body.workflow_version,
        "parameters": parameters,
        "spectral_library": {
            "id": library.id, "name": library.name, "version": library.version,
            "sha256": library.reference_set_sha256,
            "source_license": library.source_license,
            "source_url": library.source_url,
            "citation": library.citation,
            "curation_status": library.curation_status,
        } if library else None,
    }
    input_sha = sha256_json(input_payload)
    manifest = seal_manifest(
        {
            **input_payload,
            "input_sha256": input_sha,
            "execution_status": "registered_only",
            "interpretation_limit": "L'enregistrement ne lance aucun traitement LC-MS/MS. Les résultats doivent être importés d'un moteur externe identifié.",
        },
        manifest_type="lcms_processing_input",
    )
    row = LcmsProcessingRun(
        study_id=study_id, batch_id=batch_id, spectral_library_id=library.id if library else None,
        software_name=body.software_name.strip(), software_version=body.software_version.strip(),
        workflow_name=body.workflow_name.strip(), workflow_version=body.workflow_version,
        parameters=parameters, input_sha256=input_sha, output_sha256=None,
        status="registered",
        provenance_manifest={"input_manifest": manifest, "output_manifest": None},
        created_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "processing_run": _processing_dict(row)}


def _processing_dict(row: LcmsProcessingRun) -> dict:
    return {
        "id": row.id, "study_id": row.study_id, "batch_id": row.batch_id,
        "spectral_library_id": row.spectral_library_id, "software_name": row.software_name,
        "software_version": row.software_version, "workflow_name": row.workflow_name,
        "workflow_version": row.workflow_version, "input_sha256": row.input_sha256,
        "output_sha256": row.output_sha256, "status": row.status,
        "provenance_manifest": row.provenance_manifest or {},
        "output_manifest": (row.provenance_manifest or {}).get("output_manifest"),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/studies/{study_id}/processing-runs")
def list_processing_runs(
    study_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_study(db, study_id, user)
    rows = db.query(LcmsProcessingRun).filter(
        LcmsProcessingRun.study_id == study_id
    ).order_by(LcmsProcessingRun.id.desc()).offset(skip).limit(limit).all()
    return {"success": True, "processing_runs": [_processing_dict(row) for row in rows]}


@router.post("/processing-runs/{processing_run_id}/features", status_code=201)
def import_features(
    processing_run_id: int,
    features: list[FeatureCreate],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = db.query(LcmsProcessingRun).filter(
        LcmsProcessingRun.id == processing_run_id
    ).first()
    if run is None:
        raise HTTPException(404, "Traitement LC-MS/MS introuvable.")
    _owned_study(db, run.study_id, user)
    if run.status not in {"registered", "processing"}:
        raise HTTPException(409, "Ce traitement n'accepte pas d'import de features dans son état actuel.")
    if not features or len(features) > 10000:
        raise HTTPException(422, "Importer entre 1 et 10 000 features par lot.")
    keys = [item.feature_key.strip() for item in features]
    if len(set(keys)) != len(keys):
        raise HTTPException(422, "Les feature_key doivent être uniques dans un lot d'import.")
    rows = []
    for item in features:
        rows.append(LcmsFeature(
            processing_run_id=run.id, feature_key=item.feature_key.strip(),
            precursor_mz=item.precursor_mz, retention_time=item.retention_time,
            retention_time_unit=item.retention_time_unit, polarity=item.polarity,
            charge=item.charge, adduct=item.adduct,
            molecular_formula_candidate=item.molecular_formula_candidate,
            isotope_group=item.isotope_group, quality_flags=item.quality_flags,
        ))
    db.add_all(rows)
    db.flush()
    imported_payload = [
        item.model_dump(mode="json") for item in features
    ]
    output_sha = sha256_json(imported_payload)
    output_manifest = seal_manifest(
        {
            "processing_run_id": run.id,
            "input_sha256": run.input_sha256,
            "output_sha256": output_sha,
            "feature_count": len(rows),
            "feature_keys": keys,
            "feature_ids": [row.id for row in rows],
            "import_mode": "external_feature_table",
        },
        manifest_type="lcms_processing_output",
    )
    run.output_sha256 = output_sha
    run.provenance_manifest = {
        **(run.provenance_manifest or {}),
        "output_manifest": output_manifest,
    }
    run.status = "completed"
    run.completed_at = datetime.utcnow()
    db.commit()
    for row in rows:
        db.refresh(row)
    return {
        "success": True, "processing_run_id": run.id,
        "output_sha256": run.output_sha256,
        "output_manifest": (run.provenance_manifest or {}).get("output_manifest"),
        "features": [{"id": row.id, "feature_key": row.feature_key} for row in rows],
        "notice": "Les features ont été enregistrées ; aucune extraction automatique n'a été exécutée par Nexora.",
    }


def _feature_dict(row: LcmsFeature) -> dict:
    return {
        "id": row.id, "processing_run_id": row.processing_run_id,
        "feature_key": row.feature_key, "precursor_mz": float(row.precursor_mz),
        "retention_time": float(row.retention_time),
        "retention_time_unit": row.retention_time_unit,
        "polarity": row.polarity, "charge": row.charge, "adduct": row.adduct,
        "molecular_formula_candidate": row.molecular_formula_candidate,
        "isotope_group": row.isotope_group, "quality_flags": row.quality_flags or [],
    }


@router.get("/processing-runs/{processing_run_id}/features")
def list_processing_features(
    processing_run_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = db.query(LcmsProcessingRun).filter(
        LcmsProcessingRun.id == processing_run_id
    ).first()
    if run is None:
        raise HTTPException(404, "Traitement LC-MS/MS introuvable.")
    _owned_study(db, run.study_id, user)
    rows = db.query(LcmsFeature).filter(
        LcmsFeature.processing_run_id == processing_run_id
    ).order_by(LcmsFeature.id.asc()).offset(skip).limit(limit).all()
    return {"success": True, "features": [_feature_dict(row) for row in rows]}


@router.post("/features/{feature_id}/observations", status_code=201)
def create_feature_observation(
    feature_id: int,
    body: FeatureObservationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    feature = db.query(LcmsFeature).filter(LcmsFeature.id == feature_id).first()
    if feature is None:
        raise HTTPException(404, "Feature LC-MS/MS introuvable.")
    run = db.query(LcmsProcessingRun).filter(LcmsProcessingRun.id == feature.processing_run_id).one()
    _owned_study(db, run.study_id, user)
    injection = db.query(LcmsInjection).filter(
        LcmsInjection.id == body.injection_id,
        LcmsInjection.batch_id == run.batch_id,
    ).first()
    if injection is None:
        raise HTTPException(422, "L'observation doit référencer une injection du lot traité.")
    row = LcmsFeatureObservation(
        feature_id=feature.id, injection_id=injection.id,
        observed_mz=body.observed_mz, retention_time=body.retention_time,
        peak_area=body.peak_area, peak_height=body.peak_height,
        intensity=body.intensity, signal_to_noise=body.signal_to_noise,
        quality_flag=body.quality_flag,
        metadata_json=_json_object(body.metadata, "metadata"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "observation": {
        "id": row.id, "feature_id": row.feature_id, "injection_id": row.injection_id,
        "observed_mz": float(row.observed_mz) if row.observed_mz is not None else None,
        "retention_time": float(row.retention_time) if row.retention_time is not None else None,
        "peak_area": float(row.peak_area) if row.peak_area is not None else None,
        "quality_flag": row.quality_flag,
    }}


@router.get("/features/{feature_id}/observations")
def list_feature_observations(
    feature_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    feature = db.query(LcmsFeature).filter(LcmsFeature.id == feature_id).first()
    if feature is None:
        raise HTTPException(404, "Feature LC-MS/MS introuvable.")
    run = db.query(LcmsProcessingRun).filter(LcmsProcessingRun.id == feature.processing_run_id).one()
    _owned_study(db, run.study_id, user)
    rows = db.query(LcmsFeatureObservation).filter(
        LcmsFeatureObservation.feature_id == feature_id
    ).order_by(LcmsFeatureObservation.injection_id.asc()).offset(skip).limit(limit).all()
    return {"success": True, "observations": [{
        "id": row.id, "feature_id": row.feature_id, "injection_id": row.injection_id,
        "observed_mz": float(row.observed_mz) if row.observed_mz is not None else None,
        "retention_time": float(row.retention_time) if row.retention_time is not None else None,
        "peak_area": float(row.peak_area) if row.peak_area is not None else None,
        "peak_height": float(row.peak_height) if row.peak_height is not None else None,
        "intensity": float(row.intensity) if row.intensity is not None else None,
        "signal_to_noise": float(row.signal_to_noise) if row.signal_to_noise is not None else None,
        "quality_flag": row.quality_flag, "metadata": row.metadata_json or {},
    } for row in rows]}


@router.post("/features/{feature_id}/ms2-spectra", status_code=201)
def create_ms2_spectrum(
    feature_id: int,
    body: Ms2SpectrumCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    feature = db.query(LcmsFeature).filter(LcmsFeature.id == feature_id).first()
    if feature is None:
        raise HTTPException(404, "Feature LC-MS/MS introuvable.")
    run = db.query(LcmsProcessingRun).filter(LcmsProcessingRun.id == feature.processing_run_id).one()
    _owned_study(db, run.study_id, user)
    injection = db.query(LcmsInjection).filter(
        LcmsInjection.id == body.injection_id,
        LcmsInjection.batch_id == run.batch_id,
    ).first()
    if injection is None:
        raise HTTPException(422, "Le spectre MS2 doit référencer une injection du lot traité.")
    artifact = None
    if body.artifact_id is not None:
        artifact = db.query(LcmsDataArtifact).filter(
            LcmsDataArtifact.id == body.artifact_id,
            LcmsDataArtifact.injection_id == injection.id,
        ).first()
        if artifact is None:
            raise HTTPException(422, "L'artefact source du spectre doit appartenir à la même injection.")
    row = LcmsMs2Spectrum(
        feature_id=feature.id, injection_id=injection.id,
        artifact_id=artifact.id if artifact else None,
        scan_identifier=body.scan_identifier, precursor_mz=body.precursor_mz,
        isolation_window=body.isolation_window, collision_energy=body.collision_energy,
        collision_energy_unit=body.collision_energy_unit, polarity=body.polarity,
        charge=body.charge, fragments=_json_object(body.fragments, "fragments"),
        acquired_at=body.acquired_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "ms2_spectrum": {
        "id": row.id, "feature_id": row.feature_id, "injection_id": row.injection_id,
        "artifact_id": row.artifact_id, "scan_identifier": row.scan_identifier,
        "precursor_mz": float(row.precursor_mz), "polarity": row.polarity,
        "fragment_count": len(row.fragments or []),
    }}


@router.get("/features/{feature_id}/ms2-spectra")
def list_feature_ms2_spectra(
    feature_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    feature = db.query(LcmsFeature).filter(LcmsFeature.id == feature_id).first()
    if feature is None:
        raise HTTPException(404, "Feature LC-MS/MS introuvable.")
    run = db.query(LcmsProcessingRun).filter(LcmsProcessingRun.id == feature.processing_run_id).one()
    _owned_study(db, run.study_id, user)
    rows = db.query(LcmsMs2Spectrum).filter(
        LcmsMs2Spectrum.feature_id == feature_id
    ).order_by(LcmsMs2Spectrum.id.asc()).offset(skip).limit(limit).all()
    return {"success": True, "ms2_spectra": [{
        "id": row.id, "feature_id": row.feature_id, "injection_id": row.injection_id,
        "artifact_id": row.artifact_id, "scan_identifier": row.scan_identifier,
        "precursor_mz": float(row.precursor_mz), "isolation_window": float(row.isolation_window) if row.isolation_window is not None else None,
        "collision_energy": float(row.collision_energy) if row.collision_energy is not None else None,
        "collision_energy_unit": row.collision_energy_unit, "polarity": row.polarity,
        "charge": row.charge, "fragments": row.fragments or [],
    } for row in rows]}


@router.post("/features/{feature_id}/annotations", status_code=201)
def create_compound_annotation(
    feature_id: int,
    body: CompoundAnnotationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    feature = db.query(LcmsFeature).filter(LcmsFeature.id == feature_id).first()
    if feature is None:
        raise HTTPException(404, "Feature LC-MS/MS introuvable.")
    run = db.query(LcmsProcessingRun).filter(LcmsProcessingRun.id == feature.processing_run_id).one()
    _owned_study(db, run.study_id, user)
    library = None
    if body.spectral_library_id is not None:
        library = db.query(LcmsSpectralLibrary).filter(
            LcmsSpectralLibrary.id == body.spectral_library_id
        ).first()
        if library is None:
            raise HTTPException(404, "Bibliothèque spectrale introuvable.")
        if body.annotation_method == "library_match" and library.curation_status != "reviewed":
            raise HTTPException(409, "La bibliothèque doit être revue avant de soutenir une annotation acceptée.")
    candidate = None
    if body.phytochem_candidate_id is not None:
        candidate = db.query(PhytochemicalCandidate).filter(
            PhytochemicalCandidate.id == body.phytochem_candidate_id
        ).first()
        if candidate is None:
            raise HTTPException(404, "Candidat phytochimique introuvable.")
    standard = None
    if body.reference_standard_injection_id is not None:
        standard = db.query(LcmsInjection).filter(
            LcmsInjection.id == body.reference_standard_injection_id,
            LcmsInjection.batch_id == run.batch_id,
            LcmsInjection.injection_type == "reference_standard",
        ).first()
        if standard is None:
            raise HTTPException(422, "Le standard de référence doit être une injection de type reference_standard du même lot.")
    if body.msi_level == 1 and standard is None:
        raise HTTPException(422, "MSI niveau 1 exige un standard de référence traçable.")
    if body.msi_level == 1:
        orthogonal = body.evidence["orthogonal_matches"]
        standard_observation = db.query(LcmsFeatureObservation).filter(
            LcmsFeatureObservation.feature_id == feature.id,
            LcmsFeatureObservation.injection_id == standard.id,
        ).first()
        if "accurate_mass" in orthogonal and (
            standard_observation is None or standard_observation.observed_mz is None
        ):
            raise HTTPException(422, "La concordance de masse exacte exige une valeur m/z mesurée pour le standard authentique.")
        if "retention_time" in orthogonal and (
            standard_observation is None or standard_observation.retention_time is None
        ):
            raise HTTPException(422, "La concordance du temps de rétention exige une valeur mesurée pour le standard authentique.")
        if "ms2_spectrum" in orthogonal and db.query(LcmsMs2Spectrum).filter(
            LcmsMs2Spectrum.feature_id == feature.id,
            LcmsMs2Spectrum.injection_id == standard.id,
        ).first() is None:
            raise HTTPException(422, "La concordance MS2 annoncée exige un spectre MS2 acquis pour le standard authentique.")
    row = LcmsCompoundAnnotation(
        feature_id=feature.id,
        phytochem_candidate_id=candidate.id if candidate else None,
        spectral_library_id=library.id if library else None,
        reference_standard_injection_id=standard.id if standard else None,
        annotation_method=body.annotation_method, annotation_name=body.annotation_name,
        molecular_formula=body.molecular_formula, inchikey=body.inchikey,
        smiles=body.smiles, msi_level=body.msi_level,
        score_type=body.score_type, score_value=body.score_value,
        evidence=_json_object(body.evidence, "evidence"),
        status="candidate", created_by=user.id,
    )
    db.add(row)
    db.flush()
    db.add(ResearchEvidence(
        study_id=run.study_id,
        object_type="lcms_compound_annotation",
        object_id=str(row.id),
        evidence_kind="lcms_ms_feature_annotation",
        evidence_status="computational_hypothesis",
        evidence_grade="D",
        claim=(
            f"Annotation candidate LC-MS/MS de {body.annotation_name or 'feature ' + str(feature.id)} "
            f"(MSI niveau {body.msi_level}); aucune activité biologique n'est déduite."
        ),
        source_type="lcms_ms_feature",
        source_id=f"processing_run:{run.id}:feature:{feature.id}",
        source_title=body.annotation_name,
        method=body.annotation_method,
        tool_name="N3XORA LC-MS/MS Evidence Registry",
        tool_version="1.0.0",
        input_sha256=run.input_sha256,
        output_sha256=run.output_sha256,
        limitations=(
            "Une annotation spectrale est une hypothèse d'identité. "
            "Elle ne prouve ni la présence absolue/quantité dans le matériel végétal, "
            "ni une activité pharmacologique ou une efficacité thérapeutique."
        ),
        metadata_json={
            "feature_id": feature.id,
            "processing_run_id": run.id,
            "annotation_id": row.id,
            "msi_level": body.msi_level,
            "annotation_status": "candidate",
            "spectral_library_id": library.id if library else None,
            "phytochem_candidate_id": candidate.id if candidate else None,
        },
        created_by=user.id,
    ))
    db.commit()
    db.refresh(row)
    return {"success": True, "annotation": _annotation_dict(row),
            "notice": "Une annotation candidate n'est pas une identification confirmée ni une preuve d'activité biologique."}


def _annotation_dict(row: LcmsCompoundAnnotation) -> dict:
    return {
        "id": row.id, "feature_id": row.feature_id,
        "phytochem_candidate_id": row.phytochem_candidate_id,
        "spectral_library_id": row.spectral_library_id,
        "reference_standard_injection_id": row.reference_standard_injection_id,
        "annotation_method": row.annotation_method, "annotation_name": row.annotation_name,
        "molecular_formula": row.molecular_formula, "inchikey": row.inchikey,
        "msi_level": row.msi_level, "score_type": row.score_type,
        "score_value": float(row.score_value) if row.score_value is not None else None,
        "status": row.status, "evidence": row.evidence or {},
        "review_rationale": row.review_rationale,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
    }


@router.get("/features/{feature_id}/annotations")
def list_feature_annotations(
    feature_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    feature = db.query(LcmsFeature).filter(LcmsFeature.id == feature_id).first()
    if feature is None:
        raise HTTPException(404, "Feature LC-MS/MS introuvable.")
    run = db.query(LcmsProcessingRun).filter(LcmsProcessingRun.id == feature.processing_run_id).one()
    _owned_study(db, run.study_id, user)
    rows = db.query(LcmsCompoundAnnotation).filter(
        LcmsCompoundAnnotation.feature_id == feature_id
    ).order_by(LcmsCompoundAnnotation.id.asc()).offset(skip).limit(limit).all()
    return {"success": True, "annotations": [_annotation_dict(row) for row in rows]}


@router.post("/annotations/{annotation_id}/review")
def review_compound_annotation(
    annotation_id: int,
    body: AnnotationReview,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    row = db.query(LcmsCompoundAnnotation).filter(
        LcmsCompoundAnnotation.id == annotation_id
    ).first()
    if row is None:
        raise HTTPException(404, "Annotation LC-MS/MS introuvable.")
    if body.status == "accepted" and row.msi_level == 1:
        standard = db.query(LcmsInjection).filter(
            LcmsInjection.id == row.reference_standard_injection_id,
            LcmsInjection.batch_id == db.query(LcmsProcessingRun.batch_id)
                .join(LcmsFeature, LcmsFeature.processing_run_id == LcmsProcessingRun.id)
                .filter(LcmsFeature.id == row.feature_id).scalar_subquery(),
            LcmsInjection.injection_type == "reference_standard",
        ).first()
        if standard is None:
            raise HTTPException(409, "Le standard authentique de niveau MSI 1 n'est plus traçable.")
        evidence = row.evidence or {}
        orthogonal = evidence.get("orthogonal_matches")
        allowed = {"accurate_mass", "retention_time", "ms2_spectrum"}
        evidence_keys = {
            "accurate_mass": "accurate_mass_match",
            "retention_time": "retention_time_match",
            "ms2_spectrum": "ms2_match",
        }
        if (
            evidence.get("authentic_standard") is not True
            or evidence.get("same_method") is not True
            or not isinstance(orthogonal, list)
            or any(not isinstance(name, str) for name in orthogonal)
            or len(set(orthogonal)) < 2
            or not set(orthogonal).issubset(allowed)
            or any(evidence.get(evidence_keys[name]) is not True for name in orthogonal)
        ):
            raise HTTPException(409, "MSI niveau 1 exige un standard authentique et au moins deux propriétés orthogonales concordantes documentées.")
        numeric_pairs = {
            "accurate_mass": ("accurate_mass_abs_error_ppm", "accurate_mass_tolerance_ppm"),
            "retention_time": ("retention_time_abs_delta_seconds", "retention_time_tolerance_seconds"),
        }
        for property_name in orthogonal:
            if property_name in numeric_pairs:
                value_key, tolerance_key = numeric_pairs[property_name]
                value, tolerance = evidence.get(value_key), evidence.get(tolerance_key)
                if (
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(float(value)) or float(value) < 0
                    or isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
                    or not math.isfinite(float(tolerance)) or float(tolerance) <= 0
                    or float(value) > float(tolerance)
                ):
                    raise HTTPException(409, f"Les valeurs de concordance {property_name} sont absentes, non valides ou hors tolérance.")
            elif property_name == "ms2_spectrum":
                score = evidence.get("ms2_match_score")
                score_type = evidence.get("ms2_match_score_type")
                if (
                    isinstance(score, bool) or not isinstance(score, (int, float))
                    or not math.isfinite(float(score))
                    or not isinstance(score_type, str) or not score_type.strip()
                ):
                    raise HTTPException(409, "Le score de concordance MS2 et son type doivent être documentés.")
        run = db.query(LcmsProcessingRun).join(
            LcmsFeature, LcmsFeature.processing_run_id == LcmsProcessingRun.id
        ).filter(LcmsFeature.id == row.feature_id).one()
        standard = db.query(LcmsInjection).filter(
            LcmsInjection.id == row.reference_standard_injection_id,
            LcmsInjection.batch_id == run.batch_id,
            LcmsInjection.injection_type == "reference_standard",
        ).first()
        if standard is None:
            raise HTTPException(409, "Le standard authentique n'est plus traçable dans le même lot.")
        standard_observation = db.query(LcmsFeatureObservation).filter(
            LcmsFeatureObservation.feature_id == row.feature_id,
            LcmsFeatureObservation.injection_id == standard.id,
        ).first()
        if "accurate_mass" in orthogonal and (
            standard_observation is None or standard_observation.observed_mz is None
        ):
            raise HTTPException(409, "La preuve de masse exacte ne référence pas une mesure m/z du standard authentique.")
        if "retention_time" in orthogonal and (
            standard_observation is None or standard_observation.retention_time is None
        ):
            raise HTTPException(409, "La preuve de temps de rétention ne référence pas une mesure du standard authentique.")
        if "ms2_spectrum" in orthogonal and db.query(LcmsMs2Spectrum).filter(
            LcmsMs2Spectrum.feature_id == row.feature_id,
            LcmsMs2Spectrum.injection_id == standard.id,
        ).first() is None:
            raise HTTPException(409, "La preuve MS2 annoncée n'est pas associée au standard authentique.")
    row.status = body.status
    row.review_rationale = body.review_rationale.strip()
    row.reviewed_by = admin.id
    row.reviewed_at = datetime.utcnow()
    evidence_row = db.query(ResearchEvidence).filter(
        ResearchEvidence.object_type == "lcms_compound_annotation",
        ResearchEvidence.object_id == str(row.id),
    ).first()
    if evidence_row is not None:
        evidence_row.metadata_json = {
            **(evidence_row.metadata_json or {}),
            "annotation_status": body.status,
            "curated_by": admin.id,
            "curation_rationale": body.review_rationale.strip(),
            "curated_at": row.reviewed_at.isoformat(),
        }
    db.commit()
    db.refresh(row)
    return {"success": True, "annotation": _annotation_dict(row)}


@router.get("/studies/{study_id}/overview")
def lcmsms_overview(
    study_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_study(db, study_id, user)
    count = lambda model: db.query(model).filter(model.study_id == study_id).count()
    return {
        "success": True,
        "study_id": study_id,
        "counts": {
            "specimens": count(BotanicalSpecimen),
            "plant_samples": count(PlantMaterialSample),
            "extraction_batches": count(ExtractionBatch),
            "fractions": count(PlantFraction),
            "analytical_batches": count(LcmsAnalyticalBatch),
            "injections": count(LcmsInjection),
            "artifacts": count(LcmsDataArtifact),
            "quality_controls": count(LcmsQualityControlObservation),
            "processing_runs": count(LcmsProcessingRun),
        },
        "feature_count": db.query(LcmsFeature)
            .join(LcmsProcessingRun, LcmsFeature.processing_run_id == LcmsProcessingRun.id)
            .filter(LcmsProcessingRun.study_id == study_id).count(),
        "annotation_count": db.query(LcmsCompoundAnnotation)
            .join(LcmsFeature, LcmsCompoundAnnotation.feature_id == LcmsFeature.id)
            .join(LcmsProcessingRun, LcmsFeature.processing_run_id == LcmsProcessingRun.id)
            .filter(LcmsProcessingRun.study_id == study_id).count(),
        "pipeline_scope": "metadata_registration_and_external_result_import_only",
    }
