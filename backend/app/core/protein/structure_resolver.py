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
