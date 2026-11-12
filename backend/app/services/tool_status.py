"""Runtime readiness probes. Availability is reported independently from source-code presence."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from app.config import settings


def _version_command(command: list[str], timeout: float = 5.0) -> dict:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        output = (result.stdout or result.stderr or "").strip()
        return {"ok": result.returncode == 0, "returncode": result.returncode, "output": output[:300] or None}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def vina_status() -> dict:
    configured = (settings.VINA_PATH or "vina").strip()
    executable = configured if Path(configured).is_file() and os.access(configured, os.X_OK) else shutil.which(configured)
    if not executable:
        return {"available": False, "configured_path": configured, "version": None}
    version = _version_command([executable, "--version"])
    return {
        "available": bool(version["ok"]),
        "path": executable,
        "version": version.get("output"),
        "probe": version,
        "expected_research_version": "1.2.5",
        "matches_expected_research_version": bool(version.get("output") and "1.2.5" in version["output"]),
    }


def fpocket_status() -> dict:
    executable = shutil.which("fpocket")
    return {
        "available": bool(executable),
        "path": executable,
        "note": "La présence du binaire ne prouve pas que les paramètres de poche sont adaptés à une cible particulière.",
    }


def rdkit_status() -> dict:
    try:
        from rdkit import Chem, rdBase
        molecule = Chem.MolFromSmiles("CCO")
        return {"available": molecule is not None, "version": rdBase.rdkitVersion, "smiles_smoke_test": molecule is not None}
    except Exception as exc:
        return {"available": False, "error": type(exc).__name__}


def ncbi_status() -> dict:
    """Report client configuration without exposing credentials or probing NCBI."""
    api_key_configured = bool((settings.NCBI_API_KEY or "").strip())
    email_configured = bool((settings.NCBI_EMAIL or "").strip())
    return {
        "configured": True,
        "tool": settings.NCBI_TOOL or "nexora_v2",
        "email_configured": email_configured,
        "api_key_configured": api_key_configured,
        "documented_ip_rate_limit_rps": 10 if api_key_configured else 3,
        "client_rate_limit_rps_per_process": 3,
        "network_probe": "not_performed",
        "note": "Le client applique un plafond local de 3 requêtes/s; en déploiement multi-réplicas, centraliser le quota par IP.",
    }


def structure_provider_status() -> dict:
    """Expose provider readiness facts without performing slow outbound requests."""
    colabfold_binary = (settings.COLABFOLD_BINARY or "colabfold_batch").strip()
    colabfold_path = (
        colabfold_binary
        if Path(colabfold_binary).is_file() and os.access(colabfold_binary, os.X_OK)
        else shutil.which(colabfold_binary)
    )
    nvidia_enabled = bool(settings.ENABLE_DEPRECATED_NVIDIA_ESMFOLD)
    return {
        "rcsb_experimental": {
            "available_for_lookup": True,
            "kind": "experimental",
            "network_probe": "not_performed",
        },
        "rcsb_computed_models": {
            "search_supported_by_upstream": True,
            "integrated_in_nexora": False,
            "kind": "computational",
            "network_probe": "not_performed",
        },
        "esm_atlas": {
            "configured": True,
            "max_sequence_length_aa": 400,
            "authentication_required": False,
            "network_probe": "not_performed",
        },
        "tamarind_esmfold": {
            "credential_configured": bool((settings.TAMARIND_API_KEY or "").strip()),
            "max_sequence_length_aa": 1024,
            "ready": None,
            "note": "Une clé configurée ne garantit pas les quotas, crédits ou la disponibilité du service.",
        },
        "nvidia_esmfold": {
            "enabled": nvidia_enabled,
            "credential_configured": bool((settings.NVIDIA_API_KEY or "").strip()),
            "ready": None,
        },
        "colabfold_local": {
            "binary_configured": colabfold_binary,
            "binary_available": bool(colabfold_path),
            "binary_path": colabfold_path,
            "isolated_execution_recommended": True,
        },
    }


def all_tool_status() -> dict:
    from app.services.amr_analysis import amrfinder_status
    from app.services.local_research_assistant import assistant_status
    return {
        "success": True,
        "vina": vina_status(),
        "fpocket": fpocket_status(),
        "rdkit": rdkit_status(),
        "ncbi": ncbi_status(),
        "structure_providers": structure_provider_status(),
        "amrfinderplus": amrfinder_status(),
        "ollama": assistant_status(),
        "important": "L'état d'un outil est distinct de la validité biologique des résultats. Vina/RDKit/Ollama ne sont pas des validations expérimentales.",
    }
