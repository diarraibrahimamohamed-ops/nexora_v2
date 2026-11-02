"""Read-only, licence-aware registry for external PhytoEvidence sources.

This registry describes source scope, rights and ingestion decisions. It does not
download data, grant permissions, or upgrade evidence attached to candidates.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Optional


_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "phytochemical_sources.json"
)

_RIGHTS_STATUSES = {"verified", "record_specific", "permission_required", "unverified"}
_DECISIONS = {
    "eligible_for_schema_audit",
    "record_level_rights_gate",
    "noncommercial_only",
    "reference_only_until_permission",
    "blocked_pending_rights_review",
    "manual_upload_rights_review",
}


class SourceRegistryError(RuntimeError):
    """Raised when the packaged source registry is missing or invalid."""


@lru_cache(maxsize=1)
def _load_registry() -> dict:
    try:
        payload = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceRegistryError(
            "Le registre des sources PhytoEvidence est indisponible ou invalide."
        ) from exc

    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0.0":
        raise SourceRegistryError("Version de schéma du registre PhytoEvidence non prise en charge.")

    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise SourceRegistryError("Le registre PhytoEvidence ne contient aucune source.")

    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise SourceRegistryError("Une entrée du registre n'est pas un objet.")

        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip() or source_id in seen:
            raise SourceRegistryError("Identifiant de source absent ou dupliqué.")
        seen.add(source_id)

        if not isinstance(source.get("name"), str) or not source["name"].strip():
            raise SourceRegistryError(f"Nom manquant pour {source_id}.")
        if not isinstance(source.get("domains"), list) or not source["domains"]:
            raise SourceRegistryError(f"Domaines manquants pour {source_id}.")

        rights = source.get("rights")
        ingestion = source.get("ingestion")
        if not isinstance(rights, dict) or rights.get("status") not in _RIGHTS_STATUSES:
            raise SourceRegistryError(f"Statut des droits absent ou invalide pour {source_id}.")
        if not isinstance(ingestion, dict):
            raise SourceRegistryError(f"Politique d'ingestion manquante pour {source_id}.")
        if ingestion.get("decision") not in _DECISIONS:
            raise SourceRegistryError(f"Décision d'ingestion invalide pour {source_id}.")

        # This first release is deliberately descriptive: no endpoint is allowed
        # to trigger a network download or to import a source implicitly.
        if ingestion.get("automatic_download") is not False:
            raise SourceRegistryError(f"Téléchargement automatique interdit dans le registre initial ({source_id}).")

        if rights["status"] == "verified" and not rights.get("license_expression"):
            raise SourceRegistryError(f"Licence vérifiée sans expression SPDX pour {source_id}.")

        if rights["status"] == "record_specific" and ingestion["decision"] != "record_level_rights_gate":
            raise SourceRegistryError(f"Les droits par enregistrement doivent conserver un gate dédié ({source_id}).")

        if rights["status"] in {"unverified", "permission_required"} and ingestion["decision"] not in {
            "blocked_pending_rights_review",
            "reference_only_until_permission",
            "manual_upload_rights_review",
        }:
            raise SourceRegistryError(f"Source {source_id} intégrable malgré des droits non résolus.")

        for key in ("commercial_reuse", "adaptation", "redistribution", "attribution"):
            if key not in rights:
                raise SourceRegistryError(f"Champ de droits {key} absent pour {source_id}.")

        requirements = ingestion.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            raise SourceRegistryError(f"Exigences de provenance manquantes pour {source_id}.")

    return payload


def get_source_registry(domain: Optional[str] = None) -> dict:
    """Return a defensive copy of the source registry, optionally filtered by domain."""
    payload = copy.deepcopy(_load_registry())
    sources = payload["sources"]

    if domain:
        normalized = domain.strip().casefold()
        if not normalized:
            raise ValueError("Le filtre domain ne peut pas être vide.")
        sources = [
            item for item in sources
            if normalized in {str(value).casefold() for value in item.get("domains", [])}
        ]

    payload["sources"] = sources
    payload["total"] = len(sources)
    if domain:
        payload["domain_filter"] = domain
    return payload


def clear_source_registry_cache() -> None:
    """Clear the cache for tests or a controlled deployment reload."""
    _load_registry.cache_clear()
