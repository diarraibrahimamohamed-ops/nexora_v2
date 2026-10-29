"""Tamarind Bio adapters for ESMFold and RNA-FM secondary-structure prediction.

The runner discovers RNA-FM settings dynamically, validates requests before billing,
polls the uniquely named job, and downloads artifacts from the documented /result URL.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from app.core.protein.io_utils import resolve_writable_dir, write_text_file

BASE = "https://app.tamarind.bio/api/"
TOOLS_URL = BASE + "tools"
VALIDATE_URL = BASE + "validate-job"
SUBMIT_URL = BASE + "submit-job"
JOBS_URL = BASE + "jobs"
RESULT_URL = BASE + "result"
RESULT_HOST = "tamarind-results.s3.amazonaws.com"
MAX_PROTEIN_AA = 1024
MAX_RNA_NT = 10000
MAX_ZIP_BYTES = 100 * 1024 * 1024
MAX_UNPACKED_BYTES = 200 * 1024 * 1024
STANDARD_AA = set("ARNDCQEGHILKMFPSTWYV")
_PLACEHOLDERS = {"", "your_tamarind_api_key_here", "changeme", "change-me", "change_me_in_production"}


def _effective_tamarind_key(explicit: Optional[str] = None) -> Optional[str]:
    key = (explicit or os.environ.get("TAMARIND_API_KEY") or "").strip()
    if not key or key.lower() in _PLACEHOLDERS or key.lower().startswith("your_"):
        return None
    return key


def _job_name(prefix: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", prefix)[:24].strip("-_") or "n3xora"
    return f"n3xora-{safe}-{uuid.uuid4().hex[:16]}"


def _http_error(exc: httpx.HTTPStatusError, action: str) -> str:
    code = exc.response.status_code
    if code == 401:
        return "Clé API Tamarind invalide ou expirée (401)."
    if code == 403:
        return "Accès Tamarind refusé : permissions ou budget à vérifier (403)."
    if code == 429:
        return "Limite de débit Tamarind atteinte (429)."
    return f"Erreur HTTP Tamarind {code} pendant {action}."


def _result_url(response: httpx.Response) -> Optional[str]:
    try:
        value: Any = response.json()
    except ValueError:
        value = response.text.strip()
    if isinstance(value, dict):
        value = next((value[k] for k in ("url", "resultUrl", "download_url", "downloadUrl")
                      if isinstance(value.get(k), str) and value[k].strip()), None)
    if not isinstance(value, str):
        return None
    value = value.strip().strip('"').strip("'")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != RESULT_HOST or parsed.username or parsed.password:
        return None
    return value


def _download_files(client: httpx.Client, headers: Dict[str, str], job_name: str):
    try:
        result = client.post(RESULT_URL, json={"jobName": job_name}, headers=headers, timeout=30)
        result.raise_for_status()
        url = _result_url(result)
        if not url:
            return None, "L'API /result de Tamarind n'a pas renvoyé une URL de téléchargement HTTPS attendue."
        # Do not leak x-api-key to the presigned object-storage URL.
        archive_response = client.get(url, timeout=60, follow_redirects=False)
        archive_response.raise_for_status()
        archive_bytes = archive_response.content
        if len(archive_bytes) > MAX_ZIP_BYTES:
            return None, "Archive de résultats Tamarind trop volumineuse (limite 100 MiB)."
        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                infos = [item for item in archive.infolist() if not item.is_dir()]
                if len(infos) > 300 or sum(item.file_size for item in infos) > MAX_UNPACKED_BYTES:
                    return None, "Archive de résultats Tamarind dépasse les limites autorisées."
                files = []
                for info in infos:
                    # Never extract untrusted archive paths.
                    basename = Path(info.filename.replace("\\", "/")).name
                    if not basename or basename.startswith(".") or info.file_size > MAX_UNPACKED_BYTES:
                        continue
                    try:
                        files.append({"name": basename, "content": archive.read(info)})
                    except (RuntimeError, zipfile.BadZipFile, OSError):
                        continue
                return files, None
        except zipfile.BadZipFile:
            return None, "Le résultat Tamarind n'est pas une archive ZIP valide."
    except httpx.HTTPStatusError as exc:
        return None, _http_error(exc, "téléchargement des résultats")
    except httpx.TimeoutException:
        return None, "Délai dépassé pendant le téléchargement des résultats Tamarind."
    except httpx.RequestError as exc:
        return None, f"Erreur réseau Tamarind pendant les résultats ({type(exc).__name__})."


def _choose_artifact(files: List[Dict[str, Any]], kind: str):
    if kind == "protein":
        candidates = [f for f in files if Path(f["name"]).suffix.lower() in {".pdb", ".ent", ".cif", ".mmcif"}
                      and (b"ATOM" in f["content"][:250000] or b"_atom_site." in f["content"][:250000])]
        return sorted(candidates, key=lambda f: (0 if "esmfold" in f["name"].lower() else 1, f["name"].lower()))[0] if candidates else None
    allowed = {".dbn", ".dot", ".ct", ".bpseq", ".csv", ".tsv", ".txt", ".json", ".npz", ".npy"}
    candidates = [f for f in files if Path(f["name"]).suffix.lower() in allowed]
    def rank(item):
        name, suffix = item["name"].lower(), Path(item["name"]).suffix.lower()
        is_named = 0 if any(word in name for word in ("secondary", "structure", "rna-fm", "rnafm", "dotbracket", "dot_bracket", "ss_")) else 1
        suffix_rank = {".dbn": 0, ".dot": 1, ".ct": 2, ".bpseq": 3, ".csv": 4, ".tsv": 5, ".txt": 6, ".json": 7, ".npz": 8, ".npy": 9}.get(suffix, 20)
        return is_named, suffix_rank, name
    return sorted(candidates, key=rank)[0] if candidates else None


def _write_artifact(item: Dict[str, Any], output_dir: str, prefix: str):
    ext = Path(item["name"]).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
        ext = ".bin"
    filename = f"{prefix}_{uuid.uuid4().hex[:12]}{ext}"
    try:
        if ext in {".pdb", ".ent", ".cif", ".mmcif", ".dbn", ".dot", ".ct", ".bpseq", ".csv", ".tsv", ".txt", ".json"}:
            return write_text_file(output_dir, filename, item["content"].decode("utf-8-sig")), None
        dest = Path(resolve_writable_dir(output_dir)) / filename
        dest.write_bytes(item["content"])
        return str(dest.resolve()), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"Impossible d'écrire l'artefact Tamarind ({type(exc).__name__})."


def _run_job(*, api_key: str, tool_name: str, settings_payload: Dict[str, Any],
             output_dir: str, artifact_kind: str, prefix: str,
             timeout_seconds: float, poll_interval: float, max_poll_attempts: int):
    if timeout_seconds <= 0 or poll_interval <= 0 or max_poll_attempts < 1:
        return None, "Paramètres de polling Tamarind invalides.", {}
    name = _job_name(prefix)
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    metadata = {"provider": "Tamarind Bio", "tool": tool_name, "job_name": name}
    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=False) as client:
            check = client.post(VALIDATE_URL, json={"type": tool_name, "settings": settings_payload, "jobName": name}, headers=headers, timeout=30)
            check.raise_for_status()
            checked = check.json()
            if checked.get("valid") is not True:
                missing = [str(x.get("name")) for x in checked.get("missing_fields", []) if isinstance(x, dict) and x.get("name")]
                suffix = " Champs manquants: " + ", ".join(missing) + "." if missing else ""
                return None, "Validation Tamarind refusée: " + str(checked.get("error") or "paramètres invalides") + "." + suffix, metadata
            if isinstance(checked.get("normalized"), dict):
                settings_payload = checked["normalized"]
            metadata["schema_source"] = checked.get("source")
            submitted = client.post(SUBMIT_URL, json={"jobName": name, "type": tool_name, "settings": settings_payload}, headers=headers, timeout=30)
            submitted.raise_for_status()
            started = time.monotonic()
            for attempt in range(max_poll_attempts):
                if time.monotonic() - started >= timeout_seconds:
                    metadata["poll_attempts"] = attempt
                    return None, f"Timeout Tamarind après {timeout_seconds:.0f} secondes.", metadata
                try:
                    status_response = client.get(JOBS_URL, params={"jobName": name}, headers=headers, timeout=20)
                    status_response.raise_for_status()
                    status_data = status_response.json()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in (429, 500, 502, 503, 504):
                        return None, _http_error(exc, "lecture du statut"), metadata
                    time.sleep(min(poll_interval, 30))
                    continue
                except (httpx.TimeoutException, httpx.RequestError, ValueError):
                    time.sleep(min(poll_interval, 30))
                    continue
                jobs = status_data.get("jobs", []) if isinstance(status_data, dict) else []
                job = next((x for x in jobs if isinstance(x, dict) and x.get("JobName") == name), None)
                if job is None:
                    time.sleep(min(poll_interval, 30))
                    continue
                state = str(job.get("JobStatus", "")).lower()
                metadata["job_status"] = state
                if state in {"failed", "error", "stopped", "cancelled", "canceled"}:
                    return None, f"Job Tamarind {state}. Consultez son journal dans Tamarind.", metadata
                if state in {"complete", "completed", "success", "succeeded"}:
                    files, error = _download_files(client, headers, name)
                    if error:
                        return None, error, metadata
                    metadata["result_files"] = [x["name"] for x in (files or [])[:100]]
                    item = _choose_artifact(files or [], artifact_kind)
                    if not item:
                        detail = ", ".join(metadata["result_files"]) or "aucun"
                        expected = "PDB/mmCIF" if artifact_kind == "protein" else "fichier de structure ARN"
                        return None, f"Job terminé mais aucun {expected} exploitable. Fichiers: {detail}.", metadata
                    path, error = _write_artifact(item, output_dir, prefix)
                    if error or not path:
                        return None, error or "Écriture de l'artefact impossible.", metadata
                    metadata.update({"provider_artifact_name": item["name"], "artifact_filename": Path(path).name,
                                     "artifact_path": path, "artifact_format": Path(path).suffix.lower().lstrip("."),
                                     "poll_attempts": attempt + 1})
                    return path, None, metadata
                time.sleep(min(poll_interval, 30))
        metadata["poll_attempts"] = max_poll_attempts
        return None, f"Job Tamarind non terminé après {max_poll_attempts} vérifications.", metadata
    except httpx.HTTPStatusError as exc:
        return None, _http_error(exc, "soumission ou statut"), metadata
    except httpx.TimeoutException:
        return None, "Délai dépassé pendant une requête Tamarind.", metadata
    except httpx.RequestError as exc:
        return None, f"Erreur réseau Tamarind ({type(exc).__name__}).", metadata
    except (ValueError, TypeError, KeyError) as exc:
        return None, f"Réponse Tamarind invalide ({type(exc).__name__}).", metadata


def build_tamarind_structure(protein_sequence: str, output_dir: str, timeout_seconds: float = 600.0,
                              poll_interval: float = 5.0, max_poll_attempts: int = 120,
                              tamarind_api_key: Optional[str] = None):
    """Predict protein tertiary structure using Tamarind's ESMFold tool."""
    sequence = "".join((protein_sequence or "").split()).upper()
    if len(sequence) < 3:
        return None, "Séquence protéique trop courte ou vide (minimum 3 acides aminés).", {}
    if set(sequence) - STANDARD_AA:
        return None, "La séquence protéique contient des acides aminés non standards.", {}
    if len(sequence) > MAX_PROTEIN_AA:
        return None, f"Séquence trop longue pour Tamarind ESMFold ({MAX_PROTEIN_AA} aa maximum).", {}
    key = _effective_tamarind_key(tamarind_api_key)
    if not key:
        return None, "TAMARIND_API_KEY n'est pas configurée.", {}
    path, error, metadata = _run_job(api_key=key, tool_name="esmfold", settings_payload={"sequence": sequence},
        output_dir=output_dir, artifact_kind="protein", prefix="esmfold",
        timeout_seconds=timeout_seconds, poll_interval=poll_interval, max_poll_attempts=max_poll_attempts)
    if error:
        return None, error, metadata
    if not path or not Path(path).is_file():
        return None, "Tamarind n'a pas produit de fichier de structure exploitable.", metadata
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    if "ATOM" not in content[:250000] and "_atom_site." not in content[:250000]:
        return None, "L'artefact Tamarind ne contient pas de coordonnées atomiques.", metadata
    metadata.update({"method": "ESMFold (Tamarind Bio)", "sequence_length": len(sequence), "msa_free": True,
                     "interpretation_scope": "structure 3D prédite; validation indépendante requise"})
    return path, None, metadata


