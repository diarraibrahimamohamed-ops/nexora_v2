"""Pose clustering and Boltzmann-like score aggregation for docking."""

from typing import Dict, List, Optional, Tuple

import numpy as np

from .scoring import (
    boltzmann_logmean_score,
    boltzmann_mean,
    boltzmann_weights,
    logsumexp_score,
    validate_temperature,
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


def _heavy_atom_signature(pose: Dict) -> Tuple[Tuple[str, str], ...]:
    """Return the ordered heavy-atom element/name signature for pose correspondence checks."""
    atoms = [atom for atom in pose.get("atoms", []) if _atom_element(atom) != "H"]
    return tuple(
        (_atom_element(atom), str(atom.get("atom_name") or "").strip().upper())
        for atom in atoms
    )


def _validated_atom_permutations(
    atom_count: int,
    atom_symmetry_maps: Optional[List[List[int]]],
) -> List[np.ndarray]:
    """Validate atom permutations returned by an explicit molecular graph mapping."""
    identity = tuple(range(atom_count))
    if not atom_symmetry_maps:
        return [np.asarray(identity, dtype=int)]

    permutations = []
    seen = set()
    for mapping in atom_symmetry_maps:
        try:
            normalized = tuple(int(index) for index in mapping)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Each atom symmetry mapping must contain integer indices") from exc
        if len(normalized) != atom_count or set(normalized) != set(range(atom_count)):
            raise ValueError("Each atom symmetry mapping must be a complete heavy-atom permutation")
        if normalized not in seen:
            seen.add(normalized)
            permutations.append(np.asarray(normalized, dtype=int))
    if identity not in seen:
        permutations.insert(0, np.asarray(identity, dtype=int))
    return permutations


def _coordinate_rmsd(
    first: np.ndarray,
    second: np.ndarray,
    atom_permutations: Optional[List[np.ndarray]] = None,
) -> float:
    """Return minimum fixed-frame RMSD over allowed atom mappings, without Kabsch.

    The coordinates always remain in the receptor frame. Only atom indices may
    be permuted using graph automorphisms that preserve the ligand chemistry.
    No translation or rotation is fitted, so distinct binding locations remain
    distinct clusters.
    """
    if first.shape != second.shape or first.ndim != 2 or first.shape[1] != 3 or first.size == 0:
        return float("inf")
    permutations = atom_permutations or [np.arange(first.shape[0], dtype=int)]
    best = float("inf")
    for permutation in permutations:
        if permutation.shape != (first.shape[0],):
            continue
        delta = first - second[permutation]
        value = float(np.sqrt(np.mean(np.sum(delta * delta, axis=1))))
        if value < best:
            best = value
    return best


def _cluster_poses(
    poses: List[Dict],
    rmsd_threshold: float,
    atom_permutations: Optional[List[np.ndarray]] = None,
) -> List[Dict]:
    """Greedy fixed-frame, graph-symmetry-aware RMSD clustering."""
    clusters: List[Dict] = []
    for pose in sorted(poses, key=lambda item: float(item["score"])):
        coordinates = _heavy_atom_coordinates(pose)
        assigned = None
        for cluster in clusters:
            if _coordinate_rmsd(
                coordinates, cluster["coordinates"], atom_permutations
            ) <= rmsd_threshold:
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
    atom_permutations: Optional[List[np.ndarray]] = None,
) -> Tuple[float, Dict, List[Dict]]:
    clustered = _cluster_poses(poses, rmsd_threshold, atom_permutations)
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
    atom_symmetry_maps: Optional[List[List[int]]] = None,
    symmetry_metadata: Optional[Dict] = None,
) -> Tuple[float, Dict]:
    """Aggregate poses *within each pocket*, then select the best pocket.

    Distinct binding pockets are treated as alternative sites, not as members
    of one thermodynamic ensemble.  The canonical global ASP score is therefore
    the lowest size-normalized Boltzmann aggregate among the pocket ensembles.
    This avoids cross-pocket pseudo-partition-function mixing.
    """
    if not poses:
        raise ValueError("At least one pose is required")
    try:
        rmsd_threshold = float(rmsd_threshold)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("RMSD threshold must be a finite positive number") from exc
    if not np.isfinite(rmsd_threshold) or rmsd_threshold <= 0:
        raise ValueError("RMSD threshold must be finite and positive")

    # Validate temperature before clustering, including one-pose ensembles.
    temperature = validate_temperature(temperature)

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

    expected_signature = _heavy_atom_signature(poses[0])
    expected_atom_count = len(expected_signature)
    if any(_heavy_atom_signature(pose) != expected_signature for pose in poses[1:]):
        raise ValueError(
            "Vina poses do not share the same ordered heavy-atom signature; "
            "fixed-index RMSD clustering would be invalid."
        )
    atom_permutations = _validated_atom_permutations(
        expected_atom_count, atom_symmetry_maps
    )

    pocket_results = []
    representatives: List[Dict] = []
    for pocket_id, pocket_poses in by_pocket.items():
        aggregate, pocket_meta, clustered = _aggregate_single_pocket(
            pocket_poses, temperature, rmsd_threshold, pocket_id, atom_permutations
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
        "aggregation_input": "lowest-score representative per heavy-atom RMSD cluster, aggregated independently within each pocket",
        "clustering_metric": (
            "minimum fixed-frame heavy-atom RMSD over validated molecular graph automorphisms; no Kabsch superposition"
            if atom_symmetry_maps else
            "fixed-frame heavy-atom coordinate RMSD in restored PDBQT input atom order; no Kabsch superposition"
        ),
        "atom_order_signature": [
            {"element": element, "atom_name": atom_name}
            for element, atom_name in expected_signature
        ],
        "symmetry_mapping": {
            "applied": bool(atom_symmetry_maps),
            "mapping_count": len(atom_permutations) if atom_symmetry_maps else 1,
            **(symmetry_metadata or {}),
        },
        "clustering_limitations": (
            "Graph automorphism mappings are bounded and apply only after atom order is reconciled with the ligand PDBQT input."
            if atom_symmetry_maps else
            "Equivalent-atom remapping was not applied; clustering uses restored input atom order."
        ),
        "cross_pocket_rule": "alternative-site ranking; no cross-pocket pseudo-partition-function",
        "score_definition": "size-normalized Boltzmann log-mean over RMSD-cluster representatives",
        "logsumexp_effective_score": best_pocket["logsumexp_score_raw"],
        "boltzmann_mean_score": best_pocket["boltzmann_mean_score"],
    }
    return float(best_pocket["boltzmann_effective_score"]), metadata
