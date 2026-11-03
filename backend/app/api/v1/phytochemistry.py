"""Curated African phyto-chemical candidate API for N3XORA-Sahel."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.phytochemistry import PhytochemicalCandidate, PhytochemicalOccurrenceEvidence, TaxonResolution
from app.models.user import User
from app.services.auth_service import get_current_user, require_admin
from app.services.phytochemical_sources import SourceRegistryError, get_source_registry
from app.services.taxonomic_resolution import ExternalTaxonomyError, TaxonomyResolutionError, resolve_botanical_taxon, review_taxon_resolution, taxon_resolution_to_dict
from app.services.phytochemical_evidence_integration import (
    EvidenceIntegrationError,
    build_phytochemical_evidence_integration,
)
from app.services.phytochemical_occurrence import (
    OccurrenceEvidenceError, create_occurrence_evidence, review_occurrence_evidence,
    occurrence_evidence_to_dict,
)
from app.services.phytochemistry_catalog import (
    CurationError, ExternalReferenceError, candidate_to_dict, compute_descriptors,
    import_from_anpdb, import_from_afrodb, import_from_wanpdb, import_sahel_50, ready_for_docking,
    verify_identity, sha256_file, IMPORTED, IDENTITY_VERIFIED,
)

router = APIRouter(prefix="/phytochem", tags=["african-phytochemistry"])


class IdentityVerifyRequest(BaseModel):
    pubchem_cid: int = Field(..., ge=1)
    inchikey: str = Field(..., min_length=14, max_length=64)
    smiles: str = Field(..., min_length=1, max_length=4000)


class OccurrenceVerifyRequest(BaseModel):
    taxon: str = Field(..., min_length=3, max_length=255)
    pmid_doi_or_url: str = Field(..., min_length=5, max_length=1000)
    curator: Optional[str] = Field(None, min_length=2, max_length=255)  # ignored; authenticated admin is authoritative
    review_note: str = Field(..., min_length=10, max_length=4000)
    attest_compound_occurrence: bool = False
    taxon_resolution_id: Optional[int] = Field(None, ge=1)
    plant_part: Optional[str] = Field(None, min_length=2, max_length=120)
    source_title: Optional[str] = Field(None, max_length=500)
    source_locator: Optional[str] = Field(None, max_length=255)

class TaxonResolveRequest(BaseModel):
    scientific_name: str = Field(..., min_length=2, max_length=255)


class TaxonResolutionReviewRequest(BaseModel):
    decision: str = Field(..., pattern="^(accept|reject)$")
    rationale: str = Field(..., min_length=10, max_length=4000)


class OccurrenceEvidenceCreateRequest(BaseModel):
    taxon_resolution_id: int = Field(..., ge=1)
    evidence_type: str = Field(..., pattern="^(literature_report|lcms_annotation|isolated_compound|other_experimental)$")
    plant_part: str = Field(..., min_length=2, max_length=120)
    source_reference: Optional[str] = Field(None, min_length=5, max_length=2000)
    source_title: Optional[str] = Field(None, max_length=500)
    source_locator: Optional[str] = Field(None, max_length=255)
    evidence_statement: str = Field(..., min_length=10, max_length=5000)
    specimen_id: Optional[int] = Field(None, ge=1)
    sample_id: Optional[int] = Field(None, ge=1)
    extraction_batch_id: Optional[int] = Field(None, ge=1)
    fraction_id: Optional[int] = Field(None, ge=1)
    lcms_annotation_id: Optional[int] = Field(None, ge=1)
    analytical_method: Optional[str] = Field(None, max_length=255)
    measurement_value: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    measurement_unit: Optional[str] = Field(None, max_length=80)
    metadata: dict = Field(default_factory=dict)


class OccurrenceEvidenceReviewRequest(BaseModel):
    decision: str = Field(..., pattern="^(accept|reject|insufficient)$")
    rationale: str = Field(..., min_length=10, max_length=4000)


class CandidateReviewRequest(BaseModel):
    evidence_grade: str = Field("D", pattern="^[ABCD]$")
    curator_note: str = Field(..., min_length=10, max_length=4000)
    plant_part: Optional[str] = Field(None, max_length=120)
    target_organism: Optional[str] = Field(None, max_length=255)
    target_accession: Optional[str] = Field(None, max_length=120)
    assay_type: Optional[str] = Field(None, max_length=120)
    endpoint_value: Optional[float] = Field(None, allow_inf_nan=False)
    endpoint_unit: Optional[str] = Field(None, max_length=80)
    study_reference: Optional[str] = Field(None, max_length=1000)


def _import_upload(upload: UploadFile, expected_suffix: str) -> tuple[str, int]:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix != expected_suffix:
        raise HTTPException(415, f"Format attendu: {expected_suffix}")
    root = Path(settings.AFRICAN_LIBS_DIR)
    try:
        root.mkdir(parents=True, exist_ok=True)
        # Stream to a server-created file; never trust the user-supplied filename as a path.
        fd, name = tempfile.mkstemp(prefix="incoming_", suffix=expected_suffix, dir=str(root))
        size = 0
        with os.fdopen(fd, "wb") as handle:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 250 * 1024 * 1024:
                    os.unlink(name)
                    raise HTTPException(413, "Fichier trop volumineux (limite 250 MiB).")
                handle.write(chunk)
        if size == 0:
            os.unlink(name)
            raise HTTPException(400, "Fichier vide.")
        return name, size
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(500, "Impossible d'écrire le fichier de curation dans le cache local.") from exc


def _service_error(exc: Exception):
    if isinstance(exc, CurationError):
        raise HTTPException(422, str(exc))
    if isinstance(exc, ExternalReferenceError):
        raise HTTPException(503, str(exc))
    raise HTTPException(500, f"Erreur de curation contrôlée: {type(exc).__name__}")


@router.get("")
def list_candidates(
    status: Optional[str] = Query(None, alias="identity_status"),
    source: Optional[str] = Query(None),
    chemical_class: Optional[str] = Query(None),
    occurrence_status: Optional[str] = Query(None),
    query: Optional[str] = Query(None, min_length=2),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(PhytochemicalCandidate)
    if status:
        q = q.filter(PhytochemicalCandidate.identity_status == status)
    if source:
        q = q.filter(PhytochemicalCandidate.library_source == source.upper())
    if chemical_class:
        q = q.filter(func.lower(PhytochemicalCandidate.chemical_class) == chemical_class.lower())
    if occurrence_status:
        q = q.filter(PhytochemicalCandidate.occurrence_status == occurrence_status)
    if query:
        pattern = f"%{query.strip().lower()}%"
        q = q.filter(
            func.lower(PhytochemicalCandidate.name_raw).like(pattern)
            | func.lower(PhytochemicalCandidate.source_plant).like(pattern)
            | func.lower(PhytochemicalCandidate.accepted_taxon).like(pattern)
        )
    total = q.count()
    rows = q.order_by(PhytochemicalCandidate.id.asc()).offset(skip).limit(limit).all()
    return {"success": True, "candidates": [candidate_to_dict(row) for row in rows],
            "total": total, "pagination": {"limit": limit, "offset": skip, "has_more": skip + len(rows) < total}}


@router.get("/stats")
def candidate_stats(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    def counts(column):
        return [{"value": value or "non renseigné", "count": int(count)}
                for value, count in db.query(column, func.count(PhytochemicalCandidate.id)).group_by(column).order_by(func.count(PhytochemicalCandidate.id).desc()).all()]
    return {"success": True, "total": db.query(PhytochemicalCandidate).count(),
            "by_identity_status": counts(PhytochemicalCandidate.identity_status),
            "by_occurrence_status": counts(PhytochemicalCandidate.occurrence_status),
            "by_source": counts(PhytochemicalCandidate.library_source),
            "by_chemical_class": counts(PhytochemicalCandidate.chemical_class)}


@router.get("/ready-for-docking")
def ready_candidates(
    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
):
    # Candidate readiness is necessary but not sufficient: the caller must still
    # select an explicit target/accession and a named protocol in the docking call.
    q = db.query(PhytochemicalCandidate).filter(
        PhytochemicalCandidate.identity_status == IDENTITY_VERIFIED,
        PhytochemicalCandidate.occurrence_status == "VÉRIFIÉE",
        PhytochemicalCandidate.inchikey.isnot(None),
        PhytochemicalCandidate.smiles_canonical.isnot(None),
        PhytochemicalCandidate.accepted_taxon.isnot(None),
        PhytochemicalCandidate.occurrence_source.isnot(None),
    ).order_by(PhytochemicalCandidate.id.asc())
    rows = q.offset(skip).limit(limit).all()
    ready = []
    for row in rows:
        ok, blockers = ready_for_docking(row)
        if ok:
            ready.append(candidate_to_dict(row))
    return {"success": True, "candidates": ready, "count": len(ready),
            "required_on_docking_submission": ["target_accession", "protocol_id"]}


@router.get("/anpdb/search")
def search_anpdb_cache(
    q: str = Query(..., min_length=2),
    format: str = Query("csv", pattern="^csv$"),
    limit: int = Query(20, ge=1, le=100),
    _user: User = Depends(get_current_user),
):
    """Search the local official ANPDB download cache; first call downloads a dataset if needed."""
    from app.services.anpdb_client import search_compound, ANPDBClientError
    try:
        return {"success": True, **search_compound(q, format=format, max_results=limit)}
    except ANPDBClientError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/import/anpdb/download", status_code=201)
def download_and_import_anpdb(
    format: str = Query("csv", pattern="^csv$"),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Download the official ANPDB CSV to the offline cache and import a bounded subset."""
    from app.services.anpdb_client import download_dataset, ANPDBClientError
    try:
        dataset = download_dataset(format)
        result = import_from_anpdb(dataset["path"], db, settings.PHYTOCHEM_MAX_IMPORT)
        return {"success": True, "dataset": dataset, **result}
    except Exception as exc:
        db.rollback()
        if isinstance(exc, ANPDBClientError):
            raise HTTPException(503, str(exc)) from exc
        _service_error(exc)