def _field_name(field: Dict[str, Any]) -> str:
    return str(field.get("name") or field.get("id") or "").strip()


def _option_value(option: Any):
    if isinstance(option, dict):
        return next((option[k] for k in ("value", "id", "key", "name", "label", "title") if option.get(k) is not None), None)
    return option


def _option_label(option: Any) -> str:
    if isinstance(option, dict):
        return str(next((option[k] for k in ("label", "displayName", "title", "name", "value", "id") if option.get(k) is not None), option))
    return str(option)


def discover_rna_fm_settings(tools_payload: Any, sequence: str):
    """Discover the exact RNA-FM tool and its settings from Tamarind GET /tools."""
    tools = tools_payload.get("tools", []) if isinstance(tools_payload, dict) else tools_payload
    if not isinstance(tools, list):
        return None, None, "Réponse Tamarind /tools inattendue."
    candidates = []
    for tool in tools:
        if isinstance(tool, dict):
            identity = " ".join(str(tool.get(k, "")) for k in ("name", "displayName", "display_name", "description"))
            if "rnafm" in re.sub(r"[^a-z0-9]", "", identity.lower()):
                candidates.append(tool)
    if not candidates:
        return None, None, "RNA-FM n'est pas disponible pour cette clé Tamarind."
    tool = candidates[0]
    tool_name, fields = str(tool.get("name") or "").strip(), tool.get("settings")
    if not tool_name or not isinstance(fields, list):
        return None, None, "Schéma RNA-FM incomplet dans /tools."
    seq_field, task_field = None, None
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = _field_name(field)
        normalized = re.sub(r"[^a-z0-9]", "", name.lower())
        if normalized in {"sequence", "rnasequence", "inputsequence", "rnaseq"} or ("sequence" in normalized and seq_field is None):
            seq_field = name
        if normalized in {"task", "mode", "action", "operation", "predictiontype", "tasktype"}:
            task_field = field
    if not seq_field:
        return None, None, "Aucun champ de séquence identifiable dans le schéma RNA-FM."
    settings = {seq_field: sequence}
    if task_field:
        task_name = _field_name(task_field)
        options = task_field.get("options") or task_field.get("choices") or task_field.get("enum") or task_field.get("values") or []
        selected = next((_option_value(o) for o in options if isinstance(options, list)
                         and "secondary" in _option_label(o).lower()
                         and "structure" in _option_label(o).lower()), None)
        if selected is None:
            return None, None, "Le schéma RNA-FM ne propose pas explicitement la prédiction de structure secondaire."
        settings[task_name] = selected
    for field in fields:
        if not isinstance(field, dict):
            continue
        fname = _field_name(field)
        if fname and fname not in settings and field.get("required") is True:
            if field.get("default") is not None:
                settings[fname] = field["default"]
            else:
                return None, None, f"Champ RNA-FM obligatoire non résolu: {fname}."
    return tool_name, settings, None


