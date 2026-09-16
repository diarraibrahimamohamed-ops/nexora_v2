"""Research-study orchestration for the Sahel-focused N3XORA workflow.

This layer does not invent biological evidence. It records provenance and links
existing sequence, variant, structure and docking components into one study.
The first supported scope is malaria / Plasmodium falciparum with Mali/Sahel
as the default context; the data model remains pathogen-agnostic for extension.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.docking import DockingJob, JobStatus
from app.models.research import ResearchDockingRun, ResearchStudy, ResearchStructureJob, ResearchTarget, ResearchVariant, ResearchStructureComparison, ResearchDockingComparison
from app.models.sequence import Analysis, Mutation, Sequence
from app.models.user import User
from app.models.virtual_screening import ResearchScreeningRun, ResearchScreeningCompound
from app.services.auth_service import get_current_user
from app.services.variant_annotation import build_nucleotide_consequence, build_protein_consequence
from app.core.protein.structure_comparison import compare_structures
from app.workers.docking_tasks import run_vina_task
from app.core.docking.comparative import compare_completed_results
from app.core.docking.pose_comparison import compare_docking_pose_sets
import hashlib
from app.workers.research_structure_tasks import resolve_research_structure_task
from app.config import settings
from app.services.virtual_screening import ScreeningOptions, prepare_library

router = APIRouter(prefix="/research", tags=["research"])

_AA_RE = re.compile(r"[^ACDEFGHIKLMNPQRSTVWY]", re.I)
_NT_RE = re.compile(r"^[ACGTU]+$", re.I)


def _clean_protein(seq: str) -> str:
    return _AA_RE.sub("", (seq or "").upper().strip())


def _study(db: Session, study_id: int, user: User) -> ResearchStudy:
    study = db.query(ResearchStudy).filter(ResearchStudy.id == study_id, ResearchStudy.user_id == user.id).first()
    if not study:
        raise HTTPException(404, "Étude introuvable")
    return study


def _validate_provenance(value: Any) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("provenance doit être un objet JSON")
    return value


class StudyCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=255)
    disease: str = Field("malaria", min_length=2, max_length=150)
    pathogen: str = Field("Plasmodium falciparum", min_length=2, max_length=150)
    country_focus: str = Field("Mali", min_length=2, max_length=120)
    region_scope: str = Field("Sahel", min_length=2, max_length=120)
    objective: Optional[str] = Field(None, max_length=4000)
    hypothesis: Optional[str] = Field(None, max_length=4000)
    metadata: dict = Field(default_factory=dict)


class VariantCreate(BaseModel):
    source_analysis_id: Optional[int] = Field(None, ge=1)
    variant_notation: str = Field(..., min_length=1, max_length=100)
    source_accession: Optional[str] = Field(None, max_length=120)
    gene_symbol: Optional[str] = Field(None, max_length=120)
    nucleotide_position: Optional[int] = Field(None, ge=1)
    ref_nt: Optional[str] = Field(None, min_length=1, max_length=1)
    alt_nt: Optional[str] = Field(None, min_length=1, max_length=1)
    codon_ref: Optional[str] = Field(None, min_length=3, max_length=3)
    codon_alt: Optional[str] = Field(None, min_length=3, max_length=3)
    aa_ref: Optional[str] = Field(None, min_length=1, max_length=1)
    aa_alt: Optional[str] = Field(None, min_length=1, max_length=1)
    protein_position: Optional[int] = Field(None, ge=1)
    evidence_class: str = Field("candidate", min_length=3, max_length=40)
    evidence_sources: List[str] = Field(default_factory=list, max_length=50)
    notes: Optional[str] = Field(None, max_length=4000)

    @field_validator("ref_nt", "alt_nt")
    @classmethod
    def nt(cls, value):
        if value and value.upper() not in "ACGTU":
            raise ValueError("nucléotide invalide")
        return value.upper() if value else value


class TargetCreate(BaseModel):
    state: str = Field("wt", pattern="^(wt|variant)$")
    variant_id: Optional[int] = None
    gene_symbol: Optional[str] = Field(None, max_length=120)
    protein_name: Optional[str] = Field(None, max_length=255)
    protein_sequence: str = Field(..., min_length=3, max_length=1000)
    structure_source: str = Field("sequence_resolved", max_length=40)
    structure_accession: Optional[str] = Field(None, max_length=120)
    structure_path: Optional[str] = Field(None, max_length=500)
    structure_confidence: Optional[float] = Field(None, ge=0, le=1)
    provenance: dict = Field(default_factory=dict)

    @field_validator("protein_sequence")
    @classmethod
    def protein(cls, value):
        cleaned = _clean_protein(value)
        compact = re.sub(r"\s+", "", value or "")
        if len(cleaned) < 3 or len(cleaned) != len(compact):
            raise ValueError("Séquence protéique invalide: utilisez uniquement les acides aminés standards")
        return cleaned

    @field_validator("provenance")
    @classmethod
    def provenance(cls, value):
        return _validate_provenance(value)


class DockingCreate(BaseModel):
    target_id: int = Field(..., ge=1)
    variant_id: Optional[int] = Field(None, ge=1)
    ligand_smiles: str = Field(..., min_length=1, max_length=1000)
    reference_state: str = Field("wt", pattern="^(wt|variant)$")
    max_pockets: int = Field(3, ge=1, le=10)


class StructureResolveCreate(BaseModel):
    provider: str = Field("auto_rcsb", pattern="^(auto_rcsb|esmfold|colabfold)$")


def _serialize_study(study: ResearchStudy) -> dict:
    return {
        "id": study.id,
        "name": study.name,
        "disease": study.disease,
        "pathogen": study.pathogen,
        "country_focus": study.country_focus,
        "region_scope": study.region_scope,
        "objective": study.objective,
        "hypothesis": study.hypothesis,
        "status": study.status,
        "metadata": study.metadata_json or {},
        "created_at": study.created_at.isoformat() if study.created_at else None,
        "updated_at": study.updated_at.isoformat() if study.updated_at else None,
        "counts": {
            "variants": len(study.variants),
            "targets": len(study.targets),
            "docking_runs": len(study.docking_runs),
            "structure_comparisons": len(getattr(study, "structure_comparisons", []) or []),
            "docking_comparisons": len(getattr(study, "docking_comparisons", []) or []),
        },
    }


@router.post("/studies")
def create_study(body: StudyCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    study = ResearchStudy(
        user_id=current_user.id,
        name=body.name,
        disease=body.disease,
        pathogen=body.pathogen,
        country_focus=body.country_focus,
        region_scope=body.region_scope,
        objective=body.objective,
        hypothesis=body.hypothesis,
        metadata_json=body.metadata,
    )
    db.add(study)
    db.commit()
    db.refresh(study)
    return {"success": True, "study": _serialize_study(study)}


@router.get("/studies")
def list_studies(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    studies = (
        db.query(ResearchStudy)
        .filter(ResearchStudy.user_id == current_user.id)
        .order_by(ResearchStudy.created_at.desc())
        .offset(skip).limit(limit).all()
    )
    return {"success": True, "studies": [_serialize_study(s) for s in studies]}


@router.get("/studies/{study_id}")
def get_study(study_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    study = _study(db, study_id, current_user)
    return {
        "success": True,
        "study": _serialize_study(study),
        "variants": [
            {"id": v.id, "variant_notation": v.variant_notation, "gene_symbol": v.gene_symbol,
             "source_analysis_id": v.source_analysis_id, "source_accession": v.source_accession, "evidence_class": v.evidence_class,
             "evidence_sources": v.evidence_sources or [], "nucleotide_position": v.nucleotide_position,
             "aa_ref": v.aa_ref, "aa_alt": v.aa_alt, "protein_position": v.protein_position,
             "consequence": v.consequence, "annotation_method": v.annotation_method,
             "annotation_notes": v.annotation_notes}
            for v in study.variants
        ],
        "targets": [
            {"id": t.id, "state": t.state, "variant_id": t.variant_id, "gene_symbol": t.gene_symbol,
             "protein_name": t.protein_name, "protein_length": len(t.protein_sequence),
             "structure_source": t.structure_source, "structure_accession": t.structure_accession,
             "structure_confidence": float(t.structure_confidence) if t.structure_confidence is not None else None,
             "structure_status": t.structure_status,
             "structure_sequence_identity": float(t.structure_sequence_identity) if t.structure_sequence_identity is not None else None,
             "structure_sequence_coverage": float(t.structure_sequence_coverage) if t.structure_sequence_coverage is not None else None,
             "structure_validation": t.structure_validation or {}}
            for t in study.targets
        ],
        "docking_runs": [_run_summary(r, db) for r in study.docking_runs],
        "structure_comparisons": [
            {
                "id": c.id,
                "wt_target_id": c.wt_target_id,
                "variant_target_id": c.variant_target_id,
                "variant_id": c.variant_id,
                "status": c.status,
                "parameters": c.parameters or {},
                "report": c.report or {},
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in db.query(ResearchStructureComparison).filter(ResearchStructureComparison.study_id == study_id).order_by(ResearchStructureComparison.created_at.desc()).all()
        ],
        "docking_comparisons": [
            {
                "id": c.id,
                "variant_id": c.variant_id,
                "wt_run_id": c.wt_run_id,
                "variant_run_id": c.variant_run_id,
                "ligand_smiles": c.ligand_smiles,
                "status": c.status,
                "protocol": c.protocol or {},
                "result_summary": c.result_summary or {},
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in db.query(ResearchDockingComparison).filter(ResearchDockingComparison.study_id == study_id).order_by(ResearchDockingComparison.created_at.desc()).all()
        ],
    }


def _run_summary(run: ResearchDockingRun, db: Session) -> dict:
    job = db.query(DockingJob).filter(DockingJob.id == run.docking_job_id).first() if run.docking_job_id else None
    status = job.status.value if job is not None and hasattr(job.status, "value") else (str(job.status) if job else run.status)
    out = {
        "id": run.id,
        "target_id": run.target_id,
        "variant_id": run.variant_id,
        "docking_job_id": run.docking_job_id,
        "ligand_smiles": run.ligand_smiles,
        "reference_state": run.reference_state,
        "engine": run.engine,
        "aggregation_method": run.aggregation_method,
        "status": status,
        "parameters": run.parameters or {},
        "result_summary": run.result_summary,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
    if job:
        out["progress"] = job.progress or 0
        out["status_message"] = job.status_message
        if job.result_path and os.path.exists(job.result_path) and status == "completed":
            try:
                with open(job.result_path, encoding="utf-8") as f:
                    data = json.load(f)
                out["result_summary"] = {
                    "vina_best_score": data.get("vina_best_score"),
                    "boltzmann_effective_score": data.get("boltzmann_effective_score"),
                    "boltzmann_mean_score": data.get("boltzmann_mean_score"),
                    "selected_pose_id": data.get("selected_pose_id"),
                    "selected_pose_score": data.get("selected_pose_score"),
                    "best_pocket": data.get("best_pocket"),
                    "docking_engine": data.get("docking_engine"),
                    "aggregation_method": data.get("aggregation_method"),
                }
                run.result_summary = out["result_summary"]
                run.status = "completed"
                db.commit()
            except (OSError, ValueError, json.JSONDecodeError):
                pass
    return out


@router.post("/studies/{study_id}/variants")
def create_variant(study_id: int, body: VariantCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _study(db, study_id, current_user)
    variant = ResearchVariant(study_id=study_id, **body.model_dump())
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return {"success": True, "variant_id": variant.id}


@router.post("/studies/{study_id}/variants/from-analysis/{analysis_id}")
def import_variants_from_analysis(study_id: int, analysis_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _study(db, study_id, current_user)
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id, Analysis.user_id == current_user.id).first()
    if not analysis:
        raise HTTPException(404, "Analyse introuvable")
    seq = db.query(Sequence).filter(Sequence.analysis_id == analysis.id).order_by(Sequence.id.asc()).first()
    imported = []
    for m in db.query(Mutation).filter(Mutation.analysis_id == analysis.id).order_by(Mutation.id.asc()).all():
        ref = m.ref_base or "?"
        alt = m.qry_base or "?"
        pos = m.ref_pos or m.qry_pos
        notation = f"{ref}{pos}{alt}" if pos else f"mutation-{m.id}"
        v = ResearchVariant(
            study_id=study_id,
            source_analysis_id=analysis.id,
            source_accession=seq.accession if seq else None,
            gene_symbol=None,
            nucleotide_position=pos,
            ref_nt=ref if ref in "ACGTU" else None,
            alt_nt=alt if alt in "ACGTU" else None,
            variant_notation=notation,
            evidence_class="observed",
            evidence_sources=[f"N3XORA analysis:{analysis.id}"],
            notes="Variant importé depuis l'analyse N3XORA; l'import ne confère aucune causalité ni association de résistance.",
        )
        db.add(v)
        imported.append(notation)
    db.commit()
    return {"success": True, "study_id": study_id, "analysis_id": analysis_id, "imported": imported, "count": len(imported)}


def _extract_protein_from_analysis(analysis: Analysis) -> Optional[str]:
    data = analysis.data or {}
    if not isinstance(data, dict):
        return None
    protein_data = data.get("protein_data")
    candidates = []
    if isinstance(protein_data, str):
        candidates.append(protein_data)
    elif isinstance(protein_data, dict):
        candidates.extend(protein_data.get(k) for k in ("sequence", "protein_sequence", "seq"))
    candidates.extend(data.get(k) for k in ("protein_sequence",))
    for value in candidates:
        cleaned = _clean_protein(value or "")
        if len(cleaned) >= 3:
            return cleaned
    return None


@router.post("/studies/{study_id}/targets/from-analysis/{analysis_id}")
def import_target_from_analysis(study_id: int, analysis_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _study(db, study_id, current_user)
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id, Analysis.user_id == current_user.id).first()
    if not analysis:
        raise HTTPException(404, "Analyse introuvable")
    protein = _extract_protein_from_analysis(analysis)
    if not protein:
        raise HTTPException(400, "Aucune séquence protéique exploitable dans cette analyse")
    target = ResearchTarget(
        study_id=study_id,
        state="wt",
        gene_symbol=None,
        protein_name=None,
        protein_sequence=protein,
        structure_source="sequence_resolved",
        structure_status="not_generated",
        provenance={"source_analysis_id": analysis.id, "source": "N3XORA analysis", "structure_not_generated": True},
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return {"success": True, "study_id": study_id, "analysis_id": analysis_id, "target_id": target.id, "protein_length": len(protein)}


@router.post("/studies/{study_id}/variants/{variant_id}/nucleotide-consequence")
def annotate_nucleotide_consequence(
    study_id: int,
    variant_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _study(db, study_id, current_user)
    variant = db.query(ResearchVariant).filter(ResearchVariant.id == variant_id, ResearchVariant.study_id == study_id).first()
    if not variant:
        raise HTTPException(404, "Variant introuvable")
    try:
        result = build_nucleotide_consequence(
            body.get("cds_sequence"),
            nucleotide_position=body.get("nucleotide_position") or variant.nucleotide_position,
            ref_nt=body.get("ref_nt") or variant.ref_nt,
            alt_nt=body.get("alt_nt") or variant.alt_nt,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))

    variant.ref_nt = result.ref_nt
    variant.alt_nt = result.alt_nt
    variant.nucleotide_position = result.position
    variant.codon_ref = result.codon_ref
    variant.codon_alt = result.codon_alt
    variant.aa_ref = result.aa_ref
    variant.aa_alt = result.aa_alt
    variant.protein_position = result.protein_position
    variant.consequence = result.consequence
    variant.annotation_method = result.method
    variant.annotation_notes = (
        "Conséquence dérivée d'une CDS explicitement fournie en orientation codante 5'→3' et "
        "d'une coordonnée 1-based; aucune causalité ou résistance n'est inférée."
    )
    db.commit()
    return {
        "success": True,
        "study_id": study_id,
        "variant_id": variant_id,
        "annotation": {
            "nucleotide_position": result.position,
            "ref_nt": result.ref_nt,
            "alt_nt": result.alt_nt,
            "codon_ref": result.codon_ref,
            "codon_alt": result.codon_alt,
            "aa_ref": result.aa_ref,
            "aa_alt": result.aa_alt,
            "protein_position": result.protein_position,
            "consequence": result.consequence,
            "method": result.method,
            "coding_sequence_length": result.coding_sequence_length,
        },
    }


@router.post("/studies/{study_id}/variants/{variant_id}/protein-consequence")
def annotate_protein_consequence(
    study_id: int,
    variant_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _study(db, study_id, current_user)
    variant = (
        db.query(ResearchVariant)
        .filter(ResearchVariant.id == variant_id, ResearchVariant.study_id == study_id)
        .first()
    )
    if not variant:
        raise HTTPException(404, "Variant introuvable")
    try:
        reference_protein = body.get("reference_protein_sequence")
        result = build_protein_consequence(
            reference_protein,
            notation=body.get("notation") or variant.variant_notation,
            ref_aa=body.get("ref_aa") or variant.aa_ref,
            alt_aa=body.get("alt_aa") or variant.aa_alt,
            protein_position=body.get("protein_position") or variant.protein_position,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))

    variant.aa_ref = result.ref_aa
    variant.aa_alt = result.alt_aa
    variant.protein_position = result.protein_position
    variant.consequence = result.consequence
    variant.annotation_method = result.method
    variant.annotation_notes = (
        "Conséquence protéique construite par validation de la séquence WT et substitution 1-based; "
        "aucune causalité biologique n'est inférée."
    )
    db.commit()
    return {
        "success": True,
        "study_id": study_id,
        "variant_id": variant_id,
        "annotation": {
            "notation": result.notation,
            "ref_aa": result.ref_aa,
            "alt_aa": result.alt_aa,
            "protein_position": result.protein_position,
            "consequence": result.consequence,
            "method": result.method,
            "reference_protein_length": result.reference_protein_length,
            "variant_protein_length": result.variant_protein_length,
            "variant_protein_sequence": result.variant_protein_sequence,
        },
    }


@router.post("/studies/{study_id}/variants/{variant_id}/materialize-target")
def materialize_variant_target(
    study_id: int,
    variant_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    study = _study(db, study_id, current_user)
    variant = db.query(ResearchVariant).filter(ResearchVariant.id == variant_id, ResearchVariant.study_id == study_id).first()
    if not variant:
        raise HTTPException(404, "Variant introuvable")

    wt_target_id = body.get("wt_target_id")
    if not wt_target_id:
        raise HTTPException(400, "wt_target_id requis")
    wt = (
        db.query(ResearchTarget)
        .filter(ResearchTarget.id == int(wt_target_id), ResearchTarget.study_id == study_id, ResearchTarget.state == "wt")
        .first()
    )
    if not wt:
        raise HTTPException(404, "Cible WT introuvable dans cette étude")

    try:
        result = build_protein_consequence(
            wt.protein_sequence,
            notation=variant.variant_notation,
            ref_aa=variant.aa_ref,
            alt_aa=variant.aa_alt,
            protein_position=variant.protein_position,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))

    target = ResearchTarget(
        study_id=study.id,
        state="variant",
        variant_id=variant.id,
        gene_symbol=wt.gene_symbol or variant.gene_symbol,
        protein_name=wt.protein_name,
        protein_sequence=result.variant_protein_sequence,
        structure_source="sequence_derived_variant",
        structure_status="not_generated",
        provenance={
            "derived_from_wt_target_id": wt.id,
            "derived_from_variant_id": variant.id,
            "method": result.method,
            "structure_not_generated": True,
            "note": "Séquence mutante dérivée in silico; structure 3D à obtenir/valider séparément.",
        },
    )
    db.add(target)
    variant.aa_ref = result.ref_aa
    variant.aa_alt = result.alt_aa
    variant.protein_position = result.protein_position
    variant.consequence = result.consequence
    variant.annotation_method = result.method
    db.commit()
    db.refresh(target)
    return {
        "success": True,
        "study_id": study_id,
        "variant_id": variant_id,
        "wt_target_id": wt.id,
        "variant_target_id": target.id,
        "annotation": {
            "notation": result.notation,
            "protein_position": result.protein_position,
            "ref_aa": result.ref_aa,
            "alt_aa": result.alt_aa,
            "consequence": result.consequence,
            "method": result.method,
        },
        "structure_status": "not_generated",
    }


@router.post("/studies/{study_id}/targets")
def create_target(study_id: int, body: TargetCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _study(db, study_id, current_user)
    if body.state == "variant" and not body.variant_id:
        raise HTTPException(400, "Une cible variant doit référencer un variant")
    if body.state == "wt" and body.variant_id is not None:
        raise HTTPException(400, "Une cible WT ne doit pas référencer un variant")
    if body.variant_id:
        variant = db.query(ResearchVariant).filter(ResearchVariant.id == body.variant_id, ResearchVariant.study_id == study_id).first()
        if not variant:
            raise HTTPException(404, "Variant introuvable dans cette étude")
    target = ResearchTarget(study_id=study_id, **body.model_dump())
    db.add(target)
    db.commit()
    db.refresh(target)
    return {"success": True, "target_id": target.id}




def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_structure_path(path: str) -> Optional[str]:
    """Allow structure access only inside configured N3XORA storage roots."""
    if not path:
        return None
    real = os.path.realpath(path)
    roots = [
        os.path.realpath(settings.UPLOAD_DIR),
        os.path.realpath(settings.RESULTS_DIR),
        os.path.realpath(settings.TEMP_DIR),
    ]
    for root in roots:
        try:
            if os.path.commonpath([real, root]) == root:
                return real
        except ValueError:
            continue
    return None



class ScreeningPrepareCreate(BaseModel):
    target_id: int = Field(..., ge=1)
    ligands: List[str] = Field(..., min_length=1, max_length=250)
    apply_lipinski: bool = False
    reject_pains: bool = False
    max_mw: Optional[float] = Field(500.0, gt=0, le=2000)
    max_logp: Optional[float] = Field(5.0, ge=-20, le=20)
    max_hbd: Optional[int] = Field(5, ge=0, le=50)
    max_hba: Optional[int] = Field(10, ge=0, le=50)
    max_rotatable_bonds: Optional[int] = Field(10, ge=0, le=100)
    max_tpsa: Optional[float] = Field(150.0, ge=0, le=1000)


@router.post("/studies/{study_id}/screening/prepare", status_code=201)
def prepare_screening(
    study_id: int,
    body: ScreeningPrepareCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    study = _study(db, study_id, current_user)
    target = db.query(ResearchTarget).filter(
        ResearchTarget.id == body.target_id,
        ResearchTarget.study_id == study_id,
        ResearchTarget.state.in_(["wt", "variant"]),
    ).first()
    if not target:
        raise HTTPException(404, "Cible introuvable dans cette étude")
    structure_path = _safe_structure_path(target.structure_path or "")
    if not structure_path or not os.path.isfile(structure_path):
        raise HTTPException(409, "Structure de la cible absente")
    if target.structure_status not in {"accepted_for_docking_review", "predicted_reviewable"}:
        raise HTTPException(409, f"Structure non prête: {target.structure_status}")

    options = ScreeningOptions(
        apply_lipinski=body.apply_lipinski,
        reject_pains=body.reject_pains,
        max_mw=body.max_mw,
        max_logp=body.max_logp,
        max_hbd=body.max_hbd,
        max_hba=body.max_hba,
        max_rotatable_bonds=body.max_rotatable_bonds,
        max_tpsa=body.max_tpsa,
    )
    result = prepare_library(body.ligands, options, max_input=250)
    protocol = {
        "protocol_version": "research-screening-v1",
        "target_id": target.id,
        "target_state": target.state,
        "structure_sha256": _sha256_file(structure_path),
        "engine": "AutoDock Vina",
        "chemical_qc": {
            "method": result["method"],
            "apply_lipinski": body.apply_lipinski,
            "reject_pains": body.reject_pains,
            "max_mw": body.max_mw,
            "max_logp": body.max_logp,
            "max_hbd": body.max_hbd,
            "max_hba": body.max_hba,
            "max_rotatable_bonds": body.max_rotatable_bonds,
            "max_tpsa": body.max_tpsa,
        },
        "ranking_rule": "Aucun classement d'activité RDKit; les descripteurs servent au QC. Le classement principal vient du docking Vina après lancement.",
    }
    screening = ResearchScreeningRun(
        study_id=study.id,
        target_id=target.id,
        status="prepared",
        protocol=protocol,
        summary={
            "input_count": result["input_count"],
            "unique_count": result["unique_count"],
            "accepted_count": result["accepted_count"],
            "rejected_count": result["rejected_count"],
            "duplicates_removed": result["duplicates_removed"],
            "rdkit_version": result["rdkit_version"],
        },
    )
    db.add(screening)
    db.flush()
    for item in result["compounds"]:
        status = "accepted" if item.get("accepted") else "rejected"
        db.add(ResearchScreeningCompound(
            screening_id=screening.id,
            input_index=int(item["input_index"]),
            canonical_smiles=item.get("canonical_smiles") or item.get("raw_smiles"),
            descriptors=item.get("descriptors") or {},
            qc={k: v for k, v in item.items() if k not in {"descriptors", "canonical_smiles", "raw_smiles"}},
            status=status,
        ))
    db.commit()
    db.refresh(screening)
    return {"success": True, "screening_id": screening.id, "status": screening.status, "target_id": target.id, "protocol": protocol, "summary": screening.summary, "compounds": result["compounds"]}


@router.get("/studies/{study_id}/screening")
def list_screenings(
    study_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _study(db, study_id, current_user)
    rows = db.query(ResearchScreeningRun).filter(ResearchScreeningRun.study_id == study_id).order_by(ResearchScreeningRun.created_at.desc()).all()
    return {"success": True, "screenings": [
        {
            "id": r.id,
            "target_id": r.target_id,
            "status": r.status,
            "engine": r.engine,
            "protocol": r.protocol or {},
            "summary": r.summary or {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows
    ]}


@router.get("/studies/{study_id}/screening/{screening_id}")
def get_screening(
    study_id: int,
    screening_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _study(db, study_id, current_user)
    r = db.query(ResearchScreeningRun).filter(ResearchScreeningRun.id == screening_id, ResearchScreeningRun.study_id == study_id).first()
    if not r:
        raise HTTPException(404, "Screening introuvable")
    compounds = db.query(ResearchScreeningCompound).filter(ResearchScreeningCompound.screening_id == r.id).order_by(ResearchScreeningCompound.input_index.asc()).all()
    return {"success": True, "screening": {"id": r.id, "target_id": r.target_id, "status": r.status, "engine": r.engine, "protocol": r.protocol or {}, "summary": r.summary or {}, "created_at": r.created_at.isoformat() if r.created_at else None}, "compounds": [
        {"id": c.id, "input_index": c.input_index, "canonical_smiles": c.canonical_smiles, "descriptors": c.descriptors or {}, "qc": c.qc or {}, "status": c.status, "docking_job_id": c.docking_job_id, "research_docking_run_id": c.research_docking_run_id, "docking_summary": c.docking_summary or {}}
        for c in compounds
    ]}


@router.post("/studies/{study_id}/screening/{screening_id}/launch", status_code=202)
def launch_screening(
    study_id: int,
    screening_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Launch Vina for accepted compounds of a prepared, controlled screen.

    This is intentionally capped at 50 compounds per launch. The screen does
    not synthesize candidates, infer activity, or replace a benchmark. Every
    child docking run records the exact ligand and structure hash used.
    """
    study = _study(db, study_id, current_user)
    screening = db.query(ResearchScreeningRun).filter(
        ResearchScreeningRun.id == screening_id,
        ResearchScreeningRun.study_id == study_id,
    ).first()
    if not screening:
        raise HTTPException(404, "Screening introuvable")
    target = db.query(ResearchTarget).filter(
        ResearchTarget.id == screening.target_id,
        ResearchTarget.study_id == study_id,
    ).first()
    if not target:
        raise HTTPException(404, "Cible du screening introuvable")
    structure_path = _safe_structure_path(target.structure_path or "")
    if not structure_path or not os.path.isfile(structure_path):
        raise HTTPException(409, "Structure de la cible absente")
    if target.structure_status not in {"accepted_for_docking_review", "predicted_reviewable"}:
        raise HTTPException(409, f"Structure non prête: {target.structure_status}")
    if screening.status in {"queued", "running"}:
        raise HTTPException(409, "Screening déjà en cours")

    accepted = db.query(ResearchScreeningCompound).filter(
        ResearchScreeningCompound.screening_id == screening.id,
        ResearchScreeningCompound.status == "accepted",
        ResearchScreeningCompound.docking_job_id.is_(None),
    ).order_by(ResearchScreeningCompound.input_index.asc()).limit(50).all()
    if not accepted:
        raise HTTPException(409, "Aucun composé accepté disponible pour le lancement")

    structure_sha256 = _sha256_file(structure_path)
    protocol = dict(screening.protocol or {})
    protocol.update({
        "launch_compound_cap": 50,
        "vina_exhaustiveness": settings.VINA_EXHAUSTIVENESS,
        "vina_cpu": settings.VINA_CPU,
        "structure_sha256": structure_sha256,
        "ranking_after_docking": "vina_best_score",
    })
    screening.protocol = protocol
    screening.status = "queued"
    screening.summary = {**(screening.summary or {}), "launched_count": len(accepted), "structure_sha256": structure_sha256}
    db.flush()

    launched = []
    try:
        for compound in accepted:
            job = DockingJob(
                user_id=current_user.id,
                status=JobStatus.pending,
                protein_seq=target.protein_sequence,
                ligand_smiles=compound.canonical_smiles,
                progress=0,
            )
            db.add(job)
            db.flush()
            run = ResearchDockingRun(
                study_id=study.id,
                target_id=target.id,
                variant_id=target.variant_id,
                docking_job_id=job.id,
                ligand_smiles=compound.canonical_smiles,
                reference_state=target.state,
                parameters={
                    "protocol_version": "research-screening-v1",
                    "screening_id": screening.id,
                    "structure_sha256": structure_sha256,
                    "vina_exhaustiveness": settings.VINA_EXHAUSTIVENESS,
                    "vina_cpu": settings.VINA_CPU,
                    "max_pockets": 1,
                },
                status="pending",
            )
            db.add(run)
            db.flush()
            compound.docking_job_id = job.id
            compound.research_docking_run_id = run.id
            compound.status = "queued"
            launched.append((compound, job, run))
        db.commit()
    except Exception:
        db.rollback()
        raise

    for compound, job, run in launched:
        task = run_vina_task.apply_async(
            args=[job.id, target.protein_sequence, compound.canonical_smiles, 1, None, structure_path],
            queue="docking",
        )
        job.celery_task_id = task.id
        run.parameters = {**(run.parameters or {}), "celery_task_id": task.id}
    screening.status = "running"
    db.commit()
    return {"success": True, "screening_id": screening.id, "status": screening.status, "launched_count": len(launched), "compound_cap": 50}


