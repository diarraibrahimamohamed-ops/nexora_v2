"""Antimicrobial-resistance evidence layer for N3XORA.

This module deliberately does NOT convert mutation counts into a resistance
percentage.  It reports genetic evidence, its source/method, and whether a
phenotype-level conclusion is supported.  When AMRFinderPlus is installed it
is the preferred engine for bacterial assembled nucleotide sequences.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

ANTIBIOTIC_CLASSES = {
    "penicillin": {"class": "beta-lactam", "targets": {"beta-lactamase", "PBP alteration", "target protection"}},
    "tetracycline": {"class": "tetracycline", "targets": {"efflux", "ribosomal protection", "enzymatic inactivation"}},
    "chloramphenicol": {"class": "phenicol", "targets": {"enzymatic inactivation", "efflux"}},
    "streptomycin": {"class": "aminoglycoside", "targets": {"drug modification", "target alteration", "efflux"}},
    "rifampicin": {"class": "rifamycin", "targets": {"target alteration"}},
    "vancomycin": {"class": "glycopeptide", "targets": {"target replacement", "cell-wall alteration"}},
}


def _clean_dna(sequence: str) -> str:
    return "".join(c for c in (sequence or "").upper() if c in "ACGT")


def _run_amrfinder(sequence: str) -> Dict[str, Any]:
    exe = shutil.which("amrfinder") or shutil.which("amrfinderplus")
    if not exe:
        return {"available": False, "hits": [], "reason": "AMRFinderPlus executable not installed"}

    sequence = _clean_dna(sequence)
    if not sequence:
        return {"available": True, "hits": [], "reason": "empty sequence"}

    with tempfile.TemporaryDirectory(prefix="n3xora_amr_") as td:
        fasta = Path(td) / "input.fna"
        out = Path(td) / "amr.tsv"
        fasta.write_text(">N3XORA_input\n" + sequence + "\n", encoding="utf-8")
        cmd = [exe, "-n", str(fasta), "-o", str(out), "--plus"]
        try:
            completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"available": True, "hits": [], "reason": f"AMRFinderPlus execution failed: {type(exc).__name__}"}
        if completed.returncode != 0:
            return {"available": True, "hits": [], "reason": "AMRFinderPlus returned a non-zero exit code"}
        if not out.exists():
            return {"available": True, "hits": [], "reason": "AMRFinderPlus produced no output"}

        lines = [line for line in out.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        if not lines:
            return {"available": True, "hits": [], "reason": "no AMR hits reported"}
        header = lines[0].split("\t")
        hits: List[Dict[str, Any]] = []
        for line in lines[1:]:
            fields = line.split("\t")
            row = dict(zip(header, fields))
            hits.append({
                "gene_symbol": row.get("Element symbol") or row.get("Gene symbol") or row.get("Element Name"),
                "method": row.get("Method"),
                "identity_pct": _float(row.get("% Identity") or row.get("Percent Identity")),
                "coverage_pct": _float(row.get("% Coverage")),
                "target": row.get("Target Identifier"),
                "subtype": row.get("Subclass"),
                "phenotype": row.get("AMR Gene Family") or row.get("Resistant phenotype"),
                "raw": row,
            })
        return {"available": True, "hits": hits, "reason": "AMRFinderPlus"}


def _float(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def analyze_amr(sequence: str, organism_type: str = "bacterium") -> Dict[str, Any]:
    """Return a deterministic AMR evidence report.

    Virus + antibiotic is reported as not applicable: antibiotics do not
    establish antiviral resistance.  For bacteria, AMRFinderPlus is preferred;
    absent the tool, the UI must clearly report that no validated AMR engine is
    available instead of inventing a score from mutation count.
    """
    org = (organism_type or "bacterium").lower()
    if org in {"virus", "viral"}:
        return {
            "success": True,
            "organism_type": "virus",
            "applicability": "not_applicable",
            "phenotype_prediction": None,
            "confidence": None,
            "evidence_level": "none",
            "method": "scope_guard",
            "interpretation": "La résistance aux antibiotiques n'est pas applicable à un virus. Une analyse de résistance antivirale nécessiterait une autre base de données et d'autres règles.",
            "hits": [],
        }

    engine = _run_amrfinder(sequence)
    evidence_level = "none"
    if engine["hits"]:
        # These are evidence labels, not probabilities.
        methods = {str(hit.get("method") or "").upper() for hit in engine["hits"]}
        if any(m.startswith("EXACT") or m.startswith("ALLELE") for m in methods):
            evidence_level = "strong_genotypic"
        elif any(m.startswith("BLAST") or m == "HMM" or m == "POINT" for m in methods):
            evidence_level = "moderate_to_strong_genotypic"
        else:
            evidence_level = "genotypic"

    return {
        "success": True,
        "organism_type": "bacterium",
        "applicability": "applicable",
        "phenotype_prediction": None,
        "confidence": None,
        "evidence_level": evidence_level,
        "method": "AMRFinderPlus" if engine["available"] else "not_available",
        "engine_available": engine["available"],
        "engine_reason": engine["reason"],
        "interpretation": (
            "Déterminants génétiques de résistance détectés; ils doivent être interprétés avec le niveau de preuve et le contexte taxonomique."
            if engine["hits"] else
            "Aucun déterminant AMR n'a été détecté par l'analyse disponible; l'absence de hit ne prouve pas la sensibilité phénotypique."
            if engine["available"] else
            "Analyse AMR validée indisponible dans cet environnement: aucun score de résistance n'est généré."
        ),
        "hits": engine["hits"],
        "antibiotic_classes": ANTIBIOTIC_CLASSES,
        "limitations": [
            "Le génotype ne remplace pas un antibiogramme (AST).",
            "L'absence de déterminant détecté ne garantit pas la sensibilité.",
            "Les mécanismes intrinsèques, l'expression, la régulation et le contexte taxonomique peuvent modifier le phénotype.",
        ],
    }