def build_tamarind_secondary_structure(rna_sequence: str, output_dir: str, timeout_seconds: float = 600.0,
                                        poll_interval: float = 5.0, max_poll_attempts: int = 120,
                                        tamarind_api_key: Optional[str] = None):
    """Predict RNA secondary structure with RNA-FM. T is normalized to U."""
    original = "".join((rna_sequence or "").split()).upper()
    if len(original) < 4:
        return None, "Séquence ARN trop courte (minimum 4 nucléotides).", {}
    if len(original) > MAX_RNA_NT:
        return None, f"Séquence ARN trop longue ({MAX_RNA_NT} nucléotides maximum).", {}
    if set(original) - set("ACGUT"):
        return None, "Séquence invalide : seules les bases A/C/G/U/T sont acceptées.", {}
    sequence = original.replace("T", "U")
    key = _effective_tamarind_key(tamarind_api_key)
    if not key:
        return None, "TAMARIND_API_KEY n'est pas configurée.", {}
    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=False) as client:
            response = client.get(TOOLS_URL, headers={"x-api-key": key}, timeout=30)
            response.raise_for_status()
            tool_name, settings, error = discover_rna_fm_settings(response.json(), sequence)
    except httpx.HTTPStatusError as exc:
        return None, _http_error(exc, "découverte RNA-FM"), {}
    except httpx.TimeoutException:
        return None, "Délai dépassé pendant la découverte RNA-FM.", {}
    except (httpx.RequestError, ValueError) as exc:
        return None, f"Échec de la découverte RNA-FM ({type(exc).__name__}).", {}
    if error or not tool_name or settings is None:
        return None, error or "Impossible de préparer le job RNA-FM.", {}
    path, error, metadata = _run_job(api_key=key, tool_name=tool_name, settings_payload=settings,
        output_dir=output_dir, artifact_kind="rna", prefix="rnafm-secondary",
        timeout_seconds=timeout_seconds, poll_interval=poll_interval, max_poll_attempts=max_poll_attempts)
    if error:
        return None, error, metadata
    if not path or not Path(path).is_file():
        return None, "RNA-FM n'a pas produit d'artefact exploitable.", metadata
    suffix = Path(path).suffix.lower()
    metadata.update({"method": "RNA-FM (Tamarind Bio)", "sequence_length": len(sequence),
        "input_t_to_u_normalized": original != sequence, "artifact_format": suffix.lstrip(".") or "unknown",
        "artifact_is_binary": suffix in {".npz", ".npy"},
        "interpretation_scope": "prédiction informatique de structure secondaire ARN; non expérimentale"})
    if suffix in {".dbn", ".dot", ".ct", ".bpseq", ".csv", ".tsv", ".txt", ".json"}:
        try:
            metadata["preview"] = Path(path).read_text(encoding="utf-8", errors="replace")[:20000]
        except OSError:
            metadata["preview"] = None
    return path, None, metadata
