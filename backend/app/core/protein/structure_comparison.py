"""Phase 4: comparative structural analysis for WT versus variant targets.

The comparison is deliberately descriptive.  It does not infer pathogenicity,
resistance, binding affinity, or biological effect from geometry alone.

For two structures representing the same protein:
- sequence correspondence is established by global alignment;
- C-alpha coordinates are superposed with Kabsch (appropriate for comparing
  two molecular structures in potentially different coordinate frames);
- global and local RMSD/displacement statistics are reported;
- binding-pocket candidates are detected independently on each structure and
  compared after transforming variant coordinates into the WT frame.

This Kabsch usage is intentionally separate from docking-pose clustering, where
fixed-frame RMSD is required to preserve spatial placement differences.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.core.protein.structure_validation import AA3_TO_1, _atom_fields


@dataclass
class ResidueCA:
    chain_id: str
    residue_id: int
    insertion: str
    aa: str
    x: float
    y: float
    z: float


@dataclass
class Pair:
    wt: ResidueCA
    variant: ResidueCA


def parse_ca_residues(path: str) -> List[ResidueCA]:
    """Read unique C-alpha residues from a PDB/ENT file."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Structure introuvable: {path}")
    out: List[ResidueCA] = []
    seen = set()
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            atom = _atom_fields(raw.rstrip("\n"))
            if atom is None or atom["record"] != "ATOM" or atom["atom_name"] != "CA":
                continue
            if not all(isfinite(atom[k]) for k in ("x", "y", "z")):
                continue
            aa = AA3_TO_1.get(atom["residue_name"])
            if aa is None:
                continue
            key = (atom["chain_id"], atom["residue_id"], atom["insertion"])
            if key in seen:
                continue
            seen.add(key)
            out.append(ResidueCA(
                chain_id=atom["chain_id"],
                residue_id=atom["residue_id"],
                insertion=atom["insertion"],
                aa=aa,
                x=float(atom["x"]), y=float(atom["y"]), z=float(atom["z"]),
            ))
    if len(out) < 3:
        raise ValueError("Au moins 3 résidus C-alpha valides sont nécessaires")
    return out


