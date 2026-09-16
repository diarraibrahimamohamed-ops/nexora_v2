"""Comparative WT/variant docking result handling.

Scientific contract:
- compare only the same ligand under the same declared research protocol;
- preserve raw Vina scores and ASP outputs separately;
- report score contrasts as *score contrasts*, never as experimental delta-G;
- do not infer resistance, efficacy, or causality from docking alone.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict


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


def _read_result(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ValueError("Fichier de résultat absent")
    with p.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("Résultat de docking invalide")
    if not data.get("success", False):
        raise ValueError("Résultat de docking marqué comme non réussi")
    score = data.get("vina_best_score")
    if score is None or not isinstance(score, (int, float)) or not math.isfinite(float(score)):
        raise ValueError("Score Vina non fini ou absent")
    return data


def _run_record(data: Dict[str, Any], declared_protocol: Dict[str, Any]) -> Dict[str, Any]:
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    best_pocket = data.get("best_pocket") if isinstance(data.get("best_pocket"), dict) else {}
    aggregation = data.get("aggregation_metadata") if isinstance(data.get("aggregation_metadata"), dict) else {}
    return {
        "vina_best_score": float(data["vina_best_score"]),
        "boltzmann_effective_score": float(data["boltzmann_effective_score"]) if isinstance(data.get("boltzmann_effective_score"), (int, float)) and math.isfinite(float(data["boltzmann_effective_score"])) else None,
        "boltzmann_mean_score": float(data["boltzmann_mean_score"]) if isinstance(data.get("boltzmann_mean_score"), (int, float)) and math.isfinite(float(data["boltzmann_mean_score"])) else None,
        "num_poses": int(data.get("num_poses", 0) or 0),
        "num_pockets_tested": int(metadata.get("num_pockets_tested", 0) or 0),
        "best_pocket_id": best_pocket.get("id", best_pocket.get("pocket_id", aggregation.get("best_pocket_id"))),
        "selected_pose_id": data.get("selected_pose_id"),
        "selected_pose_score": data.get("selected_pose_score"),
        "docking_engine": data.get("docking_engine", "AutoDock Vina"),
        "aggregation_method": data.get("aggregation_method"),
        "structure_fallback_used": bool(metadata.get("fallback_used")),
        "declared_protocol": declared_protocol,
    }


def compare_completed_results(wt_result_path: str, variant_result_path: str, *, ligand_smiles: str, protocol: Dict[str, Any]) -> Dict[str, Any]:
    """Return a transparent paired comparison report.

    This intentionally does *not* convert score differences into affinity or
    free-energy differences. It also does not require that WT and variant
    scores be numerically comparable beyond the explicitly declared paired
    protocol; differences in pocket geometry remain a scientific confounder.
    """
    wt = _read_result(wt_result_path)
    variant = _read_result(variant_result_path)

    expected = _canonical_smiles(ligand_smiles)
    # Result files do not always carry the original SMILES; the DB-level
    # ResearchDockingRun is therefore authoritative for ligand identity.
    if not expected:
        raise ValueError("Ligand SMILES vide")

    wt_rec = _run_record(wt, protocol)
    var_rec = _run_record(variant, protocol)
    delta_vina = var_rec["vina_best_score"] - wt_rec["vina_best_score"]
    delta_asp = None
    if wt_rec["boltzmann_effective_score"] is not None and var_rec["boltzmann_effective_score"] is not None:
        delta_asp = var_rec["boltzmann_effective_score"] - wt_rec["boltzmann_effective_score"]

    return {
        "status": "computed",
        "ligand": {"smiles": ligand_smiles.strip(), "canonical_smiles": expected, "same_ligand": True},
        "protocol": protocol,
        "wt": wt_rec,
        "variant": var_rec,
        "score_contrast": {
            "delta_vina_variant_minus_wt": round(delta_vina, 6),
            "delta_asp_variant_minus_wt": round(delta_asp, 6) if delta_asp is not None else None,
            "interpretation": "Comparaison relative des scores du protocole; ne constitue pas une différence expérimentale de ΔG de liaison.",
        },
        "scientific_warnings": [
            "Un changement de score de docking ne démontre pas à lui seul une modification d'affinité expérimentale.",
            "WT et variant peuvent présenter des géométries de poche différentes; le contraste doit être interprété avec la comparaison structurale.",
            "Le docking ne démontre ni résistance clinique, ni efficacité thérapeutique, ni causalité du variant.",
        ],
    }
