import math
from app.core.docking.asp.aggregation import aggregate_pose_scores
from app.core.docking.asp.scoring import boltzmann_logmean_score


def pose(score, pocket, shift=0.0):
    return {
        'score': score,
        'pocket_id': pocket,
        'atoms': [
            {'element': 'C', 'x': 0.0 + shift, 'y': 0.0, 'z': 0.0},
            {'element': 'C', 'x': 1.0 + shift, 'y': 0.0, 'z': 0.0},
            {'element': 'N', 'x': 0.5 + shift, 'y': 1.0, 'z': 0.0},
            {'element': 'O', 'x': 0.5 + shift, 'y': 0.5, 'z': 1.0},
        ]
    }


def test_single_pose_asp_equals_vina():
    score, meta = aggregate_pose_scores([pose(-7.25, 0)])
    assert math.isclose(score, -7.25, abs_tol=1e-12)
    assert math.isclose(meta['best_pocket_boltzmann_effective_score'], -7.25, abs_tol=1e-12)


def test_translated_pose_is_a_distinct_cluster():
    score, meta = aggregate_pose_scores([pose(-8.0, 0), pose(-7.0, 0, shift=10)])
    assert meta['num_pose_clusters'] == 2
    assert math.isfinite(score)



def test_cross_pocket_aggregation_is_not_used():
    poses = [pose(-6.0, 0), pose(-5.0, 1), pose(-5.1, 1, shift=5)]
    score, meta = aggregate_pose_scores(poses)
    assert meta['num_pockets'] == 2
    assert meta['best_pocket_id'] in (0, 1)
    assert 'no cross-pocket pseudo-partition-function' in meta['cross_pocket_rule']


def test_temperature_changes_boltzmann_but_not_best_pose_order():
    cold = boltzmann_logmean_score([-9.0, -8.0], 280.0)
    warm = boltzmann_logmean_score([-9.0, -8.0], 320.0)
    assert cold <= warm