@router.post("/import/wanpdb", status_code=201)
def import_wanpdb_csv(file: UploadFile = File(...), db: Session = Depends(get_db),
                      _admin: User = Depends(require_admin)):
    path, _size = _import_upload(file, ".csv")
    try:
        result = import_from_wanpdb(path, db, settings.PHYTOCHEM_MAX_IMPORT)
        return {"success": True, **result}
    except Exception as exc:
        db.rollback()
        _service_error(exc)
    finally:
        try: os.unlink(path)
        except OSError: pass


@router.get("/sources")
def list_phytochemical_sources(
    domain: Optional[str] = Query(None, min_length=2, max_length=80),
    _user: User = Depends(get_current_user),
):
    """Return the versioned source/licence registry without network or database side effects."""
    try:
        return {"success": True, **get_source_registry(domain)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except SourceRegistryError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/{candidate_id:int}")
def candidate_detail(candidate_id: int, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    row = db.query(PhytochemicalCandidate).filter(PhytochemicalCandidate.id == candidate_id).first()
    if not row:
        raise HTTPException(404, "Candidat phytochimique introuvable.")
    return {"success": True, "candidate": candidate_to_dict(row)}


@router.get("/{candidate_id:int}/evidence-integration")
def candidate_evidence_integration(
    candidate_id: int,
    annotation_limit: int = Query(50, ge=1, le=100),
    quantification_cell_limit: int = Query(250, ge=1, le=1000),
    assay_limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Read-only botanical, chemical and LC-MS/MS evidence projection."""
    try:
        return build_phytochemical_evidence_integration(
            db,
            candidate_id=candidate_id,
            user_id=user.id,
            is_admin=bool(user.is_admin),
            annotation_limit=annotation_limit,
            quantification_cell_limit=quantification_cell_limit,
            assay_limit=assay_limit,
        )
    except EvidenceIntegrationError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{candidate_id}/source-file")
def download_candidate_source(
    candidate_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Return the immutable raw source file only after path containment and SHA-256 checks."""
    candidate = db.query(PhytochemicalCandidate).filter(
        PhytochemicalCandidate.id == candidate_id
    ).first()
    if not candidate:
        raise HTTPException(404, "Candidat phytochimique introuvable.")
    relative = Path(candidate.source_file_path or "")
    if not candidate.source_file_path or relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(404, "Aucun fichier source archivé pour ce candidat.")
    root = Path(settings.AFRICAN_LIBS_DIR).resolve()
    resolved = (root / relative).resolve()
    if root not in resolved.parents or not resolved.is_file():
        raise HTTPException(404, "Fichier source archivé introuvable.")
    expected = candidate.source_file_sha256
    actual = sha256_file(resolved)
    if not expected or actual != expected:
        raise HTTPException(409, "L'empreinte SHA-256 du fichier source ne correspond plus au registre.")
    return FileResponse(
        path=str(resolved),
        filename=resolved.name,
        media_type="application/octet-stream",
        headers={"X-Source-SHA256": actual},
    )


@router.post("/import/anpdb", status_code=201)
def import_anpdb_csv(file: UploadFile = File(...), db: Session = Depends(get_db),
                     _admin: User = Depends(require_admin)):
    path, _size = _import_upload(file, ".csv")
    try:
        result = import_from_anpdb(path, db, settings.PHYTOCHEM_MAX_IMPORT)
        return {"success": True, **result}
    except Exception as exc:
        db.rollback()
        _service_error(exc)
    finally:
        try: os.unlink(path)
        except OSError: pass


@router.post("/import/afrodb", status_code=201)
def import_afrodb_sdf(file: UploadFile = File(...), db: Session = Depends(get_db),
                      _admin: User = Depends(require_admin)):
    path, _size = _import_upload(file, ".sdf")
    try:
        result = import_from_afrodb(path, db, settings.PHYTOCHEM_MAX_IMPORT)
        return {"success": True, **result}
    except Exception as exc:
        db.rollback()
        _service_error(exc)
    finally:
        try: os.unlink(path)
        except OSError: pass


@router.post("/import/sahel50", status_code=201)
def import_sahel50(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    try:
        result = import_sahel_50(None, db)
        return {"success": True, **result}
    except Exception as exc:
        db.rollback()
        _service_error(exc)


@router.post("/{candidate_id}/taxonomy/resolve", status_code=201)
def resolve_candidate_taxonomy(candidate_id: int, body: TaxonResolveRequest,
                               db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    candidate = db.get(PhytochemicalCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(404, "Candidat phytochimique introuvable.")
    try:
        row = resolve_botanical_taxon(
            db, body.scientific_name, candidate_id=candidate_id,
            created_by=_admin.id, created_by_label=_admin.email or str(_admin.id),
        )
        db.commit()
        db.refresh(row)
        return {"success": True, "resolution": taxon_resolution_to_dict(row)}
    except TaxonomyResolutionError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except ExternalTaxonomyError as exc:
        db.rollback()
        raise HTTPException(503, str(exc)) from exc


@router.get("/taxonomy-resolutions")
def list_taxonomy_resolutions(
    review_status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    allowed = {"pending_review", "accepted", "rejected", "unresolved"}
    if review_status is not None and review_status not in allowed:
        raise HTTPException(422, "Statut de revue taxonomique invalide.")
    query = db.query(TaxonResolution)
    if review_status:
        query = query.filter(TaxonResolution.review_status == review_status)
    total = query.count()
    rows = query.order_by(TaxonResolution.created_at.desc(), TaxonResolution.id.desc()).offset(skip).limit(limit).all()
    return {
        "success": True,
        "total": total,
        "count": len(rows),
        "resolutions": [taxon_resolution_to_dict(row) for row in rows],
    }


@router.get("/taxonomy-resolutions/{resolution_id}")
def get_taxonomy_resolution(resolution_id: int, db: Session = Depends(get_db),
                            _admin: User = Depends(require_admin)):
    row = db.get(TaxonResolution, resolution_id)
    if row is None:
        raise HTTPException(404, "Résolution taxonomique introuvable.")
    return {"success": True, "resolution": taxon_resolution_to_dict(row)}


@router.post("/taxonomy-resolutions/{resolution_id}/review")
def review_taxonomy_resolution(resolution_id: int, body: TaxonResolutionReviewRequest,
                               db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    row = db.get(TaxonResolution, resolution_id)
    if row is None:
        raise HTTPException(404, "Résolution taxonomique introuvable.")
    try:
        review_taxon_resolution(
            db, row, decision=body.decision,
            reviewer=admin.email or str(admin.id), rationale=body.rationale,
            reviewer_user_id=admin.id,
        )
        db.commit()
        db.refresh(row)
        return {"success": True, "resolution": taxon_resolution_to_dict(row)}
    except TaxonomyResolutionError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.post("/{candidate_id}/occurrence-evidence", status_code=201)
def create_candidate_occurrence_evidence(
    candidate_id: int,
    body: OccurrenceEvidenceCreateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if db.get(PhytochemicalCandidate, candidate_id) is None:
        raise HTTPException(404, "Candidat phytochimique introuvable.")
    try:
        row = create_occurrence_evidence(
            db, candidate_id=candidate_id, created_by=admin.id, **body.model_dump()
        )
        db.commit()
        db.refresh(row)
        return {
            "success": True,
            "verified": False,
            "message": "Preuve enregistrée en attente de revue indépendante.",
            "evidence": occurrence_evidence_to_dict(row),
        }
    except OccurrenceEvidenceError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.get("/{candidate_id}/occurrence-evidence")
def list_candidate_occurrence_evidence(
    candidate_id: int,
    review_status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    if db.get(PhytochemicalCandidate, candidate_id) is None:
        raise HTTPException(404, "Candidat phytochimique introuvable.")
    allowed = {"pending_review", "accepted", "rejected", "insufficient"}
    if review_status is not None and review_status not in allowed:
        raise HTTPException(422, "Statut de revue de preuve invalide.")
    query = db.query(PhytochemicalOccurrenceEvidence).filter(
        PhytochemicalOccurrenceEvidence.candidate_id == candidate_id
    )
    if review_status:
        query = query.filter(PhytochemicalOccurrenceEvidence.review_status == review_status)
    rows = query.order_by(
        PhytochemicalOccurrenceEvidence.created_at.desc(),
        PhytochemicalOccurrenceEvidence.id.desc(),
    ).all()
    return {
        "success": True,
        "count": len(rows),
        "evidence": [occurrence_evidence_to_dict(row) for row in rows],
    }


@router.post("/occurrence-evidence/{evidence_id}/review")
def review_candidate_occurrence_evidence(
    evidence_id: int,
    body: OccurrenceEvidenceReviewRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    row = db.get(PhytochemicalOccurrenceEvidence, evidence_id)
    if row is None:
        raise HTTPException(404, "Preuve d'occurrence introuvable.")
    try:
        review_occurrence_evidence(
            db, row, decision=body.decision, reviewer_id=admin.id,
            reviewer_label=admin.email or str(admin.id), rationale=body.rationale,
        )
        db.commit()
        db.refresh(row)
        return {"success": True, "evidence": occurrence_evidence_to_dict(row)}
    except OccurrenceEvidenceError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.post("/{candidate_id}/verify-identity")
def identity_verification(candidate_id: int, body: IdentityVerifyRequest,
                          db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    candidate = db.query(PhytochemicalCandidate).filter(PhytochemicalCandidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(404, "Candidat phytochimique introuvable.")
    try:
        result = verify_identity(candidate, body.pubchem_cid, body.inchikey, body.smiles)
        candidate.curator = admin.email
        if not result["verified"]:
            candidate.reviewed_at = __import__("datetime").datetime.utcnow()
        db.commit()
        return {"success": bool(result["verified"]), "candidate_id": candidate.id, **result}
    except Exception as exc:
        db.rollback()
        _service_error(exc)


@router.post("/{candidate_id}/verify-occurrence", status_code=201)
def occurrence_verification(candidate_id: int, body: OccurrenceVerifyRequest,
                             db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Backward-compatible literature intake; the claim still requires a separate review."""
    candidate = db.get(PhytochemicalCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(404, "Candidat phytochimique introuvable.")
    if not body.attest_compound_occurrence:
        raise HTTPException(422, "L'attestation explicite de l'occurrence doit être fournie.")
    if not candidate.accepted_taxon or " ".join(body.taxon.casefold().split()) != " ".join(candidate.accepted_taxon.casefold().split()):
        raise HTTPException(422, "Le taxon fourni doit correspondre au nom accepté du candidat.")
    plant_part = (body.plant_part or candidate.plant_part or "").strip()
    if len(plant_part) < 2:
        raise HTTPException(422, "La partie végétale doit être renseignée.")
    resolution = db.get(TaxonResolution, body.taxon_resolution_id) if body.taxon_resolution_id else db.query(
        TaxonResolution
    ).filter(
        TaxonResolution.candidate_id == candidate.id,
        TaxonResolution.review_status == "accepted",
    ).order_by(TaxonResolution.created_at.desc(), TaxonResolution.id.desc()).first()
    if resolution is None:
        raise HTTPException(422, "Une résolution WCVP acceptée est obligatoire.")
    try:
        row = create_occurrence_evidence(
            db,
            candidate_id=candidate.id,
            taxon_resolution_id=resolution.id,
            evidence_type="literature_report",
            plant_part=plant_part,
            source_reference=body.pmid_doi_or_url,
            source_title=body.source_title,
            source_locator=body.source_locator,
            evidence_statement=body.review_note,
            created_by=admin.id,
        )
        db.commit()
        db.refresh(row)
        return {
            "success": True,
            "verified": False,
            "candidate_id": candidate.id,
            "message": "Référence enregistrée en attente de revue ; le statut occurrence n'est pas encore vérifié.",
            "evidence": occurrence_evidence_to_dict(row),
        }
    except OccurrenceEvidenceError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.post("/{candidate_id}/review")
def review_candidate(candidate_id: int, body: CandidateReviewRequest,
                     db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Record a human curation decision; this endpoint does not verify identity by itself."""
    candidate = db.query(PhytochemicalCandidate).filter(PhytochemicalCandidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(404, "Candidat phytochimique introuvable.")
    if body.evidence_grade in {"A", "B"} and (not body.study_reference or not body.assay_type):
        raise HTTPException(422, "Les grades A/B exigent une référence d'étude et un type d'essai documentés.")
    if body.endpoint_value is not None and not body.endpoint_unit:
        raise HTTPException(422, "Une unité est obligatoire avec une valeur d'endpoint.")
    candidate.evidence_grade = body.evidence_grade
    candidate.curator = admin.email
    candidate.reviewed_at = datetime.utcnow()
    candidate.review_note = body.curator_note.strip()
    candidate.plant_part = body.plant_part.strip() if body.plant_part else candidate.plant_part
    candidate.target_organism = body.target_organism.strip() if body.target_organism else candidate.target_organism
    candidate.target_accession = body.target_accession.strip() if body.target_accession else candidate.target_accession
    candidate.assay_type = body.assay_type.strip() if body.assay_type else candidate.assay_type
    fields_set = body.model_fields_set
    if "endpoint_value" in fields_set:
        candidate.endpoint_value = body.endpoint_value
    if "endpoint_unit" in fields_set:
        candidate.endpoint_unit = body.endpoint_unit.strip() if body.endpoint_unit else None
    if "study_reference" in fields_set:
        candidate.study_reference = body.study_reference.strip() if body.study_reference else None
    db.commit()
    return {"success": True, "candidate": candidate_to_dict(candidate),
            "note": "Revue humaine enregistrée; elle ne modifie pas automatiquement les statuts identité ou occurrence."}


@router.post("/{candidate_id}/descriptors")
def descriptors(candidate_id: int, db: Session = Depends(get_db),
                _admin: User = Depends(require_admin)):
    candidate = db.query(PhytochemicalCandidate).filter(PhytochemicalCandidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(404, "Candidat phytochimique introuvable.")
    try:
        result = compute_descriptors(candidate)
        db.commit()
        return {"success": True, "candidate_id": candidate.id, "descriptors": result}
    except Exception as exc:
        db.rollback()
        _service_error(exc)
