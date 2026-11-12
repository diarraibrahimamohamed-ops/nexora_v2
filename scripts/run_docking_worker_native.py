#!/usr/bin/env python3
"""Run the Nexora docking Celery worker on the host Python environment.

PostgreSQL and Redis may remain in Docker Compose. This process runs on the
Ubuntu host so it can use the host's fpocket, RDKit, Open Babel and Vina without
installing or compiling those tools into the API/worker image.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DOTENV = ROOT / ".env"


def fail(message: str, code: int = 1) -> None:
    print(f"[Nexora native docking worker] ERREUR: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_environment() -> dict[str, str]:
    try:
        from dotenv import dotenv_values
    except ImportError:
        fail(
            "python-dotenv est absent de cet environnement Python. "
            "Installe les dépendances backend dans cet environnement avant de lancer le worker."
        )
    if not DOTENV.is_file():
        fail(f"Fichier .env introuvable: {DOTENV}")
    values = dotenv_values(DOTENV)
    env = dict(os.environ)
    for key, value in values.items():
        if value is not None and key not in env:
            env[key] = str(value)
    return env


def rewrite_service_url(value: str, service: str, host_port: tuple[str, int]) -> str:
    """Translate Compose-only hostnames to their localhost-published ports."""
    parsed = urlsplit(value)
    if parsed.hostname != service:
        return value
    host, port = host_port
    userinfo = ""
    if "@" in parsed.netloc:
        userinfo = parsed.netloc.rsplit("@", 1)[0] + "@"
    netloc = f"{userinfo}{host}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def wait_for_port(host: str, port: int, label: str) -> None:
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return
    except OSError as exc:
        fail(
            f"{label} injoignable sur {host}:{port} ({exc}). "
            "Démarre PostgreSQL et Redis avec: docker compose up -d db redis"
        )


def describe_local_tools(env: dict[str, str]) -> None:
    fpocket = shutil.which("fpocket")
    if not fpocket:
        fail("fpocket n'est pas dans le PATH du worker natif.")
    print(f"fpocket: {fpocket} (la bannière '--version' ne suffit pas à identifier le tag source)")

    vina_setting = (env.get("VINA_PATH") or "").strip()
    vina_path = (
        vina_setting
        if vina_setting and Path(vina_setting).is_file() and os.access(vina_setting, os.X_OK)
        else shutil.which(vina_setting or "vina")
    )
    if not vina_path:
        fail(
            "AutoDock Vina est introuvable. Configure VINA_PATH dans .env "
            "avec le chemin retourné par 'command -v vina'."
        )
    env["VINA_PATH"] = vina_path
    print(f"AutoDock Vina: {vina_path}")

    try:
        from rdkit import rdBase
        from rdkit import Chem
        test_molecule = Chem.MolFromSmiles("CCO")
        if test_molecule is None:
            fail("RDKit est importable mais le test SMILES de base a échoué.")
        print(f"RDKit: {rdBase.rdkitVersion}")
    except ImportError as exc:
        fail(f"RDKit est requis dans l'environnement Python du worker: {exc}")

    obabel = shutil.which("obabel")
    if obabel:
        try:
            result = subprocess.run(
                [obabel, "-V"], capture_output=True, text=True, timeout=5, check=False
            )
            print(f"Open Babel CLI: {obabel} — {(result.stdout or result.stderr).strip()[:160]}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"AVERTISSEMENT: Open Babel CLI détecté mais non vérifiable: {exc}")
    else:
        print(
            "AVERTISSEMENT: commande obabel absente du PATH. "
            "Les bindings Python Open Babel seuls ne garantissent pas la disponibilité du CLI."
        )

    try:
        from openbabel import openbabel
        print(f"Open Babel Python bindings: {openbabel.OBReleaseVersion()}")
    except ImportError:
        print("Open Babel Python bindings: non installés (le CLI peut néanmoins être disponible).")


def main() -> None:
    os.chdir(ROOT)
    env = load_environment()

    env["DATABASE_URL"] = rewrite_service_url(
        env.get("DATABASE_URL", ""), "db", ("127.0.0.1", 5434)
    )
    for key in ("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
        if env.get(key):
            env[key] = rewrite_service_url(env[key], "redis", ("127.0.0.1", 6381))

    # The Compose API and host worker must share the same artifact directories.
    for key in (
        "STORAGE_DIR",
        "UPLOAD_DIR",
        "RESULTS_DIR",
        "TEMP_DIR",
        "COLABFOLD_OUTPUT_DIR",
        "AFRICAN_LIBS_DIR",
        "LCMS_STORAGE_DIR",
        "OFFLINE_PACKAGES_DIR",
        "AMRFINDER_DB_DIR",
    ):
        value = env.get(key)
        if value and value.startswith("/storage"):
            suffix = value.removeprefix("/storage").lstrip("/")
            env[key] = str(ROOT / "storage" / suffix) if suffix else str(ROOT / "storage")

    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(BACKEND), env.get("PYTHONPATH", "")) if item
    )

    if not env.get("DATABASE_URL"):
        fail("DATABASE_URL manque dans .env.")
    if not env.get("REDIS_URL") or not env.get("CELERY_BROKER_URL"):
        fail("REDIS_URL/CELERY_BROKER_URL manquent dans .env.")

    wait_for_port("127.0.0.1", 5434, "PostgreSQL Docker")
    wait_for_port("127.0.0.1", 6381, "Redis Docker")
    describe_local_tools(env)

    allow_heuristics = env.get("DOCKING_ALLOW_HEURISTIC_POCKET_FALLBACK", "false").lower()
    print(f"Fallback de poche heuristique activé: {allow_heuristics in {'1', 'true', 'yes', 'on'} }")
    print("File Celery: docking | concurrency=1 | exécution native Ubuntu")
    print("Arrête le service Docker worker concurrent avant d'utiliser ce script:")
    print("  docker compose stop worker")
    print("Pour revenir au worker Docker: Ctrl+C puis docker compose up -d worker")

    os.execvpe(
        sys.executable,
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.workers.celery_app",
            "worker",
            "--loglevel=info",
            "-Q",
            "docking",
            "--concurrency=1",
        ],
        env,
    )


if __name__ == "__main__":
    main()
