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
RCSB_CIF_URL    = "https://files.rcsb.org/download/{entry_id}.cif"
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
    coverage_cutoff: float = 0.70,
    acceptance_identity_cutoff: Optional[float] = None,
    max_candidates: int = 8,
) -> Tuple[Optional[str], Optional[str], Dict]:
    """Find the best experimentally determined RCSB chain for a target.

    The RCSB query can be made more permissive than the acceptance gate.
    Every candidate is downloaded and aligned locally; a hit is returned only
    when its best chain passes the configured identity and coverage thresholds.
    Candidates are ranked by actual alignment quality, not only the first
    search-result position.

    Returns (local PDB path, error, provenance/validation metadata).
    """
    import httpx

    from app.core.protein.io_utils import write_text_file
    from app.core.protein.structure_formats import normalize_mmcif_to_pdb
    from app.core.protein.structure_validation import validate_structure

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
    if not 0 < coverage_cutoff <= 1:
        return None, "coverage_cutoff doit être dans ]0,1]", {}
    minimum_identity = (
        identity_cutoff
        if acceptance_identity_cutoff is None
        else acceptance_identity_cutoff
    )
    if not 0 < minimum_identity <= 1:
        return None, "acceptance_identity_cutoff doit être dans ]0,1]", {}
    if max_candidates < 1 or max_candidates > 20:
        return None, "max_candidates doit être compris entre 1 et 20", {}

    # RCSB Search API accepts discrete identity cut-offs. Keep the query
    # threshold separate from the local acceptance threshold: a permissive
    # discovery query must never lower the scientific acceptance gate.
    RCSB_IDENTITY_STEPS = (0.9, 0.7, 0.5, 0.3, 0.1)
    identity_used = min(RCSB_IDENTITY_STEPS, key=lambda v: abs(v - identity_cutoff))
    query = {
        "query": {
            "type": "terminal",
            "service": "sequence",
            "parameters": {
                "evalue_cutoff": evalue_cutoff,
                "identity_cutoff": identity_used,
                "sequence_type": "protein",
                "value": sequence,
            },
        },
        "return_type": "polymer_entity",
        "request_options": {
            "results_content_type": ["experimental"],
            "results_verbosity": "verbose",
            "paginate": {"start": 0, "rows": min(max_candidates * 2, 20)},
            "scoring_strategy": "sequence",
        },
    }
    metadata = {
        "provider": "RCSB PDB Search API",
        "method": "automatic experimental sequence search with candidate validation",
        "identity_cutoff": identity_used,
        "identity_requested": identity_cutoff,
        "acceptance_identity_cutoff": minimum_identity,
        "coverage_cutoff": coverage_cutoff,
        "evalue_cutoff": evalue_cutoff,
        "candidates_checked": [],
        "candidate_count": 0,
    }

    last_error = "Recherche RCSB: échec sans détail"
    for attempt in range(_MAX_RETRIES):
        if attempt:
            wait = _RETRY_BASE_S ** attempt
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
                # Search is retried as a unit for transient HTTP/network errors.
                resp_search = client.post(RCSB_SEARCH_URL, json=query)
                if resp_search.status_code == 204 or not resp_search.text.strip():
                    metadata["failure_category"] = "no_experimental_hit"
                    metadata["http_status"] = resp_search.status_code
                    return None, (
                        f"Aucun match expérimental RCSB pour la séquence "
                        f"(recherche identity≥{identity_cutoff}, "
                        f"acceptation identity≥{minimum_identity}, "
                        f"coverage≥{coverage_cutoff})"
                    ), metadata
                resp_search.raise_for_status()

                payload = resp_search.json()
                result_set = payload.get("result_set", []) if isinstance(payload, dict) else []
                if not isinstance(result_set, list) or not result_set:
                    metadata["failure_category"] = "no_experimental_hit"
                    return None, (
                        f"Aucun match expérimental RCSB pour la séquence "
                        f"(recherche identity≥{identity_cutoff}, "
                        f"acceptation identity≥{minimum_identity}, "
                        f"coverage≥{coverage_cutoff})"
                    ), metadata

                # Group chain-level hits by entry because the downloaded PDB/mmCIF
                # contains all chains. Keep the highest-scoring hit per entry.
                grouped_hits = {}
                for hit in result_set:
                    if not isinstance(hit, dict):
                        continue
                    polymer_entity_id = str(hit.get("identifier") or "").strip()
                    entry_id = polymer_entity_id.split("_", 1)[0].upper()
                    if len(entry_id) != 4 or not entry_id.isalnum():
                        continue
                    score = hit.get("score")
                    try:
                        score = float(score) if score is not None else -1.0
                    except (TypeError, ValueError):
                        score = -1.0
                    existing = grouped_hits.get(entry_id)
                    if existing is None or score > existing.get("_score", -1.0):
                        grouped_hits[entry_id] = {
                            **hit,
                            "_score": score,
                            "_polymer_entity_id": polymer_entity_id,
                        }

                candidates = sorted(
                    grouped_hits.items(),
                    key=lambda item: item[1].get("_score", -1.0),
                    reverse=True,
                )[:max_candidates]
                metadata["candidate_count"] = len(candidates)
                if not candidates:
                    metadata["failure_category"] = "invalid_search_identifiers"
                    return None, "RCSB a renvoyé des identifiants d'entrées expérimentales non exploitables.", metadata

                best = None
                candidate_errors = []
                for entry_id, hit in candidates:
                    polymer_entity_id = hit["_polymer_entity_id"]
                    score = hit.get("_score", -1.0)
                    pdb_url = RCSB_PDB_URL.format(entry_id=entry_id)
                    cif_url = RCSB_CIF_URL.format(entry_id=entry_id)
                    format_metadata = {
                        "source_format": "pdb",
                        "normalized_format": "pdb",
                    }

                    # Prefer legacy PDB where available; large or newer entries
                    # may only expose mmCIF, which is normalized for current
                    # PDB-only validation/docking consumers.
                    try:
                        resp_structure = _request_with_retries(
                            client, "get", pdb_url, timeout_seconds=timeout_seconds,
                        )
                        structure_text = resp_structure.text
                        if not any(
                            line.startswith(("ATOM  ", "HETATM"))
                            for line in structure_text.splitlines()
                        ):
                            raise ValueError("Le fichier PDB RCSB ne contient aucun atome.")
                        local_path = write_text_file(
                            output_dir, f"rcsb_{entry_id}.pdb", structure_text
                        )
                        source_url = pdb_url
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code not in {400, 404, 406, 415}:
                            raise
                        resp_cif = _request_with_retries(
                            client, "get", cif_url, timeout_seconds=timeout_seconds,
                        )
                        cif_text = resp_cif.text
                        if "_atom_site." not in cif_text[:500000]:
                            raise ValueError("Le mmCIF RCSB ne contient pas de table atom_site.")
                        cif_path = write_text_file(
                            output_dir, f"rcsb_{entry_id}.cif", cif_text
                        )
                        local_path, format_metadata = normalize_mmcif_to_pdb(cif_path)
                        source_url = cif_url
                    except ValueError as exc:
                        # An empty/malformed PDB can sometimes coexist with a
                        # valid mmCIF coordinate file for the same entry.
                        if "PDB RCSB" not in str(exc):
                            raise
                        resp_cif = _request_with_retries(
                            client, "get", cif_url, timeout_seconds=timeout_seconds,
                        )
                        cif_text = resp_cif.text
                        if "_atom_site." not in cif_text[:500000]:
                            raise ValueError("Le mmCIF RCSB ne contient pas de table atom_site.")
                        cif_path = write_text_file(
                            output_dir, f"rcsb_{entry_id}.cif", cif_text
                        )
                        local_path, format_metadata = normalize_mmcif_to_pdb(cif_path)
                        source_url = cif_url

                    report = validate_structure(
                        local_path,
                        sequence,
                        "experimental_pdb",
                        identity_cutoff=minimum_identity,
                        coverage_cutoff=coverage_cutoff,
                    )
                    match = report.get("sequence_match", {}).get("best_chain", {})
                    identity = float(match.get("identity") or 0.0)
                    coverage = float(match.get("coverage") or 0.0)
                    eligible = bool(report.get("eligible_for_docking_review"))
                    candidate_result = {
                        "entry_id": entry_id,
                        "polymer_entity_id": polymer_entity_id,
                        "search_score": score,
                        "identity": identity,
                        "coverage": coverage,
                        "eligible": eligible,
                        "format": format_metadata.get("source_format"),
                    }
                    metadata["candidates_checked"].append(candidate_result)

                    if not eligible:
                        candidate_errors.append(
                            f"{entry_id}: identité={identity:.3f}, couverture={coverage:.3f}"
                        )
                        continue

                    rank = (identity * coverage, coverage, identity, score)
                    if best is None or rank > best[0]:
                        selected_metadata = {
                            "method": "automatic RCSB experimental sequence search",
                            "provider": "RCSB PDB Search API",
                            "entry_id": entry_id,
                            "polymer_entity_id": polymer_entity_id,
                            "sequence_match_score": score if score >= 0 else None,
                            "identity_cutoff": identity_used,
                            "identity_requested": identity_cutoff,
                            "acceptance_identity_cutoff": minimum_identity,
                            "coverage_cutoff": coverage_cutoff,
                            "evalue_cutoff": evalue_cutoff,
                            "source_url": source_url,
                            "rcsb_attempts": attempt + 1,
                            "sequence_validation": report,
                            **format_metadata,
                        }
                        best = (rank, local_path, selected_metadata)

                    # Stop on a full-length, exact match: later candidates
                    # cannot improve sequence correspondence.
                    if identity >= 0.999 and coverage >= 0.999:
                        break

                if best is not None:
                    _, selected_path, selected_metadata = best
                    selected_metadata["candidates_checked"] = metadata["candidates_checked"]
                    selected_metadata["candidate_count"] = metadata["candidate_count"]
                    selected_metadata["candidate_ranking"] = (
                        "sequence identity × target coverage, then coverage, identity, RCSB search score"
                    )
                    return selected_path, None, selected_metadata

                metadata["failure_category"] = "no_candidate_passed_sequence_validation"
                detail = "; ".join(candidate_errors[:5]) or "aucun candidat ne contenait une chaîne protéique exploitable."
                return None, (
                    "Aucun candidat expérimental RCSB n'a franchi les seuils locaux "
                    f"(identity≥{minimum_identity}, coverage≥{coverage_cutoff}). {detail}"
                ), metadata

        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in {408, 429, 500, 502, 503, 504} and attempt < _MAX_RETRIES - 1:
                last_error = f"RCSB: erreur HTTP transitoire {code}"
                retry_after = exc.response.headers.get("Retry-After")
                try:
                    delay = min(max(float(retry_after), 0.0), 15.0) if retry_after else _RETRY_BASE_S ** attempt
                except ValueError:
                    delay = _RETRY_BASE_S ** attempt
                time.sleep(delay)
                continue
            metadata["failure_category"] = "provider_unavailable" if code >= 500 or code == 429 else "request_rejected"
            metadata["http_status"] = code
            return None, f"RCSB: erreur HTTP {code}.", metadata
        except httpx.TimeoutException as exc:
            last_error = f"RCSB: timeout ({timeout_seconds}s) — {type(exc).__name__}"
            if attempt < _MAX_RETRIES - 1:
                continue
            metadata["failure_category"] = "provider_timeout"
            return None, f"{last_error} après {_MAX_RETRIES} tentatives.", metadata
        except httpx.RequestError as exc:
            last_error = f"RCSB: erreur réseau ({type(exc).__name__})"
            if attempt < _MAX_RETRIES - 1:
                continue
            metadata["failure_category"] = "provider_unavailable"
            return None, f"{last_error} après {_MAX_RETRIES} tentatives.", metadata
        except (ValueError, KeyError) as exc:
            last_error = f"RCSB: réponse invalide ({type(exc).__name__})"
            if attempt < _MAX_RETRIES - 1:
                continue
            metadata["failure_category"] = "invalid_provider_response"
            return None, f"{last_error} après {_MAX_RETRIES} tentatives.", metadata

    metadata["failure_category"] = "provider_unavailable"
    return None, f"{last_error} après {_MAX_RETRIES} tentatives.", metadata


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
