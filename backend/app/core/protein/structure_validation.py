"""Validation and provenance utilities for N3XORA research structures.

Phase 3 deliberately separates:
- experimental structures (quality described from experimental metadata when available),
- predicted structures (pLDDT-like confidence, if present), and
- computational acceptance checks against the requested protein sequence.

No structure is declared biologically correct by this module.  The output is an
operational quality/readiness report that must be interpreted with provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

AA3_TO_1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}


@dataclass
class ChainRecord:
    chain_id: str
    residues: List[Tuple[int, str]]
    sequence: str


@dataclass
class AlignmentStats:
    identity: float
    coverage: float
    aligned_length: int
    matches: int
    gaps_in_target: int
    gaps_in_structure: int


def _atom_fields(line: str) -> Optional[dict]:
    if not (line.startswith("ATOM  ") or line.startswith("HETATM")):
        return None
    if len(line) < 54:
        return None
    altloc = line[16:17].strip()
    if altloc not in ("", "A", "1"):
        return None
    try:
        serial = int(line[6:11].strip())
    except ValueError:
        serial = 0
    atom_name = line[12:16].strip()
    residue_name = line[17:20].strip().upper()
    chain_id = (line[21:22].strip() or "_")
    try:
        residue_id = int(line[22:26].strip())
    except ValueError:
        return None
    insertion = line[26:27].strip()
    try:
        x = float(line[30:38].strip())
        y = float(line[38:46].strip())
        z = float(line[46:54].strip())
    except ValueError:
        return None
    b_factor = None
    if len(line) >= 66:
        try:
            b_factor = float(line[60:66].strip())
        except ValueError:
            pass
    element = line[76:78].strip().upper() if len(line) >= 78 else ""
    return {
        "serial": serial, "atom_name": atom_name, "residue_name": residue_name,
        "chain_id": chain_id, "residue_id": residue_id, "insertion": insertion,
        "x": x, "y": y, "z": z, "b_factor": b_factor, "element": element,
        "record": line[:6].strip(),
    }


def parse_pdb_structure(path: str) -> Dict:
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Structure introuvable: {path}")

    chains: Dict[str, List[Tuple[Tuple[int, str], str]]] = {}
    atom_count = 0
    heavy_atom_count = 0
    ca_count = 0
    invalid_coordinates = 0
    plddt_values: List[float] = []
    seen_ca = set()

    with p.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            atom = _atom_fields(line.rstrip("\n"))
            if atom is None or atom["record"] != "ATOM":
                continue
            atom_count += 1
            if not all(isfinite(atom[k]) for k in ("x", "y", "z")):
                invalid_coordinates += 1
                continue
            if (atom["element"] or atom["atom_name"][:1]).upper() != "H":
                heavy_atom_count += 1
            if atom["atom_name"] == "CA":
                key = (atom["chain_id"], atom["residue_id"], atom["insertion"])
                if key not in seen_ca:
                    seen_ca.add(key)
                    ca_count += 1
                    if atom["residue_name"] in AA3_TO_1:
                        chains.setdefault(atom["chain_id"], []).append(
                            ((atom["residue_id"], atom["insertion"]), AA3_TO_1[atom["residue_name"]])
                        )
                        if atom["b_factor"] is not None and 0.0 <= atom["b_factor"] <= 100.0:
                            plddt_values.append(atom["b_factor"])

    chain_records: List[ChainRecord] = []
    for chain_id, residues in chains.items():
        residues = sorted(residues, key=lambda x: (x[0][0], x[0][1]))
        # Collapse accidental duplicate residue identifiers while retaining order.
        seen = set()
        clean = []
        for loc, aa in residues:
            if loc in seen:
                continue
            seen.add(loc)
            clean.append((loc[0], aa))
        chain_records.append(ChainRecord(chain_id, clean, "".join(aa for _, aa in clean)))

    chain_records.sort(key=lambda c: (-len(c.sequence), c.chain_id))
    return {
        "atom_count": atom_count,
        "heavy_atom_count": heavy_atom_count,
        "ca_count": ca_count,
        "chain_count": len(chain_records),
        "chains": [
            {"chain_id": c.chain_id, "length": len(c.sequence), "sequence": c.sequence}
            for c in chain_records
        ],
        "plddt": {
            "available": bool(plddt_values),
            "mean": mean(plddt_values) if plddt_values else None,
            "median": median(plddt_values) if plddt_values else None,
            "n_residues": len(plddt_values),
        },
        "invalid_coordinates": invalid_coordinates,
    }


def needleman_wunsch_stats(reference: str, structure: str) -> AlignmentStats:
    """Global alignment stats with linear gap penalty.

    Used only for sequence-to-structure correspondence and not as a phylogenetic
    or evolutionary inference engine.
    """
    ref = reference.upper()
    qry = structure.upper()
    n, m = len(ref), len(qry)
    if not n or not m:
        return AlignmentStats(0.0, 0.0, 0, 0, n, m)

    match_score, mismatch_score, gap_score = 2, -1, -2
    # Two-row score DP plus traceback matrix for the compact target sizes used here.
    scores = [[0] * (m + 1) for _ in range(n + 1)]
    trace = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        scores[i][0] = i * gap_score
        trace[i][0] = "U"
    for j in range(1, m + 1):
        scores[0][j] = j * gap_score
        trace[0][j] = "L"
    trace[0][0] = "E"

    for i in range(1, n + 1):
        ri = ref[i - 1]
        for j in range(1, m + 1):
            diag = scores[i - 1][j - 1] + (match_score if ri == qry[j - 1] else mismatch_score)
            up = scores[i - 1][j] + gap_score
            left = scores[i][j - 1] + gap_score
            best = max((diag, "D"), (up, "U"), (left, "L"), key=lambda x: x[0])
            scores[i][j], trace[i][j] = best

    i, j = n, m
    matches = aligned = gaps_target = gaps_structure = 0
    while i > 0 or j > 0:
        step = trace[i][j]
        if step == "D":
            aligned += 1
            if ref[i - 1] == qry[j - 1]:
                matches += 1
            i -= 1
            j -= 1
        elif step == "U":
            gaps_in_structure = True
            aligned += 1
            gaps_structure += 1
            i -= 1
        elif step == "L":
            aligned += 1
            gaps_target += 1
            j -= 1
        else:
            break

    identity = matches / max(1, aligned - gaps_target - gaps_structure)
    # Coverage is the fraction of target residues represented by aligned structure residues.
    coverage = (n - gaps_structure) / max(1, n)
    return AlignmentStats(identity, coverage, aligned, matches, gaps_target, gaps_structure)


def best_chain_match(reference_sequence: str, parsed: Dict) -> Dict:
    best = None
    for chain in parsed["chains"]:
        stats = needleman_wunsch_stats(reference_sequence, chain["sequence"])
        candidate = {
            "chain_id": chain["chain_id"],
            "chain_length": chain["length"],
            "identity": stats.identity,
            "coverage": stats.coverage,
            "aligned_length": stats.aligned_length,
            "matches": stats.matches,
            "gaps_in_target": stats.gaps_in_target,
            "gaps_in_structure": stats.gaps_in_structure,
        }
        rank = (stats.identity * stats.coverage, stats.coverage, stats.identity, -abs(len(reference_sequence) - chain["length"]))
        if best is None or rank > best[0]:
            best = (rank, candidate)
    return best[1] if best else {
        "chain_id": None, "chain_length": 0, "identity": 0.0, "coverage": 0.0,
        "aligned_length": 0, "matches": 0, "gaps_in_target": len(reference_sequence), "gaps_in_structure": 0,
    }


def validate_structure(
    pdb_path: str,
    target_sequence: str,
    source_type: str,
    identity_cutoff: float = 0.70,
    coverage_cutoff: float = 0.70,
) -> Dict:
    target = "".join(target_sequence.split()).upper()
    parsed = parse_pdb_structure(pdb_path)
    if len(target) < 3:
        raise ValueError("Séquence cible trop courte pour la validation structurale")
    if parsed["atom_count"] == 0 or parsed["ca_count"] == 0:
        raise ValueError("Structure PDB sans atomes protéiques exploitables")
    if parsed["invalid_coordinates"]:
        raise ValueError(f"Structure contenant {parsed['invalid_coordinates']} coordonnées non finies")

    best = best_chain_match(target, parsed)
    plddt = parsed["plddt"]
    plddt_score = (plddt["mean"] / 100.0) if plddt["available"] else None

    sequence_ok = best["identity"] >= identity_cutoff and best["coverage"] >= coverage_cutoff
    if source_type == "experimental_pdb":
        status = "accepted_for_docking_review" if sequence_ok else "sequence_mismatch"
        confidence = None
    elif source_type in {"predicted_esmfold", "predicted_colabfold", "predicted"}:
        status = "predicted_reviewable" if sequence_ok else "sequence_mismatch"
        confidence = plddt_score
    else:
        status = "accepted_for_docking_review" if sequence_ok else "sequence_mismatch"
        confidence = None

    return {
        "status": status,
        "sequence_match": {
            "reference_length": len(target),
            "best_chain": best,
            "identity_cutoff": identity_cutoff,
            "coverage_cutoff": coverage_cutoff,
        },
        "geometry": {
            "atom_count": parsed["atom_count"],
            "heavy_atom_count": parsed["heavy_atom_count"],
            "ca_count": parsed["ca_count"],
            "chain_count": parsed["chain_count"],
        },
        "predicted_confidence": {
            "metric": "pLDDT-like B-factor" if plddt["available"] else None,
            "mean": plddt["mean"],
            "median": plddt["median"],
            "normalized_mean": plddt_score,
            "n_residues": plddt["n_residues"],
        },
        "source_type": source_type,
        "note": (
            "Critère opérationnel de correspondance séquence/structure; il ne constitue pas une validation biologique. "
            "Pour une structure prédite, pLDDT informe la confiance locale du modèle et non sa justesse biologique globale."
        ),
        "eligible_for_docking_review": bool(sequence_ok),
    }
