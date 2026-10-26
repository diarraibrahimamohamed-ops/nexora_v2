"""Offline-first ANPDB download/cache helpers.

The public ANPDB download page exposes the complete CSV and SDF datasets. This
module uses those documented static download URLs; it does not pretend that a
public search API exists. Regional filtering must be performed on curated source
organism/taxonomy fields after import.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import httpx

from app.config import settings

_DOWNLOADS = {
    "csv": "https://phabidb.vm.uni-freiburg.de/static/anpdb/download/ANPDB.csv",
    "sdf": "https://phabidb.vm.uni-freiburg.de/static/anpdb/download/ANPDB.sdf",
}


class ANPDBClientError(RuntimeError):
    pass


def _cache_dir() -> Path:
    path = Path(settings.AFRICAN_LIBS_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def cached_dataset(format: str = "csv") -> Optional[Path]:
    fmt = format.lower().lstrip(".")
    if fmt not in _DOWNLOADS:
        raise ValueError("Format ANPDB non supporté; utilisez csv ou sdf.")
    path = _cache_dir() / f"ANPDB.{fmt}"
    return path if path.is_file() and path.stat().st_size > 0 else None


def dataset_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_dataset(format: str = "csv", timeout: float = 60.0) -> dict:
    """Download the official complete ANPDB CSV/SDF, or reuse an offline cache."""
    fmt = format.lower().lstrip(".")
    if fmt not in _DOWNLOADS:
        raise ValueError("Format ANPDB non supporté; utilisez csv ou sdf.")
    destination = _cache_dir() / f"ANPDB.{fmt}"
    try:
        with httpx.stream("GET", _DOWNLOADS[fmt], timeout=timeout, follow_redirects=True) as response:
            response.raise_for_status()
            temp = destination.with_suffix(destination.suffix + ".part")
            total = 0
            with temp.open("wb") as out:
                for chunk in response.iter_bytes(1024 * 1024):
                    total += len(chunk)
                    if total > 250 * 1024 * 1024:
                        raise ANPDBClientError("Dataset ANPDB dépasse la limite de sécurité de 250 MiB.")
                    out.write(chunk)
            if total == 0:
                raise ANPDBClientError("Le téléchargement ANPDB est vide.")
            temp.replace(destination)
    except Exception as exc:
        if destination.is_file() and destination.stat().st_size > 0:
            return {
                "path": str(destination),
                "sha256": dataset_sha256(destination),
                "format": fmt,
                "bytes": destination.stat().st_size,
                "source": _DOWNLOADS[fmt],
                "from_cache": True,
                "network_warning": f"Téléchargement indisponible ({type(exc).__name__}); cache réutilisé.",
            }
        if isinstance(exc, ANPDBClientError):
            raise
        raise ANPDBClientError(
            f"ANPDB non joignable et aucun cache {fmt.upper()} disponible: {type(exc).__name__}"
        ) from exc

    return {
        "path": str(destination),
        "sha256": dataset_sha256(destination),
        "format": fmt,
        "bytes": destination.stat().st_size,
        "source": _DOWNLOADS[fmt],
        "from_cache": False,
        "network_warning": None,
    }


def search_compound(name: str, format: str = "csv", max_results: int = 20) -> dict:
    """Search the local ANPDB CSV cache; download it once if no cache is present."""
    query = (name or "").strip().casefold()
    if len(query) < 2:
        raise ValueError("La recherche ANPDB doit contenir au moins deux caractères.")
    dataset = cached_dataset(format)
    if dataset is None:
        dataset_info = download_dataset(format)
        dataset = Path(dataset_info["path"])
    import csv

    results = []
    with dataset.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        for row in reader:
            if any(query in str(value or "").casefold() for value in row.values()):
                results.append(row)
                if len(results) >= max_results:
                    break
    return {"query": name, "source_file": str(dataset), "matches": results, "count": len(results)}
