"""Test simple pour déboguer ASP."""

import sys
sys.path.insert(0, '/home/empereur/Bureau/nexora_v2/backend')

from app.core.docking.asp import aggregate_pose_scores

# Test avec des poses simples
test_poses = [
    {
        "score": -5.0,
        "pocket_id": 0,
        "atoms": [
            {"element": "C", "x": 0.0, "y": 0.0, "z": 0.0},
            {"element": "C", "x": 1.0, "y": 0.0, "z": 0.0},
        ]
    },
    {
        "score": -6.0,
        "pocket_id": 0,
        "atoms": [
            {"element": "C", "x": 0.5, "y": 0.5, "z": 0.5},
            {"element": "C", "x": 1.5, "y": 0.5, "z": 0.5},
        ]
    },
    {
        "score": -4.5,
        "pocket_id": 1,
        "atoms": [
            {"element": "C", "x": 2.0, "y": 2.0, "z": 2.0},
            {"element": "C", "x": 3.0, "y": 2.0, "z": 2.0},
        ]
    }
]

print("Test ASP avec 3 poses...")
try:
    effective_score, metadata = aggregate_pose_scores(test_poses)
    print(f"✓ ASP terminé: score effectif={effective_score:.3f}")
    print(f"  Métadonnées: {metadata}")
except Exception as e:
    print(f"✗ Erreur ASP: {e}")
    import traceback
    traceback.print_exc()
