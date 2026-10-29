"""AMRFinderPlus integration for bacterial AMR determinant evidence.

This service reports genotypic evidence only. It never converts hit counts into a
clinical resistance probability and never treats no hit as proof of susceptibility.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

REQUIRED_AMRFINDER_DB_FILES = (
    "version.txt", "AMR.LIB", "AMRProt.fa", "AMRProt-mutation.tsv",
    "fam.tsv", "taxgroup.tsv", "database_format_version.txt",
)
VALID_IUPAC_DNA = set("ACGTRYSWKMBDHVN")
ANTIBIOTIC_CLASSES = {
    "penicillin": {"class": "beta-lactam"},
    "tetracycline": {"class": "tetracycline"},
    "chloramphenicol": {"class": "phenicol"},
    "streptomycin": {"class": "aminoglycoside"},
    "rifampicin": {"class": "rifamycin"},
    "vancomycin": {"class": "glycopeptide"},
}


def normalize_amr_fasta(raw: str) -> Tuple[str, int, int]:
    """Normalize plain DNA or multi-record FASTA without losing contig boundaries."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Séquence ADN/FASTA vide.")
    records: List[str] = []
    parts: List[str] = []

    def flush_record() -> None:
        nonlocal parts
        sequence = "".join(parts)
        if sequence:
            records.append(sequence)
        parts = []

    for source_line in raw.splitlines():
        line = source_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            flush_record()
            continue
        sequence_line = re.sub(r"\s+", "", line).upper()
        invalid = set(sequence_line) - VALID_IUPAC_DNA
        if invalid:
            display = ", ".join(sorted(invalid)[:8])
            raise ValueError("Séquence invalide pour AMRFinderPlus; caractères non nucléotidiques ou gaps détectés: " + display + ".")
        if sequence_line:
            parts.append(sequence_line)

    flush_record()
    if not records:
        raise ValueError("Aucune séquence nucléotidique exploitable dans l'entrée.")
    normalized = "\n".join(
        ">N3XORA_contig_" + str(index) + "\n" + sequence
        for index, sequence in enumerate(records, start=1)
    )
    return normalized + "\n", len(records), sum(len(record) for record in records)


def amrfinder_status() -> Dict[str, Any]:
    """Check executable, version, and required reference-database files."""
    configured = (settings.AMRFINDER_PATH or "amrfinder").strip()
    executable = (configured if Path(configured).is_file() else shutil.which(configured)) or shutil.which("amrfinder") or shutil.which("amrfinderplus")
    db_root = Path(settings.AMRFINDER_DB_DIR)
    db_path = db_root / "latest"
    if not db_path.is_dir() and db_root.is_dir() and any(db_root.iterdir()):
        db_path = db_root
    missing = [name for name in REQUIRED_AMRFINDER_DB_FILES if not (db_path / name).is_file()] if db_path.is_dir() else list(REQUIRED_AMRFINDER_DB_FILES)
    database_available = db_path.is_dir() and not missing
    version = None
    version_error = None
    if executable:
        try:
            result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10, check=False)
            version = (result.stdout or result.stderr).strip()[:300] or None
            if result.returncode != 0:
                version_error = "amrfinder --version returned " + str(result.returncode)
        except (OSError, subprocess.SubprocessError) as exc:
            version_error = "version probe failed: " + type(exc).__name__

    database_version = None
    database_release = None
    if database_available:
        try:
            database_version = (db_path / "version.txt").read_text(encoding="utf-8", errors="replace").strip()[:200] or None
        except OSError:
            pass
        try:
            database_release = db_path.resolve().name
        except OSError:
            database_release = db_path.name

    ready = bool(executable and version and not version_error and database_available)
    if ready:
        message = "AMRFinderPlus prêt."
    elif not executable:
        message = "Exécutable AMRFinderPlus absent; reconstruire l'image avec INSTALL_AMRFINDERPLUS=true."
    elif not version or version_error:
        message = "La version du binaire AMRFinderPlus ne peut pas être confirmée."
    else:
        message = "Base AMRFinderPlus absente/incomplète; exécuter backend/scripts/update_amrfinder_db.sh. Fichiers manquants: " + ", ".join(missing)

    return {
        "executable_available": bool(executable), "executable_path": executable,
        "version": version, "version_error": version_error,
        "database_available": database_available, "database_path": str(db_path),
        "database_missing_files": missing, "database_version": database_version,
        "database_release": database_release, "ready": ready, "message": message,
        "scope": "déterminants génotypiques bactériens; ne remplace pas un AST/antibiogramme",
    }


