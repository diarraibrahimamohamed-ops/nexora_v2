import math
import pytest
import numpy as np
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


@pytest.mark.parametrize(
    "temperature",
    [0.0, -1.0, float("nan"), float("inf"), float("-inf")],
)
def test_boltzmann_weights_reject_nonfinite_or_nonpositive_temperature(temperature):
    with pytest.raises(ValueError, match="Temperature must be finite and positive"):
        boltzmann_weights([-8.0, -7.0], temperature=temperature)


def test_boltzmann_weights_reject_non_numeric_temperature():
    with pytest.raises(ValueError, match="Temperature must be a finite positive number"):
        boltzmann_weights([-8.0], temperature="not-a-temperature")


@pytest.mark.parametrize(
    "threshold",
    [0.0, -0.1, float("nan"), float("inf"), float("-inf")],
)
def test_asp_rejects_nonfinite_or_nonpositive_rmsd_threshold(threshold):
    with pytest.raises(ValueError, match="RMSD threshold must be finite and positive"):
        aggregate_pose_scores(
            [_pose(-8.0, 0, [(0.0, 0.0, 0.0)])],
            rmsd_threshold=threshold,
        )


@pytest.mark.parametrize("temperature", [float("nan"), float("inf"), 0.0])
def test_asp_validates_temperature_even_for_single_pose(temperature):
    with pytest.raises(ValueError, match="Temperature must be finite and positive"):
        aggregate_pose_scores(
            [_pose(-8.0, 0, [(0.0, 0.0, 0.0)])],
            temperature=temperature,
        )


def test_numeric_string_temperature_is_normalized_before_score_scaling():
    numeric_temperature = boltzmann_logmean_score([-8.0, -7.0], temperature=298.15)
    text_temperature = boltzmann_logmean_score([-8.0, -7.0], temperature="298.15")
    assert math.isclose(numeric_temperature, text_temperature, rel_tol=1e-12, abs_tol=1e-12)


def test_symmetry_rmsd_merges_equivalent_atom_permutations_without_kabsch():
    from app.core.docking.asp.aggregation import _coordinate_rmsd

    first = np.asarray([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]], dtype=float)
    second = np.asarray([[2.0, 0.0, 0.0], [0.0, 0.0, 0.0]], dtype=float)
    maps = [np.asarray([0, 1]), np.asarray([1, 0])]

    assert math.isclose(_coordinate_rmsd(first, second), 2.0)
    assert math.isclose(_coordinate_rmsd(first, second, maps), 0.0)


def test_symmetry_rmsd_does_not_align_translated_poses():
    from app.core.docking.asp.aggregation import _coordinate_rmsd

    first = np.asarray([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]], dtype=float)
    translated = np.asarray([[10.0, 0.0, 0.0], [12.0, 0.0, 0.0]], dtype=float)
    maps = [np.asarray([0, 1]), np.asarray([1, 0])]

    assert _coordinate_rmsd(first, translated, maps) > 2.0


def test_asp_uses_explicit_symmetry_maps_for_equivalent_ligand_poses():
    poses = [
        _pose(-8.0, 0, [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0)]),
        _pose(-7.9, 0, [(2.0, 0.0, 0.0), (0.0, 0.0, 0.0)]),
    ]
    _, ordinary = aggregate_pose_scores(poses, rmsd_threshold=0.1)
    _, symmetric = aggregate_pose_scores(
        poses,
        rmsd_threshold=0.1,
        atom_symmetry_maps=[[0, 1], [1, 0]],
        symmetry_metadata={"source": "unit-test", "mapping_count": 2},
    )
    assert ordinary["num_pose_clusters"] == 2
    assert symmetric["num_pose_clusters"] == 1
    assert symmetric["symmetry_mapping"]["applied"] is True
    assert "no Kabsch" in symmetric["clustering_metric"]


def test_asp_rejects_inconsistent_heavy_atom_order_between_poses():
    poses = [
        {
            "score": -8.0, "pocket_id": 0,
            "atoms": [
                {"element": "C", "x": 0.0, "y": 0.0, "z": 0.0},
                {"element": "O", "x": 1.0, "y": 0.0, "z": 0.0},
            ],
        },
        {
            "score": -7.9, "pocket_id": 0,
            "atoms": [
                {"element": "O", "x": 1.0, "y": 0.0, "z": 0.0},
                {"element": "C", "x": 0.0, "y": 0.0, "z": 0.0},
            ],
        },
    ]
    with pytest.raises(ValueError, match="ordered heavy-atom signature"):
        aggregate_pose_scores(poses)


def test_rdkit_symmetry_maps_require_matching_molblock_atom_order():
    pytest.importorskip("rdkit")
    from rdkit import Chem
    from rdkit.Chem import AllChem
    from app.core.docking.vina_runner import _build_ligand_symmetry_atom_maps

    molecule = Chem.AddHs(Chem.MolFromSmiles("CC"))
    assert AllChem.EmbedMolecule(molecule, randomSeed=42) == 0
    mol_block = Chem.MolToMolBlock(molecule)

    maps, metadata = _build_ligand_symmetry_atom_maps(mol_block, ["C", "C"])
    assert maps is not None
    assert len(maps) == 2
    assert tuple(range(2)) in {tuple(mapping) for mapping in maps}
    assert metadata["applied"] is True
    assert metadata["atom_order_check"] == "passed_element_sequence"

    maps, metadata = _build_ligand_symmetry_atom_maps(mol_block, ["O", "C"])
    assert maps is None
    assert metadata["applied"] is False
    assert metadata["atom_order_check"] == "failed_element_sequence"


def test_rdkit_symmetry_maps_stay_disabled_without_shared_molblock():
    from app.core.docking.vina_runner import _build_ligand_symmetry_atom_maps

    maps, metadata = _build_ligand_symmetry_atom_maps(None, ["C", "C"])
    assert maps is None
    assert metadata["applied"] is False
    assert "MolBlock" in metadata["reason"]


def test_asp_rejects_poses_without_finite_heavy_atom_coordinates():
    poses = [
        _pose(-8.0, 0, [(0.0, 0.0, 0.0)]),
        _pose(-7.9, 0, [(float("nan"), 0.0, 0.0)]),
    ]
    with pytest.raises(ValueError, match="finite heavy-atom coordinate"):
        aggregate_pose_scores(poses)
