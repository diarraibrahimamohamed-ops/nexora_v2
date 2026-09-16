"""ESMFold structure prediction — double provider.

Provider 1 : NVIDIA NIM ESMFold   (requiert NVIDIA_API_KEY, jusqu'à 1024 aa)
Provider 2 : ESM Metagenomic Atlas (gratuit, sans auth,    jusqu'à 400 aa)

Ordre de préférence automatique :
  1. NVIDIA si NVIDIA_API_KEY ou NVAPI_KEY est défini dans l'environnement
  2. ESM Atlas en fallback (SSL verify=False — cert Meta expiré côté serveur)

Lin et al., Science 2023 (ESMFold) : https://doi.org/10.1126/science.ade2574
"""

import os
import time
import httpx
from typing import Tuple, Optional, Dict

from app.core.protein.io_utils import write_text_file

_PLACEHOLDER_NVIDIA_KEYS = {
    "",
    "your_nvidia_api_key_here",
    "changeme",
    "change-me",
    "change_me_in_production",
}


def _effective_nvidia_key(explicit: Optional[str] = None) -> Optional[str]:
    """Ignore les placeholders copiés depuis .env.example."""
    raw = (
        explicit
        or os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("NVAPI_KEY")
        or ""
    ).strip()
    if not raw or raw.lower() in _PLACEHOLDER_NVIDIA_KEYS or raw.startswith("your_"):
        return None
    return raw


# ── Endpoints ────────────────────────────────────────────────────────────────
ESMFOLD_NVIDIA_URL = "https://health.api.nvidia.com/v1/biology/nvidia/esmfold"
ESMFOLD_ATLAS_URL  = "https://api.esmatlas.com/foldSequence/v1/pdb/"

# Limite hard de l'ESM Atlas (la réponse renvoie "Sequence is longer than 400.")
ATLAS_MAX_LENGTH   = 400


