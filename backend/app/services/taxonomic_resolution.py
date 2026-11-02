"""WCVP-backed botanical-name resolution with human review and provenance.

Name resolution is nomenclatural support only. It does not identify a physical
specimen, prove a plant-compound occurrence, or establish bioactivity/efficacy.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.lcmsms import BotanicalSpecimen
from app.models.phytochemistry import PhytochemicalCandidate, TaxonResolution


WCVP_CHECKLIST_KEY = "f382f0ce-323a-4091-bb9f-add557f3a9a2"
WCVP_DATASET_DOI = "10.15468/6h8ucr"
WCVP_DATASET_VERSION = "WCVP v16 (2026-06-04)"
WCVP_PUBLISHER_DOI = "10.34885/egs6-cp24"
GBIF_MATCH_URL = "https://api.gbif.org/v2/species/match"
PROVIDER = "GBIF ChecklistBank Species Match API v2 / WCVP"
ACCEPTABLE_PLANT_RANKS = {"SPECIES", "SUBSPECIES", "VARIETY", "SUBVARIETY", "FORM"}
ACCEPTABLE_MATCH_TYPES = {"EXACT", "VARIANT"}
ACCEPTABLE_MATCHED_STATUSES = {
    "ACCEPTED", "SYNONYM", "HETEROTYPIC_SYNONYM", "HOMOTYPIC_SYNONYM",
}


class TaxonomyResolutionError(ValueError):
    """Invalid or scientifically ineligible taxonomic resolution."""


class ExternalTaxonomyError(RuntimeError):
    """WCVP service is unavailable and no exact cached response exists."""


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cache_path(query: str) -> Path:
    cache_key = "|".join((WCVP_CHECKLIST_KEY, WCVP_DATASET_VERSION, query.strip().casefold()))
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    return Path(settings.AFRICAN_LIBS_DIR) / "reference_cache" / "wcvp" / (digest + ".json")


def _write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(path.parent), suffix=".part", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            handle.flush()
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def _read_cache(path: Path) -> Optional[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _classification_value(payload: dict, usage: dict, rank: str) -> Optional[str]:
    value = usage.get(rank.casefold()) or payload.get(rank.casefold())
    if value:
        return str(value)
    classification = payload.get("classification") or usage.get("classification") or []
    for item in classification:
        if str(item.get("rank", "")).upper() == rank.upper():
            return str(item.get("name") or "")
    return None


def taxon_resolution_to_dict(row: TaxonResolution) -> dict:
    return {
        "id": row.id,
        "candidate_id": row.candidate_id,
        "specimen_id": row.specimen_id,
        "requested_name": row.requested_name,
        "matched_name": row.matched_name,
        "matched_identifier": row.matched_identifier,
        "matched_status": row.matched_status,
        "matched_rank": row.matched_rank,
        "accepted_name": row.accepted_name,
        "accepted_identifier": row.accepted_identifier,
        "accepted_status": row.accepted_status,
        "accepted_rank": row.accepted_rank,
        "kingdom": row.kingdom,
        "family": row.family,
        "match_type": row.match_type,
        "confidence": row.confidence,
        "provider": row.provider,
        "checklist_key": row.checklist_key,
        "dataset_doi": row.dataset_doi,
        "dataset_version": row.dataset_version,
        "source_mode": row.source_mode,
        "source_url": row.source_url,
        "response_sha256": row.response_sha256,
        "response_payload": row.response_payload or {},
        "review_status": row.review_status,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_note": row.review_note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def resolve_botanical_taxon(
    db: Session,
    scientific_name: str,
    *,
    candidate_id: Optional[int] = None,
    specimen_id: Optional[int] = None,
) -> TaxonResolution:
    """Create a WCVP resolution proposal; do not change specimen/occurrence status."""
    query = (scientific_name or "").strip()
    if len(query) < 2 or len(query) > 255:
        raise TaxonomyResolutionError("Le nom scientifique doit contenir entre 2 et 255 caractères.")
    if (candidate_id is None) == (specimen_id is None):
        raise TaxonomyResolutionError(
            "La résolution doit viser exactement un candidat phytochimique ou un spécimen botanique."
        )
    if candidate_id is not None and db.get(PhytochemicalCandidate, candidate_id) is None:
        raise TaxonomyResolutionError("Candidat phytochimique introuvable.")
    if specimen_id is not None and db.get(BotanicalSpecimen, specimen_id) is None:
        raise TaxonomyResolutionError("Spécimen botanique introuvable.")

    cache_path = _cache_path(query)
    source_mode = "LIVE"
    response_bundle = None
    try:
        response = httpx.get(
            GBIF_MATCH_URL,
            params={
                "scientificName": query,
                "checklistKey": WCVP_CHECKLIST_KEY,
                "kingdom": "Plantae",
            },
            timeout=20.0,
            headers={"User-Agent": "N3XORA-Sahel-BioEvidence/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Réponse JSON GBIF invalide.")
        response_bundle = {
            "provider": PROVIDER,
            "query": query,
            "checklist_key": WCVP_CHECKLIST_KEY,
            "dataset_doi": WCVP_DATASET_DOI,
            "dataset_version": WCVP_DATASET_VERSION,
            "publisher_doi": WCVP_PUBLISHER_DOI,
            "fetched_at_utc": datetime.utcnow().isoformat() + "Z",
            "response": payload,
        }
        try:
            _write_cache(cache_path, response_bundle)
        except OSError:
            pass
    except (httpx.HTTPError, ValueError) as exc:
        cached = _read_cache(cache_path)
        if (
            cached
            and str(cached.get("query", "")).casefold() == query.casefold()
            and cached.get("checklist_key") == WCVP_CHECKLIST_KEY
            and cached.get("dataset_version") == WCVP_DATASET_VERSION
            and isinstance(cached.get("response"), dict)
        ):
            response_bundle = cached
            payload = cached["response"]
            source_mode = "CACHE"
        else:
            raise ExternalTaxonomyError(
                "Le service WCVP/GBIF est indisponible et aucun cache exact n'existe: "
                + type(exc).__name__
            ) from exc

    # ChecklistBank Species Match v2 wraps the matched NameUsage in "usage";
    # synonym matches may additionally expose the accepted name usage.
    usage = payload.get("usage") or {}
    accepted_usage = payload.get("acceptedUsage") or {}
    matched_status = str(usage.get("status") or "").upper() or None
    if matched_status == "ACCEPTED":
        accepted_usage = accepted_usage or usage
    elif matched_status not in ACCEPTABLE_MATCHED_STATUSES:
        accepted_usage = {}

    accepted_name = (
        str(accepted_usage.get("name") or accepted_usage.get("canonicalName") or "").strip()
        or None
    )
    accepted_key = str(accepted_usage.get("key") or "").strip() or None
    accepted_status = str(accepted_usage.get("status") or "").upper() or None
    matched_name = str(usage.get("name") or usage.get("canonicalName") or "").strip() or None
    matched_key = str(usage.get("key") or "").strip() or None
    matched_rank = str(usage.get("rank") or "").upper() or None
    accepted_rank = str(accepted_usage.get("rank") or matched_rank or "").upper() or None

    diagnostics = payload.get("diagnostics") or {}
    match_type = str(diagnostics.get("matchType") or "NONE").upper()
    confidence_raw = diagnostics.get("confidence", usage.get("confidence"))
    confidence = int(confidence_raw) if isinstance(confidence_raw, (int, float)) else None
    kingdom = _classification_value(payload, usage, "KINGDOM")
    family = _classification_value(payload, usage, "FAMILY")

    valid_proposal = bool(
        matched_key
        and matched_status in ACCEPTABLE_MATCHED_STATUSES
        and accepted_name
        and accepted_key
        and accepted_status == "ACCEPTED"
        and (kingdom or "").casefold() == "plantae"
        and accepted_rank in ACCEPTABLE_PLANT_RANKS
        and match_type in ACCEPTABLE_MATCH_TYPES
    )
    source_url = GBIF_MATCH_URL + "?" + urlencode({
        "scientificName": query,
        "checklistKey": WCVP_CHECKLIST_KEY,
        "kingdom": "Plantae",
    })
    response_sha256 = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()

    row = TaxonResolution(
        candidate_id=candidate_id,
        specimen_id=specimen_id,
        requested_name=query,
        matched_name=matched_name,
        matched_identifier=matched_key,
        matched_status=matched_status,
        matched_rank=matched_rank,
        accepted_name=accepted_name if valid_proposal else None,
        accepted_identifier=accepted_key if valid_proposal else None,
        accepted_status=accepted_status,
        accepted_rank=accepted_rank,
        kingdom=kingdom,
        family=family,
        match_type=match_type,
        confidence=confidence,
        provider=PROVIDER,
        checklist_key=WCVP_CHECKLIST_KEY,
        dataset_doi=WCVP_DATASET_DOI,
        dataset_version=WCVP_DATASET_VERSION,
        source_mode=source_mode,
        source_url=source_url,
        response_sha256=response_sha256,
        response_payload=response_bundle,
        review_status="pending_review" if valid_proposal else "unresolved",
    )
    db.add(row)
    db.flush()
    return row


def review_taxon_resolution(
    db: Session,
    row: TaxonResolution,
    *,
    decision: str,
    reviewer: str,
    rationale: str,
) -> TaxonResolution:
    """Review nomenclature without verifying specimen identity or compound occurrence."""
    decision = (decision or "").strip().lower()
    reviewer = (reviewer or "").strip()
    rationale = (rationale or "").strip()
    if decision not in {"accept", "reject"}:
        raise TaxonomyResolutionError("La décision doit être accept ou reject.")
    if not reviewer or len(rationale) < 10:
        raise TaxonomyResolutionError("Identité du réviseur et justification d'au moins 10 caractères requises.")
    if row.review_status != "pending_review":
        raise TaxonomyResolutionError("Cette résolution n'est plus en attente de revue.")
    if decision == "accept":
        if (
            not row.accepted_name
            or not row.accepted_identifier
            or row.accepted_status != "ACCEPTED"
            or (row.kingdom or "").casefold() != "plantae"
            or row.accepted_rank not in ACCEPTABLE_PLANT_RANKS
            or row.match_type not in ACCEPTABLE_MATCH_TYPES
        ):
            raise TaxonomyResolutionError(
                "Seul un taxon Plantae accepté au rang espèce/infraspécifique et issu d'une correspondance stricte peut être accepté."
            )

        if row.candidate_id is not None:
            candidate = db.get(PhytochemicalCandidate, row.candidate_id)
            if candidate is None:
                raise TaxonomyResolutionError("Candidat lié introuvable.")
            candidate.accepted_taxon = row.accepted_name
            candidate.taxon_identifier = "WCVP:" + row.accepted_identifier
            candidate.taxonomy_resolution_source = "WCVP_VIA_GBIF_CHECKLISTBANK"
            candidate.taxonomy_cache_sha256 = row.response_sha256
        else:
            specimen = db.get(BotanicalSpecimen, row.specimen_id)
            if specimen is None:
                raise TaxonomyResolutionError("Spécimen lié introuvable.")
            specimen.accepted_taxon = row.accepted_name
            specimen.taxon_identifier = "WCVP:" + row.accepted_identifier
            specimen.taxonomy_source = "WCVP via GBIF ChecklistBank Species Match API v2"
            # Keep the specimen's independent physical-identification status unchanged.
            references = list(specimen.evidence_references or [])
            references.append({
                "kind": "botanical_taxon_name_resolution",
                "provider": row.provider,
                "requested_name": row.requested_name,
                "accepted_name": row.accepted_name,
                "accepted_identifier": "WCVP:" + row.accepted_identifier,
                "checklist_key": row.checklist_key,
                "dataset_doi": row.dataset_doi,
                "dataset_version": row.dataset_version,
                "source_url": row.source_url,
                "response_sha256": row.response_sha256,
                "source_mode": row.source_mode,
            })
            specimen.evidence_references = references

    row.review_status = "accepted" if decision == "accept" else "rejected"
    row.reviewed_by = reviewer
    row.reviewed_at = datetime.utcnow()
    row.review_note = rationale
    return row
