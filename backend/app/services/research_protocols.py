"""Named, versioned research protocols for auditable N3XORA-Sahel screening.

The Vina runner is adaptive: exhaustiveness depends on protein length and ligand
rotatable bonds. The exact realized value is recorded in each docking result.
"""
from copy import deepcopy

SCREENING_PROTOCOLS = {
    "N3XORA_VINA_ADAPTIVE_V1": {
        "protocol_id": "N3XORA_VINA_ADAPTIVE_V1",
        "protocol_version": "1.0.0",
        "engine": "AutoDock Vina",
        "engine_version": "1.2.5",
        "max_pockets": 1,
        "seed": 42,
        "num_modes": 20,
        "energy_range_kcal_mol": 3,
        "pocket_policy": "premier résultat fpocket du récepteur; aucune étiquette actif/décoy utilisée",
        "exhaustiveness_policy": "adaptative selon longueur protéique et nombre de liaisons rotatables; valeur réalisée rapportée par le moteur",
        "ligand_preparation": "RDKit puis Open Babel si disponible; le fournisseur effectif est archivé par le moteur",
        "ranking_primary": "vina_best_score",
        "interpretation": "screening exploratoire; score Vina et agrégation ASP ne sont pas des affinités expérimentales",
    }
}


def get_screening_protocol(protocol_id: str) -> dict:
    """Return a defensive copy or raise ValueError for an unknown protocol."""
    try:
        return deepcopy(SCREENING_PROTOCOLS[protocol_id])
    except KeyError as exc:
        raise ValueError(
            "Protocole inconnu. Choisir N3XORA_VINA_ADAPTIVE_V1."
        ) from exc
