import math

from app.core.docking.asp.aggregation import aggregate_pose_scores
from app.core.docking.asp.scoring import (
    boltzmann_logmean_score,
    boltzmann_weights,
    logsumexp_score,
)


def _pose(score, pocket_id, coordinates):
    return {
        "score": score,
        "pocket_id": pocket_id,
        "atoms": [
            {"element": "C", "x": x, "y": y, "z": z}
            for x, y, z in coordinates
        ],
    }


def test_weights_are_normalized_and_stable():
    weights = boltzmann_weights([-100.0, -99.0])
    assert math.isclose(sum(weights), 1.0)
    assert weights[0] > weights[1]


def test_logsumexp_is_at_least_as_favorable_as_best_score():
    assert logsumexp_score([-8.0, -7.0]) <= -8.0


def test_logmean_is_invariant_to_duplicate_identical_members():
    one = boltzmann_logmean_score([-8.0])
    duplicated = boltzmann_logmean_score([-8.0, -8.0, -8.0])
    assert math.isclose(one, duplicated, rel_tol=1e-12, abs_tol=1e-12)


def test_fixed_frame_rmsd_does_not_merge_translated_poses():
    poses = [
        _pose(-8.0, 0, [(0, 0, 0), (1, 0, 0)]),
        _pose(-7.0, 0, [(10, 0, 0), (11, 0, 0)]),
    ]
    _, metadata = aggregate_pose_scores(poses, rmsd_threshold=2.0)
    assert metadata["num_pose_clusters"] == 2
    assert metadata["clustering_metric"].startswith("fixed-frame heavy-atom")


def test_hydrogens_are_excluded_from_clustering_rmsd():
    poses = [
        {
            "score": -8.0,
            "pocket_id": 0,
            "atoms": [
                {"element": "C", "x": 0, "y": 0, "z": 0},
                {"element": "H", "x": 100, "y": 100, "z": 100},
            ],
        },
        {
            "score": -7.9,
            "pocket_id": 0,
            "atoms": [
                {"element": "C", "x": 0.1, "y": 0, "z": 0},
                {"element": "H", "x": -100, "y": -100, "z": -100},
            ],
        },
    ]
    _, metadata = aggregate_pose_scores(poses, rmsd_threshold=2.0)
    assert metadata["num_pose_clusters"] == 1


def test_non_finite_scores_are_rejected():
    import pytest
    with pytest.raises(ValueError):
        aggregate_pose_scores([_pose(float("nan"), 0, [(0, 0, 0)])])


def test_aggregation_does_not_mix_distinct_pockets():
    poses = [
        _pose(-8.0, 0, [(0, 0, 0), (1, 0, 0)]),
        _pose(-7.9, 0, [(0.1, 0, 0), (1.1, 0, 0)]),
        _pose(-7.5, 1, [(10, 0, 0), (11, 0, 0)]),
    ]
    score, metadata = aggregate_pose_scores(poses, rmsd_threshold=2.0)
    assert metadata["num_raw_poses"] == 3
    assert metadata["num_pose_clusters"] == 2
    assert metadata["num_pockets"] == 2
    assert metadata["cross_pocket_rule"] == "alternative-site ranking; no cross-pocket pseudo-partition-function"
    assert metadata["best_pocket_id"] == 0
    assert score == metadata["best_pocket_boltzmann_effective_score"]
