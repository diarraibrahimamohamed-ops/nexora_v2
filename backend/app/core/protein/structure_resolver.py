"""Automatic lightweight receptor resolution from a protein sequence.

RCSB sequence search is used first so an 8GB host does not need a local
structure-prediction stack. The selected structure and match metadata are
returned for reproducibility.

Robustesse ajoutée :
  - 3 tentatives avec backoff exponentiel (2s, 4s)
  - Gestion distincte 429 / 500 / timeout
  - Messages d'erreur diagnostiques
"""

import time
from typing import Dict, Optional, Tuple

from app.core.protein.io_utils import write_text_file

RCSB_SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
RCSB_PDB_URL    = "https://files.rcsb.org/download/{entry_id}.pdb"
RCSB_HEADERS    = {
    "User-Agent": "Nexora/2.0 (academic molecular docking)",
    "Accept": "application/json",
}

_MAX_RETRIES    = 3   # Tentatives pour la recherche RCSB
_RETRY_BASE_S   = 2   # Backoff de base (2s, 4s entre les retries)


def resolve_experimental_pdb(
    protein_sequence: str,
    output_dir: str,
    identity_cutoff: float = 0.9,
    evalue_cutoff: float = 1.0,
    timeout_seconds: float = 90.0,
) -> Tuple[Optional[str], Optional[str], Dict]:
    """Trouve et télécharge le meilleur match PDB expérimental RCSB.

    Retente jusqu'à _MAX_RETRIES fois en cas d'erreur serveur ou timeout.
    Returns:
        (chemin_pdb, erreur, métadonnées)
    """
    sequence = "".join(protein_sequence.split()).upper()
    if not sequence:
        return None, "Séquence protéique vide", {}
    if not 0 < identity_cutoff <= 1:
        return None, "identity_cutoff doit être dans ]0,1]", {}
    if len(sequence) < 20:
        return None, (
            f"Séquence trop courte pour la recherche RCSB: {len(sequence)} aa "
            f"(minimum conseillé: 20 aa). Passez directement à ESMFold "
            f"ou fournissez un PDB externe."
        ), {}

    # RCSB Search API n'accepte qu'un ensemble discret de cut-offs d'identité
    RCSB_IDENTITY_STEPS = (0.9, 0.7, 0.5, 0.3, 0.1)
    identity_used = min(RCSB_IDENTITY_STEPS, key=lambda v: abs(v - identity_cutoff))

    try:
        import httpx
    except ImportError:
        return None, "httpx est requis pour la recherche automatique RCSB", {}

    query = {
        "query": {
            "type": "terminal",
            "service": "sequence",
            "parameters": {
                "evalue_cutoff":   evalue_cutoff,
                "identity_cutoff": identity_used,
                "sequence_type":   "protein",
                "value":           sequence,
            },
        },
        "return_type": "polymer_entity",
        "request_options": {
            "results_content_type": ["experimental"],
            "results_verbosity":    "verbose",
            "paginate":             {"start": 0, "rows": 10},
            "scoring_strategy":     "sequence",
        },
    }

    last_error: str = "Recherche RCSB: échec sans détail"

    for attempt in range(_MAX_RETRIES):
        if attempt > 0:
            wait = _RETRY_BASE_S ** attempt   # 2s puis 4s
            print(
                f"[RCSB] Tentative {attempt + 1}/{_MAX_RETRIES} "
                f"dans {wait}s ({last_error})…",
                flush=True,
            )
            time.sleep(wait)

        try:
            with httpx.Client(
                timeout=timeout_seconds,
                follow_redirects=True,
                headers=RCSB_HEADERS,
            ) as client:

                # ── Recherche séquentielle ────────────────────────────────
                resp_search = client.post(RCSB_SEARCH_URL, json=query)
                resp_search.raise_for_status()

                # HTTP 204 No Content = aucun hit (spec RCSB Search API).
                # Un body vide en 200 est le même cas : ce n'est PAS retryable.
                body = resp_search.text
                if resp_search.status_code == 204 or not body or not body.strip():
                    return None, (
                        f"Aucun match expérimental RCSB pour la séquence "
                        f"(identity≥{identity_cutoff}, e-value≤{evalue_cutoff})"
                    ), {
                        "provider":           "RCSB PDB",
                        "identity_cutoff":    identity_used,
                        "identity_requested": identity_cutoff,
                        "evalue_cutoff":      evalue_cutoff,
                        "http_status":        resp_search.status_code,
                    }

                result_set = resp_search.json().get("result_set", [])
                if not result_set:
                    return None, (
                        f"Aucun match expérimental RCSB pour la séquence "
                        f"(identity≥{identity_cutoff}, e-value≤{evalue_cutoff})"
                    ), {
                        "provider":           "RCSB PDB",
                        "identity_cutoff":    identity_used,
                        "identity_requested": identity_cutoff,
                        "evalue_cutoff":      evalue_cutoff,
                    }

                hit = result_set[0]
                polymer_entity_id = hit.get("identifier", "")
                entry_id = polymer_entity_id.split("_", 1)[0].upper()
                if len(entry_id) != 4:
                    last_error = (
                        f"Identifiant RCSB inattendu: '{polymer_entity_id}' "
                        f"→ entry_id='{entry_id}'"
                    )
                    continue  # retry

                # ── Téléchargement PDB ────────────────────────────────────
                pdb_url = RCSB_PDB_URL.format(entry_id=entry_id)
                resp_pdb = client.get(pdb_url)
                resp_pdb.raise_for_status()

        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code == 429:
                last_error = (
                    f"RCSB: limite de requêtes atteinte (429). "
                    f"Attente avant retry…"
                )
                time.sleep(5)   # Pause supplémentaire pour le 429
                continue
            if code == 500:
                last_error = f"RCSB: erreur serveur interne (500) — tentative {attempt + 1}"
                continue
            if code in (400, 422):
                return None, (
                    f"RCSB: requête rejetée (HTTP {code}) — séquence trop courte "
                    f"ou caractères non canoniques. {exc}"
                ), {}
            # Erreur non-transiente → abandon immédiat
            return None, f"RCSB: erreur HTTP {code}: {exc}", {}

        except httpx.TimeoutException:
            last_error = (
                f"RCSB: timeout ({timeout_seconds}s) — tentative {attempt + 1}/{_MAX_RETRIES}. "
                f"Le serveur de recherche est peut-être surchargé."
            )
            continue

        except httpx.RequestError as exc:
            last_error = (
                f"RCSB: erreur réseau ({type(exc).__name__}): {exc} — "
                f"tentative {attempt + 1}/{_MAX_RETRIES}"
            )
            continue

        except ValueError as exc:
            last_error = f"RCSB: réponse JSON invalide: {exc}"
            continue

        destination = write_text_file(
            output_dir, f"rcsb_{entry_id}.pdb", resp_pdb.text
        )

        return destination, None, {
            "method":                 "automatic RCSB experimental sequence search",
            "provider":               "RCSB PDB Search API",
            "entry_id":               entry_id,
            "polymer_entity_id":      polymer_entity_id,
            "sequence_match_score":   hit.get("score"),
            "sequence_match_context": hit.get("services"),
            "identity_cutoff":        identity_used,
            "identity_requested":     identity_cutoff,
            "evalue_cutoff":          evalue_cutoff,
            "source_url":             pdb_url,
            "rcsb_attempts":          attempt + 1,
        }

    # Toutes les tentatives épuisées
    return None, (
        f"{last_error} | Toutes les {_MAX_RETRIES} tentatives RCSB ont épuisé "
        f"le backoff. Vérifiez que la séquence a une vraie entrée expérimentale "
        f"RCSB (≥ 20 aa), ou passez à structure_provider='external_pdb' / ESMFold."
    ), {}


