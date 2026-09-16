"""
api/v1/ligands.py
GET    /api/v1/ligands              → remplace ligands_api.php?action=get_validated_ligands
GET    /api/v1/ligands/categories   → remplace api.php?action=get_ligand_categories
GET    /api/v1/ligands/search       → remplace api.php?action=search_ligands
GET    /api/v1/ligands/{id}         → remplace api.php?action=get_ligand_by_id
POST   /api/v1/ligands/{id}/validate → remplace api.php?action=validate_ligand_selection
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models.ligand import ValidatedLigand

router = APIRouter(prefix="/ligands", tags=["ligands"])


# ── Helpers (migration exacte de api.php) ───────────────────────────────────

def _lipinski(ligand: ValidatedLigand) -> list[str]:
    violations = []
    if ligand.molecular_weight > 500: violations.append("Poids moléculaire > 500 Da")
    if ligand.logp > 5:               violations.append("LogP > 5")
    if ligand.hydrogen_bond_donors > 5:    violations.append("Donneurs H > 5")
    if ligand.hydrogen_bond_acceptors > 10: violations.append("Accepteurs H > 10")
    return violations

def _drug_likeness_status(score: float) -> str:
    if score >= 0.8: return "excellent"
    if score >= 0.6: return "good"
    if score >= 0.4: return "moderate"
    if score >= 0.2: return "poor"
    return "very_poor"

def _binding_potential(ligand: ValidatedLigand) -> dict:
    score = 0; factors = []
    score += float(ligand.drug_likeness_score) * 40
    factors.append(f"Drug-likeness: {ligand.drug_likeness_score}")
    if ligand.rotatable_bonds <= 7:
        score += 20; factors.append("Flexibilité optimale")
    elif ligand.rotatable_bonds <= 10:
        score += 10; factors.append("Flexibilité modérée")
    if 20 <= float(ligand.topological_polar_surface_area) <= 140:
        score += 20; factors.append("Surface polaire optimale")
    if ligand.status == "approved":
        score += 20; factors.append("Médicament approuvé")
    elif ligand.status == "experimental":
        score += 10; factors.append("Expérimental")
    score = min(100, score)
    return {"score": score, "factors": factors,
            "rating": "excellent" if score >= 80 else ("good" if score >= 60 else ("moderate" if score >= 40 else "poor"))}

def _validate_compatibility(ligand: ValidatedLigand) -> dict:
    category_scores = {
        'kinase_inhibitor': 90, 'protease_inhibitor': 85, 'antibiotic': 80,
        'antineoplastic': 85, 'anti-inflammatory': 75, 'antiviral': 80,
        'neurotransmitter': 70, 'vitamin': 60, 'steroid': 75,
        'analgesic': 75, 'stimulant': 70, 'opioid': 75, 'benzodiazepine': 75,
    }
    score = category_scores.get(ligand.category, 70)
    notes = [f"Score de base catégorie {ligand.category}: {score}"]
    if float(ligand.drug_likeness_score) >= 0.8:
        score += 10; notes.append("Excellent drug-likeness (+10)")
    elif float(ligand.drug_likeness_score) >= 0.6:
        score += 5;  notes.append("Bon drug-likeness (+5)")
    params = {"exhaustiveness": 8, "num_modes": 9, "energy_range": 3, "search_space_radius": 20}
    if float(ligand.molecular_weight) > 400:
        params.update({"exhaustiveness": 12, "num_modes": 20, "energy_range": 5})
        notes.append("Paramètres augmentés pour ligand de grande taille")
    warnings = []
    violations = _lipinski(ligand)
    if violations:
        warnings.append("Violations de Lipinski: " + ", ".join(violations)); score -= 10
    if ligand.status == "research":
        warnings.append("Ligand de recherche — validation expérimentale requise"); score -= 5
    return {"score": max(0, min(100, score)), "notes": notes, "warnings": warnings, "parameters": params}

def _serialize(ligand: ValidatedLigand, full: bool = False) -> dict:
    d = {
        "id":                             ligand.id,
        "name":                           ligand.name,
        "smiles":                         ligand.smiles,
        "molecular_weight":               float(ligand.molecular_weight),
        "logp":                           float(ligand.logp),
        "hydrogen_bond_donors":           ligand.hydrogen_bond_donors,
        "hydrogen_bond_acceptors":        ligand.hydrogen_bond_acceptors,
        "rotatable_bonds":                ligand.rotatable_bonds,
        "topological_polar_surface_area": float(ligand.topological_polar_surface_area),
        "drug_likeness_score":            float(ligand.drug_likeness_score),
        "category":                       ligand.category,
        "description":                    ligand.description,
        "pubchem_cid":                    ligand.pubchem_cid,
        "status":                         ligand.status,
        "drug_likeness_status":           _drug_likeness_status(float(ligand.drug_likeness_score)),
        "lipinski_violations":            _lipinski(ligand),
        "binding_potential":              _binding_potential(ligand),
    }
    if full:
        d.update({"chebi_id": ligand.chebi_id, "uniprot_target": ligand.uniprot_target,
                  "binding_affinity_kcal": float(ligand.binding_affinity_kcal) if ligand.binding_affinity_kcal else None})
    return d


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("")
def get_ligands(
    category: Optional[str] = Query(None),
    status:   Optional[str] = Query(None),
    skip:     int           = Query(0, ge=0),
    limit:    int           = Query(50, le=200),
    db:       Session       = Depends(get_db),
):
    q = db.query(ValidatedLigand)
    if category: q = q.filter(ValidatedLigand.category == category)
    if status:   q = q.filter(ValidatedLigand.status   == status)
    total   = q.count()
    ligands = q.offset(skip).limit(limit).all()
    return {
        "success": True,
        "ligands": [_serialize(l) for l in ligands],
        "total":   total,
        "pagination": {"limit": limit, "offset": skip, "has_more": (skip + limit) < total},
    }


@router.get("/categories")
def get_categories(db: Session = Depends(get_db)):
    rows = (db.query(ValidatedLigand.category, func.count(ValidatedLigand.id))
              .group_by(ValidatedLigand.category)
              .order_by(func.count(ValidatedLigand.id).desc())
              .all())
    return {"success": True, "categories": [{"category": r[0], "count": r[1]} for r in rows]}


@router.get("/search")
def search_ligands(
    q:     str = Query(..., min_length=2),
    limit: int = Query(20, le=100),
    db:    Session = Depends(get_db),
):
    pattern = f"%{q.lower()}%"
    from sqlalchemy import or_
    results = (
        db.query(ValidatedLigand)
        .filter(or_(
            func.lower(ValidatedLigand.name).like(pattern),
            func.lower(ValidatedLigand.description).like(pattern),
        ))
        .limit(limit).all()
    )
    return {"success": True, "query": q, "results": [_serialize(l) for l in results], "count": len(results)}


@router.get("/{ligand_id}")
def get_ligand(ligand_id: int, db: Session = Depends(get_db)):
    l = db.query(ValidatedLigand).filter(ValidatedLigand.id == ligand_id).first()
    if not l:
        raise HTTPException(404, "Ligand introuvable")
    return {"success": True, "ligand": _serialize(l, full=True)}


@router.post("/{ligand_id}/validate")
def validate_ligand(ligand_id: int, analysis_id: int, db: Session = Depends(get_db)):
    l = db.query(ValidatedLigand).filter(ValidatedLigand.id == ligand_id).first()
    if not l:
        raise HTTPException(404, "Ligand introuvable")
    compat = _validate_compatibility(l)
    return {
        "success": True,
        "validation": {
            "ligand_valid": True,
            "ligand_info":  {"name": l.name, "smiles": l.smiles,
                             "category": l.category, "status": l.status,
                             "drug_likeness_score": float(l.drug_likeness_score)},
            "compatibility_score":    compat["score"],
            "compatibility_notes":    compat["notes"],
            "recommended_parameters": compat["parameters"],
            "warnings":               compat["warnings"],
        },
    }