def _nw_pairs(wt: List[ResidueCA], variant: List[ResidueCA]) -> List[Pair]:
    """Needleman-Wunsch correspondence using the supplied residue sequences."""
    a = "".join(r.aa for r in wt)
    b = "".join(r.aa for r in variant)
    n, m = len(a), len(b)
    gap, match, mismatch = -2, 2, -1
    score = [[0] * (m + 1) for _ in range(n + 1)]
    trace = [["E"] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = i * gap
        trace[i][0] = "U"
    for j in range(1, m + 1):
        score[0][j] = j * gap
        trace[0][j] = "L"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            candidates = (
                (score[i - 1][j - 1] + (match if a[i - 1] == b[j - 1] else mismatch), "D"),
                (score[i - 1][j] + gap, "U"),
                (score[i][j - 1] + gap, "L"),
            )
            score[i][j], trace[i][j] = max(candidates, key=lambda x: x[0])
    i, j = n, m
    pairs: List[Pair] = []
    while i > 0 or j > 0:
        step = trace[i][j]
        if step == "D":
            pairs.append(Pair(wt[i - 1], variant[j - 1]))
            i -= 1; j -= 1
        elif step == "U":
            i -= 1
        elif step == "L":
            j -= 1
        else:
            break
    pairs.reverse()
    return pairs


def _kabsch(moving: np.ndarray, fixed: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Return rotation R and translation t such that moving@R.T+t ≈ fixed."""
    if moving.shape != fixed.shape or moving.shape[0] < 3:
        raise ValueError("Kabsch nécessite au moins 3 correspondances de même taille")
    c_mov = moving.mean(axis=0)
    c_fix = fixed.mean(axis=0)
    x = moving - c_mov
    y = fixed - c_fix
    h = x.T @ y
    u, _, vt = np.linalg.svd(h)
    d = np.linalg.det(vt.T @ u.T)
    correction = np.eye(3)
    if d < 0:
        correction[-1, -1] = -1
    r = vt.T @ correction @ u.T
    t = c_fix - c_mov @ r.T
    return r, t


def _rmsd(a: np.ndarray, b: np.ndarray) -> float:
    diff = a - b
    return float(np.sqrt(np.mean(np.sum(diff * diff, axis=1))))


def _centroid(coords: np.ndarray) -> List[float]:
    return [float(x) for x in coords.mean(axis=0)]


def _extract_all_atoms(path: str) -> List[Dict]:
    atoms = []
    p = Path(path)
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            atom = _atom_fields(raw.rstrip("\n"))
            if atom is None or atom["record"] != "ATOM":
                continue
            if not all(isfinite(atom[k]) for k in ("x", "y", "z")):
                continue
            atoms.append(atom)
    return atoms


def _transform_pocket(pocket: Dict, r: np.ndarray, t: np.ndarray) -> Dict:
    coords = np.asarray(pocket.get("coordinates") or [], dtype=float)
    out = dict(pocket)
    if coords.size:
        transformed = coords @ r.T + t
        out["center_x"] = float(transformed[:, 0].mean())
        out["center_y"] = float(transformed[:, 1].mean())
        out["center_z"] = float(transformed[:, 2].mean())
        out["coordinates"] = transformed.tolist()
    else:
        c = np.array([[pocket.get("center_x", 0.0), pocket.get("center_y", 0.0), pocket.get("center_z", 0.0)]], dtype=float)
        tc = c @ r.T + t
        out["center_x"], out["center_y"], out["center_z"] = map(float, tc[0])
    return out


def _distance(a: Dict, b: Dict) -> float:
    return sqrt(sum((float(a[k]) - float(b[k])) ** 2 for k in ("center_x", "center_y", "center_z")))


def _pocket_summary(wt_atoms: List[Dict], var_atoms: List[Dict], r: np.ndarray, t: np.ndarray) -> Dict:
    try:
        from app.core.docking.pocket_detector import detect_binding_pockets_enriched_fixed
        wt_pockets, wt_err = detect_binding_pockets_enriched_fixed(wt_atoms)
        var_pockets, var_err = detect_binding_pockets_enriched_fixed(var_atoms)
    except Exception as exc:
        return {"available": False, "error_type": type(exc).__name__, "message": str(exc)}
    if not wt_pockets or not var_pockets:
        return {
            "available": False,
            "wt_count": len(wt_pockets or []),
            "variant_count": len(var_pockets or []),
            "wt_error": wt_err,
            "variant_error": var_err,
        }
    var_transformed = [_transform_pocket(p, r, t) for p in var_pockets]
    matches = []
    for wp in wt_pockets:
        nearest = min(var_transformed, key=lambda vp: _distance(wp, vp))
        matches.append({
            "wt_pocket_id": wp.get("pocket_id"),
            "variant_pocket_id": nearest.get("pocket_id"),
            "center_distance_A": _distance(wp, nearest),
            "wt_method": wp.get("method"),
            "variant_method": nearest.get("method"),
            "wt_detection_source": wp.get("detection_source"),
            "variant_detection_source": nearest.get("detection_source"),
            "wt_score": wp.get("score"),
            "variant_score": nearest.get("score"),
        })
    return {
        "available": True,
        "wt_count": len(wt_pockets),
        "variant_count": len(var_pockets),
        "matched_by_nearest_center_after_superposition": matches,
        "note": "Les poches sont comparées après superposition structurale; la proximité spatiale ne prouve pas une identité biologique de site actif.",
    }


def compare_structures(
    wt_path: str,
    variant_path: str,
    variant_protein_position: Optional[int] = None,
    local_radius_A: float = 8.0,
) -> Dict:
    wt_res = parse_ca_residues(wt_path)
    var_res = parse_ca_residues(variant_path)
    pairs = _nw_pairs(wt_res, var_res)
    if len(pairs) < 3:
        raise ValueError("Moins de 3 correspondances structurales après alignement")

    wt_xyz = np.array([[p.wt.x, p.wt.y, p.wt.z] for p in pairs], dtype=float)
    var_xyz = np.array([[p.variant.x, p.variant.y, p.variant.z] for p in pairs], dtype=float)
    pre_rmsd = _rmsd(var_xyz, wt_xyz)
    r, t = _kabsch(var_xyz, wt_xyz)
    var_fit = var_xyz @ r.T + t
    post_rmsd = _rmsd(var_fit, wt_xyz)
    displacements = np.sqrt(np.sum((var_fit - wt_xyz) ** 2, axis=1))

    identity = sum(p.wt.aa == p.variant.aa for p in pairs) / len(pairs)
    coverage_wt = len(pairs) / len(wt_res)
    coverage_variant = len(pairs) / len(var_res)
    mut_record = None
    if variant_protein_position is not None and variant_protein_position >= 1:
        for idx, p in enumerate(pairs):
            if p.wt.residue_id == variant_protein_position or (idx + 1) == variant_protein_position:
                mut_record = {
                    "alignment_index_1_based": idx + 1,
                    "wt_residue_id": p.wt.residue_id,
                    "variant_residue_id": p.variant.residue_id,
                    "wt_aa": p.wt.aa,
                    "variant_aa": p.variant.aa,
                    "ca_displacement_A": float(displacements[idx]),
                }
                break

    local = None
    if mut_record:
        idx = mut_record["alignment_index_1_based"] - 1
        local_mask = np.linalg.norm(wt_xyz - wt_xyz[idx], axis=1) <= float(local_radius_A)
        local_idx = np.where(local_mask)[0]
        if len(local_idx) >= 3:
            local = {
                "radius_A": float(local_radius_A),
                "n_corresponding_residues": int(len(local_idx)),
                "ca_rmsd_A": _rmsd(var_fit[local_idx], wt_xyz[local_idx]),
                "max_ca_displacement_A": float(displacements[local_idx].max()),
                "mean_ca_displacement_A": float(displacements[local_idx].mean()),
            }

    report = {
        "status": "computed",
        "alignment": {
            "corresponding_residues": len(pairs),
            "wt_length": len(wt_res),
            "variant_length": len(var_res),
            "identity": float(identity),
            "coverage_wt": float(coverage_wt),
            "coverage_variant": float(coverage_variant),
        },
        "superposition": {
            "method": "Kabsch over sequence-corresponding C-alpha atoms",
            "pre_alignment_rmsd_A": pre_rmsd,
            "post_alignment_rmsd_A": post_rmsd,
            "mean_ca_displacement_A": float(displacements.mean()),
            "median_ca_displacement_A": float(np.median(displacements)),
            "max_ca_displacement_A": float(displacements.max()),
        },
        "variant_site": mut_record,
        "local_region": local,
        "pocket_comparison": _pocket_summary(_extract_all_atoms(wt_path), _extract_all_atoms(variant_path), r, t),
        "scientific_status": "descriptive_structural_comparison",
        "limitations": [
            "La superposition Kabsch compare deux structures dans un même repère; elle n'est pas utilisée pour le clustering des poses de docking.",
            "Un RMSD faible ou élevé ne démontre ni stabilité, ni résistance, ni efficacité thérapeutique.",
            "La comparaison de poches repose sur les coordonnées obtenues et ne prouve pas qu'une poche est un site actif validé.",
            "La qualité du résultat dépend de la qualité et de la provenance des deux structures.",
        ],
    }
    return report