# ---------------------------------------------------------------------------
# Computed structure models: RCSB sequence index → AlphaFold DB coordinates
# ---------------------------------------------------------------------------

def _request_with_retries(client, method: str, url: str, *, timeout_seconds: float, **kwargs):
    """Retry transient public-provider errors without hiding permanent failures."""
    import httpx

    last_error = None
    for attempt in range(3):
        try:
            response = getattr(client, method)(url, timeout=timeout_seconds, **kwargs)
            if response.status_code in {408, 429, 500, 502, 503, 504}:
                last_error = f"HTTP {response.status_code}"
                if attempt < 2:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = min(max(float(retry_after), 0.0), 10.0) if retry_after else float(2 ** attempt)
                    except ValueError:
                        delay = float(2 ** attempt)
                    time.sleep(delay)
                    continue
            response.raise_for_status()
            return response
        except httpx.TimeoutException as exc:
            last_error = f"timeout ({type(exc).__name__})"
            if attempt < 2:
                time.sleep(float(2 ** attempt))
                continue
            raise
        except httpx.RequestError as exc:
            last_error = f"réseau ({type(exc).__name__})"
            if attempt < 2:
                time.sleep(float(2 ** attempt))
                continue
            raise
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code not in {408, 429, 500, 502, 503, 504} or attempt >= 2:
                raise
            last_error = f"HTTP {code}"
            time.sleep(float(2 ** attempt))
    raise RuntimeError(f"Échecs transitoires après 3 tentatives: {last_error or 'inconnu'}")