def build_esmfold_structure(
    protein_sequence: str,
    output_dir: str,
    timeout_seconds: float = 120.0,
    nvidia_api_key: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Dict]:
    """Prédit la structure 3D via ESMFold (MSA-free).

    Args:
        protein_sequence: Séquence d'acides aminés (20 AA standard).
        output_dir:       Répertoire de sortie pour le fichier PDB.
        timeout_seconds:  Timeout par requête API.
        nvidia_api_key:   Clé NVIDIA NIM (optionnel ; surcharge la variable
                          d'environnement NVIDIA_API_KEY / NVAPI_KEY).

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

    api_key = _effective_nvidia_key(nvidia_api_key)
    nvidia_error: Optional[str] = None

    # ── Provider 1 : NVIDIA NIM (si clé disponible) ──────────────────────
    if api_key:
        if len(sequence) > 1024:
            return None, (
                f"Séquence trop longue pour ESMFold: {len(sequence)} aa "
                f"(maximum NVIDIA NIM: 1024 aa)"
            ), {}

        path, error, meta = _via_nvidia(sequence, output_dir, api_key, timeout_seconds)
        if not error:
            return path, None, meta

        nvidia_error = error
        print(
            f"[ESMFold] NVIDIA API échoué ({error}), "
            f"tentative fallback ESM Atlas...",
            flush=True,
        )

    # ── Provider 2 : ESM Metagenomic Atlas (gratuit, sans auth) ──────────
    if len(sequence) > ATLAS_MAX_LENGTH:
        nvidia_bit = (
            f"ESMFold NVIDIA a échoué ({nvidia_error}) et "
            if nvidia_error else
            "Pas de NVIDIA_API_KEY valide et "
        )
        return None, (
            f"{nvidia_bit}la séquence est trop longue "
            f"pour ESM Atlas ({len(sequence)} aa > {ATLAS_MAX_LENGTH} aa). "
            f"Vérifiez NVIDIA_API_KEY ou installez ColabFold."
        ), {}

    path, error, meta = _via_atlas(sequence, output_dir, timeout_seconds)
    if error and nvidia_error:
        error = f"NVIDIA: {nvidia_error} | {error}"
    return path, error, meta


# ─────────────────────────────────────────────────────────────────────────────
# Provider 1 — NVIDIA NIM ESMFold
# ─────────────────────────────────────────────────────────────────────────────

def _via_nvidia(
    sequence: str,
    output_dir: str,
    api_key: str,
    timeout_seconds: float,
) -> Tuple[Optional[str], Optional[str], Dict]:
    """Appel NVIDIA NIM ESMFold.
    
    Doc: https://build.nvidia.com/meta/esmfold/v1/predict
    Auth: Bearer token requis (build.nvidia.com → API Key).
    """
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        payload = {
            "sequence": sequence
        }
        
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(
                ESMFOLD_NVIDIA_URL,
                json=payload,
                headers=headers
            )
            response.raise_for_status()
            
            result = response.json()
            
            # Parser la réponse ESMFold NVIDIA
            # La réponse peut varier, essayer plusieurs formats possibles
            pdb_content = None
            
            if "pdb" in result:
                pdb_content = result["pdb"]
            elif "structure" in result:
                pdb_content = result["structure"]
            elif "data" in result and isinstance(result["data"], dict):
                pdb_content = result["data"].get("pdb") or result["data"].get("structure")
            elif isinstance(result, str):
                pdb_content = result
            
            if not pdb_content or not pdb_content.strip():
                return None, f"ESMFold a renvoyé une structure vide. Réponse: {result}", {}

            destination = write_text_file(
                output_dir, "esmfold_nvidia_prediction.pdb", pdb_content
            )
            metadata = {
                "method": "ESMFold (NVIDIA build.nvidia.com)",
                "provider": "Meta/Facebook ESMFold via NVIDIA API",
                "sequence_length": len(sequence),
                "api_url": ESMFOLD_NVIDIA_URL,
                "msa_free": True,
            }
            return destination, None, metadata

    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 401:
            return None, (
                "NVIDIA ESMFold: clé API invalide ou expirée (401). "
                "Vérifiez NVIDIA_API_KEY sur build.nvidia.com."
            ), {}
        if code == 403:
            return None, (
                "NVIDIA ESMFold: accès refusé (403). "
                "Activez 'Public API Endpoints' sur build.nvidia.com → Settings."
            ), {}
        if code == 422:
            return None, (
                f"NVIDIA ESMFold: séquence rejetée (422). "
                f"Longueur: {len(sequence)} aa."
            ), {}
        if code == 429:
            return None, (
                "NVIDIA ESMFold: quota dépassé (429). "
                "Réessayez dans quelques minutes."
            ), {}
        if code in (500, 504):
            return None, (
                f"NVIDIA ESMFold: erreur serveur ({code}). "
                "Service temporairement indisponible."
            ), {}
        return None, f"NVIDIA ESMFold: erreur HTTP {code}: {exc}", {}

    except httpx.TimeoutException:
        return None, (
            f"NVIDIA ESMFold: timeout ({timeout_seconds}s). "
            f"Séquence {len(sequence)} aa peut-être trop longue pour ce timeout."
        ), {}

    except Exception as exc:
        return None, f"NVIDIA ESMFold: {type(exc).__name__}: {exc}", {}


# ─────────────────────────────────────────────────────────────────────────────
# Provider 2 — ESM Metagenomic Atlas (gratuit, sans auth)
# ─────────────────────────────────────────────────────────────────────────────

def _via_atlas(
    sequence: str,
    output_dir: str,
    timeout_seconds: float,
) -> Tuple[Optional[str], Optional[str], Dict]:
    """Appel ESM Metagenomic Atlas fold API (Meta/Facebook, libre d'accès).

    Endpoint : POST https://api.esmatlas.com/foldSequence/v1/pdb/
    Body     : séquence brute (text/plain, pas JSON)
    Limite   : 400 aa maximum
    Robustesse : 3 tentatives avec backoff exponentiel (2s, 4s) sur les erreurs
                 transitoires (500/502/503/504/521/429 et timeouts).
    SSL      : verify=False — certificat intermédiaire absent côté serveur Meta
               depuis oct. 2023 (issue #629 facebookresearch/esm).
               Aucun risque car le domaine *.esmatlas.com est contrôlé par Meta.
    """
    last_error = "ESM Atlas: échec sans détail"
    _ATLAS_RETRIES = 3

    for attempt in range(_ATLAS_RETRIES):
        if attempt > 0:
            wait = 2 ** attempt   # 2s puis 4s
            print(f"[ESM Atlas] tentative {attempt + 1}/{_ATLAS_RETRIES} "
                  f"dans {wait}s ({last_error})…", flush=True)
            time.sleep(wait)

        try:
            with httpx.Client(
                timeout=timeout_seconds,
                follow_redirects=True,
                verify=False,          # Cert intermédiaire Meta manquant (non révoqué)
            ) as client:
                response = client.post(
                    ESMFOLD_ATLAS_URL,
                    content=sequence.encode("ascii"),
                    headers={"Content-Type": "text/plain"},
                )
                response.raise_for_status()

            pdb_content = response.text
            if not pdb_content or not pdb_content.strip():
                last_error = "ESM Atlas: réponse vide"
                continue

            # Valider que c'est bien un PDB (et pas un message d'erreur JSON/HTML)
            first_line = pdb_content.strip().split("\n")[0]
            pdb_sentinels = ("ATOM", "HETATM", "REMARK", "HEADER", "MODEL", "TITLE")
            if not any(first_line.startswith(s) for s in pdb_sentinels):
                last_error = (
                    f"ESM Atlas: réponse non-PDB reçue: '{first_line[:120]}' "
                    f"(le service renvoie parfois une erreur HTML/JSON)"
                )
                continue

            # Ne pas retenter l'API si seul l'écriture locale échoue :
            # un PDB root:root 0644 bloquait le worker UID 1000.
            destination = write_text_file(
                output_dir, "esmfold_atlas_prediction.pdb", pdb_content
            )
            return destination, None, {
                "method":          "ESMFold (ESM Metagenomic Atlas — gratuit)",
                "provider":        "Meta/Facebook ESMFold via ESM Atlas",
                "sequence_length": len(sequence),
                "api_url":         ESMFOLD_ATLAS_URL,
                "msa_free":        True,
                "auth_required":   False,
                "max_length":      ATLAS_MAX_LENGTH,
                "atlas_attempts":  attempt + 1,
            }

        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code == 413:
                return None, (
                    f"ESM Atlas: séquence trop longue (413). "
                    f"Limite: {ATLAS_MAX_LENGTH} aa."
                ), {}
            if code in (429, 500, 502, 503, 504, 521):
                last_error = (
                    f"ESM Atlas: erreur serveur transitoire (HTTP {code}) — "
                    f"tentative {attempt + 1}/{_ATLAS_RETRIES}"
                )
                continue
            return None, f"ESM Atlas: erreur HTTP {code}: {exc}", {}

        except httpx.TimeoutException:
            last_error = (
                f"ESM Atlas: timeout ({timeout_seconds}s) — "
                f"tentative {attempt + 1}/{_ATLAS_RETRIES}"
            )
            continue

        except OSError as exc:
            return None, (
                f"ESM Atlas: impossible d'écrire le PDB ({type(exc).__name__}: {exc})"
            ), {}

        except Exception as exc:
            last_error = f"ESM Atlas: {type(exc).__name__}: {exc}"
            continue

    return None, (
        f"{last_error} | Le service public ESM Atlas a échoué {_ATLAS_RETRIES} fois. "
        f"Actions possibles : (1) définir NVIDIA_API_KEY pour ESMFold NVIDIA, "
        f"(2) installer ColabFold, (3) fournir un PDB externe "
        f"(structure_provider='external_pdb')."
    ), {}
