"""Tamarind Bio ESMFold structure prediction provider.

Provider : Tamarind Bio Cloud ESMFold (requiert TAMARIND_API_KEY)
Alternative légère à ColabFold et NVIDIA NIM pour les environnements à ressources limitées.

Documentation : https://app.tamarind.bio/api
"""

import os
import time
import httpx
from typing import Tuple, Optional, Dict

from app.core.protein.io_utils import write_text_file

_PLACEHOLDER_TAMARIND_KEYS = {
    "",
    "your_tamarind_api_key_here",
    "changeme",
    "change-me",
    "change_me_in_production",
}


def _effective_tamarind_key(explicit: Optional[str] = None) -> Optional[str]:
    """Ignore les placeholders copiés depuis .env.example."""
    raw = (
        explicit
        or os.environ.get("TAMARIND_API_KEY")
        or ""
    ).strip()
    if not raw or raw.lower() in _PLACEHOLDER_TAMARIND_KEYS or raw.startswith("your_"):
        return None
    return raw


# ── Endpoints ────────────────────────────────────────────────────────────────
TAMARIND_BASE_URL = "https://app.tamarind.bio/api/"
TAMARIND_SUBMIT_URL = TAMARIND_BASE_URL + "submit-job"
TAMARIND_JOBS_URL = TAMARIND_BASE_URL + "jobs"
TAMARIND_MAX_LENGTH = 1024  # Limite suggérée pour ESMFold


def build_tamarind_structure(
    protein_sequence: str,
    output_dir: str,
    timeout_seconds: float = 300.0,
    poll_interval: float = 5.0,
    max_poll_attempts: int = 120,  # 10 minutes max avec 5s d'intervalle
    tamarind_api_key: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Dict]:
    """Prédit la structure 3D via Tamarind Bio ESMFold.

    Args:
        protein_sequence: Séquence d'acides aminés (20 AA standard).
        output_dir:       Répertoire de sortie pour le fichier PDB.
        timeout_seconds:  Timeout global pour la requête (incluant le polling).
        poll_interval:    Intervalle entre chaque vérification de statut (secondes).
        max_poll_attempts: Nombre maximum de tentatives de polling.
        tamarind_api_key: Clé API Tamarind (optionnel ; surcharge la variable
                          d'environnement TAMARIND_API_KEY).

    Returns:
        (chemin_pdb, erreur, métadonnées)
    """
    # ── Validation séquence ───────────────────────────────────────────────
    sequence = "".join(protein_sequence.split()).upper()
    if not sequence:
        return None, "Séquence protéique vide", {}
    if len(sequence) < 3:
        return None, f"Protéine trop courte: {len(sequence)} aa (minimum 3 aa)", {}

    valid_aa = set("ARNDCQEGHILKMFPSTWYV")
    invalid_chars = set(sequence) - valid_aa
    if invalid_chars:
        return None, (
            f"Caractères invalides dans la séquence: {invalid_chars}. "
            f"Seuls les 20 acides aminés standard sont acceptés."
        ), {}

    api_key = _effective_tamarind_key(tamarind_api_key)
    if not api_key:
        return None, (
            "TAMARIND_API_KEY non défini. Configurez TAMARIND_API_KEY dans .env"
        ), {}

    if len(sequence) > TAMARIND_MAX_LENGTH:
        return None, (
            f"Séquence trop longue pour Tamarind ESMFold: {len(sequence)} aa "
            f"(maximum suggéré: {TAMARIND_MAX_LENGTH} aa)"
        ), {}

    # ── Soumission du job ─────────────────────────────────────────────────
    try:
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json"
        }

        payload = {
            "jobName": f"esmfold_{len(sequence)}aa",
            "type": "esmfold",
            "settings": {
                "sequence": sequence
            }
        }

        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                TAMARIND_SUBMIT_URL,
                json=payload,
                headers=headers
            )
            response.raise_for_status()

        # La réponse est un simple message de confirmation, pas de JSON
        print(f"[Tamarind] Job soumis: {response.text}", flush=True)

    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 401:
            return None, (
                "Tamarind: clé API invalide (401). "
                "Vérifiez TAMARIND_API_KEY."
            ), {}
        if code == 403:
            return None, (
                "Tamarind: accès refusé (403). "
                "Vérifiez les permissions de votre clé API."
            ), {}
        if code == 429:
            return None, (
                "Tamarind: quota dépassé (429). "
                "Réessayez dans quelques minutes."
            ), {}
        return None, f"Tamarind: erreur HTTP {code}: {exc}", {}

    except Exception as exc:
        return None, f"Tamarind: erreur de soumission ({type(exc).__name__}): {exc}", {}

    # ── Polling du statut du job ────────────────────────────────────────────
    # Note: L'API Tamarind ne retourne pas directement le job_id dans la réponse
    # Nous devons récupérer la liste des jobs et trouver le plus récent du bon type
    job_id = None
    start_time = time.time()

    for attempt in range(max_poll_attempts):
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            return None, (
                f"Tamarind: timeout global ({timeout_seconds}s) "
                f"avant que le job ne soit terminé."
            ), {}

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(
                    TAMARIND_JOBS_URL,
                    headers=headers
                )
                response.raise_for_status()

            jobs_data = response.json()
            jobs = jobs_data.get("jobs", [])

            # Trouver le job le plus récent de type "esmfold"
            esmfold_jobs = [j for j in jobs if j.get("Type") == "esmfold"]
            if esmfold_jobs:
                # Prendre le plus récent (premier de la liste si trié par date)
                latest_job = esmfold_jobs[0]
                job_status = latest_job.get("JobStatus")
                job_name = latest_job.get("JobName")

                print(f"[Tamarind] Job {job_name}: {job_status} (tentative {attempt + 1}/{max_poll_attempts})", flush=True)

                if job_status == "Complete":
                    # Job terminé avec succès
                    pdb_content = latest_job.get("pdb_result")
                    if not pdb_content:
                        return None, (
                            "Tamarind: job terminé mais pas de PDB dans la réponse"
                        ), {}

                    destination = write_text_file(
                        output_dir, "tamarind_esmfold_prediction.pdb", pdb_content
                    )
                    metadata = {
                        "method": "ESMFold (Tamarind Bio Cloud)",
                        "provider": "Tamarind Bio",
                        "sequence_length": len(sequence),
                        "api_url": TAMARIND_BASE_URL,
                        "msa_free": True,
                        "job_name": job_name,
                        "poll_attempts": attempt + 1,
                    }
                    return destination, None, metadata

                elif job_status == "Stopped":
                    return None, (
                        f"Tamarind: job arrêté (Status: {job_status})"
                    ), {}

                elif job_status in ("Failed", "Error"):
                    errors = latest_job.get("errors", "sans détail")
                    return None, (
                        f"Tamarind: job échoué ({job_status}): {errors}"
                    ), {}

                # Job encore en cours (In Queue, Running, etc.)
                time.sleep(poll_interval)
                continue

            else:
                # Pas de jobs esmfold trouvés, attendre un peu
                time.sleep(poll_interval)
                continue

        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (429, 500, 502, 503, 504):
                print(f"[Tamarind] Erreur transitoire {code}, retry...", flush=True)
                time.sleep(poll_interval)
                continue
            return None, f"Tamarind: erreur HTTP {code} lors du polling: {exc}", {}

        except Exception as exc:
            print(f"[Tamarind] Erreur polling: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(poll_interval)
            continue

    return None, (
        f"Tamarind: job non terminé après {max_poll_attempts} tentatives "
        f"({max_poll_attempts * poll_interval:.0f}s). "
        f"Le service peut être encombré."
    ), {}