def _afdb_accession_from_csm_id(csm_id: str):
    """Map RCSB's AF_AFB3EWR1F1 CSM (or AF_AFB3EWR1F1_1 entity) to AFDB ID."""
    import re

    normalized = str(csm_id or "").strip().upper()
    # RCSB entry IDs are AF_A..., while polymer-entity IDs may append _<entity>.
    if normalized.startswith("AF_"):
        normalized = re.sub(r"_[0-9]+$", "", normalized)
    match = re.fullmatch(r"AF_AF(?P<accession>[A-Z0-9-]+)(?P<fragment>F[0-9]+)", normalized)
    if not match:
        return None
    accession = match.group("accession")
    fragment = match.group("fragment")
    return accession, f"AF-{accession}-{fragment}"


def resolve_computed_alphafold_model(
    protein_sequence: str,
    output_dir: str,
    identity_cutoff: float = 0.70,
    coverage_cutoff: float = 0.70,
    evalue_cutoff: float = 10.0,
    timeout_seconds: float = 60.0,
    max_candidates: int = 8,
) -> Tuple[Optional[str], Optional[str], Dict]:
    """Resolve an AlphaFold DB computed model through RCSB sequence search.

    RCSB provides the sequence-search index over computational models (CSMs);
    coordinates and the current versioned CIF URL are retrieved from the
    AlphaFold DB prediction API. ModelArchive (MA_*) identifiers are skipped
    explicitly; they are not silently treated as AlphaFold DB records.
    """
    import httpx
    from urllib.parse import urlparse

    from app.core.protein.io_utils import write_text_file
    from app.core.protein.structure_formats import normalize_mmcif_to_pdb
    from app.core.protein.structure_validation import validate_structure

    sequence = "".join((protein_sequence or "").split()).upper()
    if not sequence:
        return None, "Séquence protéique vide.", {}
    if len(sequence) < 20:
        return None, "Séquence trop courte pour la recherche de modèles calculés RCSB (minimum conseillé : 20 aa).", {}
    if not 0 < identity_cutoff <= 1 or not 0 < coverage_cutoff <= 1:
        return None, "Seuils d'identité/couverture invalides.", {}
    if max_candidates < 1:
        return None, "max_candidates doit être positif.", {}

    # Search API supports a distinct computational content type. This prevents
    # AlphaFold predictions from being labelled as experimental PDB structures.
    query = {
        "query": {
            "type": "terminal",
            "service": "sequence",
            "parameters": {
                "evalue_cutoff": evalue_cutoff,
                "identity_cutoff": 0.5,
                "sequence_type": "protein",
                "value": sequence,
            },
        },
        # Use the CSM entry ID (AF_...), not a polymer-entity ID (..._1).
        "return_type": "entry",
        "request_options": {
            "results_content_type": ["computational"],
            "results_verbosity": "verbose",
            "paginate": {"start": 0, "rows": min(max_candidates * 2, 20)},
            "scoring_strategy": "sequence",
        },
    }
    metadata = {
        "provider": "AlphaFold Protein Structure Database",
        "search_provider": "RCSB PDB Search API",
        "source_type": "predicted_alphafold_db",
        "model_class": "computed_structure_model",
        "identity_cutoff": identity_cutoff,
        "coverage_cutoff": coverage_cutoff,
        "evalue_cutoff": evalue_cutoff,
        "candidates_checked": [],
        "modelarchive_candidates_skipped": 0,
    }
    headers = {
        "User-Agent": "Nexora/2.0 (academic bioinformatics structure resolver)",
        "Accept": "application/json",
    }
    best = None
    last_candidate_error = None

    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True, headers=headers) as client:
            search_response = _request_with_retries(
                client, "post", RCSB_SEARCH_URL,
                timeout_seconds=timeout_seconds, json=query,
            )
            if search_response.status_code == 204 or not search_response.text.strip():
                return None, "Aucun modèle calculé RCSB/AlphaFold DB ne correspond à cette séquence.", metadata
            search_payload = search_response.json()
            hits = search_payload.get("result_set", []) if isinstance(search_payload, dict) else []
            if not hits:
                return None, "Aucun modèle calculé RCSB/AlphaFold DB ne correspond à cette séquence.", metadata

            for hit in hits[:max_candidates]:
                csm_id = str(hit.get("identifier") or "").upper()
                parsed_id = _afdb_accession_from_csm_id(csm_id)
                if parsed_id is None:
                    if csm_id.startswith("MA_"):
                        metadata["modelarchive_candidates_skipped"] += 1
                    continue

                uniprot_accession, expected_model_id = parsed_id
                try:
                    api_response = _request_with_retries(
                        client,
                        "get",
                        f"https://alphafold.ebi.ac.uk/api/prediction/{uniprot_accession}",
                        timeout_seconds=timeout_seconds,
                    )
                    records = api_response.json()
                    if not isinstance(records, list):
                        raise ValueError("Réponse AlphaFold API inattendue (liste attendue).")

                    record = next((
                        item for item in records
                        if isinstance(item, dict)
                        and str(item.get("modelEntityId") or item.get("entryId") or "").upper() == expected_model_id
                    ), None)
                    if record is None:
                        # A prediction API may expose model fragments under new
                        # modelEntityId; use sequence interval only as a guarded
                        # fallback if the identifier matches the requested CSM.
                        record = next((
                            item for item in records
                            if isinstance(item, dict)
                            and str(item.get("modelEntityId") or item.get("entryId") or "").upper().replace("-", "")
                            == expected_model_id.replace("-", "")
                        ), None)
                    if record is None:
                        raise ValueError(f"Modèle {expected_model_id} absent de la réponse AlphaFold API.")

                    cif_url = record.get("cifUrl")
                    parsed_url = urlparse(str(cif_url or ""))
                    if (
                        parsed_url.scheme != "https"
                        or parsed_url.hostname != "alphafold.ebi.ac.uk"
                        or parsed_url.username
                        or parsed_url.password
                    ):
                        raise ValueError("URL CIF AlphaFold absente ou hors domaine autorisé.")

                    cif_response = _request_with_retries(
                        client, "get", cif_url, timeout_seconds=timeout_seconds,
                    )
                    cif_bytes = cif_response.content
                    if len(cif_bytes) > 25 * 1024 * 1024:
                        raise ValueError("Fichier mmCIF AlphaFold dépasse la limite de 25 MiB.")
                    cif_text = cif_bytes.decode("utf-8-sig", errors="strict")
                    if "_atom_site." not in cif_text[:500000]:
                        raise ValueError("Le mmCIF AlphaFold ne contient pas de table atom_site.")

                    safe_model_name = expected_model_id.replace("-", "_")
                    cif_path = write_text_file(output_dir, f"alphafold_db_{safe_model_name}.cif", cif_text)
                    pdb_path, format_metadata = normalize_mmcif_to_pdb(cif_path)
                    report = validate_structure(
                        pdb_path,
                        sequence,
                        "predicted_alphafold_db",
                        identity_cutoff=identity_cutoff,
                        coverage_cutoff=coverage_cutoff,
                    )
                    match = report.get("sequence_match", {}).get("best_chain", {})
                    global_confidence = record.get("globalMetricValue")
                    try:
                        global_confidence = float(global_confidence) if global_confidence is not None else None
                    except (TypeError, ValueError):
                        global_confidence = None

                    candidate_metadata = {
                        **metadata,
                        **format_metadata,
                        "csm_id": csm_id,
                        "model_entity_id": record.get("modelEntityId") or record.get("entryId") or expected_model_id,
                        "uniprot_accession": uniprot_accession,
                        "model_version": record.get("latestVersion"),
                        "sequence_start": record.get("sequenceStart") or record.get("uniprotStart"),
                        "sequence_end": record.get("sequenceEnd") or record.get("uniprotEnd"),
                        "model_sequence_length": len(record.get("sequence") or record.get("uniprotSequence") or ""),
                        "global_plddt": global_confidence,
                        "source_url": cif_url,
                        "rcsb_search_score": hit.get("score"),
                        "sequence_validation": report,
                        "resolved_provider": "alphafold_db",
                    }
                    metadata["candidates_checked"].append({
                        "csm_id": csm_id,
                        "identity": match.get("identity"),
                        "coverage": match.get("coverage"),
                        "eligible": report.get("eligible_for_docking_review", False),
                    })
                    if not report.get("eligible_for_docking_review"):
                        last_candidate_error = (
                            f"{csm_id}: identité={match.get('identity')} couverture={match.get('coverage')}"
                        )
                        continue

                    rank = (
                        float(match.get("identity") or 0) * float(match.get("coverage") or 0),
                        float(match.get("coverage") or 0),
                        float(match.get("identity") or 0),
                        global_confidence if global_confidence is not None else -1.0,
                    )
                    if best is None or rank > best[0]:
                        best = (rank, pdb_path, candidate_metadata)
                    if rank[0] >= 0.999:
                        break
                except (httpx.HTTPError, ValueError, KeyError, OSError, UnicodeError, RuntimeError) as exc:
                    last_candidate_error = f"{csm_id}: {type(exc).__name__}: {exc}"
                    metadata["candidates_checked"].append({
                        "csm_id": csm_id,
                        "error_type": type(exc).__name__,
                    })
                    continue

    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        return None, f"RCSB/AlphaFold DB: erreur HTTP {code}.", metadata
    except httpx.TimeoutException:
        return None, f"RCSB/AlphaFold DB: timeout après {timeout_seconds:.0f}s.", metadata
    except httpx.RequestError as exc:
        return None, f"RCSB/AlphaFold DB: erreur réseau ({type(exc).__name__}).", metadata
    except (ValueError, KeyError) as exc:
        return None, f"RCSB/AlphaFold DB: réponse invalide ({type(exc).__name__}).", metadata

    if best is None:
        if metadata["modelarchive_candidates_skipped"] and not metadata["candidates_checked"]:
            detail = "RCSB a renvoyé des modèles ModelArchive (MA_) que ce connecteur ne télécharge pas encore."
        else:
            detail = last_candidate_error or "aucun modèle AlphaFold DB compatible n'a été trouvé."
        return None, f"Aucun modèle calculé AlphaFold DB validable pour cette cible : {detail}", metadata

    _, selected_path, selected_metadata = best
    return selected_path, None, selected_metadata
