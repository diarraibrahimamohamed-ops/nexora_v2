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


def all_tool_status() -> dict:
    from app.services.amr_analysis import amrfinder_status
    from app.services.local_research_assistant import assistant_status
    return {
        "success": True,
        "vina": vina_status(),
        "fpocket": fpocket_status(),
        "rdkit": rdkit_status(),
        "amrfinderplus": amrfinder_status(),
        "ollama": assistant_status(),
        "important": "L'état d'un outil est distinct de la validité biologique des résultats. Vina/RDKit/Ollama ne sont pas des validations expérimentales.",
    }
