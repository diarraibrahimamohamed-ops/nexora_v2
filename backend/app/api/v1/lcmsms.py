"""LC-MS/MS experimental lineage API.

This module registers experimental material, analytical acquisitions, external
artifacts, imported results and the integrated OpenMS/matchms processing workflow.
MSI-level annotations remain candidates until the required experimental review.
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

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.evidence import ResearchEvidence
from app.models.lcmsms import (
    BotanicalSpecimen,
    ExtractionBatch,
    LcmsAnalyticalBatch,
    LcmsAlignedFeature,
    LcmsAlignedFeatureMember,
    LcmsBatchValidationReport,
    LcmsCompoundAnnotation,
    LcmsDataArtifact,
    LcmsFeature,
    LcmsFeatureAlignmentRun,
    LcmsFeatureObservation,
    LcmsInjection,
    LcmsMs2Spectrum,
    LcmsNormalizedFeatureQuantification,
    LcmsProcessingRun,
    LcmsQuantificationRun,
    LcmsQualityControlObservation,
    LcmsSpectralLibrary,
    PlantFraction,
    PlantMaterialSample,
)
from app.models.phytochemistry import PhytochemicalCandidate, TaxonResolution
from app.services.taxonomic_resolution import ExternalTaxonomyError, TaxonomyResolutionError, resolve_botanical_taxon, taxon_resolution_to_dict
from app.models.research import ResearchStudy
from app.models.scientific_workflow import ScientificWorkflowEvent, ScientificWorkflowRun
from app.models.user import User
from app.services.auth_service import get_current_user, require_admin
from app.services.scientific_provenance import seal_manifest, sha256_json
from app.services.lcmsms_alignment_engine import (
    LcmsAlignmentEngineError,
    build_alignment_input_manifest,
    build_quantification_input_manifest,
    collect_alignment_inputs,
)
from app.services.lcmsms_alignment_workflow import (
    LCMS_FEATURE_ALIGNMENT_WORKFLOW,
    LCMS_FEATURE_QUANTIFICATION_WORKFLOW,
)
from app.services.lcmsms_validation import (
    LcmsValidationError,
    assess_lcms_batch_quality,
    build_validation_report_payload,
)
from app.services.scientific_workflow_engine import InvalidWorkflow, create_workflow_run
from app.services.scientific_workflow_policy import WorkflowDefinition, WorkflowStepDefinition

router = APIRouter(prefix="/lcmsms", tags=["LC-MS/MS experimental evidence"])

_LCMSMS_WORKFLOW_DEFINITION = WorkflowDefinition(
    key="nexora.lcmsms.untargeted-metabolomics",
    version="1.0.0",
    description=(
        "Semantic mzML validation, centroided MS1 untargeted feature finding, "
        "MS2-to-feature association and curated MSP spectral-library matching."
    ),
    requires_quality_review=True,
    steps=(
        WorkflowStepDefinition(
            key="openms_matchms",
            task_name="lcmsms.process_mzml",
            queue_name="lcmsms",
            max_attempts=2,
        ),
    ),
)

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


class BotanicalTaxonResolveRequest(BaseModel):
    scientific_name: str = Field(..., min_length=2, max_length=255)


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
        if not self.source_reference.strip() or len(self.rationale.strip()) < 10:
            raise ValueError("La source documentaire et la justification de curation doivent être renseignées.")
        if self.status == "verified":
            required = {
                "taxon accepté": self.accepted_taxon,
                "identifiant taxonomique": self.taxon_identifier,
                "source taxonomique WCVP": self.taxonomy_source,
                "méthode d'identification": self.identification_method,
                "identificateur": self.identifier_name,
                "code voucher": self.voucher_code,
                "herbier": self.herbarium_name,
            }
            missing = [label for label, value in required.items() if not value or not value.strip()]
            if missing:
                raise ValueError(
                    "Une identification botanique vérifiée exige : " + ", ".join(missing) + "."
                )
            if "wcvp" not in self.taxonomy_source.casefold():
                raise ValueError("La source taxonomique doit correspondre au référentiel WCVP accepté.")
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


class LcmsMetricAcceptanceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: Optional[float] = Field(None, allow_inf_nan=False)
    max: Optional[float] = Field(None, allow_inf_nan=False)
    max_abs: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    max_rsd_pct: Optional[float] = Field(None, ge=0, le=500, allow_inf_nan=False)
    max_blank_to_pooled_qc_pct: Optional[float] = Field(None, ge=0, le=10000, allow_inf_nan=False)
    max_abs_drift_pct_per_10_injections: Optional[float] = Field(None, ge=0, le=10000, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("La limite minimale doit être inférieure ou égale à la limite maximale.")
        return self


class LcmsBatchValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_pooled_qc_injections: int = Field(3, ge=3, le=500)
    max_pooled_qc_rsd_pct: float = Field(30.0, gt=0, le=500, allow_inf_nan=False)
    metric_limits: dict[str, LcmsMetricAcceptanceLimits] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_metric_names(self):
        for name in self.metric_limits:
            if not name.strip() or len(name) > 120:
                raise ValueError("Les noms de métriques doivent contenir de 1 à 120 caractères.")
        return self


class LcmsFeatureAlignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processing_run_ids: list[int] = Field(..., min_length=3, max_length=200)
    reference_injection_ids: dict[Literal["positive", "negative"], int] = Field(default_factory=dict)
    anchor_mass_tolerance_ppm: float = Field(8.0, gt=0, le=100, allow_inf_nan=False)
    max_initial_rt_delta_seconds: float = Field(120.0, gt=0, le=1800, allow_inf_nan=False)
    max_anchor_residual_seconds: float = Field(20.0, gt=0, le=300, allow_inf_nan=False)
    minimum_anchors: int = Field(8, ge=8, le=500)
    minimum_injections: int = Field(3, ge=3, le=200)
    minimum_anchor_rt_coverage: float = Field(0.5, ge=0.1, le=1.0, allow_inf_nan=False)
    group_mass_tolerance_ppm: float = Field(10.0, gt=0, le=100, allow_inf_nan=False)
    group_rt_tolerance_seconds: float = Field(10.0, gt=0, le=600, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_sources(self):
        if len(set(self.processing_run_ids)) != len(self.processing_run_ids):
            raise ValueError("processing_run_ids ne doit contenir aucun doublon.")
        if any(value < 1 for value in self.processing_run_ids):
            raise ValueError("Chaque processing_run_id doit être positif.")
        if any(value < 1 for value in self.reference_injection_ids.values()):
            raise ValueError("Chaque reference_injection_id doit être positif.")
        return self


class LcmsQcLoessQuantificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_qc_injections: int = Field(8, ge=8, le=500)
    minimum_qc_span_ratio: float = Field(0.6, ge=0.1, le=1.0, allow_inf_nan=False)
    loess_span_fraction: float = Field(0.75, ge=0.3, le=1.0, allow_inf_nan=False)
    robust_iterations: int = Field(2, ge=0, le=5)
    minimum_correction_factor: float = Field(0.2, gt=0, le=1.0, allow_inf_nan=False)
    maximum_correction_factor: float = Field(5.0, gt=1.0, le=20.0, allow_inf_nan=False)
    maximum_qc_rsd_worsening_pct: float = Field(0.0, ge=0, le=20, allow_inf_nan=False)
    no_correction_threshold_pct: float = Field(2.0, ge=0, le=25, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_factor_bounds(self):
        if self.minimum_correction_factor >= self.maximum_correction_factor:
            raise ValueError("minimum_correction_factor doit être inférieur à maximum_correction_factor.")
        return self


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


class LcmsMzmlProcessingRequest(BaseModel):
    mzml_artifact_id: int = Field(..., ge=1)
    spectral_library_id: Optional[int] = Field(None, ge=1)
    mass_error_ppm: float = Field(..., gt=0, le=100, allow_inf_nan=False)
    noise_threshold_int: float = Field(..., gt=0, allow_inf_nan=False)
    precursor_mass_tolerance_ppm: float = Field(..., gt=0, le=100, allow_inf_nan=False)
    retention_time_tolerance_seconds: float = Field(..., gt=0, le=600, allow_inf_nan=False)
    fragment_mz_tolerance_da: float = Field(..., gt=0, le=1, allow_inf_nan=False)
    minimum_cosine_score: float = Field(..., ge=0, le=1, allow_inf_nan=False)
    minimum_matched_peaks: int = Field(3, ge=2, le=100)
    fragment_storage_limit: int = Field(500, ge=20, le=5000)
    max_annotations_per_feature: int = Field(3, ge=1, le=10)


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
    spectrum_type: Literal["centroided", "profile", "unknown"] = "unknown"
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
            if self.annotation_method != "authentic_standard" or self.reference_standard_injection_id is None:
                raise ValueError("MSI niveau 1 exige une annotation par standard authentique traçable.")
            if self.evidence.get("authentic_standard") is not True or self.evidence.get("same_method") is not True:
                raise ValueError("MSI niveau 1 exige un standard authentique analysé par la même méthode.")
            sample_injection_id = self.evidence.get("sample_injection_id")
            if isinstance(sample_injection_id, bool) or not isinstance(sample_injection_id, int) or sample_injection_id < 1:
                raise ValueError("MSI niveau 1 doit référencer l'injection d'échantillon comparée au standard.")
            orthogonal = self.evidence.get("orthogonal_matches")
            if (
                not isinstance(orthogonal, list)
                or any(not isinstance(name, str) for name in orthogonal)
                or len(orthogonal) != 2
                or set(orthogonal) != {"accurate_mass", "retention_time"}
            ):
                raise ValueError("MSI niveau 1 exige actuellement la masse exacte et le temps de rétention, recalculés depuis les mesures d'échantillon et de standard.")
            for key in ("accurate_mass_tolerance_ppm", "retention_time_tolerance_seconds"):
                value = self.evidence.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
                    raise ValueError(f"MSI niveau 1 exige une tolérance positive et finie pour {key}.")
        if self.annotation_method == "library_match" and self.spectral_library_id is None:
            raise ValueError("Une annotation par bibliothèque doit référencer une bibliothèque spectrale.")
        return self


def _validate_msi1_measurements(
    db: Session,
    *,
    feature: LcmsFeature,
    run: LcmsProcessingRun,
    standard: LcmsInjection,
    evidence: dict[str, Any],
    persisted: bool = False,
    error_status_code: int = 422,
) -> dict[str, Any]:
    """Ground MSI-1 mass/RT matches in paired sample and authentic-standard observations."""
    def reject(message: str) -> None:
        raise HTTPException(error_status_code, message)

    if not isinstance(evidence, dict):
        reject("Les preuves MSI niveau 1 doivent être un objet JSON.")

    sample_injection_id = evidence.get("sample_injection_id")
    if isinstance(sample_injection_id, bool) or not isinstance(sample_injection_id, int) or sample_injection_id < 1:
        reject("L'injection d'échantillon comparée au standard doit être explicitement référencée.")

    sample_injection = db.query(LcmsInjection).filter(
        LcmsInjection.id == sample_injection_id,
        LcmsInjection.batch_id == run.batch_id,
        LcmsInjection.injection_type == "sample",
    ).first()
    if sample_injection is None or (
        sample_injection.plant_sample_id is None and sample_injection.fraction_id is None
    ):
        reject("L'injection comparée doit être une injection d'échantillon matériel du même lot analytique.")

    sample_observation = db.query(LcmsFeatureObservation).filter(
        LcmsFeatureObservation.feature_id == feature.id,
        LcmsFeatureObservation.injection_id == sample_injection.id,
    ).first()
    standard_observation = db.query(LcmsFeatureObservation).filter(
        LcmsFeatureObservation.feature_id == feature.id,
        LcmsFeatureObservation.injection_id == standard.id,
    ).first()
    if (
        sample_observation is None or standard_observation is None
        or sample_observation.observed_mz is None or standard_observation.observed_mz is None
        or sample_observation.retention_time is None or standard_observation.retention_time is None
    ):
        reject("MSI niveau 1 exige les mesures m/z et temps de rétention de la feature dans l'échantillon et le standard.")

    standard_mz = float(standard_observation.observed_mz)
    if not math.isfinite(standard_mz) or standard_mz <= 0:
        reject("La mesure m/z du standard authentique est invalide.")
    actual_mass_error_ppm = abs(
        float(sample_observation.observed_mz) - standard_mz
    ) / standard_mz * 1_000_000

    rt_delta = abs(
        float(sample_observation.retention_time) - float(standard_observation.retention_time)
    )
    actual_rt_delta_seconds = rt_delta * (60.0 if feature.retention_time_unit == "minute" else 1.0)

    mass_tolerance = evidence.get("accurate_mass_tolerance_ppm")
    rt_tolerance = evidence.get("retention_time_tolerance_seconds")
    for key, value in (
        ("accurate_mass_tolerance_ppm", mass_tolerance),
        ("retention_time_tolerance_seconds", rt_tolerance),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
            reject(f"La tolérance {key} doit être positive et finie.")

    if actual_mass_error_ppm > float(mass_tolerance):
        reject(
            f"La masse mesurée de l'échantillon diffère du standard de {actual_mass_error_ppm:.6f} ppm, "
            f"au-delà de la tolérance déclarée ({float(mass_tolerance):.6f} ppm)."
        )
    if actual_rt_delta_seconds > float(rt_tolerance):
        reject(
            f"Le temps de rétention mesuré diffère du standard de {actual_rt_delta_seconds:.6f} s, "
            f"au-delà de la tolérance déclarée ({float(rt_tolerance):.6f} s)."
        )

    calculated = {
        "accurate_mass_match": True,
        "accurate_mass_abs_error_ppm": actual_mass_error_ppm,
        "retention_time_match": True,
        "retention_time_abs_delta_seconds": actual_rt_delta_seconds,
    }
    if persisted:
        for key, actual in (
            ("accurate_mass_abs_error_ppm", actual_mass_error_ppm),
            ("retention_time_abs_delta_seconds", actual_rt_delta_seconds),
        ):
            stored = evidence.get(key)
            if (
                isinstance(stored, bool) or not isinstance(stored, (int, float))
                or not math.isfinite(float(stored))
                or not math.isclose(float(stored), actual, rel_tol=1e-9, abs_tol=1e-7)
            ):
                reject(f"La valeur MSI niveau 1 enregistrée pour {key} ne correspond plus aux mesures brutes liées.")
        if any(evidence.get(key) is not True for key in ("accurate_mass_match", "retention_time_match")):
            reject("Les concordances MSI niveau 1 enregistrées ne correspondent pas aux mesures validées.")
        return evidence

    return {**evidence, **calculated}


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


@router.post("/specimens/{specimen_id}/taxonomy/resolve", status_code=201)
def resolve_specimen_taxonomy(specimen_id: int, body: BotanicalTaxonResolveRequest,
                              db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    specimen = db.get(BotanicalSpecimen, specimen_id)
    if specimen is None:
        raise HTTPException(404, "Spécimen botanique introuvable.")
    _owned_study(db, specimen.study_id, user)
    try:
        row = resolve_botanical_taxon(db, body.scientific_name, specimen_id=specimen_id)
        db.commit()
        db.refresh(row)
        return {"success": True, "resolution": taxon_resolution_to_dict(row)}
    except TaxonomyResolutionError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except ExternalTaxonomyError as exc:
        db.rollback()
        raise HTTPException(503, str(exc)) from exc


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
    if row.created_by is not None and row.created_by == admin.id:
        raise HTTPException(
            403,
            "La revue botanique doit être indépendante : le créateur du spécimen ne peut pas le vérifier lui-même.",
        )

    accepted_resolution = None
    if body.status == "verified":
        accepted_resolution = db.query(TaxonResolution).filter(
            TaxonResolution.specimen_id == row.id,
            TaxonResolution.review_status == "accepted",
            TaxonResolution.accepted_name.isnot(None),
            TaxonResolution.accepted_identifier.isnot(None),
        ).order_by(TaxonResolution.created_at.desc(), TaxonResolution.id.desc()).first()
        if accepted_resolution is None:
            raise HTTPException(
                422,
                "Une résolution WCVP acceptée et liée à ce spécimen est obligatoire avant sa vérification physique.",
            )
        normalized_requested = " ".join(body.accepted_taxon.casefold().split())
        normalized_accepted = " ".join(accepted_resolution.accepted_name.casefold().split())
        expected_identifier = "WCVP:" + accepted_resolution.accepted_identifier
        if normalized_requested != normalized_accepted or body.taxon_identifier.strip() != expected_identifier:
            raise HTTPException(
                422,
                "Le nom et l'identifiant du spécimen doivent correspondre exactement à sa résolution WCVP acceptée.",
            )

    references = list(row.evidence_references or [])
    references.append({
        "kind": "botanical_taxonomic_curation",
        "status": body.status,
        "source_reference": body.source_reference.strip(),
        "rationale": body.rationale.strip(),
        "curator_id": admin.id,
        "taxon_resolution_id": accepted_resolution.id if accepted_resolution else None,
        "taxonomy_provider": accepted_resolution.provider if accepted_resolution else (body.taxonomy_source or row.taxonomy_source),
        "reviewed_at": datetime.utcnow().isoformat(),
    })
    row.identification_status = body.status
    row.accepted_taxon = accepted_resolution.accepted_name if accepted_resolution else (body.accepted_taxon.strip() if body.accepted_taxon else row.accepted_taxon)
    row.taxon_identifier = ("WCVP:" + accepted_resolution.accepted_identifier) if accepted_resolution else (body.taxon_identifier.strip() if body.taxon_identifier else row.taxon_identifier)
    row.taxonomy_source = accepted_resolution.provider if accepted_resolution else (body.taxonomy_source.strip() if body.taxonomy_source else row.taxonomy_source)
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




def _batch_validation_report_dict(row: LcmsBatchValidationReport) -> dict[str, Any]:
    return {
        "id": row.id,
        "study_id": row.study_id,
        "batch_id": row.batch_id,
        "protocol_version": row.protocol_version,
        "status": row.validation_status,
        "criteria": row.criteria or {},
        "coverage": row.coverage or {},
        "metrics": row.metrics or [],
        "limitations": row.limitations or [],
        "source_processing_run_ids": row.source_processing_run_ids or [],
        "source_observation_ids": row.source_observation_ids or [],
        "input_sha256": row.input_sha256,
        "report_sha256": row.report_sha256,
        "report_manifest": row.report_manifest or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "created_by": row.created_by,
    }


@router.post("/batches/{batch_id}/validation-reports", status_code=201)
def create_batch_validation_report(
    batch_id: int,
    body: LcmsBatchValidationRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    batch = db.query(LcmsAnalyticalBatch).filter(
        LcmsAnalyticalBatch.id == batch_id
    ).first()
    if batch is None:
        raise HTTPException(404, "Lot analytique introuvable.")
    _owned_study(db, batch.study_id, user)

    completed_runs = db.query(LcmsProcessingRun).filter(
        LcmsProcessingRun.batch_id == batch_id,
        LcmsProcessingRun.study_id == batch.study_id,
        LcmsProcessingRun.status == "completed",
        LcmsProcessingRun.output_sha256.isnot(None),
    ).order_by(LcmsProcessingRun.id.asc()).all()
    if not completed_runs:
        raise HTTPException(
            409,
            "Au moins un traitement LC-MS/MS terminé avec une empreinte de sortie est requis pour valider le lot.",
        )

    observations = db.query(LcmsQualityControlObservation).filter(
        LcmsQualityControlObservation.batch_id == batch_id,
        LcmsQualityControlObservation.study_id == batch.study_id,
    ).order_by(LcmsQualityControlObservation.id.asc()).all()
    injection_rows = db.query(LcmsInjection).filter(
        LcmsInjection.batch_id == batch_id,
        LcmsInjection.study_id == batch.study_id,
    ).all()
    injection_orders = {int(row.id): int(row.injection_order) for row in injection_rows}
    criteria = body.model_dump(exclude_none=True)
    try:
        assessment = assess_lcms_batch_quality(
            observations,
            injection_orders=injection_orders,
            criteria=criteria,
        )
        payload = build_validation_report_payload(
            assessment,
            batch_id=batch_id,
            study_id=batch.study_id,
            processing_runs=completed_runs,
            observations=observations,
            injection_orders=injection_orders,
            created_at=datetime.utcnow(),
        )
    except LcmsValidationError as exc:
        raise HTTPException(422, str(exc)) from exc

    existing = db.query(LcmsBatchValidationReport).filter(
        LcmsBatchValidationReport.batch_id == batch_id,
        LcmsBatchValidationReport.report_sha256 == payload["report_sha256"],
    ).first()
    if existing is not None:
        return {
            "success": True,
            "idempotent_replay": True,
            "validation_report": _batch_validation_report_dict(existing),
        }

    report = LcmsBatchValidationReport(
        study_id=batch.study_id,
        batch_id=batch_id,
        protocol_version=assessment["protocol_version"],
        validation_status=assessment["status"],
        criteria=assessment["criteria"],
        coverage=assessment["coverage"],
        metrics=assessment["metrics"],
        limitations=assessment["limitations"],
        source_processing_run_ids=[int(row.id) for row in completed_runs],
        source_observation_ids=payload["source_quality_control_observation_ids"],
        input_sha256=payload["input_sha256"],
        report_sha256=payload["report_sha256"],
        report_manifest=payload,
        created_by=user.id,
    )
    db.add(report)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(LcmsBatchValidationReport).filter(
            LcmsBatchValidationReport.batch_id == batch_id,
            LcmsBatchValidationReport.report_sha256 == payload["report_sha256"],
        ).first()
        if existing is not None:
            return {
                "success": True,
                "idempotent_replay": True,
                "validation_report": _batch_validation_report_dict(existing),
            }
        raise HTTPException(409, "Conflit lors de la création du rapport de validation LC-MS/MS.")
    db.refresh(report)
    return {
        "success": True,
        "idempotent_replay": False,
        "validation_report": _batch_validation_report_dict(report),
    }


@router.get("/batches/{batch_id}/validation-reports")
def list_batch_validation_reports(
    batch_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    batch = db.query(LcmsAnalyticalBatch).filter(
        LcmsAnalyticalBatch.id == batch_id
    ).first()
    if batch is None:
        raise HTTPException(404, "Lot analytique introuvable.")
    _owned_study(db, batch.study_id, user)
    rows = db.query(LcmsBatchValidationReport).filter(
        LcmsBatchValidationReport.batch_id == batch_id,
        LcmsBatchValidationReport.study_id == batch.study_id,
    ).order_by(LcmsBatchValidationReport.id.desc()).offset(skip).limit(limit).all()
    return {
        "success": True,
        "validation_reports": [_batch_validation_report_dict(row) for row in rows],
    }


@router.get("/validation-reports/{report_id}")
def get_batch_validation_report(
    report_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.query(LcmsBatchValidationReport).filter(
        LcmsBatchValidationReport.id == report_id
    ).first()
    if row is None:
        raise HTTPException(404, "Rapport de validation LC-MS/MS introuvable.")
    _owned_study(db, row.study_id, user)
    return {"success": True, "validation_report": _batch_validation_report_dict(row)}




def _alignment_run_dict(row: LcmsFeatureAlignmentRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "study_id": row.study_id,
        "batch_id": row.batch_id,
        "scientific_workflow_run_id": row.scientific_workflow_run_id,
        "software_name": row.software_name,
        "software_version": row.software_version,
        "algorithm_name": row.algorithm_name,
        "algorithm_version": row.algorithm_version,
        "parameters": row.parameters or {},
        "source_processing_run_ids": row.source_processing_run_ids or [],
        "source_injection_ids": row.source_injection_ids or [],
        "input_sha256": row.input_sha256,
        "output_sha256": row.output_sha256,
        "status": row.status,
        "alignment_manifest": row.alignment_manifest or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def _quantification_run_dict(row: LcmsQuantificationRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "study_id": row.study_id,
        "batch_id": row.batch_id,
        "alignment_run_id": row.alignment_run_id,
        "scientific_workflow_run_id": row.scientific_workflow_run_id,
        "method": row.method,
        "method_version": row.method_version,
        "parameters": row.parameters or {},
        "source_injection_ids": row.source_injection_ids or [],
        "input_sha256": row.input_sha256,
        "output_sha256": row.output_sha256,
        "status": row.status,
        "quantification_manifest": row.quantification_manifest or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


@router.post("/batches/{batch_id}/feature-alignments", status_code=202)
def start_batch_feature_alignment(
    batch_id: int,
    body: LcmsFeatureAlignmentRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    batch = db.query(LcmsAnalyticalBatch).filter(
        LcmsAnalyticalBatch.id == batch_id
    ).first()
    if batch is None:
        raise HTTPException(404, "Lot analytique introuvable.")
    _owned_study(db, batch.study_id, user)
    study_id = int(batch.study_id)
    processing_run_ids = sorted(body.processing_run_ids)
    try:
        features, injections, snapshots, injection_ids = collect_alignment_inputs(
            db,
            study_id=study_id,
            batch_id=batch_id,
            processing_run_ids=processing_run_ids,
        )
        if len(injection_ids) < body.minimum_injections:
            raise LcmsAlignmentEngineError(
                "ALIGNMENT_INSUFFICIENT_INJECTIONS",
                f"{body.minimum_injections} injections distinctes sont requises.",
            )
        reference_ids = dict(body.reference_injection_ids)
        injection_by_id = {item.injection_id: item for item in injections}
        for polarity, reference_id in reference_ids.items():
            if reference_id not in injection_by_id:
                raise LcmsAlignmentEngineError(
                    "ALIGNMENT_REFERENCE_INVALID",
                    f"L'injection de référence {reference_id} n'appartient pas à la sélection.",
                )
            if injection_by_id[reference_id].injection_type != "pooled_qc":
                raise LcmsAlignmentEngineError(
                    "ALIGNMENT_REFERENCE_NOT_POOLED_QC",
                    f"La référence {polarity} doit être une injection pooled_qc.",
                )
        processing_runs = db.query(LcmsProcessingRun).filter(
            LcmsProcessingRun.id.in_(processing_run_ids),
            LcmsProcessingRun.study_id == study_id,
            LcmsProcessingRun.batch_id == batch_id,
        ).order_by(LcmsProcessingRun.id.asc()).all()
        parameters = body.model_dump(exclude={"reference_injection_ids"})
        input_manifest = build_alignment_input_manifest(
            study_id=study_id,
            batch_id=batch_id,
            processing_runs=processing_runs,
            injections=injections,
            feature_snapshots=snapshots,
            parameters=parameters,
            reference_injection_ids=reference_ids,
        )
        input_sha = sha256_json(input_manifest)
    except LcmsAlignmentEngineError as exc:
        raise HTTPException(422, str(exc)) from exc

    existing = db.query(LcmsFeatureAlignmentRun).filter(
        LcmsFeatureAlignmentRun.batch_id == batch_id,
        LcmsFeatureAlignmentRun.input_sha256 == input_sha,
    ).first()
    if existing is not None:
        return {
            "success": True,
            "idempotent_replay": True,
            "alignment_run": _alignment_run_dict(existing),
            "workflow_run_id": existing.scientific_workflow_run_id,
        }

    run = LcmsFeatureAlignmentRun(
        study_id=study_id,
        batch_id=batch_id,
        software_name="Nexora deterministic alignment",
        software_version="1.0.0",
        algorithm_name="mutual_unique_mz_anchors_piecewise_linear",
        algorithm_version="1.0.0",
        parameters={**parameters, "reference_injection_ids": reference_ids},
        source_processing_run_ids=processing_run_ids,
        source_injection_ids=sorted(injection_ids),
        input_sha256=input_sha,
        status="registered",
        alignment_manifest={
            "input_manifest": input_manifest,
            "status": "registered",
        },
        created_by=user.id,
    )
    db.add(run)
    try:
        db.flush()
        workflow_run, replay = create_workflow_run(
            db,
            LCMS_FEATURE_ALIGNMENT_WORKFLOW,
            owner_user_id=user.id,
            scope_type="lcms_feature_alignment_run",
            scope_id=run.id,
            context={
                "study_id": study_id,
                "batch_id": batch_id,
                "alignment_run_id": run.id,
            },
            input_manifest=input_manifest,
            step_inputs={
                "align_features": {
                    "args": [run.id],
                    "kwargs": {},
                }
            },
            idempotency_key="lcmsms-alignment:" + input_sha,
        )
        if replay:
            db.rollback()
            raise HTTPException(
                409,
                "Un workflow d'alignement existe déjà pour cette empreinte, mais son run associé est absent.",
            )
        run.scientific_workflow_run_id = workflow_run.id
        run.alignment_manifest = {
            **(run.alignment_manifest or {}),
            "scientific_workflow_run_id": workflow_run.id,
            "workflow_definition_version": workflow_run.definition_version,
        }
        db.commit()
        db.refresh(run)
        return {
            "success": True,
            "idempotent_replay": False,
            "alignment_run": _alignment_run_dict(run),
            "workflow_run_id": workflow_run.id,
            "workflow_state": workflow_run.state,
            "message": "L'alignement est planifié sur la file LC-MS/MS. Une revue qualité sera requise après exécution.",
        }
    except InvalidWorkflow as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        existing = db.query(LcmsFeatureAlignmentRun).filter(
            LcmsFeatureAlignmentRun.batch_id == batch_id,
            LcmsFeatureAlignmentRun.input_sha256 == input_sha,
        ).first()
        if existing is not None:
            return {
                "success": True,
                "idempotent_replay": True,
                "alignment_run": _alignment_run_dict(existing),
                "workflow_run_id": existing.scientific_workflow_run_id,
            }
        raise HTTPException(409, "Conflit lors de la création de l'alignement LC-MS/MS.") from exc


@router.get("/batches/{batch_id}/feature-alignments")
def list_batch_feature_alignments(
    batch_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    batch = db.query(LcmsAnalyticalBatch).filter(LcmsAnalyticalBatch.id == batch_id).first()
    if batch is None:
        raise HTTPException(404, "Lot analytique introuvable.")
    _owned_study(db, batch.study_id, user)
    rows = db.query(LcmsFeatureAlignmentRun).filter(
        LcmsFeatureAlignmentRun.batch_id == batch_id,
        LcmsFeatureAlignmentRun.study_id == batch.study_id,
    ).order_by(LcmsFeatureAlignmentRun.id.desc()).offset(skip).limit(limit).all()
    return {"success": True, "alignment_runs": [_alignment_run_dict(row) for row in rows]}


@router.get("/feature-alignments/{alignment_run_id}")
def get_feature_alignment_run(
    alignment_run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.query(LcmsFeatureAlignmentRun).filter(
        LcmsFeatureAlignmentRun.id == alignment_run_id
    ).first()
    if row is None:
        raise HTTPException(404, "Exécution d'alignement introuvable.")
    _owned_study(db, row.study_id, user)
    return {"success": True, "alignment_run": _alignment_run_dict(row)}


@router.get("/feature-alignments/{alignment_run_id}/features")
def list_aligned_features(
    alignment_run_id: int,
    polarity: Optional[Literal["positive", "negative"]] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = db.query(LcmsFeatureAlignmentRun).filter(
        LcmsFeatureAlignmentRun.id == alignment_run_id
    ).first()
    if run is None:
        raise HTTPException(404, "Exécution d'alignement introuvable.")
    _owned_study(db, run.study_id, user)
    query = db.query(LcmsAlignedFeature).filter(
        LcmsAlignedFeature.alignment_run_id == alignment_run_id
    )
    if polarity is not None:
        query = query.filter(LcmsAlignedFeature.polarity == polarity)
    rows = query.order_by(
        LcmsAlignedFeature.polarity.asc(),
        LcmsAlignedFeature.consensus_mz.asc(),
        LcmsAlignedFeature.aligned_retention_time.asc(),
    ).offset(skip).limit(limit).all()
    feature_ids = [int(item.id) for item in rows]
    members = db.query(LcmsAlignedFeatureMember).filter(
        LcmsAlignedFeatureMember.aligned_feature_id.in_(feature_ids)
    ).order_by(
        LcmsAlignedFeatureMember.aligned_feature_id.asc(),
        LcmsAlignedFeatureMember.injection_id.asc(),
    ).all() if feature_ids else []
    by_feature: dict[int, list[dict[str, Any]]] = {}
    for item in members:
        by_feature.setdefault(int(item.aligned_feature_id), []).append({
            "processing_run_id": item.processing_run_id,
            "injection_id": item.injection_id,
            "feature_id": item.feature_id,
            "source_observation_id": item.source_observation_id,
            "original_mz": float(item.original_mz),
            "original_retention_time": float(item.original_retention_time),
            "aligned_retention_time": float(item.aligned_retention_time),
            "delta_retention_time_seconds": float(item.delta_retention_time_seconds),
            "delta_mz_ppm": float(item.delta_mz_ppm),
            "raw_intensity": float(item.raw_intensity) if item.raw_intensity is not None else None,
            "metadata": item.metadata_json or {},
        })
    return {
        "success": True,
        "alignment_run_id": alignment_run_id,
        "features": [{
            "id": item.id,
            "polarity": item.polarity,
            "consensus_mz": float(item.consensus_mz),
            "aligned_retention_time": float(item.aligned_retention_time),
            "retention_time_unit": item.retention_time_unit,
            "member_count": item.member_count,
            "injection_count": item.injection_count,
            "alignment_status": item.alignment_status,
            "quality_metrics": item.quality_metrics or {},
            "members": by_feature.get(int(item.id), []),
        } for item in rows],
    }


@router.post("/feature-alignments/{alignment_run_id}/quantifications", status_code=202)
def start_lcms_quantification(
    alignment_run_id: int,
    body: LcmsQcLoessQuantificationRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    alignment = db.query(LcmsFeatureAlignmentRun).filter(
        LcmsFeatureAlignmentRun.id == alignment_run_id
    ).first()
    if alignment is None:
        raise HTTPException(404, "Exécution d'alignement introuvable.")
    _owned_study(db, alignment.study_id, user)
    if alignment.status != "completed" or not alignment.output_sha256:
        raise HTTPException(409, "La quantification exige un alignement terminé et scellé.")

    alignment_workflow = db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == alignment.scientific_workflow_run_id,
        ScientificWorkflowRun.workflow_key == "nexora.lcmsms.feature-alignment",
        ScientificWorkflowRun.scope_type == "lcms_feature_alignment_run",
        ScientificWorkflowRun.scope_id == str(alignment.id),
        ScientificWorkflowRun.requires_quality_review.is_(True),
        ScientificWorkflowRun.state == "succeeded",
    ).first()
    approved_review = None
    if alignment_workflow is not None:
        approved_review = db.query(ScientificWorkflowEvent).filter(
            ScientificWorkflowEvent.workflow_run_id == alignment_workflow.id,
            ScientificWorkflowEvent.action == "approve",
            ScientificWorkflowEvent.to_state == "quality_review.accepted",
            ScientificWorkflowEvent.actor_user_id.isnot(None),
        ).first()
    if alignment_workflow is None or approved_review is None:
        raise HTTPException(
            409,
            "La quantification QC-RLSC exige un alignement terminé et approuvé lors de la revue qualité.",
        )

    injection_ids = [int(value) for value in (alignment.source_injection_ids or [])]
    injections = db.query(LcmsInjection).filter(
        LcmsInjection.id.in_(injection_ids),
        LcmsInjection.study_id == alignment.study_id,
        LcmsInjection.batch_id == alignment.batch_id,
    ).order_by(LcmsInjection.injection_order.asc()).all()
    if len(injections) != len(injection_ids):
        raise HTTPException(409, "Les injections source de l'alignement ne sont plus toutes disponibles.")
    members = db.query(LcmsAlignedFeatureMember).filter(
        LcmsAlignedFeatureMember.alignment_run_id == alignment_run_id
    ).order_by(
        LcmsAlignedFeatureMember.aligned_feature_id.asc(),
        LcmsAlignedFeatureMember.injection_id.asc(),
    ).all()
    parameters = body.model_dump()
    input_manifest = build_quantification_input_manifest(
        alignment_run=alignment,
        injections=injections,
        members=members,
        parameters=parameters,
    )
    input_sha = sha256_json(input_manifest)
    existing = db.query(LcmsQuantificationRun).filter(
        LcmsQuantificationRun.alignment_run_id == alignment_run_id,
        LcmsQuantificationRun.input_sha256 == input_sha,
    ).first()
    if existing is not None:
        return {
            "success": True,
            "idempotent_replay": True,
            "quantification_run": _quantification_run_dict(existing),
            "workflow_run_id": existing.scientific_workflow_run_id,
        }

    run = LcmsQuantificationRun(
        study_id=alignment.study_id,
        batch_id=alignment.batch_id,
        alignment_run_id=alignment_run_id,
        method="qc_rlsc_loess",
        method_version="1.0.0",
        parameters=parameters,
        source_injection_ids=injection_ids,
        input_sha256=input_sha,
        output_sha256=None,
        status="registered",
        quantification_manifest={
            "input_manifest": input_manifest,
            "status": "registered",
        },
        created_by=user.id,
    )
    db.add(run)
    try:
        db.flush()
        workflow_run, replay = create_workflow_run(
            db,
            LCMS_FEATURE_QUANTIFICATION_WORKFLOW,
            owner_user_id=user.id,
            scope_type="lcms_quantification_run",
            scope_id=run.id,
            context={
                "study_id": alignment.study_id,
                "batch_id": alignment.batch_id,
                "alignment_run_id": alignment_run_id,
                "quantification_run_id": run.id,
            },
            input_manifest=input_manifest,
            step_inputs={
                "quantify_features": {
                    "args": [run.id],
                    "kwargs": {},
                }
            },
            idempotency_key="lcmsms-quantification:" + input_sha,
        )
        if replay:
            db.rollback()
            raise HTTPException(
                409,
                "Un workflow de quantification existe déjà pour cette empreinte, mais son run associé est absent.",
            )
        run.scientific_workflow_run_id = workflow_run.id
        run.quantification_manifest = {
            **(run.quantification_manifest or {}),
            "scientific_workflow_run_id": workflow_run.id,
            "workflow_definition_version": workflow_run.definition_version,
        }
        db.commit()
        db.refresh(run)
        return {
            "success": True,
            "idempotent_replay": False,
            "quantification_run": _quantification_run_dict(run),
            "workflow_run_id": workflow_run.id,
            "workflow_state": workflow_run.state,
            "message": "La correction QC-RLSC est planifiée séparément ; les valeurs brutes ne seront pas modifiées.",
        }
    except InvalidWorkflow as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        existing = db.query(LcmsQuantificationRun).filter(
            LcmsQuantificationRun.alignment_run_id == alignment_run_id,
            LcmsQuantificationRun.input_sha256 == input_sha,
        ).first()
        if existing is not None:
            return {
                "success": True,
                "idempotent_replay": True,
                "quantification_run": _quantification_run_dict(existing),
                "workflow_run_id": existing.scientific_workflow_run_id,
            }
        raise HTTPException(409, "Conflit lors de la création de la quantification LC-MS/MS.") from exc


@router.get("/quantification-runs/{quantification_run_id}")
def get_lcms_quantification_run(
    quantification_run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.query(LcmsQuantificationRun).filter(
        LcmsQuantificationRun.id == quantification_run_id
    ).first()
    if row is None:
        raise HTTPException(404, "Exécution de quantification introuvable.")
    _owned_study(db, row.study_id, user)
    return {"success": True, "quantification_run": _quantification_run_dict(row)}


@router.get("/quantification-runs/{quantification_run_id}/matrix")
def get_lcms_quantification_matrix(
    quantification_run_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = db.query(LcmsQuantificationRun).filter(
        LcmsQuantificationRun.id == quantification_run_id
    ).first()
    if run is None:
        raise HTTPException(404, "Exécution de quantification introuvable.")
    _owned_study(db, run.study_id, user)
    rows = db.query(LcmsNormalizedFeatureQuantification).filter(
        LcmsNormalizedFeatureQuantification.quantification_run_id == quantification_run_id
    ).order_by(
        LcmsNormalizedFeatureQuantification.aligned_feature_id.asc(),
        LcmsNormalizedFeatureQuantification.injection_id.asc(),
    ).offset(skip).limit(limit).all()
    return {
        "success": True,
        "quantification_run_id": quantification_run_id,
        "matrix": [{
            "id": item.id,
            "aligned_feature_id": item.aligned_feature_id,
            "injection_id": item.injection_id,
            "processing_run_id": item.processing_run_id,
            "feature_id": item.feature_id,
            "qc_type": item.qc_type,
            "raw_intensity": float(item.raw_intensity) if item.raw_intensity is not None else None,
            "corrected_intensity": float(item.corrected_intensity) if item.corrected_intensity is not None else None,
            "correction_factor": float(item.correction_factor) if item.correction_factor is not None else None,
            "normalization_status": item.normalization_status,
            "qc_rsd_before_pct": float(item.qc_rsd_before_pct) if item.qc_rsd_before_pct is not None else None,
            "qc_rsd_after_pct": float(item.qc_rsd_after_pct) if item.qc_rsd_after_pct is not None else None,
            "quality_metrics": item.quality_metrics or {},
        } for item in rows],
    }


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
        "reference_set_size_bytes": row.reference_set_size_bytes,
        "reference_set_original_filename": row.reference_set_original_filename,
        "curation_status": row.curation_status,
        "curation_rationale": row.curation_rationale,
        "curated_by": row.curated_by,
        "curated_at": row.curated_at.isoformat() if row.curated_at else None,
    }


@router.post("/spectral-libraries/{library_id}/upload-msp", status_code=201)
def upload_spectral_library_msp(
    library_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    row = db.query(LcmsSpectralLibrary).filter(
        LcmsSpectralLibrary.id == library_id
    ).first()
    if row is None:
        raise HTTPException(404, "Bibliothèque spectrale introuvable.")
    raw_filename = (file.filename or "").replace("\\", "/")
    filename = Path(raw_filename).name
    if (
        not filename or len(filename) > 255 or Path(filename).suffix.lower() != ".msp"
        or any(ord(char) < 32 for char in filename)
    ):
        raise HTTPException(415, "Le fichier de référence doit être un fichier .msp.")
    root = Path(settings.LCMS_STORAGE_DIR).resolve()
    library_dir = (root / "libraries").resolve()
    if root not in library_dir.parents:
        raise HTTPException(400, "Chemin de bibliothèque invalide.")
    temporary_path = None
    try:
        library_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(prefix="msp-upload-", suffix=".part", dir=str(library_dir))
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(fd, "wb") as destination:
            while True:
                block = file.file.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > settings.LCMS_MAX_LIBRARY_BYTES:
                    raise HTTPException(413, "La bibliothèque MSP dépasse la limite configurée.")
                digest.update(block)
                destination.write(block)
            destination.flush()
            os.fsync(destination.fileno())
        if size == 0:
            raise HTTPException(400, "La bibliothèque MSP est vide.")
        sha = digest.hexdigest()
        if row.reference_set_sha256 and row.reference_set_sha256.lower() != sha:
            raise HTTPException(409, "Le SHA-256 téléversé ne correspond pas à l'empreinte déclarée lors de l'enregistrement.")
        relative_path = Path("libraries") / f"{sha}.msp"
        target = (root / relative_path).resolve()
        if root not in target.parents:
            raise HTTPException(400, "Chemin de bibliothèque invalide.")
        if target.exists():
            existing_sha, existing_size = _sha256_path(target), target.stat().st_size
            if existing_sha != sha or existing_size != size:
                raise HTTPException(409, "Collision d'empreinte dans le stockage des bibliothèques.")
            os.unlink(temporary_path)
            temporary_path = None
        else:
            os.replace(temporary_path, target)
            temporary_path = None

        row.reference_set_sha256 = sha
        row.reference_set_storage_path = relative_path.as_posix()
        row.reference_set_size_bytes = size
        row.reference_set_original_filename = filename
        row.data_format = "msp"
        # Uploading a new reference set invalidates any prior curation decision.
        row.curation_status = "registered"
        row.curation_rationale = None
        row.curated_by = None
        row.curated_at = None
        db.commit()
        db.refresh(row)
        return {
            "success": True,
            "spectral_library": _library_dict(row),
            "notice": "Empreinte SHA-256 calculée et fichier MSP archivé. Le contenu doit encore être revu par un curateur.",
        }
    except HTTPException:
        db.rollback()
        raise
    except OSError as exc:
        db.rollback()
        raise HTTPException(500, "Échec du stockage de la bibliothèque MSP.") from exc
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
        file.file.close()


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
    if body.status == "reviewed" and (
        not row.reference_set_sha256 or not row.reference_set_storage_path
        or not row.reference_set_size_bytes or row.data_format != "msp"
    ):
        raise HTTPException(422, "Une bibliothèque ne peut être revue sans fichier MSP archivé, taille et SHA-256.")
    if body.status == "reviewed":
        root = Path(settings.LCMS_STORAGE_DIR).resolve()
        relative = Path(row.reference_set_storage_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise HTTPException(409, "Le chemin du fichier MSP est invalide.")
        stored = (root / relative).resolve()
        if root not in stored.parents or not stored.is_file():
            raise HTTPException(409, "Le fichier MSP de référence est absent du stockage externe.")
        actual_sha = _sha256_path(stored)
        if actual_sha != row.reference_set_sha256 or stored.stat().st_size != row.reference_set_size_bytes:
            raise HTTPException(409, "Le fichier MSP de référence ne correspond plus à son empreinte curée.")
    if body.status == "reviewed" and not (row.citation or row.source_url):
        raise HTTPException(422, "Une bibliothèque revue doit conserver une citation ou une source publique.")
    row.curation_status = body.status
    row.curation_rationale = body.rationale.strip()
    row.curated_by = admin.id
    row.curated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"success": True, "spectral_library": _library_dict(row)}


@router.post("/studies/{study_id}/batches/{batch_id}/process-mzml", status_code=202)
def start_mzml_processing_workflow(
    study_id: int,
    batch_id: int,
    body: LcmsMzmlProcessingRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    batch = _owned_row(db, LcmsAnalyticalBatch, batch_id, study_id, user)
    internal_key = None
    if idempotency_key is not None:
        raw_key = idempotency_key.strip()
        if not raw_key or len(raw_key) > 128:
            raise HTTPException(422, "Idempotency-Key doit contenir 1 à 128 caractères.")
        internal_key = "lcmsms:" + hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    artifact = db.query(LcmsDataArtifact).filter(
        LcmsDataArtifact.id == body.mzml_artifact_id,
        LcmsDataArtifact.study_id == study_id,
        LcmsDataArtifact.injection_id.in_(
            db.query(LcmsInjection.id).filter(
                LcmsInjection.batch_id == batch.id,
                LcmsInjection.study_id == study_id,
            )
        ),
    ).first()
    if artifact is None:
        raise HTTPException(404, "Artefact mzML introuvable dans ce lot analytique.")
    if artifact.data_format != "mzml" or Path(artifact.original_filename).suffix.lower() != ".mzml":
        raise HTTPException(415, "Le moteur intégré accepte uniquement des artefacts mzML.")
    if artifact.artifact_role not in {"raw_data", "converted_data"}:
        raise HTTPException(422, "Un fichier mzML doit être enregistré comme raw_data ou converted_data.")
    injection = db.query(LcmsInjection).filter(
        LcmsInjection.id == artifact.injection_id,
        LcmsInjection.batch_id == batch.id,
        LcmsInjection.study_id == study_id,
    ).first()
    if injection is None:
        raise HTTPException(422, "L'injection du fichier mzML ne correspond pas au lot analytique.")

    root = Path(settings.LCMS_STORAGE_DIR).resolve()
    artifact_relative = Path(artifact.storage_path)
    if artifact_relative.is_absolute() or ".." in artifact_relative.parts:
        raise HTTPException(409, "Chemin de stockage mzML invalide.")
    artifact_path = (root / artifact_relative).resolve()
    if root not in artifact_path.parents or not artifact_path.is_file():
        raise HTTPException(404, "Le fichier mzML n'existe plus dans le stockage externe.")
    if artifact.size_bytes > settings.LCMS_MAX_PROCESSING_BYTES:
        raise HTTPException(
            413,
            f"Le mzML dépasse la limite de calcul ({settings.LCMS_MAX_PROCESSING_BYTES} octets).",
        )

    library = None
    library_path = None
    if body.spectral_library_id is not None:
        library = db.query(LcmsSpectralLibrary).filter(
            LcmsSpectralLibrary.id == body.spectral_library_id
        ).first()
        if (
            library is None or library.curation_status != "reviewed"
            or library.data_format != "msp"
            or not library.reference_set_storage_path
            or not library.reference_set_sha256
            or not library.reference_set_size_bytes
        ):
            raise HTTPException(409, "La bibliothèque doit être une MSP archivée et revue par un curateur.")
        library_relative = Path(library.reference_set_storage_path)
        if library_relative.is_absolute() or ".." in library_relative.parts:
            raise HTTPException(409, "Chemin de bibliothèque MSP invalide.")
        library_path = (root / library_relative).resolve()
        if root not in library_path.parents or not library_path.is_file():
            raise HTTPException(409, "Le fichier MSP curé est absent du stockage externe.")
        if library.reference_set_size_bytes > settings.LCMS_MAX_LIBRARY_BYTES:
            raise HTTPException(413, "La bibliothèque MSP dépasse la limite de traitement configurée.")
    if body.minimum_matched_peaks < 2:
        raise HTTPException(422, "Au moins deux fragments concordants sont exigés pour une annotation MS/MS.")

    parameters = body.model_dump()
    parameters["mzml_artifact_id"] = artifact.id
    parameters["mzml_injection_id"] = injection.id
    input_payload = {
        "study_id": study_id,
        "batch_id": batch.id,
        "injection_id": injection.id,
        "mzml_artifact": {
            "id": artifact.id,
            "sha256": artifact.sha256,
            "size_bytes": artifact.size_bytes,
            "original_filename": artifact.original_filename,
            "data_format": artifact.data_format,
        },
        "spectral_library": {
            "id": library.id,
            "name": library.name,
            "version": library.version,
            "sha256": library.reference_set_sha256,
            "size_bytes": library.reference_set_size_bytes,
            "license": library.source_license,
            "citation": library.citation,
            "source_url": library.source_url,
        } if library else None,
        "parameters": body.model_dump(),
        "engine": {
            "openms_algorithm": "FeatureFindingMetabo",
            "pyopenms_version": "3.6.0",
            "spectral_similarity": "CosineGreedy" if library else None,
            "matchms_version": "0.33.1" if library else None,
        },
    }
    input_sha = sha256_json(input_payload)
    expected_workflow_manifest = seal_manifest(
        input_payload, manifest_type="scientific_workflow_input"
    )

    def _return_idempotent_replay(existing_workflow: ScientificWorkflowRun):
        stored_manifest = existing_workflow.input_manifest or {}
        if stored_manifest.get("manifest_sha256") != expected_workflow_manifest["manifest_sha256"]:
            raise HTTPException(
                409,
                "Cette Idempotency-Key a déjà été utilisée avec un autre fichier, une autre bibliothèque ou d'autres paramètres.",
            )
        existing_processing = db.query(LcmsProcessingRun).filter(
            LcmsProcessingRun.scientific_workflow_run_id == existing_workflow.id
        ).first()
        if (
            existing_processing is None
            or existing_processing.study_id != study_id
            or existing_processing.batch_id != batch.id
        ):
            raise HTTPException(409, "Le workflow idempotent ne possède pas de run LC-MS/MS cohérent.")
        return {
            "success": True,
            "idempotent_replay": True,
            "processing_run": _processing_dict(existing_processing),
            "workflow_run_id": existing_workflow.id,
            "workflow_state": existing_workflow.state,
        }

    if internal_key is not None:
        existing_workflow = db.query(ScientificWorkflowRun).filter(
            ScientificWorkflowRun.owner_user_id == user.id,
            ScientificWorkflowRun.idempotency_key == internal_key,
        ).with_for_update().first()
        if existing_workflow is not None:
            return _return_idempotent_replay(existing_workflow)

    input_manifest = seal_manifest(
        {
            **input_payload,
            "input_sha256": input_sha,
            "processing_scope": "single_injection_untargeted_ms1_plus_ms2",
            "alignment_status": "not_performed",
        },
        manifest_type="lcms_openms_matchms_input",
    )
    processing_run = LcmsProcessingRun(
        study_id=study_id,
        batch_id=batch.id,
        spectral_library_id=library.id if library else None,
        software_name="OpenMS.FeatureFindingMetabo + matchms.CosineGreedy",
        software_version="pyOpenMS 3.6.0 / matchms 0.33.1",
        workflow_name=_LCMSMS_WORKFLOW_DEFINITION.key,
        workflow_version=_LCMSMS_WORKFLOW_DEFINITION.version,
        parameters=parameters,
        input_sha256=input_sha,
        output_sha256=None,
        status="registered",
        provenance_manifest={"input_manifest": input_manifest, "output_manifest": None},
        created_by=user.id,
    )
    try:
        db.add(processing_run)
        db.flush()
        workflow_run, replay = create_workflow_run(
            db,
            _LCMSMS_WORKFLOW_DEFINITION,
            owner_user_id=user.id,
            scope_type="lcms_processing_run",
            scope_id=processing_run.id,
            context={
                "study_id": study_id,
                "batch_id": batch.id,
                "processing_run_id": processing_run.id,
            },
            input_manifest=input_payload,
            step_inputs={
                "openms_matchms": {
                    "args": [processing_run.id],
                    "kwargs": {},
                }
            },
            idempotency_key=internal_key,
        )
        if replay:
            if (workflow_run.input_manifest or {}).get("manifest_sha256") != expected_workflow_manifest["manifest_sha256"]:
                db.rollback()
                raise HTTPException(
                    409,
                    "Cette Idempotency-Key a déjà été utilisée avec une autre requête scientifique.",
                )
            db.delete(processing_run)
            db.flush()
            existing_processing = db.query(LcmsProcessingRun).filter(
                LcmsProcessingRun.scientific_workflow_run_id == workflow_run.id
            ).first()
            db.commit()
            if existing_processing is None:
                raise HTTPException(409, "Le workflow idempotent existe sans son run analytique associé.")
            return {
                "success": True,
                "idempotent_replay": True,
                "processing_run": _processing_dict(existing_processing),
                "workflow_run_id": workflow_run.id,
                "workflow_state": workflow_run.state,
            }
        processing_run.scientific_workflow_run_id = workflow_run.id
        processing_run.provenance_manifest = {
            **(processing_run.provenance_manifest or {}),
            "scientific_workflow_run_id": workflow_run.id,
            "workflow_definition_version": workflow_run.definition_version,
        }
        db.commit()
        db.refresh(processing_run)
        db.refresh(workflow_run)
        return {
            "success": True,
            "idempotent_replay": False,
            "processing_run": _processing_dict(processing_run),
            "workflow_run_id": workflow_run.id,
            "workflow_state": workflow_run.state,
            "message": "Le traitement mzML est en file d'attente sur le worker LC-MS/MS dédié. Une revue qualité sera requise après exécution.",
        }
    except InvalidWorkflow as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        if internal_key is not None:
            existing_workflow = db.query(ScientificWorkflowRun).filter(
                ScientificWorkflowRun.owner_user_id == user.id,
                ScientificWorkflowRun.idempotency_key == internal_key,
            ).first()
            if existing_workflow is not None:
                return _return_idempotent_replay(existing_workflow)
        raise HTTPException(409, "Conflit d'idempotence lors de la création du traitement LC-MS/MS.") from exc
    except Exception:
        db.rollback()
        raise


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
        "spectral_library_id": row.spectral_library_id,
        "scientific_workflow_run_id": row.scientific_workflow_run_id,
        "software_name": row.software_name,
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


@router.get("/processing-runs/{processing_run_id}/ms2-spectra")
def list_processing_ms2_spectra(
    processing_run_id: int,
    association_status: Optional[Literal["matched", "ambiguous", "unmatched"]] = None,
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
    injection_ids = db.query(LcmsInjection.id).filter(
        LcmsInjection.batch_id == run.batch_id,
        LcmsInjection.study_id == run.study_id,
    )
    query = db.query(LcmsMs2Spectrum).filter(
        LcmsMs2Spectrum.injection_id.in_(injection_ids)
    )
    if association_status is not None:
        query = query.filter(LcmsMs2Spectrum.association_status == association_status)
    rows = query.order_by(LcmsMs2Spectrum.id.asc()).offset(skip).limit(limit).all()
    return {"success": True, "ms2_spectra": [{
        "id": row.id, "feature_id": row.feature_id, "injection_id": row.injection_id,
        "artifact_id": row.artifact_id, "scan_identifier": row.scan_identifier,
        "association_status": row.association_status,
        "association_candidates": row.association_candidates or [],
        "spectrum_type": row.spectrum_type,
        "precursor_mz": float(row.precursor_mz),
        "isolation_window": float(row.isolation_window) if row.isolation_window is not None else None,
        "collision_energy": float(row.collision_energy) if row.collision_energy is not None else None,
        "collision_energy_unit": row.collision_energy_unit,
        "polarity": row.polarity, "charge": row.charge,
        "fragments": row.fragments or [],
    } for row in rows]}


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
        scan_identifier=body.scan_identifier, association_status="matched",
        association_candidates=[], precursor_mz=body.precursor_mz,
        isolation_window=body.isolation_window, collision_energy=body.collision_energy,
        collision_energy_unit=body.collision_energy_unit, polarity=body.polarity,
        spectrum_type=body.spectrum_type,
        charge=body.charge, fragments=_json_object(body.fragments, "fragments"),
        acquired_at=body.acquired_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "ms2_spectrum": {
        "id": row.id, "feature_id": row.feature_id, "injection_id": row.injection_id,
        "artifact_id": row.artifact_id, "scan_identifier": row.scan_identifier,
        "association_status": row.association_status,
        "association_candidates": row.association_candidates or [],
        "spectrum_type": row.spectrum_type,
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
        "association_status": row.association_status,
        "association_candidates": row.association_candidates or [],
        "spectrum_type": row.spectrum_type,
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
    evidence_payload = _json_object(body.evidence, "evidence")
    if body.msi_level == 1:
        evidence_payload = _validate_msi1_measurements(
            db, feature=feature, run=run, standard=standard,
            evidence=evidence_payload, persisted=False, error_status_code=422,
        )
    row = LcmsCompoundAnnotation(
        feature_id=feature.id,
        phytochem_candidate_id=candidate.id if candidate else None,
        spectral_library_id=library.id if library else None,
        reference_standard_injection_id=standard.id if standard else None,
        annotation_method=body.annotation_method, annotation_name=body.annotation_name,
        molecular_formula=body.molecular_formula, inchikey=body.inchikey,
        smiles=body.smiles, msi_level=body.msi_level,
        score_type=body.score_type, score_value=body.score_value,
        evidence=evidence_payload,
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
        run = db.query(LcmsProcessingRun).join(
            LcmsFeature, LcmsFeature.processing_run_id == LcmsProcessingRun.id
        ).filter(LcmsFeature.id == row.feature_id).one()
        feature = db.get(LcmsFeature, row.feature_id)
        standard = db.query(LcmsInjection).filter(
            LcmsInjection.id == row.reference_standard_injection_id,
            LcmsInjection.batch_id == run.batch_id,
            LcmsInjection.injection_type == "reference_standard",
        ).first()
        if feature is None or standard is None:
            raise HTTPException(409, "La feature ou le standard authentique MSI niveau 1 n'est plus traçable.")
        _validate_msi1_measurements(
            db, feature=feature, run=run, standard=standard,
            evidence=row.evidence or {}, persisted=True, error_status_code=409,
        )
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
        "ms2_spectrum_count": db.query(LcmsMs2Spectrum)
            .join(LcmsInjection, LcmsMs2Spectrum.injection_id == LcmsInjection.id)
            .filter(LcmsInjection.study_id == study_id).count(),
        "unassigned_ms2_spectrum_count": db.query(LcmsMs2Spectrum)
            .join(LcmsInjection, LcmsMs2Spectrum.injection_id == LcmsInjection.id)
            .filter(
                LcmsInjection.study_id == study_id,
                LcmsMs2Spectrum.association_status.in_(("ambiguous", "unmatched")),
            ).count(),
        "scientific_workflow_count": db.query(ScientificWorkflowRun).filter(
            ScientificWorkflowRun.owner_user_id == user.id,
            ScientificWorkflowRun.scope_type == "lcms_processing_run",
        ).count(),
        "pipeline_scope": "single_injection_openms_feature_finding_and_msms_library_matching",
    }
