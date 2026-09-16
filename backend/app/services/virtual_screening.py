"""Transparent small-scale virtual-screening preparation for N3XORA.

This module deliberately separates chemical QC/standardisation from docking.
No fabricated activity score or efficacy prediction is produced. RDKit descriptors
are reported as descriptors; optional filters are explicit study parameters.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from rdkit import Chem
from rdkit.Chem import Descriptors, Lipinski, QED
from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams


PAINS_CATALOG = None
try:
    _params = FilterCatalogParams()
    _params.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS)
    PAINS_CATALOG = FilterCatalog(_params)
except Exception:
    PAINS_CATALOG = None


@dataclass(frozen=True)
class ScreeningOptions:
    apply_lipinski: bool = False
    reject_pains: bool = False
    max_mw: float | None = 500.0
    max_logp: float | None = 5.0
    max_hbd: int | None = 5
    max_hba: int | None = 10
    max_rotatable_bonds: int | None = 10
    max_tpsa: float | None = 150.0


def _float(value: Any) -> float:
    return float(value)


def _pains_match(mol: Chem.Mol) -> tuple[bool, list[str]]:
    if PAINS_CATALOG is None:
        return False, []
    matches = PAINS_CATALOG.GetMatches(mol)
    return bool(matches), [m.GetDescription() for m in matches]


def standardize_and_profile(smiles: str, options: ScreeningOptions) -> dict:
    raw = (smiles or "").strip()
    if not raw:
        return {"accepted": False, "reason": "empty_smiles"}

    mol = Chem.MolFromSmiles(raw)
    if mol is None:
        return {"accepted": False, "reason": "invalid_smiles"}

    canonical = Chem.MolToSmiles(mol, canonical=True)
    mw = _float(Descriptors.MolWt(mol))
    logp = _float(Descriptors.MolLogP(mol))
    hbd = int(Lipinski.NumHDonors(mol))
    hba = int(Lipinski.NumHAcceptors(mol))
    rot = int(Lipinski.NumRotatableBonds(mol))
    tpsa = _float(Descriptors.TPSA(mol))
    qed = _float(QED.qed(mol))
    pains, pains_matches = _pains_match(mol)

    violations: list[str] = []
    if mw > 500:
        violations.append("MW>500")
    if logp > 5:
        violations.append("LogP>5")
    if hbd > 5:
        violations.append("HBD>5")
    if hba > 10:
        violations.append("HBA>10")

    rejected: list[str] = []
    if options.apply_lipinski and violations:
        rejected.extend(violations)
    if options.max_mw is not None and mw > options.max_mw:
        rejected.append("max_mw")
    if options.max_logp is not None and logp > options.max_logp:
        rejected.append("max_logp")
    if options.max_hbd is not None and hbd > options.max_hbd:
        rejected.append("max_hbd")
    if options.max_hba is not None and hba > options.max_hba:
        rejected.append("max_hba")
    if options.max_rotatable_bonds is not None and rot > options.max_rotatable_bonds:
        rejected.append("max_rotatable_bonds")
    if options.max_tpsa is not None and tpsa > options.max_tpsa:
        rejected.append("max_tpsa")
    if options.reject_pains and pains:
        rejected.append("PAINS")

    return {
        "accepted": not rejected,
        "reason": None if not rejected else "filter_rejection",
        "raw_smiles": raw,
        "canonical_smiles": canonical,
        "descriptors": {
            "molecular_weight": round(mw, 4),
            "logp": round(logp, 4),
            "hbd": hbd,
            "hba": hba,
            "rotatable_bonds": rot,
            "tpsa": round(tpsa, 4),
            "qed": round(qed, 6),
        },
        "lipinski_violations": violations,
        "pains_match": pains,
        "pains_matches": pains_matches,
        "rejected_by": rejected,
    }


def prepare_library(smiles_items: Iterable[str], options: ScreeningOptions, max_input: int = 250) -> dict:
    items = list(smiles_items)[:max_input]
    seen: set[str] = set()
    compounds: list[dict] = []
    duplicates = 0
    invalid = 0

    for idx, raw in enumerate(items):
        profile = standardize_and_profile(raw, options)
        if not profile.get("raw_smiles"):
            invalid += 1
            continue
        key = profile.get("canonical_smiles") or profile.get("raw_smiles")
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        profile["input_index"] = idx
        compounds.append(profile)
        if profile.get("accepted") is False:
            invalid += 1

    accepted = [c for c in compounds if c.get("accepted")]
    return {
        "method": "RDKit chemical QC + optional explicit filters",
        "rdkit_version": getattr(__import__("rdkit"), "__version__", "unknown"),
        "input_count": len(items),
        "unique_count": len(compounds),
        "accepted_count": len(accepted),
        "rejected_count": len(compounds) - len(accepted),
        "duplicates_removed": duplicates,
        "compounds": compounds,
    }
