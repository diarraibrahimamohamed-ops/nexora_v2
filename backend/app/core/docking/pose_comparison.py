"""Comparative analysis of docked poses for matched WT/variant runs.

Phase 6 contract:
- same ligand and declared paired protocol only;
- transform the *variant receptor/ligand frame* into the WT frame using a
  protein C-alpha Kabsch superposition;
- compare ligand poses using fixed-frame RMSD after that receptor superposition;
- compare protein-contact residue sets using a conservative heavy-atom distance
  rule; do not label contacts as H-bonds, salt bridges, hydrophobic contacts,
  etc. without a dedicated interaction profiler;
- never convert score contrasts into experimental affinity/free-energy claims.

This module deliberately stays geometry-first and provenance-rich. It is not
an interaction-energy model and does not infer biological causality.
"""
from __future__ import annotations

from collections import Counter
from math import isfinite, sqrt
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from app.core.protein.structure_validation import AA3_TO_1, _atom_fields


DEFAULT_CONTACT_CUTOFF_A = 4.0
DEFAULT_TOP_POSES = 10

_AA1_TO_AA3 = {
    "A":"ALA","R":"ARG","N":"ASN","D":"ASP","C":"CYS","Q":"GLN",
    "E":"GLU","G":"GLY","H":"HIS","I":"ILE","L":"LEU","K":"LYS",
    "M":"MET","F":"PHE","P":"PRO","S":"SER","T":"THR","W":"TRP",
    "Y":"TYR","V":"VAL",
}

def _aa3_from_aa1(aa: str) -> str:
    return _AA1_TO_AA3.get(str(aa).upper(), "UNK")


def _finite_xyz(atom: Dict[str, Any]) -> bool:
    return all(isfinite(float(atom.get(k))) for k in ("x", "y", "z"))


def _heavy(atom: Dict[str, Any]) -> bool:
    return str(atom.get("element") or atom.get("atom") or "").upper() not in {"H", "HD"}


def _pose_heavy_atoms(pose: Dict[str, Any]) -> List[Dict[str, Any]]:
    atoms = pose.get("atoms") if isinstance(pose.get("atoms"), list) else []
    out = [a for a in atoms if isinstance(a, dict) and _finite_xyz(a) and _heavy(a)]
    return out


def _canonical_smiles(smiles: str) -> str:
    text = (smiles or "").strip()
    if not text:
        return ""
    try:
        from rdkit import Chem
        mol = Chem.MolFromSmiles(text)
        if mol is not None:
            return Chem.MolToSmiles(mol, canonical=True)
    except Exception:
        pass
    return text


def _load_json(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Résultat absent: {path}")
    import json
    with p.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not data.get("success"):
        raise ValueError("Résultat de docking invalide ou non réussi")
    poses = data.get("poses")
    if not isinstance(poses, list) or not poses:
        raise ValueError("Aucune pose exploitable dans le résultat")
    return data


def _load_protein_ca(path: str) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Structure absente: {path}")
    out: List[Dict[str, Any]] = []
    seen = set()
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            atom = _atom_fields(raw.rstrip("\n"))
            if atom is None or atom["record"] != "ATOM" or atom["atom_name"] != "CA":
                continue
            if not _finite_xyz(atom):
                continue
            aa = AA3_TO_1.get(atom["residue_name"])
            if aa is None:
                continue
            key = (atom["chain_id"], atom["residue_id"], atom["insertion"])
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "chain_id": atom["chain_id"],
                "residue_id": int(atom["residue_id"]),
                "insertion": atom["insertion"],
                "aa": aa,
                "x": float(atom["x"]), "y": float(atom["y"]), "z": float(atom["z"]),
            })
    if len(out) < 3:
        raise ValueError("Au moins 3 C-alpha valides sont nécessaires pour la superposition")
    return out


