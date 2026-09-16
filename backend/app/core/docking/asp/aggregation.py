"""Pose clustering and Boltzmann-like score aggregation for docking."""

from typing import Dict, List, Tuple

import numpy as np

from .scoring import (
    boltzmann_logmean_score,
    boltzmann_mean,
    boltzmann_weights,
    logsumexp_score,
)


def _atom_element(atom: Dict) -> str:
    """Return a normalized chemical element for a parsed docking atom."""
    element = str(atom.get("element", "")).strip().upper()
    if element:
        # PDBQT aromatic carbon can be encoded as AutoDock atom type ``A``.
        if element == "A":
            return "C"
        return element[:2] if len(element) > 1 and element[:2] in {
            "CL", "BR", "SI", "NA", "MG", "FE", "ZN", "CA", "MN", "CU", "CO", "NI", "SE"
        } else element[0]
    atom_type = str(atom.get("atom", "")).strip().upper()
    if atom_type == "A":
        return "C"
    return atom_type[:2] if atom_type[:2] in {"CL", "BR"} else (atom_type[:1] or "")


def _heavy_atom_coordinates(pose: Dict) -> np.ndarray:
    """Return heavy-atom coordinates in the receptor frame, preserving atom order."""
    atoms = [atom for atom in pose.get("atoms", []) if _atom_element(atom) != "H"]
    try:
        coords = np.asarray([[atom["x"], atom["y"], atom["z"]] for atom in atoms], dtype=float)
    except (KeyError, TypeError, ValueError):
        return np.empty((0, 3), dtype=float)
    if coords.size == 0 or not np.all(np.isfinite(coords)):
        return np.empty((0, 3), dtype=float)
    return coords


def _coordinate_rmsd(first: np.ndarray, second: np.ndarray) -> float:
    """Return fixed-frame RMSD without Kabsch superposition.

    Docking poses are expressed in the same receptor coordinate frame. A rigid
    Kabsch alignment would erase the translational/rotational distinction
    between different binding modes and can incorrectly merge poses that are
    far apart in the binding site. This clustering RMSD therefore compares
    corresponding heavy atoms directly in the receptor frame.
    """
    if first.shape != second.shape or first.ndim != 2 or first.shape[1] != 3 or first.size == 0:
        return float("inf")
    delta = first - second
    return float(np.sqrt(np.mean(np.sum(delta * delta, axis=1))))


def _cluster_poses(poses: List[Dict], rmsd_threshold: float) -> List[Dict]:
    """Greedy fixed-frame RMSD clustering; lowest-score pose represents each cluster."""
    clusters: List[Dict] = []
    for pose in sorted(poses, key=lambda item: float(item["score"])):
        coordinates = _heavy_atom_coordinates(pose)
        assigned = None
        for cluster in clusters:
            if _coordinate_rmsd(coordinates, cluster["coordinates"]) <= rmsd_threshold:
                assigned = cluster
                break
        if assigned is None:
            clusters.append({"representative": pose, "coordinates": coordinates, "members": [pose]})
        else:
            assigned["members"].append(pose)
    return [cluster["representative"] for cluster in clusters]


def _aggregate_single_pocket(
    poses: List[Dict],
    temperature: float,
    rmsd_threshold: float,
    pocket_id,
) -> Tuple[float, Dict, List[Dict]]:
    clustered = _cluster_poses(poses, rmsd_threshold)
    scores = [float(pose["score"]) for pose in clustered]
    weights = boltzmann_weights(scores, temperature)
    for cluster_id, (pose, weight) in enumerate(zip(clustered, weights)):
        pose["pose_cluster_id"] = cluster_id
        pose["boltzmann_weight"] = weight
        pose["pocket_id"] = pocket_id

    aggregate = boltzmann_logmean_score(scores, temperature)
    metadata = {
        "pocket_id": pocket_id,
        "num_raw_poses": len(poses),
        "num_pose_clusters": len(clustered),
        "best_vina_score": min(scores),
        "boltzmann_effective_score": aggregate,
        "boltzmann_mean_score": boltzmann_mean(scores, temperature),
        "logsumexp_score_raw": logsumexp_score(scores, temperature),
    }
    return aggregate, metadata, clustered


