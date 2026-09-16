"""Écriture robuste des PDB — le worker Docker tourne souvent en UID 1000
alors que des fichiers précédents peuvent rester root:root 0644."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Optional


def resolve_writable_dir(preferred: Optional[str] = None) -> str:
    """Retourne un répertoire où l'on peut créer des fichiers.

    Essaie ``preferred``, puis un sous-dossier de /tmp, puis mkdtemp.
    Un dossier 0777 avec un fichier 0644 root n'est PAS un échec ici :
    on pourra quand même y créer un nouveau nom.
    """
    candidates = []
    if preferred:
        candidates.append(Path(preferred))
    candidates.append(Path(tempfile.gettempdir()) / "nexora_structure")

    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / f".write_probe_{os.getpid()}_{time.time_ns()}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return str(path.resolve())
        except OSError:
            continue

    return tempfile.mkdtemp(prefix="nexora_structure_")


def write_text_file(output_dir: str, filename: str, content: str) -> str:
    """Écrit ``filename`` dans un dossier writable.

    Si le fichier cible existe et n'est pas inscriptible (cas typique :
    PDB root:root 0644 dans un dossier 0777), on utilise un nom unique.
    Si même ça échoue, repli sur tempfile.
    """
    directory = Path(resolve_writable_dir(output_dir))
    dest = directory / filename

    if dest.exists() and not os.access(dest, os.W_OK):
        dest = directory / _unique_name(filename)

    try:
        dest.write_text(content, encoding="utf-8")
        return str(dest.resolve())
    except OSError:
        dest = directory / _unique_name(filename)
        try:
            dest.write_text(content, encoding="utf-8")
            return str(dest.resolve())
        except OSError:
            fallback_dir = Path(tempfile.mkdtemp(prefix="nexora_pdb_"))
            fallback = fallback_dir / Path(filename).name
            fallback.write_text(content, encoding="utf-8")
            return str(fallback.resolve())


def _unique_name(filename: str) -> str:
    path = Path(filename)
    return f"{path.stem}_{os.getpid()}_{time.time_ns()}{path.suffix}"
