"""Auditable plant-compound occurrence evidence with strict material lineage.

A WCVP taxon name match alone is not evidence of occurrence. Literature,
specimen-linked experiments and LC-MS/MS observations are recorded separately.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.lcmsms import (
    BotanicalSpecimen, ExtractionBatch, LcmsCompoundAnnotation, LcmsFeature,
    LcmsFeatureObservation, LcmsInjection, LcmsProcessingRun, PlantFraction,
    PlantMaterialSample,
)
from app.models.phytochemistry import (
    PhytochemicalCandidate, PhytochemicalOccurrenceEvidence, TaxonResolution,
)
from app.services.phytochemistry_catalog import OCCURRENCE_VERIFIED


ALLOWED_EVIDENCE_TYPES = {
    "literature_report", "lcms_annotation", "isolated_compound", "other_experimental"
}


class OccurrenceEvidenceError(ValueError):
    """Occurrence evidence is incomplete, inconsistent, or scientifically ineligible."""


def _norm(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _measurement_for_hash(value: Any) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)).normalize(), "f")


def _reference_is_valid(value: str) -> bool:
    text = (value or "").strip()
    return bool(
        re.search(r"10\.\d{4,9}/[^\s]+", text, re.I)
        or re.search(r"\bPMID\s*:?\s*\d+\b", text, re.I)
        or text.lower().startswith(("https://", "http://"))
    )


def _evidence_payload(row: PhytochemicalOccurrenceEvidence) -> dict:
    return {
        "candidate_id": row.candidate_id,
        "taxon_resolution_id": row.taxon_resolution_id,
        "specimen_id": row.specimen_id,
        "sample_id": row.sample_id,
        "extraction_batch_id": row.extraction_batch_id,
        "fraction_id": row.fraction_id,
        "lcms_annotation_id": row.lcms_annotation_id,
        "evidence_type": row.evidence_type,
        "plant_part": row.plant_part,
        "source_reference": row.source_reference,
        "source_title": row.source_title,
        "source_locator": row.source_locator,
        "evidence_statement": row.evidence_statement,
        "analytical_method": row.analytical_method,
        "measurement_value": _measurement_for_hash(row.measurement_value),
        "measurement_unit": row.measurement_unit,
        "metadata": row.metadata_json or {},
    }


def occurrence_evidence_to_dict(row: PhytochemicalOccurrenceEvidence) -> dict:
    return {
        "id": row.id,
        "candidate_id": row.candidate_id,
        "taxon_resolution_id": row.taxon_resolution_id,
        "specimen_id": row.specimen_id,
        "sample_id": row.sample_id,
        "extraction_batch_id": row.extraction_batch_id,
        "fraction_id": row.fraction_id,
        "lcms_annotation_id": row.lcms_annotation_id,
        "evidence_type": row.evidence_type,
        "plant_part": row.plant_part,
        "source_reference": row.source_reference,
        "source_title": row.source_title,
        "source_locator": row.source_locator,
        "evidence_statement": row.evidence_statement,
        "analytical_method": row.analytical_method,
        "measurement_value": float(row.measurement_value) if row.measurement_value is not None else None,
        "measurement_unit": row.measurement_unit,
        "evidence_sha256": row.evidence_sha256,
        "metadata": row.metadata_json or {},
        "review_status": row.review_status,
        "created_by": row.created_by,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_note": row.review_note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _validate_taxon_link(
    db: Session,
    candidate: PhytochemicalCandidate,
    resolution: TaxonResolution,
    specimen_id: int | None,
) -> BotanicalSpecimen | None:
    if resolution.review_status != "accepted":
        raise OccurrenceEvidenceError("La résolution taxonomique doit être acceptée.")
    if not candidate.accepted_taxon or not candidate.taxon_identifier:
        raise OccurrenceEvidenceError("Le candidat ne possède pas de taxon accepté.")
    if _norm(resolution.accepted_name) != _norm(candidate.accepted_taxon):
        raise OccurrenceEvidenceError("Le taxon accepté de la résolution ne correspond pas au candidat.")
    expected_identifier = "WCVP:" + str(resolution.accepted_identifier or "")
    if not resolution.accepted_identifier or candidate.taxon_identifier != expected_identifier:
        raise OccurrenceEvidenceError("L'identifiant WCVP du candidat ne correspond pas à la résolution acceptée.")

    if resolution.candidate_id == candidate.id:
        if resolution.specimen_id is not None:
            raise OccurrenceEvidenceError("La résolution candidate ne doit pas référencer simultanément un spécimen.")
    elif resolution.specimen_id is not None:
        if specimen_id != resolution.specimen_id:
            raise OccurrenceEvidenceError("La résolution taxonomique ne correspond pas au spécimen fourni.")
    else:
        raise OccurrenceEvidenceError("La résolution n'appartient ni au candidat ni au spécimen fourni.")

    if specimen_id is None:
        return None
    specimen = db.get(BotanicalSpecimen, specimen_id)
    if specimen is None:
        raise OccurrenceEvidenceError("Spécimen botanique introuvable.")
    if specimen.identification_status != "verified":
        raise OccurrenceEvidenceError(
            "Une preuve liée à un spécimen exige une identification botanique physique au statut verified."
        )
    if _norm(specimen.accepted_taxon) != _norm(candidate.accepted_taxon):
        raise OccurrenceEvidenceError("Le taxon accepté du spécimen ne correspond pas à celui du candidat.")
    if specimen.taxon_identifier != expected_identifier:
        raise OccurrenceEvidenceError("L'identifiant WCVP du spécimen ne correspond pas à la résolution acceptée.")
    if resolution.specimen_id is not None and resolution.specimen_id != specimen.id:
        raise OccurrenceEvidenceError("Le spécimen ne correspond pas à la résolution taxonomique liée.")
    return specimen


def _validate_material_chain(
    db: Session,
    *,
    candidate: PhytochemicalCandidate,
    resolution: TaxonResolution,
    specimen_id: int | None,
    sample_id: int | None,
    extraction_batch_id: int | None,
    fraction_id: int | None,
    plant_part: str,
    evidence_type: str,
    lcms_annotation_id: int | None,
) -> tuple[int | None, int | None, int | None, int | None]:
    if fraction_id is not None:
        fraction = db.get(PlantFraction, fraction_id)
        if fraction is None:
            raise OccurrenceEvidenceError("Fraction végétale introuvable.")
        if extraction_batch_id is not None and extraction_batch_id != fraction.extraction_batch_id:
            raise OccurrenceEvidenceError("La fraction ne dépend pas du lot d'extraction fourni.")
        extraction_batch_id = fraction.extraction_batch_id

    if extraction_batch_id is not None:
        extraction = db.get(ExtractionBatch, extraction_batch_id)
        if extraction is None:
            raise OccurrenceEvidenceError("Lot d'extraction introuvable.")
        if sample_id is not None and sample_id != extraction.sample_id:
            raise OccurrenceEvidenceError("Le lot d'extraction ne dépend pas de l'échantillon fourni.")
        sample_id = extraction.sample_id

    if sample_id is not None:
        sample = db.get(PlantMaterialSample, sample_id)
        if sample is None:
            raise OccurrenceEvidenceError("Échantillon végétal introuvable.")
        if specimen_id is not None and specimen_id != sample.specimen_id:
            raise OccurrenceEvidenceError("L'échantillon ne dépend pas du spécimen fourni.")
        specimen_id = sample.specimen_id
        if _norm(sample.plant_part) != _norm(plant_part):
            raise OccurrenceEvidenceError("La partie végétale ne correspond pas à celle de l'échantillon.")

    _validate_taxon_link(db, candidate, resolution, specimen_id)
    if evidence_type != "literature_report" and specimen_id is None:
        raise OccurrenceEvidenceError("Une preuve expérimentale doit être rattachée à un spécimen botanique identifié.")
    if evidence_type in {"isolated_compound", "other_experimental"} and sample_id is None:
        raise OccurrenceEvidenceError("Une preuve expérimentale doit référencer l'échantillon matériel analysé.")

    if evidence_type == "lcms_annotation":
        if lcms_annotation_id is None or sample_id is None:
            raise OccurrenceEvidenceError("Une preuve LC-MS/MS exige une annotation et un échantillon végétal.")
        annotation = db.get(LcmsCompoundAnnotation, lcms_annotation_id)
        if annotation is None:
            raise OccurrenceEvidenceError("Annotation LC-MS/MS introuvable.")
        if annotation.phytochem_candidate_id != candidate.id or annotation.status != "accepted":
            raise OccurrenceEvidenceError("L'annotation doit être acceptée et liée au même candidat phytochimique.")
        if annotation.msi_level != 1:
            raise OccurrenceEvidenceError("Une occurrence LC-MS/MS confirmée exige une annotation MSI niveau 1.")
        feature = db.get(LcmsFeature, annotation.feature_id)
        if feature is None:
            raise OccurrenceEvidenceError("Feature LC-MS/MS de l'annotation introuvable.")
        processing_run = db.get(LcmsProcessingRun, feature.processing_run_id)
        if processing_run is None:
            raise OccurrenceEvidenceError("Traitement LC-MS/MS de la feature introuvable.")

        observations = (
            db.query(LcmsFeatureObservation.id)
            .join(LcmsInjection, LcmsFeatureObservation.injection_id == LcmsInjection.id)
            .filter(
                LcmsFeatureObservation.feature_id == feature.id,
                LcmsInjection.batch_id == processing_run.batch_id,
            )
        )
        if fraction_id is not None:
            observations = observations.filter(LcmsInjection.fraction_id == fraction_id)
        else:
            observations = observations.filter(LcmsInjection.plant_sample_id == sample_id)
        if observations.first() is None:
            raise OccurrenceEvidenceError(
                "Aucune observation de cette feature n'est reliée à une injection du même échantillon ou de la même fraction."
            )
    elif lcms_annotation_id is not None:
        raise OccurrenceEvidenceError("Une annotation LC-MS/MS n'est permise qu'avec evidence_type='lcms_annotation'.")

    return specimen_id, sample_id, extraction_batch_id, fraction_id


def create_occurrence_evidence(
    db: Session,
    *,
    candidate_id: int,
    taxon_resolution_id: int,
    evidence_type: str,
    plant_part: str,
    evidence_statement: str,
    created_by: int | None,
    source_reference: str | None = None,
    source_title: str | None = None,
    source_locator: str | None = None,
    specimen_id: int | None = None,
    sample_id: int | None = None,
    extraction_batch_id: int | None = None,
    fraction_id: int | None = None,
    lcms_annotation_id: int | None = None,
    analytical_method: str | None = None,
    measurement_value: float | None = None,
    measurement_unit: str | None = None,
    metadata: dict | None = None,
) -> PhytochemicalOccurrenceEvidence:
    if evidence_type not in ALLOWED_EVIDENCE_TYPES:
        raise OccurrenceEvidenceError("Type de preuve d'occurrence invalide.")
    candidate = db.get(PhytochemicalCandidate, candidate_id)
    if candidate is None:
        raise OccurrenceEvidenceError("Candidat phytochimique introuvable.")
    resolution = db.get(TaxonResolution, taxon_resolution_id)
    if resolution is None:
        raise OccurrenceEvidenceError("Résolution taxonomique introuvable.")
    part = (plant_part or "").strip()
    statement = (evidence_statement or "").strip()
    if len(part) < 2 or len(statement) < 10:
        raise OccurrenceEvidenceError("Partie végétale et énoncé scientifique de preuve obligatoires.")

    specimen_id, sample_id, extraction_batch_id, fraction_id = _validate_material_chain(
        db, candidate=candidate, resolution=resolution, specimen_id=specimen_id,
        sample_id=sample_id, extraction_batch_id=extraction_batch_id,
        fraction_id=fraction_id, plant_part=part, evidence_type=evidence_type,
        lcms_annotation_id=lcms_annotation_id,
    )

    reference = (source_reference or "").strip()
    if evidence_type == "lcms_annotation":
        internal_reference = "NEXORA-LCMS-ANNOTATION:" + str(lcms_annotation_id)
        if reference and reference != internal_reference and not _reference_is_valid(reference):
            raise OccurrenceEvidenceError("La référence doit être un DOI/PMID/URL ou l'identifiant interne de l'annotation.")
        reference = reference or internal_reference
    elif not _reference_is_valid(reference):
        raise OccurrenceEvidenceError("La preuve doit citer un DOI, un PMID ou une URL primaire.")

    if (measurement_value is None) != (measurement_unit is None):
        raise OccurrenceEvidenceError("Valeur mesurée et unité doivent être fournies ensemble.")
    if measurement_value is not None and measurement_value <= 0:
        raise OccurrenceEvidenceError("La valeur mesurée doit être strictement positive.")

    payload = {
        "candidate_id": candidate.id, "taxon_resolution_id": resolution.id,
        "specimen_id": specimen_id, "sample_id": sample_id,
        "extraction_batch_id": extraction_batch_id, "fraction_id": fraction_id,
        "lcms_annotation_id": lcms_annotation_id, "evidence_type": evidence_type,
        "plant_part": part, "source_reference": reference, "source_title": source_title,
        "source_locator": source_locator, "evidence_statement": statement,
        "analytical_method": analytical_method, "measurement_value": _measurement_for_hash(measurement_value),
        "measurement_unit": measurement_unit, "metadata": metadata or {},
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    row = PhytochemicalOccurrenceEvidence(
        candidate_id=candidate.id, taxon_resolution_id=resolution.id,
        specimen_id=specimen_id, sample_id=sample_id,
        extraction_batch_id=extraction_batch_id, fraction_id=fraction_id,
        lcms_annotation_id=lcms_annotation_id, evidence_type=evidence_type,
        plant_part=part, source_reference=reference, source_title=source_title,
        source_locator=source_locator, evidence_statement=statement,
        analytical_method=analytical_method, measurement_value=measurement_value,
        measurement_unit=measurement_unit, evidence_sha256=digest,
        metadata_json=metadata or {}, review_status="pending_review", created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def review_occurrence_evidence(
    db: Session,
    row: PhytochemicalOccurrenceEvidence,
    *,
    decision: str,
    reviewer_id: int | None,
    reviewer_label: str,
    rationale: str,
) -> PhytochemicalOccurrenceEvidence:
    decision = (decision or "").strip().lower()
    reviewer_label = (reviewer_label or "").strip()
    rationale = (rationale or "").strip()
    if decision not in {"accept", "reject", "insufficient"}:
        raise OccurrenceEvidenceError("Décision attendue: accept, reject ou insufficient.")
    if not reviewer_label or len(rationale) < 10:
        raise OccurrenceEvidenceError("Identité du réviseur et justification d'au moins 10 caractères requises.")
    if row.review_status != "pending_review":
        raise OccurrenceEvidenceError("Cette preuve n'est plus en attente de revue.")
    if reviewer_id is not None and row.created_by is not None and reviewer_id == row.created_by:
        raise OccurrenceEvidenceError("La revue doit être indépendante : le créateur ne peut pas approuver sa propre preuve.")
    current_hash = hashlib.sha256(_canonical_json(_evidence_payload(row)).encode("utf-8")).hexdigest()
    if current_hash != row.evidence_sha256:
        raise OccurrenceEvidenceError("L'empreinte de la preuve ne correspond plus au contenu enregistré.")

    if decision == "accept":
        candidate = db.get(PhytochemicalCandidate, row.candidate_id)
        resolution = db.get(TaxonResolution, row.taxon_resolution_id)
        if candidate is None or resolution is None:
            raise OccurrenceEvidenceError("Candidat ou résolution taxonomique lié introuvable.")
        _validate_material_chain(
            db, candidate=candidate, resolution=resolution, specimen_id=row.specimen_id,
            sample_id=row.sample_id, extraction_batch_id=row.extraction_batch_id,
            fraction_id=row.fraction_id, plant_part=row.plant_part,
            evidence_type=row.evidence_type, lcms_annotation_id=row.lcms_annotation_id,
        )
        candidate.occurrence_status = OCCURRENCE_VERIFIED
        candidate.occurrence_source = row.source_reference
        candidate.plant_part = row.plant_part
        candidate.curator = reviewer_label
        candidate.reviewed_at = datetime.utcnow()
        candidate.review_note = rationale

    row.review_status = {"accept": "accepted", "reject": "rejected", "insufficient": "insufficient"}[decision]
    row.reviewed_by = reviewer_id
    row.reviewed_at = datetime.utcnow()
    row.review_note = rationale
    return row