def aggregate_pose_scores(
    poses: List[Dict],
    temperature: float = 298.15,
    rmsd_threshold: float = 2.0,
) -> Tuple[float, Dict]:
    """Aggregate poses *within each pocket*, then select the best pocket.

    Distinct binding pockets are treated as alternative sites, not as members
    of one thermodynamic ensemble.  The canonical global ASP score is therefore
    the lowest size-normalized Boltzmann aggregate among the pocket ensembles.
    This avoids cross-pocket pseudo-partition-function mixing.
    """
    if not poses:
        raise ValueError("At least one pose is required")
    if rmsd_threshold <= 0:
        raise ValueError("RMSD threshold must be positive")

    by_pocket: Dict[object, List[Dict]] = {}
    for pose in poses:
        try:
            score = float(pose["score"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Each pose must contain a finite numeric 'score'")
        if not np.isfinite(score):
            raise ValueError("Each pose must contain a finite numeric 'score'")
        coordinates = _heavy_atom_coordinates(pose)
        if coordinates.shape[0] == 0:
            raise ValueError("Each pose must contain at least one finite heavy-atom coordinate")
        by_pocket.setdefault(pose.get("pocket_id", 0), []).append(pose)

    pocket_results = []
    representatives: List[Dict] = []
    for pocket_id, pocket_poses in by_pocket.items():
        aggregate, pocket_meta, clustered = _aggregate_single_pocket(
            pocket_poses, temperature, rmsd_threshold, pocket_id
        )
        pocket_results.append(pocket_meta)
        representatives.extend(clustered)

    best_pocket = min(pocket_results, key=lambda item: item["boltzmann_effective_score"])
    best_pocket_reps = [p for p in representatives if p.get("pocket_id") == best_pocket["pocket_id"]]
    best_pocket_weights = boltzmann_weights([float(p["score"]) for p in best_pocket_reps], temperature)
    selected_pose = min(best_pocket_reps, key=lambda p: float(p["score"]))
    best_pocket["selected_pose_id"] = selected_pose.get("pose_id")
    best_pocket["selected_pose_score"] = float(selected_pose["score"])
    best_pocket["selected_pose_boltzmann_weight"] = float(max(best_pocket_weights))
    weights = boltzmann_weights([float(p["score"]) for p in representatives], temperature)
    for pose, weight in zip(representatives, weights):
        # Diagnostic only: cross-pocket weights are not used for the canonical score.
        pose["global_diagnostic_boltzmann_weight"] = weight

    metadata = {
        "temperature_K": temperature,
        "rmsd_threshold_A": rmsd_threshold,
        "num_raw_poses": len(poses),
        "num_pose_clusters": len(representatives),
        "num_pockets": len(pocket_results),
        "best_pocket_id": best_pocket["pocket_id"],
        "best_pocket_vina_score": best_pocket["best_vina_score"],
        "best_pocket_boltzmann_effective_score": best_pocket["boltzmann_effective_score"],
        "best_pocket_boltzmann_mean_score": best_pocket["boltzmann_mean_score"],
        "selected_pose_id": best_pocket.get("selected_pose_id"),
        "selected_pose_score": best_pocket.get("selected_pose_score"),
        "selected_pose_boltzmann_weight": best_pocket.get("selected_pose_boltzmann_weight"),
        "pocket_results": pocket_results,
        "aggregation_input": "lowest-score representative per fixed-frame heavy-atom RMSD cluster, aggregated independently within each pocket",
        "clustering_metric": "fixed-frame heavy-atom coordinate RMSD; no Kabsch superposition",
        "clustering_limitations": "atom correspondence follows pose atom order; ligand symmetry/equivalent-atom remapping is not explicitly optimized",
        "cross_pocket_rule": "alternative-site ranking; no cross-pocket pseudo-partition-function",
        "score_definition": "size-normalized Boltzmann log-mean over RMSD-cluster representatives",
        "logsumexp_effective_score": best_pocket["logsumexp_score_raw"],
        "boltzmann_mean_score": best_pocket["boltzmann_mean_score"],
    }
    return float(best_pocket["boltzmann_effective_score"]), metadata
