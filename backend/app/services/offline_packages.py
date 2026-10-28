"""Offline research package manifests with path-safe SHA-256 inventory."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
MAX_FILES = 500
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_ALLOWED_SUFFIXES = {
    ".fasta", ".fa", ".fna", ".faa", ".gb", ".gbk", ".gff", ".vcf",
    ".pdb", ".cif", ".sdf", ".mol", ".smi", ".smiles", ".csv", ".tsv",
    ".json", ".txt", ".md", ".pdf", ".xml", ".yaml", ".yml", ".gz", ".zip",
}


class OfflinePackageError(ValueError):
    pass


def _root() -> Path:
    root = Path(settings.OFFLINE_PACKAGES_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _manifest_dir() -> Path:
    path = _root() / "_manifests"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _validate_package_id(value: str) -> str:
    value = (value or "").strip()
    if not PACKAGE_ID_RE.fullmatch(value):
        raise OfflinePackageError("package_id invalide (80 caractères max, lettres/chiffres/._-).")
    return value


def _resolve_local_file(relative_path: str) -> tuple[Path, str]:
    rel = Path((relative_path or "").strip())
    if not relative_path or rel.is_absolute() or ".." in rel.parts:
        raise OfflinePackageError(f"Chemin relatif interdit: {relative_path!r}")
    root = _root()
    resolved = (root / rel).resolve()
    if resolved == root or root not in resolved.parents:
        raise OfflinePackageError(f"Le chemin sort du répertoire des paquets hors ligne: {relative_path!r}")
    if not resolved.is_file():
        raise OfflinePackageError(f"Fichier absent du paquet hors ligne: {relative_path!r}")
    if resolved.suffix.lower() not in _ALLOWED_SUFFIXES:
        raise OfflinePackageError(f"Format non autorisé dans un paquet de recherche: {resolved.suffix}")
    return resolved, rel.as_posix()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(data: dict) -> str:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def create_manifest(*, package_id: str, profile_id: str, profile_version: str,
                    reference_set_version: str, relative_files: list[str],
                    references: list[dict] | None = None, licenses: list[dict] | None = None,
                    notes: str | None = None) -> dict:
    package_id = _validate_package_id(package_id)
    if not profile_id.strip() or not profile_version.strip() or not reference_set_version.strip():
        raise OfflinePackageError("profile_id, profile_version et reference_set_version sont requis.")
    if not relative_files or len(relative_files) > MAX_FILES:
        raise OfflinePackageError(f"Le paquet doit contenir 1 à {MAX_FILES} chemins de fichiers.")
    normalized = [str(p).strip() for p in relative_files]
    if len(set(normalized)) != len(normalized):
        raise OfflinePackageError("La liste de fichiers contient des doublons.")
    files = []
    total_bytes = 0
    for relative in normalized:
        path, stored_relative = _resolve_local_file(relative)
        size = path.stat().st_size
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise OfflinePackageError("La taille totale des fichiers dépasse 2 GiB.")
        files.append({
            "path": stored_relative,
            "size_bytes": size,
            "sha256": sha256_file(path),
        })
    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": "1.0",
        "package_id": package_id,
        "created_at_utc": now,
        "profile": {"id": profile_id, "version": profile_version},
        "reference_set_version": reference_set_version,
        "files": files,
        "references": references or [],
        "licenses": licenses or [],
        "notes": notes,
        "integrity": {"algorithm": "SHA-256", "verified_at_creation": True},
    }
    manifest["manifest_sha256"] = _canonical_sha256(manifest)
    path = _manifest_dir() / (package_id + ".json")
    fd, temp_name = tempfile.mkstemp(prefix=package_id + ".", suffix=".part", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return manifest


def list_manifests() -> list[dict]:
    results = []
    for path in sorted(_manifest_dir().glob("*.json")):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            results.append({
                "package_id": manifest.get("package_id"),
                "created_at_utc": manifest.get("created_at_utc"),
                "profile": manifest.get("profile"),
                "file_count": len(manifest.get("files", [])),
                "manifest_sha256": manifest.get("manifest_sha256"),
                "path": str(path.relative_to(_root())),
            })
        except (OSError, json.JSONDecodeError):
            results.append({"package_id": path.stem, "status": "invalid_manifest"})
    return results


def verify_manifest(package_id: str) -> dict:
    package_id = _validate_package_id(package_id)
    path = _manifest_dir() / (package_id + ".json")
    if not path.is_file():
        raise OfflinePackageError(f"Manifeste introuvable pour {package_id}.")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfflinePackageError("Manifeste illisible ou JSON invalide.") from exc
    expected_manifest_sha = manifest.get("manifest_sha256")
    without_digest = dict(manifest)
    without_digest.pop("manifest_sha256", None)
    digest_ok = bool(expected_manifest_sha and _canonical_sha256(without_digest) == expected_manifest_sha)
    files = []
    for item in manifest.get("files", []):
        try:
            local, normalized = _resolve_local_file(item.get("path", ""))
            actual_sha = sha256_file(local)
            actual_size = local.stat().st_size
            files.append({
                "path": normalized,
                "expected_sha256": item.get("sha256"),
                "actual_sha256": actual_sha,
                "expected_size_bytes": item.get("size_bytes"),
                "actual_size_bytes": actual_size,
                "ok": actual_sha == item.get("sha256") and actual_size == item.get("size_bytes"),
            })
        except OfflinePackageError as exc:
            files.append({"path": item.get("path"), "ok": False, "error": str(exc)})
    ok = digest_ok and all(item.get("ok") for item in files)
    return {
        "package_id": package_id,
        "ok": ok,
        "manifest_sha256_ok": digest_ok,
        "file_count": len(files),
        "files_ok": sum(1 for item in files if item.get("ok")),
        "files_failed": [item for item in files if not item.get("ok")],
        "profile": manifest.get("profile"),
        "reference_set_version": manifest.get("reference_set_version"),
        "note": "L’intégrité SHA-256 confirme l’identité des fichiers, pas leur validité biologique ni leur licence.",
    }