@router.get("/studies/{study_id}/screening/{screening_id}/status")
def screening_status(
    study_id: int,
    screening_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _study(db, study_id, current_user)
    screening = db.query(ResearchScreeningRun).filter(ResearchScreeningRun.id == screening_id, ResearchScreeningRun.study_id == study_id).first()
    if not screening:
        raise HTTPException(404, "Screening introuvable")
    compounds = db.query(ResearchScreeningCompound).filter(ResearchScreeningCompound.screening_id == screening.id).all()
    counts = {"accepted": 0, "queued": 0, "running": 0, "completed": 0, "failed": 0, "rejected": 0}
    rows = []
    for c in compounds:
        st = c.status
        if c.docking_job_id:
            job = db.query(DockingJob).filter(DockingJob.id == c.docking_job_id).first()
            if job:
                value = job.status.value if hasattr(job.status, "value") else str(job.status)
                if value == "completed": st = "completed"
                elif value == "failed": st = "failed"
                elif value in {"running", "pending"}: st = value
                if value == "completed" and job.result_path and os.path.isfile(job.result_path):
                    try:
                        with open(job.result_path, encoding="utf-8") as fh:
                            result = json.load(fh)
                        c.docking_summary = {
                            "vina_best_score": result.get("vina_best_score", result.get("docking_score")),
                            "boltzmann_effective_score": result.get("boltzmann_effective_score"),
                            "num_poses": result.get("num_poses", len(result.get("poses", []) or [])),
                        }
                    except (OSError, json.JSONDecodeError):
                        pass
        if st in counts: counts[st] += 1
        rows.append({"id": c.id, "status": st, "canonical_smiles": c.canonical_smiles, "descriptors": c.descriptors or {}, "docking_job_id": c.docking_job_id, "docking_summary": c.docking_summary or {}})
    launched = counts["queued"] + counts["running"] + counts["completed"] + counts["failed"]
    terminal = counts["completed"] + counts["failed"]
    if screening.status in {"running", "queued"} and launched > 0 and terminal == launched:
        screening.status = "completed" if counts["failed"] == 0 else "completed_with_failures"
        screening.summary = {**(screening.summary or {}), "status_counts": counts, "terminal_count": terminal}
        db.commit()
    return {"success": True, "screening_id": screening.id, "status": screening.status, "counts": counts, "compounds": rows}

@router.post("/studies/{study_id}/structure-comparisons", status_code=201)
def compare_wt_variant_structures(
    study_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Compare a validated WT target against its linked variant target."""
    _study(db, study_id, current_user)
    try:
        wt_id = int(body.get("wt_target_id"))
        variant_id = int(body.get("variant_target_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "wt_target_id et variant_target_id sont requis")
    wt = db.query(ResearchTarget).filter(ResearchTarget.id == wt_id, ResearchTarget.study_id == study_id, ResearchTarget.state == "wt").first()
    variant_target = db.query(ResearchTarget).filter(ResearchTarget.id == variant_id, ResearchTarget.study_id == study_id, ResearchTarget.state == "variant").first()
    if not wt or not variant_target:
        raise HTTPException(404, "Cible WT ou cible variant introuvable dans cette étude")
    if not variant_target.variant_id:
        raise HTTPException(400, "La cible variant n'est pas liée à un variant")
    if wt.structure_status not in {"accepted_for_docking_review", "predicted_reviewable"} or variant_target.structure_status not in {"accepted_for_docking_review", "predicted_reviewable"}:
        raise HTTPException(409, "Les deux structures doivent être validées avant la comparaison")
    wt_path = _safe_structure_path(wt.structure_path or "")
    var_path = _safe_structure_path(variant_target.structure_path or "")
    if not wt_path or not var_path or not os.path.isfile(wt_path) or not os.path.isfile(var_path):
        raise HTTPException(409, "Les fichiers PDB des deux cibles sont requis")
    try:
        report = compare_structures(
            wt_path, var_path,
            variant_protein_position=variant_target.variant.protein_position if variant_target.variant else None,
            local_radius_A=float(body.get("local_radius_A", 8.0)),
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(400, f"Comparaison structurale impossible: {exc}")
    comparison = ResearchStructureComparison(
        study_id=study_id, wt_target_id=wt.id, variant_target_id=variant_target.id,
        variant_id=variant_target.variant_id, status=report.get("status", "computed"),
        parameters={"local_radius_A": float(body.get("local_radius_A", 8.0)), "alignment": "sequence-corresponding C-alpha + Kabsch"},
        report=report,
    )
    db.add(comparison)
    # Do not overwrite the primary validation result. Keep a separate Phase 4 provenance entry.
    wt.provenance = {**(wt.provenance or {}), "phase4_structure_comparison_ids": [*(wt.provenance or {}).get("phase4_structure_comparison_ids", []), comparison.id]}
    variant_target.provenance = {**(variant_target.provenance or {}), "phase4_structure_comparison_ids": [*(variant_target.provenance or {}).get("phase4_structure_comparison_ids", []), comparison.id]}
    db.commit()
    db.refresh(comparison)
    return {"success": True, "comparison_id": comparison.id, "study_id": study_id, "report": report}


@router.get("/studies/{study_id}/structure-comparisons")
def list_structure_comparisons(
    study_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    _study(db, study_id, current_user)
    items = db.query(ResearchStructureComparison).filter(ResearchStructureComparison.study_id == study_id).order_by(ResearchStructureComparison.created_at.desc()).all()
    return {"success": True, "comparisons": [{"id": c.id, "wt_target_id": c.wt_target_id, "variant_target_id": c.variant_target_id, "variant_id": c.variant_id, "status": c.status, "parameters": c.parameters or {}, "report": c.report or {}, "created_at": c.created_at.isoformat() if c.created_at else None} for c in items]}


@router.post("/studies/{study_id}/targets/{target_id}/resolve-structure", status_code=202)
def resolve_structure(
    study_id: int,
    target_id: int,
    body: StructureResolveCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _study(db, study_id, current_user)
    target = db.query(ResearchTarget).filter(
        ResearchTarget.id == target_id,
        ResearchTarget.study_id == study_id,
    ).first()
    if not target:
        raise HTTPException(404, "Cible introuvable")
    target.structure_status = "pending"
    target.structure_validation = {}
    job = ResearchStructureJob(
        study_id=study_id,
        target_id=target_id,
        provider=body.provider,
        status="pending",
        progress=0,
        metadata_json={
            "sequence_length": len(target.protein_sequence),
            "requested_provider": body.provider,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    task = resolve_research_structure_task.apply_async(args=[job.id], queue="structure")
    job.celery_task_id = task.id
    db.commit()
    return {
        "success": True,
        "study_id": study_id,
        "target_id": target_id,
        "structure_job_id": job.id,
        "task_id": task.id,
        "provider": body.provider,
        "status": "pending",
    }


@router.post("/studies/{study_id}/targets/{target_id}/structure/upload", status_code=201)
async def upload_structure(
    study_id: int,
    target_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a PDB for a specific target, then validate its sequence correspondence."""
    _study(db, study_id, current_user)
    target = db.query(ResearchTarget).filter(
        ResearchTarget.id == target_id,
        ResearchTarget.study_id == study_id,
    ).first()
    if not target:
        raise HTTPException(404, "Cible introuvable")
    filename = os.path.basename(file.filename or "structure.pdb")
    if not filename.lower().endswith((".pdb", ".ent", ".txt")):
        raise HTTPException(400, "Le fichier de structure doit être PDB/ENT/TXT")
    content_type = (file.content_type or "").lower()
    if content_type not in {"", "chemical/x-pdb", "application/octet-stream", "text/plain", "text/x-pdb"}:
        raise HTTPException(400, "Type MIME de structure non accepté")

    base = os.path.join(settings.UPLOAD_DIR, "research_structures", f"study_{study_id}", f"target_{target_id}")
    os.makedirs(base, exist_ok=True)
    import uuid
    dest = os.path.join(base, f"{uuid.uuid4().hex}.pdb")
    size = 0
    max_bytes = 10 * 1024 * 1024
    try:
        with open(dest, "wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, "Structure PDB trop volumineuse (maximum 10 MB)")
                handle.write(chunk)
    finally:
        await file.close()

    try:
        from app.core.protein.structure_validation import validate_structure
        report = validate_structure(
            dest,
            target.protein_sequence,
            "user_provided",
            identity_cutoff=settings.STRUCTURE_IDENTITY_CUTOFF,
            coverage_cutoff=settings.STRUCTURE_COVERAGE_CUTOFF,
        )
    except (ValueError, OSError) as exc:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise HTTPException(400, f"Structure PDB invalide: {exc}")

    best = report["sequence_match"]["best_chain"]
    target.structure_source = "user_provided"
    target.structure_path = os.path.realpath(dest)
    target.structure_accession = None
    target.structure_status = report["status"]
    target.structure_sequence_identity = best["identity"]
    target.structure_sequence_coverage = best["coverage"]
    target.structure_confidence = None
    target.structure_validation = report
    target.provenance = {
        **(target.provenance or {}),
        "uploaded_filename": filename,
        "uploaded_size_bytes": size,
        "structure_resolution": "user_upload",
        "validation": report,
    }
    db.commit()
    return {
        "success": True,
        "target_id": target_id,
        "structure_source": target.structure_source,
        "structure_status": target.structure_status,
        "structure_validation": report,
    }


@router.get("/studies/{study_id}/targets/{target_id}/structure")
def get_structure_file(
    study_id: int,
    target_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _study(db, study_id, current_user)
    target = db.query(ResearchTarget).filter(
        ResearchTarget.id == target_id,
        ResearchTarget.study_id == study_id,
    ).first()
    if not target:
        raise HTTPException(404, "Cible introuvable")
    safe = _safe_structure_path(target.structure_path or "")
    if not safe or not os.path.isfile(safe):
        raise HTTPException(404, "Aucune structure disponible pour cette cible")
    return FileResponse(safe, media_type="chemical/x-pdb", filename=os.path.basename(safe))


@router.get("/studies/{study_id}/structure-jobs")
def list_structure_jobs(
    study_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _study(db, study_id, current_user)
    jobs = db.query(ResearchStructureJob).filter(
        ResearchStructureJob.study_id == study_id
    ).order_by(ResearchStructureJob.created_at.desc()).all()
    return {"success": True, "jobs": [
        {
            "id": j.id,
            "target_id": j.target_id,
            "provider": j.provider,
            "status": j.status,
            "progress": j.progress,
            "error": j.error,
            "metadata": j.metadata_json or {},
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        for j in jobs
    ]}

@router.post("/studies/{study_id}/docking", status_code=202)
def create_docking_run(study_id: int, body: DockingCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    study = _study(db, study_id, current_user)
    target = db.query(ResearchTarget).filter(ResearchTarget.id == body.target_id, ResearchTarget.study_id == study_id).first()
    if not target:
        raise HTTPException(404, "Cible introuvable dans cette étude")
    if body.reference_state != target.state:
        raise HTTPException(400, f"reference_state={body.reference_state} incompatible avec l'état de la cible ({target.state})")
    if body.reference_state == "variant":
        if not body.variant_id or body.variant_id != target.variant_id:
            raise HTTPException(400, "Le variant du docking doit correspondre au variant de la cible")
    else:
        if body.variant_id is not None:
            raise HTTPException(400, "Une exécution WT ne doit pas référencer un variant")
    # Phase 3: ne pas lancer un docking Research avec une structure silencieusement différente.
    structure_path = _safe_structure_path(target.structure_path or "")
    if not structure_path or not os.path.isfile(structure_path):
        raise HTTPException(409, "Structure de la cible absente. Résolvez/importez et contrôlez la structure avant le docking.")
    if target.structure_status not in {"accepted_for_docking_review", "predicted_reviewable"}:
        raise HTTPException(409, f"Structure non prête pour revue docking: {target.structure_status}")

    job = DockingJob(
        user_id=current_user.id,
        status=JobStatus.pending,
        protein_seq=target.protein_sequence,
        ligand_smiles=body.ligand_smiles.strip(),
        progress=0,
    )
    db.add(job)
    db.flush()

    run = ResearchDockingRun(
        study_id=study.id,
        target_id=target.id,
        variant_id=body.variant_id,
        docking_job_id=job.id,
        ligand_smiles=body.ligand_smiles.strip(),
        reference_state=body.reference_state,
        parameters={"max_pockets": body.max_pockets, "target_structure_source": target.structure_source, "target_structure_accession": target.structure_accession, "protocol_version": "research-vina-paired-v1"},
        status="pending",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    task = run_vina_task.apply_async(args=[job.id, target.protein_sequence, body.ligand_smiles.strip(), body.max_pockets, None, structure_path], queue="docking")
    job.celery_task_id = task.id
    db.commit()

    return {"success": True, "study_id": study.id, "research_run_id": run.id, "docking_job_id": job.id, "task_id": task.id, "status": "pending"}


@router.post("/studies/{study_id}/docking-comparisons", status_code=202)
def create_docking_comparison(
    study_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Submit a paired WT/variant docking experiment for one ligand.

    Both targets must already have accepted structures. The same ligand and
    declared pocket-count protocol are used for both runs. The endpoint does
    not infer biological effect; it only creates a paired computational study.
    """
    study = _study(db, study_id, current_user)
    try:
        wt_id = int(body.get("wt_target_id"))
        var_id = int(body.get("variant_target_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "wt_target_id et variant_target_id sont requis")
    variant_id = body.get("variant_id")
    try:
        variant_id = int(variant_id) if variant_id is not None else None
    except (TypeError, ValueError):
        raise HTTPException(400, "variant_id invalide")
    ligand = str(body.get("ligand_smiles") or "").strip()
    if not ligand:
        raise HTTPException(400, "ligand_smiles requis")
    try:
        max_pockets = int(body.get("max_pockets", 3))
    except (TypeError, ValueError):
        raise HTTPException(400, "max_pockets invalide")
    if not 1 <= max_pockets <= 10:
        raise HTTPException(400, "max_pockets doit être compris entre 1 et 10")

    wt = db.query(ResearchTarget).filter(ResearchTarget.id == wt_id, ResearchTarget.study_id == study_id, ResearchTarget.state == "wt").first()
    var = db.query(ResearchTarget).filter(ResearchTarget.id == var_id, ResearchTarget.study_id == study_id, ResearchTarget.state == "variant").first()
    if not wt or not var:
        raise HTTPException(404, "Cible WT ou cible variant introuvable dans cette étude")
    if not var.variant_id:
        raise HTTPException(400, "La cible variant n'est pas liée à un variant")
    if variant_id is not None and variant_id != var.variant_id:
        raise HTTPException(400, "variant_id incompatible avec la cible variant")
    variant_id = var.variant_id
    if wt.structure_status not in {"accepted_for_docking_review", "predicted_reviewable"} or var.structure_status not in {"accepted_for_docking_review", "predicted_reviewable"}:
        raise HTTPException(409, "Les deux structures doivent être validées avant le docking comparatif")
    wt_path = _safe_structure_path(wt.structure_path or "")
    var_path = _safe_structure_path(var.structure_path or "")
    if not wt_path or not var_path or not os.path.isfile(wt_path) or not os.path.isfile(var_path):
        raise HTTPException(409, "Les fichiers PDB WT et variant sont requis")
    if wt.protein_sequence == var.protein_sequence:
        raise HTTPException(409, "Les séquences WT et variant sont identiques; aucun contraste variant n'est défini")

    protocol = {
        "protocol_version": "research-vina-paired-v1",
        "engine": "AutoDock Vina",
        "max_pockets": max_pockets,
        "same_ligand_required": True,
        "comparison_type": "paired_wt_variant",
        "structure_paths_sha256": {
            "wt": _sha256_file(wt_path),
            "variant": _sha256_file(var_path),
        },
        "scientific_note": "Score contrasts are relative computational outputs, not experimental ΔG differences.",
    }

    wt_job = DockingJob(user_id=current_user.id, status=JobStatus.pending, protein_seq=wt.protein_sequence, ligand_smiles=ligand, progress=0)
    var_job = DockingJob(user_id=current_user.id, status=JobStatus.pending, protein_seq=var.protein_sequence, ligand_smiles=ligand, progress=0)
    db.add_all([wt_job, var_job])
    db.flush()
    wt_run = ResearchDockingRun(study_id=study.id, target_id=wt.id, variant_id=None, docking_job_id=wt_job.id, ligand_smiles=ligand, reference_state="wt", parameters={**protocol, "target_structure_source": wt.structure_source, "target_structure_accession": wt.structure_accession}, status="pending")
    var_run = ResearchDockingRun(study_id=study.id, target_id=var.id, variant_id=variant_id, docking_job_id=var_job.id, ligand_smiles=ligand, reference_state="variant", parameters={**protocol, "target_structure_source": var.structure_source, "target_structure_accession": var.structure_accession}, status="pending")
    db.add_all([wt_run, var_run])
    db.flush()
    comparison = ResearchDockingComparison(study_id=study.id, variant_id=variant_id, wt_run_id=wt_run.id, variant_run_id=var_run.id, ligand_smiles=ligand, status="pending", protocol=protocol, result_summary={})
    db.add(comparison)
    db.commit()
    db.refresh(comparison)

    wt_task = run_vina_task.apply_async(args=[wt_job.id, wt.protein_sequence, ligand, max_pockets, None, wt_path], queue="docking")
    var_task = run_vina_task.apply_async(args=[var_job.id, var.protein_sequence, ligand, max_pockets, None, var_path], queue="docking")
    wt_job.celery_task_id = wt_task.id
    var_job.celery_task_id = var_task.id
    db.commit()
    return {"success": True, "comparison_id": comparison.id, "wt_run_id": wt_run.id, "variant_run_id": var_run.id, "wt_job_id": wt_job.id, "variant_job_id": var_job.id, "status": "pending", "protocol": protocol}


def _docking_comparison_status(cmp: ResearchDockingComparison, db: Session) -> dict:
    wt_run = db.query(ResearchDockingRun).filter(ResearchDockingRun.id == cmp.wt_run_id, ResearchDockingRun.study_id == cmp.study_id).first()
    var_run = db.query(ResearchDockingRun).filter(ResearchDockingRun.id == cmp.variant_run_id, ResearchDockingRun.study_id == cmp.study_id).first()
    if not wt_run or not var_run:
        return {"status": "invalid", "error": "Runs de docking comparatif introuvables"}
    wt_job = db.query(DockingJob).filter(DockingJob.id == wt_run.docking_job_id).first()
    var_job = db.query(DockingJob).filter(DockingJob.id == var_run.docking_job_id).first()
    def st(j): return j.status.value if j is not None and hasattr(j.status, "value") else (str(j.status) if j is not None else "unknown")
    wt_status, var_status = st(wt_job), st(var_job)
    if wt_status == "failed" or var_status == "failed":
        cmp.status = "failed"
        db.commit()
        return {"status": "failed", "wt_status": wt_status, "variant_status": var_status, "error": "Au moins un run de docking a échoué"}
    if wt_status != "completed" or var_status != "completed":
        return {"status": "running", "wt_status": wt_status, "variant_status": var_status, "wt_progress": wt_job.progress if wt_job else 0, "variant_progress": var_job.progress if var_job else 0}
    try:
        report = compare_completed_results(wt_job.result_path, var_job.result_path, ligand_smiles=cmp.ligand_smiles, protocol=cmp.protocol or {})
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        cmp.status = "failed"
        cmp.result_summary = {"error": str(exc)}
        db.commit()
        return {"status": "failed", "error": str(exc)}

    # Phase 6: couple completed docking runs to the already validated WT/variant
    # structural pair. This is deliberately post-Vina analysis and does not
    # alter the docking scores. Results are persisted so repeated GET requests
    # do not recompute the expensive pose/contact analysis.
    if not isinstance(cmp.result_summary, dict) or not isinstance(cmp.result_summary.get("phase6_pose_analysis"), dict):
        try:
            wt_target = db.query(ResearchTarget).filter(ResearchTarget.id == wt_run.target_id, ResearchTarget.study_id == cmp.study_id).first()
            var_target = db.query(ResearchTarget).filter(ResearchTarget.id == var_run.target_id, ResearchTarget.study_id == cmp.study_id).first()
            wt_path = _safe_structure_path(wt_target.structure_path or "") if wt_target else ""
            var_path = _safe_structure_path(var_target.structure_path or "") if var_target else ""
            phase6 = compare_docking_pose_sets(
                wt_job.result_path,
                var_job.result_path,
                wt_path,
                var_path,
                ligand_smiles=cmp.ligand_smiles,
                top_poses=min(int((cmp.protocol or {}).get("top_poses_phase6", 10)), 20),
                contact_cutoff_A=float((cmp.protocol or {}).get("contact_cutoff_A", 4.0)),
            )
            report["phase6_pose_analysis"] = phase6
        except (ValueError, OSError, json.JSONDecodeError, TypeError) as exc:
            report["phase6_pose_analysis"] = {
                "status": "unavailable",
                "error": str(exc),
                "scientific_warning": "Le contraste de scores reste disponible; l'analyse pose/contact n'a pas pu être calculée."
            }

    cmp.status = "computed"
    cmp.result_summary = report
    db.commit()
    return {"status": "computed", "report": report}


@router.get("/studies/{study_id}/docking-comparisons")
def list_docking_comparisons(study_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _study(db, study_id, current_user)
    items = db.query(ResearchDockingComparison).filter(ResearchDockingComparison.study_id == study_id).order_by(ResearchDockingComparison.created_at.desc()).all()
    out = []
    for c in items:
        status = _docking_comparison_status(c, db)
        out.append({"id": c.id, "variant_id": c.variant_id, "wt_run_id": c.wt_run_id, "variant_run_id": c.variant_run_id, "ligand_smiles": c.ligand_smiles, "status": status.get("status", c.status), "protocol": c.protocol or {}, "result_summary": c.result_summary or status.get("report") or {}, "runtime": status})
    return {"success": True, "comparisons": out}


@router.get("/studies/{study_id}/docking-comparisons/{comparison_id}")
def get_docking_comparison(study_id: int, comparison_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _study(db, study_id, current_user)
    cmp = db.query(ResearchDockingComparison).filter(ResearchDockingComparison.id == comparison_id, ResearchDockingComparison.study_id == study_id).first()
    if not cmp:
        raise HTTPException(404, "Comparaison de docking introuvable")
    status = _docking_comparison_status(cmp, db)
    return {"success": True, "comparison": {"id": cmp.id, "variant_id": cmp.variant_id, "wt_run_id": cmp.wt_run_id, "variant_run_id": cmp.variant_run_id, "ligand_smiles": cmp.ligand_smiles, "status": status.get("status", cmp.status), "protocol": cmp.protocol or {}, "result_summary": cmp.result_summary or status.get("report") or {}, "runtime": status}}


@router.get("/studies/{study_id}/docking-runs")
def list_docking_runs(study_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _study(db, study_id, current_user)
    runs = db.query(ResearchDockingRun).filter(ResearchDockingRun.study_id == study_id).order_by(ResearchDockingRun.created_at.desc()).all()
    return {"success": True, "runs": [_run_summary(r, db) for r in runs]}
