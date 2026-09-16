"""Test réaliste pour déboguer ASP avec des poses similaires au docking."""

import sys
sys.path.insert(0, '/app')

from app.core.docking.asp import aggregate_pose_scores

# Simuler des poses réelles de Vina (avec atomes)
def create_pose(score, pocket_id, offset=0):
    return {
        "score": score,
        "pocket_id": pocket_id,
        "atoms": [
            {"element": "C", "x": 0.0 + offset, "y": 0.0, "z": 0.0},
            {"element": "C", "x": 1.0 + offset, "y": 0.0, "z": 0.0},
            {"element": "N", "x": 0.5 + offset, "y": 1.0, "z": 0.0},
            {"element": "O", "x": 0.5 + offset, "y": 0.5, "z": 1.0},
        ]
    }

# Test 1: Quelques poses (cas normal)
print("Test 1: 5 poses (cas normal)...")
poses_5 = [create_pose(-5.0 - i*0.5, 0, i*0.1) for i in range(5)]
try:
    score, meta = aggregate_pose_scores(poses_5)
    print(f"✓ Terminé: score={score:.3f}, poses={meta['num_raw_poses']}, clusters={meta['num_pose_clusters']}")
except Exception as e:
    print(f"✗ Erreur: {e}")

# Test 2: Plus de poses (cas docking réel)
print("\nTest 2: 20 poses (cas docking réel)...")
poses_20 = []
for i in range(20):
    pocket = i % 3  # 3 poches différentes
    poses_20.append(create_pose(-5.0 - i*0.3, pocket, i*0.05))
try:
    score, meta = aggregate_pose_scores(poses_20)
    print(f"✓ Terminé: score={score:.3f}, poses={meta['num_raw_poses']}, clusters={meta['num_pose_clusters']}")
except Exception as e:
    print(f"✗ Erreur: {e}")

# Test 3: Beaucoup de poses (cas extrême)
print("\nTest 3: 100 poses (cas extrême)...")
poses_100 = []
for i in range(100):
    pocket = i % 5  # 5 poches différentes
    poses_100.append(create_pose(-5.0 - i*0.1, pocket, i*0.02))
try:
    score, meta = aggregate_pose_scores(poses_100)
    print(f"✓ Terminé: score={score:.3f}, poses={meta['num_raw_poses']}, clusters={meta['num_pose_clusters']}")
except Exception as e:
    print(f"✗ Erreur: {e}")

# Test 4: Poses sans atomes (cas edge)
print("\nTest 4: Poses sans atomes (cas edge)...")
poses_no_atoms = [
    {"score": -5.0, "pocket_id": 0, "atoms": []},
    {"score": -6.0, "pocket_id": 0, "atoms": []},
]
try:
    score, meta = aggregate_pose_scores(poses_no_atoms)
    print(f"✓ Terminé: score={score:.3f}")
except Exception as e:
    print(f"✗ Erreur: {e}")

print("\n=== Tous les tests terminés ===")