@lru_cache(maxsize=8)
def _organism_groups_cached(executable: str) -> Tuple[str, ...]:
    """Cache valid NCBI taxonomic groups reported by the installed executable."""
    try:
        result = subprocess.run([executable, "--list_organisms"], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode != 0:
        return ()
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    groups = []
    seen = set()
    for source_line in output.splitlines():
        line = source_line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        candidate = line.split("\t", 1)[0].strip()
        if candidate.lower() in {"organism", "organisms", "organism group", "organism groups", "taxonomic group", "taxonomic groups", "name"}:
            continue
        if len(candidate) > 120 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._()/-]*", candidate):
            continue
        if candidate not in seen:
            seen.add(candidate)
            groups.append(candidate)
    return tuple(groups)


def list_amrfinder_organisms() -> List[str]:
    """Return organism groups supported by the installed AMRFinderPlus binary."""
    status = amrfinder_status()
    executable = status.get("executable_path")
    return list(_organism_groups_cached(executable)) if executable else []


def _float(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _run_amrfinder(normalized_fasta: str, taxonomic_group: Optional[str] = None) -> Dict[str, Any]:
    status = amrfinder_status()
    executable = status["executable_path"]
    if not status["ready"]:
        return {"available": False, "hits": [], "reason": status["message"], "status": status}
    if taxonomic_group and taxonomic_group not in list_amrfinder_organisms():
        return {
            "available": False, "hits": [],
            "reason": "Groupe taxonomique non reconnu par cette version d'AMRFinderPlus.",
            "status": status,
        }

    with tempfile.TemporaryDirectory(prefix="n3xora_amr_") as temporary_dir:
        fasta_path = Path(temporary_dir) / "input.fna"
        output_path = Path(temporary_dir) / "amr.tsv"
        fasta_path.write_text(normalized_fasta, encoding="utf-8")
        command = [executable, "-n", str(fasta_path), "-o", str(output_path), "--database", status["database_path"]]
        if taxonomic_group:
            command.extend(["--organism", taxonomic_group])
        try:
            completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            return {"available": True, "hits": [], "reason": "AMRFinderPlus a dépassé le délai maximal de 600 secondes.", "status": status}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"available": True, "hits": [], "reason": "Échec d'exécution AMRFinderPlus (" + type(exc).__name__ + ").", "status": status}
        if completed.returncode != 0:
            return {
                "available": True, "hits": [], "reason": "AMRFinderPlus a retourné un code d'erreur non nul.",
                "status": status, "diagnostic": (completed.stderr or completed.stdout or "")[-1000:],
            }
        if not output_path.exists():
            return {"available": True, "hits": [], "reason": "AMRFinderPlus n'a pas produit de fichier résultat.", "status": status}
        lines = [line for line in output_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        if not lines:
            return {"available": True, "hits": [], "reason": "aucun déterminant AMR rapporté", "status": status}
        header = lines[0].lstrip("# ").split("\t")
        hits: List[Dict[str, Any]] = []
        for line in lines[1:]:
            if line.startswith("#"):
                continue
            row = dict(zip(header, line.split("\t")))
            hits.append({
                "gene_symbol": row.get("Element symbol") or row.get("Gene symbol") or row.get("Element Name"),
                "method": row.get("Method"),
                "identity_pct": _float(row.get("% Identity") or row.get("Percent Identity")),
                "coverage_pct": _float(row.get("% Coverage"),
                ),
                "target": row.get("Target Identifier"),
                "subtype": row.get("Subclass"),
                "phenotype": row.get("AMR Gene Family") or row.get("Resistant phenotype"),
                "raw": row,
            })
        return {"available": True, "hits": hits, "reason": "AMRFinderPlus" if hits else "aucun déterminant AMR rapporté", "status": status}


def analyze_amr(sequence: str, organism_type: str = "bacterium", taxonomic_group: Optional[str] = None) -> Dict[str, Any]:
    """Return genotypic evidence; the result is not a clinical phenotype prediction."""
    organism = (organism_type or "bacterium").strip().lower()
    if organism in {"virus", "viral", "human", "humain"}:
        scope = "virus" if organism in {"virus", "viral"} else "humain"
        return {
            "success": True, "organism_type": scope, "applicability": "not_applicable",
            "phenotype_prediction": None, "confidence": None, "evidence_level": "none",
            "method": "scope_guard", "engine_available": False,
            "interpretation": "AMRFinderPlus cible les déterminants bactériens de résistance antimicrobienne; cette séquence ne doit pas être interprétée avec ce moteur.",
            "hits": [],
            "limitations": ["Pour un virus, utiliser une méthode de résistance antivirale spécifique; pour l'humain, l'analyse AMR bactérienne est hors périmètre."],
        }
    if organism not in {"bacterium", "bacteria", "bactérie", "microbe"}:
        raise ValueError("organism_type doit être bacterium, virus ou human.")

    normalized_fasta, record_count, sequence_length = normalize_amr_fasta(sequence)
    group = (taxonomic_group or "").strip() or None
    engine = _run_amrfinder(normalized_fasta, group)
    evidence_level = "none"
    if engine["hits"]:
        methods = {str(hit.get("method") or "").upper() for hit in engine["hits"]}
        if any(method.startswith(("EXACT", "ALLELE")) for method in methods):
            evidence_level = "strong_genotypic"
        elif any(method.startswith(("BLAST", "HMM", "POINT")) for method in methods):
            evidence_level = "moderate_to_strong_genotypic"
        else:
            evidence_level = "genotypic"

    status = engine.get("status") or {}
    available = bool(engine["available"])
    if engine["hits"]:
        interpretation = "Déterminants génétiques détectés; examiner le gène, la méthode et le contexte taxonomique avant toute conclusion."
    elif available:
        interpretation = "Aucun déterminant AMR détecté; cela ne prouve pas la sensibilité phénotypique."
    else:
        interpretation = "Analyse AMR validée indisponible; aucun score de résistance n'est généré."
    if available and not group:
        interpretation += " Sans groupe taxonomique explicite, certains appels de mutations spécifiques à un organisme peuvent ne pas être effectués."

    return {
        "success": True, "organism_type": "bacterium", "applicability": "applicable",
        "phenotype_prediction": None, "confidence": None, "evidence_level": evidence_level,
        "method": "AMRFinderPlus" if available else "not_available",
        "engine_available": available, "engine_reason": engine["reason"],
        "taxonomic_group": group, "sequence_length": sequence_length, "input_records": record_count,
        "software_version": status.get("version"), "database_version": status.get("database_version"),
        "database_release": status.get("database_release"), "hits": engine["hits"],
        "interpretation": interpretation,
        "limitations": [
            "Le génotype ne remplace pas un antibiogramme (AST).",
            "L'absence de déterminant détecté ne garantit pas la sensibilité.",
            "Les mécanismes intrinsèques, l'expression, la régulation et le contexte taxonomique peuvent modifier le phénotype.",
            "Les appels de mutations spécifiques peuvent nécessiter un groupe taxonomique explicite.",
        ],
    }