def _nw_correspondence(wt: Sequence[Dict[str, Any]], var: Sequence[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    a = "".join(r["aa"] for r in wt)
    b = "".join(r["aa"] for r in var)
    n, m = len(a), len(b)
    gap, match, mismatch = -2, 2, -1
    score = [[0] * (m + 1) for _ in range(n + 1)]
    trace = [["E"] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0], trace[i][0] = i * gap, "U"
    for j in range(1, m + 1):
        score[0][j], trace[0][j] = j * gap, "L"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cands = (
                (score[i - 1][j - 1] + (match if a[i - 1] == b[j - 1] else mismatch), "D"),
                (score[i - 1][j] + gap, "U"),
                (score[i][j - 1] + gap, "L"),
            )
            score[i][j], trace[i][j] = max(cands, key=lambda x: x[0])
    i, j = n, m
    pairs = []
    while i > 0 or j > 0:
        step = trace[i][j]
        if step == "D":
            pairs.append((wt[i - 1], var[j - 1])); i -= 1; j -= 1
        elif step == "U":
            i -= 1
        elif step == "L":
            j -= 1
        else:
            break
    pairs.reverse()
    return pairs


def _kabsch(moving: np.ndarray, fixed: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    if moving.shape != fixed.shape or moving.shape[0] < 3:
        raise ValueError("Kabsch nécessite des correspondances de même taille (>=3)")
    c_mov = moving.mean(axis=0)
    c_fix = fixed.mean(axis=0)
    x = moving - c_mov
    y = fixed - c_fix
    h = x.T @ y
    u, _, vt = np.linalg.svd(h)
    correction = np.eye(3)
    if np.linalg.det(vt.T @ u.T) < 0:
        correction[-1, -1] = -1
    r = vt.T @ correction @ u.T
    t = c_fix - c_mov @ r.T
    return r, t


def _apply_transform(atoms: Iterable[Dict[str, Any]], r: np.ndarray, t: np.ndarray) -> List[Dict[str, Any]]:
    out = []
    for atom in atoms:
        xyz = np.array([[float(atom["x"]), float(atom["y"]), float(atom["z"])]], dtype=float)
        new_xyz = xyz @ r.T + t
        a = dict(atom)
        a["x"], a["y"], a["z"] = map(float, new_xyz[0])
        out.append(a)
    return out


def _match_ligand_atoms(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Match by element + atom name + occurrence, fallback to order.

    Vina normally preserves ligand atom order between paired runs because the
    same prepared ligand is used. Duplicate names are disambiguated by their
    occurrence index. The result records this contract explicitly.
    """
    def keyed(atoms):
        counts = Counter()
        out = {}
        for atom in atoms:
            key0 = (str(atom.get("element") or atom.get("atom") or "").upper(), str(atom.get("atom_name") or "").upper())
            counts[key0] += 1
            out[(key0, counts[key0])] = atom
        return out
    ka, kb = keyed(a), keyed(b)
    shared = [key for key in ka.keys() if key in kb]
    if len(shared) == min(len(a), len(b)) and len(a) == len(b):
        return [(ka[k], kb[k]) for k in sorted(shared, key=str)]
    if len(a) == len(b):
        return list(zip(a, b))
    return []


def _rmsd(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape or not len(a):
        raise ValueError("Coordonnées incompatibles pour le RMSD")
    return float(np.sqrt(np.mean(np.sum((a - b) ** 2, axis=1))))


def _centroid(atoms: Sequence[Dict[str, Any]]) -> List[float]:
    xyz = np.array([[float(a["x"]), float(a["y"]), float(a["z"])] for a in atoms], dtype=float)
    return [float(v) for v in xyz.mean(axis=0)]


def _contact_fingerprint(protein_path: str, ligand_atoms: Sequence[Dict[str, Any]], cutoff_A: float) -> List[str]:
    """Return residue keys with >=1 protein heavy atom within cutoff."""
    p = Path(protein_path)
    if not p.is_file():
        raise ValueError(f"Structure absente: {protein_path}")
    protein_atoms = []
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            atom = _atom_fields(raw.rstrip("\n"))
            if atom is None or atom["record"] != "ATOM" or not _finite_xyz(atom) or not _heavy(atom):
                continue
            protein_atoms.append(atom)
    lig = np.array([[float(a["x"]), float(a["y"]), float(a["z"])] for a in ligand_atoms], dtype=float)
    seen = set()
    cutoff2 = float(cutoff_A) ** 2
    for atom in protein_atoms:
        pxyz = np.array([float(atom["x"]), float(atom["y"]), float(atom["z"])])
        d2 = np.min(np.sum((lig - pxyz) ** 2, axis=1))
        if d2 <= cutoff2:
            seen.add(f"{atom['chain_id']}:{atom['residue_id']}{atom['insertion']}:{atom['residue_name']}")
    return sorted(seen)


def _jaccard(a: Sequence[str], b: Sequence[str]) -> Optional[float]:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa and sb or sb and not sa:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _pose_score(pose: Dict[str, Any]) -> float:
    value = pose.get("score")
    if not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise ValueError("Pose avec score non fini")
    return float(value)


def compare_docking_pose_sets(
    wt_result_path: str,
    variant_result_path: str,
    wt_structure_path: str,
    variant_structure_path: str,
    *,
    ligand_smiles: str,
    top_poses: int = DEFAULT_TOP_POSES,
    contact_cutoff_A: float = DEFAULT_CONTACT_CUTOFF_A,
) -> Dict[str, Any]:
    if not (1 <= int(top_poses) <= 20):
        raise ValueError("top_poses doit être compris entre 1 et 20")
    if not (2.5 <= float(contact_cutoff_A) <= 5.5):
        raise ValueError("contact_cutoff_A doit être compris entre 2.5 et 5.5 Å")
    if not _canonical_smiles(ligand_smiles):
        raise ValueError("Ligand absent")

    wt_data = _load_json(wt_result_path)
    var_data = _load_json(variant_result_path)
    wt_poses = sorted(wt_data["poses"], key=_pose_score)[: int(top_poses)]
    var_poses = sorted(var_data["poses"], key=_pose_score)[: int(top_poses)]
    if not wt_poses or not var_poses:
        raise ValueError("Aucune pose sélectionnable")

    wt_ca = _load_protein_ca(wt_structure_path)
    var_ca = _load_protein_ca(variant_structure_path)
    pairs = _nw_correspondence(wt_ca, var_ca)
    if len(pairs) < 3:
        raise ValueError("Correspondance WT/variant insuffisante pour superposition")
    wt_xyz = np.array([[p[0]["x"], p[0]["y"], p[0]["z"]] for p in pairs], dtype=float)
    var_xyz = np.array([[p[1]["x"], p[1]["y"], p[1]["z"]] for p in pairs], dtype=float)
    r, t = _kabsch(var_xyz, wt_xyz)
    protein_post_rmsd = _rmsd(var_xyz @ r.T + t, wt_xyz)

    transformed_variant_poses = []
    for pose in var_poses:
        heavy = _pose_heavy_atoms(pose)
        if not heavy:
            continue
        transformed_variant_poses.append((pose, _apply_transform(heavy, r, t)))
    transformed_wt_poses = [(pose, _pose_heavy_atoms(pose)) for pose in wt_poses if _pose_heavy_atoms(pose)]
    if not transformed_variant_poses or not transformed_wt_poses:
        raise ValueError("Aucune pose avec coordonnées lourdes exploitables")

    # Map variant residue identifiers into WT identifiers after the same structural
    # correspondence used for superposition, so contact similarity is not based
    # on a fragile assumption that residue numbering is identical.
    var_to_wt = {}
    for wt_res, var_res in pairs:
        var_key = f"{var_res['chain_id']}:{var_res['residue_id']}{var_res['insertion']}:{_aa3_from_aa1(var_res['aa'])}"
        wt_key = f"{wt_res['chain_id']}:{wt_res['residue_id']}{wt_res['insertion']}:{_aa3_from_aa1(wt_res['aa'])}"
        var_to_wt[var_key] = wt_key

    pair_reports = []
    for wt_pose, wt_atoms in transformed_wt_poses:
        for var_pose, transformed_var_atoms in transformed_variant_poses:
            native_var_atoms = _pose_heavy_atoms(var_pose)
            matched = _match_ligand_atoms(wt_atoms, transformed_var_atoms)
            if not matched:
                continue
            wt_coords = np.array([[a["x"], a["y"], a["z"]] for a, _ in matched], dtype=float)
            var_coords = np.array([[b["x"], b["y"], b["z"]] for _, b in matched], dtype=float)
            pose_rmsd = _rmsd(var_coords, wt_coords)
            wt_contacts = _contact_fingerprint(wt_structure_path, wt_atoms, contact_cutoff_A)
            var_native_contacts = _contact_fingerprint(variant_structure_path, native_var_atoms, contact_cutoff_A)
            var_contacts_mapped = sorted({var_to_wt.get(k, f"variant:{k}") for k in var_native_contacts})
            pair_reports.append({
                "wt_pose_id": wt_pose.get("pose_id"),
                "variant_pose_id": var_pose.get("pose_id"),
                "wt_score": _pose_score(wt_pose),
                "variant_score": _pose_score(var_pose),
                "delta_score_variant_minus_wt": round(_pose_score(var_pose) - _pose_score(wt_pose), 6),
                "ligand_heavy_atoms_compared": len(matched),
                "ligand_pose_rmsd_fixed_frame_A": round(pose_rmsd, 6),
                "wt_ligand_centroid_A": _centroid(wt_atoms),
                "variant_ligand_centroid_in_wt_frame_A": _centroid(transformed_var_atoms),
                "wt_contact_residues": wt_contacts,
                "variant_contact_residues_native": var_native_contacts,
                "variant_contact_residues_mapped_to_wt": var_contacts_mapped,
                "contact_residue_jaccard": round(_jaccard(wt_contacts, var_contacts_mapped), 6),
            })

    if not pair_reports:
        raise ValueError("Aucune paire de poses compatible (atom mapping) trouvée")

    best_rmsd_pair = min(pair_reports, key=lambda x: x["ligand_pose_rmsd_fixed_frame_A"])
    same_rank = next((p for p in pair_reports if p["wt_pose_id"] == p["variant_pose_id"]), None)
    top1 = same_rank or pair_reports[0]

    pocket_summary = {"available": False}
    wt_best = wt_data.get("best_pocket") if isinstance(wt_data.get("best_pocket"), dict) else None
    var_best = var_data.get("best_pocket") if isinstance(var_data.get("best_pocket"), dict) else None
    if wt_best and var_best:
        wc = np.array([[float(wt_best.get("center_x", 0.0)), float(wt_best.get("center_y", 0.0)), float(wt_best.get("center_z", 0.0))]])
        vc = np.array([[float(var_best.get("center_x", 0.0)), float(var_best.get("center_y", 0.0)), float(var_best.get("center_z", 0.0))]])
        vc_fit = vc @ r.T + t
        pocket_summary = {
            "available": True,
            "wt_pocket_id": wt_best.get("id", wt_best.get("pocket_id")),
            "variant_pocket_id": var_best.get("id", var_best.get("pocket_id")),
            "variant_best_pocket_center_in_wt_frame_A": [float(x) for x in vc_fit[0]],
            "wt_best_pocket_center_A": [float(x) for x in wc[0]],
            "best_pocket_center_distance_A": round(float(np.linalg.norm(vc_fit[0] - wc[0])), 6),
            "note": "Comparaison des deux poches canoniques de sortie; l'identité biologique du site actif n'est pas déduite.",
        }

    return {
        "status": "computed",
        "ligand": {
            "smiles": ligand_smiles.strip(),
            "canonical_smiles": _canonical_smiles(ligand_smiles),
            "same_ligand": True,
        },
        "structural_frame": {
            "method": "protein_Calpha_Kabsch_superposition",
            "corresponding_residues": len(pairs),
            "post_superposition_protein_ca_rmsd_A": round(protein_post_rmsd, 6),
            "variant_coordinates_transformed_into_wt_frame": True,
        },
        "pocket_alignment": pocket_summary,
        "pose_analysis": {
            "top_poses_per_state": int(top_poses),
            "pose_pairs_compared": len(pair_reports),
            "same_rank_pair": top1,
            "best_geometry_pair_by_ligand_RMSD": best_rmsd_pair,
            "pairwise": sorted(pair_reports, key=lambda x: (x["wt_pose_id"], x["variant_pose_id"])),
        },
        "contact_method": {
            "method": "protein-ligand heavy-atom distance fingerprint",
            "cutoff_A": float(contact_cutoff_A),
            "interpretation": "Les résidus listés ont au moins un atome lourd de la protéine à <= cutoff du ligand. Ce n'est pas une classification complète des interactions chimiques.",
        },
        "scientific_warnings": [
            "La superposition Kabsch est appliquée aux structures protéiques pour comparer les poses dans un repère commun; elle ne doit pas être utilisée pour effacer les déplacements lors du clustering des poses de docking.",
            "Le RMSD des poses et la similarité des résidus en contact décrivent une géométrie computationnelle; ils ne démontrent pas une affinité, une résistance ou une efficacité biologique.",
            "Les correspondances atomiques du ligand supposent que le même ligand préparé conserve un ordre d'atomes cohérent entre les deux runs.",
            "Les contacts sont intentionnellement conservateurs et ne sont pas présentés comme des liaisons hydrogène, ponts salins ou autres interactions sans analyse dédiée.",
        ],
    }
