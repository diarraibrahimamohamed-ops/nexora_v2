"""Authenticated API for the first real scientific workflow adapters."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.docking import DockingJob, JobStatus
from app.models.hsa import HSAJob
from app.models.phytochemistry import PhytochemicalCandidate
from app.models.research import ResearchStructureJob, ResearchStudy, ResearchTarget
from app.models.rna_structure_job import RnaStructureJob
from app.models.scientific_workflow import (
    ScientificWorkflowEvent,
    ScientificWorkflowRun,
    ScientificWorkflowStep,
)
from app.models.sequence import Analysis, AnalysisStatus, Sequence as BioSequence, SeqType
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.scientific_workflow_engine import (
    InvalidWorkflow,
    WorkflowConflict,
    create_workflow_run,
    transition_workflow_run,
)
from app.services.scientific_workflow_policy import (
    WorkflowDefinition,
    WorkflowStepDefinition,
)

router = APIRouter(prefix="/scientific-workflows", tags=["scientific workflows"])

_SEQUENCE_DEFINITION = WorkflowDefinition(
    key="nexora.sequence-analysis",
    version="1.0.0",
    description="Persisted DNA analysis followed by structural validation of its scientific output.",
    requires_quality_review=False,
    steps=(
        WorkflowStepDefinition(
            key="sequence_analysis",
            task_name="analysis.run",
            queue_name="analysis",
            max_attempts=2,
        ),
        WorkflowStepDefinition(
            key="validate_sequence_output",
            task_name="workflow.validate_sequence_result",
            queue_name="workflow",
            depends_on=("sequence_analysis",),
            max_attempts=1,
        ),
    ),
)

_HSA_DEFINITION = WorkflowDefinition(
    key="nexora.hsa-stability",
    version="1.0.0",
    description="Persisted HSA heuristic stability index with explicit non-clinical interpretation limits.",
    requires_quality_review=False,
    steps=(
        WorkflowStepDefinition(
            key="hsa_analysis",
            task_name="hsa.run",
            queue_name="qsa",
            max_attempts=2,
        ),
    ),
)

_QSA_COMPAT_DEFINITION = WorkflowDefinition(
    key="nexora.qsa-compatibility",
    version="1.0.0",
    description="Compatibility workflow for the legacy QSA route using the reviewed HSA engine.",
    requires_quality_review=False,
    steps=(
        WorkflowStepDefinition(
            key="qsa_analysis",
            task_name="qsa.run",
            queue_name="qsa",
            max_attempts=2,
        ),
    ),
)

_RNA_STRUCTURE_DEFINITION = WorkflowDefinition(
    key="nexora.rna-secondary-structure",
    version="1.0.0",
    description="RNA-FM secondary-structure prediction followed by persisted artifact integrity validation.",
    requires_quality_review=False,
    steps=(
        WorkflowStepDefinition(
            key="rna_secondary_structure",
            task_name="rna_structure.predict_secondary",
            queue_name="structure",
            max_attempts=1,
        ),
    ),
)

_RESEARCH_STRUCTURE_DEFINITION = WorkflowDefinition(
    key="nexora.research-target-structure",
    version="1.0.0",
    description="Resolve an experimental or predicted target structure and enforce sequence/coverage quality gates.",
    requires_quality_review=False,
    steps=(
        WorkflowStepDefinition(
            key="resolve_target_structure",
            task_name="research.resolve_structure",
            queue_name="structure",
            max_attempts=2,
        ),
    ),
)


_DOCKING_DEFINITION = WorkflowDefinition(
    key="nexora.protein-ligand-docking",
    version="1.0.0",
    description="Validate persisted protein/ligand inputs, execute AutoDock Vina, then validate the persisted result artifact.",
    requires_quality_review=False,
    steps=(
        WorkflowStepDefinition(
            key="validate_docking_input",
            task_name="workflow.validate_docking_input",
            queue_name="workflow",
            max_attempts=1,
        ),
        WorkflowStepDefinition(
            key="vina_docking",
            task_name="docking.run_vina",
            queue_name="docking",
            depends_on=("validate_docking_input",),
            max_attempts=2,
        ),
        WorkflowStepDefinition(
            key="validate_docking_output",
            task_name="workflow.validate_docking_result",
            queue_name="workflow",
            depends_on=("vina_docking",),
            max_attempts=1,
        ),
    ),
)


class SequenceWorkflowRequest(BaseModel):
    sequence: str = Field(..., min_length=1, max_length=200_000)
    name: Optional[str] = Field(None, max_length=255)


class DockingWorkflowRequest(BaseModel):
    protein_sequence: str = Field(..., min_length=3, max_length=200_000)
    ligand_smiles: str = Field(..., min_length=1, max_length=4_000)
    max_pockets: int = Field(default=3, ge=1, le=10)


class HSAWorkflowRequest(BaseModel):
    protein_sequence: str = Field(..., min_length=3, max_length=200_000)
    env_factors: dict = Field(default_factory=dict)


class RNAStructureWorkflowRequest(BaseModel):
    sequence: str = Field(..., min_length=4, max_length=10_000)


class ResearchStructureWorkflowRequest(BaseModel):
    provider: str = Field(
        default="auto_rcsb",
        pattern="^(auto_rcsb|tamarind_esmfold|esmfold|colabfold)$",
    )


class WorkflowActionRequest(BaseModel):
    action: str = Field(..., min_length=3, max_length=60)
    reason: Optional[str] = Field(None, max_length=4000)
    expected_state_version: Optional[int] = Field(None, ge=0)


def _namespaced_idempotency_key(namespace: str, idempotency_key: str | None) -> str | None:
    if idempotency_key is None:
        return None
    key = idempotency_key.strip()
    if not key or len(key) > 128:
        raise HTTPException(422, "Idempotency-Key doit contenir 1 à 128 caractères.")
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"


def _existing_idempotent_run(
    db: Session, user_id: int, namespace: str, idempotency_key: str | None
) -> ScientificWorkflowRun | None:
    internal_key = _namespaced_idempotency_key(namespace, idempotency_key)
    if internal_key is None:
        return None
    return db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.owner_user_id == user_id,
        ScientificWorkflowRun.idempotency_key == internal_key,
    ).first()


def _run_response(run: ScientificWorkflowRun, *, replay: bool = False) -> dict:
    return {
        "success": True,
        "workflow_run_id": run.id,
        "workflow_key": run.workflow_key,
        "definition_version": run.definition_version,
        "state": run.state,
        "state_version": run.state_version,
        "scope_type": run.scope_type,
        "scope_id": run.scope_id,
        "idempotent_replay": replay,
    }


def _owned_run(db: Session, run_id: int, user: User, *, lock: bool = False) -> ScientificWorkflowRun:
    query = db.query(ScientificWorkflowRun).filter(
        ScientificWorkflowRun.id == run_id,
        ScientificWorkflowRun.owner_user_id == user.id,
    )
    if lock:
        query = query.with_for_update()
    run = query.first()
    if run is None:
        raise HTTPException(404, "Workflow scientifique introuvable.")
    return run


@router.post("/sequence-analysis", status_code=202)
def start_sequence_analysis_workflow(
    body: SequenceWorkflowRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    existing = _existing_idempotent_run(db, user.id, "sequence", idempotency_key)
    if existing:
        return {
            **_run_response(existing, replay=True),
            "analysis_id": int(existing.scope_id) if existing.scope_id else None,
        }

    sequence = re.sub(r"[^ACGT]", "", body.sequence.upper())
    if not sequence:
        raise HTTPException(422, "Séquence vide ou invalide (seuls A/C/G/T acceptés).")

    analysis = Analysis(
        user_id=user.id,
        name=(body.name or f"workflow_sequence_{datetime.utcnow():%Y%m%d_%H%M%S}"),
        type="DNA",
        status=AnalysisStatus.pending,
        data={
            "input_length": len(sequence),
            "mode": "scientific_workflow",
            "workflow_engine": "N3XORA_SCIENTIFIC_WORKFLOW_CORE_V1",
        },
    )
    db.add(analysis)
    db.flush()
    db.add(BioSequence(
        analysis_id=analysis.id,
        accession=None,
        header=body.name or f"Nexora workflow sequence {analysis.id}",
        sequence=sequence,
        length=len(sequence),
        type=SeqType.dna,
    ))
    db.flush()

    try:
        run, replay = create_workflow_run(
            db,
            _SEQUENCE_DEFINITION,
            owner_user_id=user.id,
            scope_type="analysis",
            scope_id=analysis.id,
            context={"analysis_id": analysis.id, "sequence_type": "DNA"},
            input_manifest={
                "sequence_length": len(sequence),
                "sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
            },
            step_inputs={
                "sequence_analysis": {"args": [analysis.id]},
                "validate_sequence_output": {"args": [analysis.id]},
            },
            idempotency_key=_namespaced_idempotency_key("sequence", idempotency_key),
        )
        if replay:
            # A concurrent request won the unique idempotency key. Remove this
            # request's unused business record rather than leaving an orphan.
            db.delete(analysis)
            db.flush()
        else:
            analysis.data = {
                **(analysis.data or {}),
                "scientific_workflow_run_id": run.id,
            }
        db.commit()
        db.refresh(run)
        return {
            **_run_response(run, replay=replay),
            "analysis_id": int(run.scope_id),
            "message": "Analyse enregistrée. Le dispatcher scientifique va prendre en charge les étapes du workflow.",
        }
    except (InvalidWorkflow, ValueError) as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.post("/docking", status_code=202)
def start_docking_workflow(
    body: DockingWorkflowRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    existing = _existing_idempotent_run(db, user.id, "docking", idempotency_key)
    if existing:
        return {
            **_run_response(existing, replay=True),
            "docking_job_id": int(existing.scope_id) if existing.scope_id else None,
        }

    protein = re.sub(r"\s+", "", body.protein_sequence.upper())
    if len(protein) < 3 or any(letter not in "ACDEFGHIKLMNPQRSTVWY" for letter in protein):
        raise HTTPException(422, "La séquence protéique contient des symboles non pris en charge.")

    try:
        from rdkit import Chem
        molecule = Chem.MolFromSmiles(body.ligand_smiles.strip())
        if molecule is None:
            raise ValueError("SMILES non valide.")
        canonical_smiles = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
    except Exception as exc:
        raise HTTPException(422, f"Structure du ligand invalide : {exc}") from exc

    matching_candidates = db.query(PhytochemicalCandidate).filter(
        PhytochemicalCandidate.smiles_canonical == canonical_smiles
    ).all()
    if matching_candidates:
        raise HTTPException(
            422,
            detail={
                "message": (
                    "Ce ligand correspond à un candidat du catalogue phytochimique. "
                    "Utilise le workflow Research Screening après curation pour conserver la provenance."
                ),
                "candidate_ids": [candidate.id for candidate in matching_candidates],
            },
        )

    job = DockingJob(
        user_id=user.id,
        status=JobStatus.pending,
        protein_seq=protein,
        ligand_smiles=canonical_smiles,
        progress=0,
        status_message="En attente du workflow scientifique.",
    )
    db.add(job)
    db.flush()

    try:
        run, replay = create_workflow_run(
            db,
            _DOCKING_DEFINITION,
            owner_user_id=user.id,
            scope_type="docking_job",
            scope_id=job.id,
            context={"docking_job_id": job.id, "engine": "AutoDock Vina"},
            input_manifest={
                "protein_length": len(protein),
                "protein_sha256": hashlib.sha256(protein.encode("ascii")).hexdigest(),
                "ligand_sha256": hashlib.sha256(canonical_smiles.encode("utf-8")).hexdigest(),
                "max_pockets": body.max_pockets,
            },
            step_inputs={
                "validate_docking_input": {"args": [job.id], "kwargs": {"max_pockets": body.max_pockets}},
                "vina_docking": {"args": [job.id, body.max_pockets]},
                "validate_docking_output": {"args": [job.id]},
            },
            idempotency_key=_namespaced_idempotency_key("docking", idempotency_key),
        )
        if replay:
            db.delete(job)
            db.flush()
        db.commit()
        db.refresh(run)
        return {
            **_run_response(run, replay=replay),
            "docking_job_id": int(run.scope_id),
            "message": "Docking enregistré. La validation des entrées précédera le lancement de Vina.",
        }
    except (InvalidWorkflow, ValueError) as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        db.rollback()
        raise


def _start_hsa_compatibility_workflow(
    body: HSAWorkflowRequest,
    db: Session,
    user: User,
    idempotency_key: Optional[str],
    *,
    qsa_compatibility: bool,
):
    namespace = "qsa" if qsa_compatibility else "hsa"
    definition = _QSA_COMPAT_DEFINITION if qsa_compatibility else _HSA_DEFINITION
    step_key = "qsa_analysis" if qsa_compatibility else "hsa_analysis"

    existing = _existing_idempotent_run(db, user.id, namespace, idempotency_key)
    if existing:
        return {
            **_run_response(existing, replay=True),
            "hsa_job_id": int(existing.scope_id) if existing.scope_id else None,
            "method": "QSA compatibility alias" if qsa_compatibility else "HSA",
        }

    sequence = re.sub(r"\s+", "", body.protein_sequence.upper())
    if not sequence or any(base not in "ACGTU" for base in sequence):
        raise HTTPException(422, "HSA/QSA attend une séquence nucléotidique ADN/ARN composée de A/C/G/U/T.")
    env_factors = dict(body.env_factors or {})
    try:
        # Reject non-JSON values before they enter the workflow snapshot/manifest.
        env_encoded = json.dumps(env_factors, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "env_factors doit être un objet JSON composé de valeurs finies.") from exc

    job = HSAJob(
        user_id=user.id,
        protein_sequence=sequence,
        env_factors=env_factors,
        status="pending",
        progress=0,
    )
    db.add(job)
    db.flush()
    try:
        run, replay = create_workflow_run(
            db,
            definition,
            owner_user_id=user.id,
            scope_type="hsa_job",
            scope_id=job.id,
            context={
                "hsa_job_id": job.id,
                "method": "QSA compatibility alias" if qsa_compatibility else "HSA",
                "formula_version_expected": "HSA-2.0-transparent-index",
                "interpretation_limit": "Heuristic exploratory index; not a mutation probability or clinical predictor.",
            },
            input_manifest={
                "sequence_length": len(sequence),
                "sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
                "env_factors_sha256": hashlib.sha256(env_encoded.encode("utf-8")).hexdigest(),
            },
            step_inputs={step_key: {"args": [job.id]}},
            idempotency_key=_namespaced_idempotency_key(namespace, idempotency_key),
        )
        if replay:
            db.delete(job)
            db.flush()
        db.commit()
        db.refresh(run)
        return {
            **_run_response(run, replay=replay),
            "hsa_job_id": int(run.scope_id),
            "method": "QSA compatibility alias" if qsa_compatibility else "HSA",
            "dispatch": "scientific_workflow_dispatcher",
        }
    except (InvalidWorkflow, ValueError) as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.post("/hsa", status_code=202)
def start_hsa_workflow(
    body: HSAWorkflowRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    return _start_hsa_compatibility_workflow(
        body, db, user, idempotency_key, qsa_compatibility=False
    )


@router.post("/qsa", status_code=202)
def start_qsa_compatibility_workflow(
    body: HSAWorkflowRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    return _start_hsa_compatibility_workflow(
        body, db, user, idempotency_key, qsa_compatibility=True
    )


@router.post("/rna-secondary-structure", status_code=202)
def start_rna_secondary_structure_workflow(
    body: RNAStructureWorkflowRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    existing = _existing_idempotent_run(db, user.id, "rna-secondary", idempotency_key)
    if existing:
        return {
            **_run_response(existing, replay=True),
            "rna_structure_job_id": int(existing.scope_id) if existing.scope_id else None,
        }

    sequence = re.sub(r"\s+", "", body.sequence.upper())
    if len(sequence) < 4 or len(sequence) > 10_000 or set(sequence) - set("ACGUT"):
        raise HTTPException(422, "Séquence ARN invalide : 4 à 10 000 bases A/C/G/U/T requises.")
    api_key = (settings.TAMARIND_API_KEY or "").strip().lower()
    if not api_key or api_key.startswith("your_") or api_key in {
        "changeme", "change-me", "change_me_in_production",
    }:
        raise HTTPException(503, "TAMARIND_API_KEY n'est pas configurée.")

    job = RnaStructureJob(
        user_id=user.id,
        sequence=sequence,
        status="pending",
        progress=0,
        metadata_json={
            "input_sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
            "submission_source": "scientific_workflow",
        },
    )
    db.add(job)
    db.flush()
    try:
        run, replay = create_workflow_run(
            db,
            _RNA_STRUCTURE_DEFINITION,
            owner_user_id=user.id,
            scope_type="rna_structure_job",
            scope_id=job.id,
            context={"rna_structure_job_id": job.id, "engine": "Tamarind RNA-FM"},
            input_manifest={
                "sequence_length": len(sequence),
                "sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
            },
            step_inputs={"rna_secondary_structure": {"args": [job.id]}},
            idempotency_key=_namespaced_idempotency_key("rna-secondary", idempotency_key),
        )
        if replay:
            db.delete(job)
            db.flush()
        else:
            job.metadata_json = {
                **(job.metadata_json or {}),
                "scientific_workflow_run_id": run.id,
            }
        db.commit()
        db.refresh(run)
        return {
            **_run_response(run, replay=replay),
            "rna_structure_job_id": int(run.scope_id),
            "dispatch": "scientific_workflow_dispatcher",
        }
    except (InvalidWorkflow, ValueError) as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.post("/research-structure/{study_id}/{target_id}", status_code=202)
def start_research_structure_workflow(
    study_id: int,
    target_id: int,
    body: ResearchStructureWorkflowRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    namespace = f"research-structure:{study_id}:{target_id}"
    existing = _existing_idempotent_run(db, user.id, namespace, idempotency_key)
    if existing:
        return {
            **_run_response(existing, replay=True),
            "structure_job_id": int(existing.scope_id) if existing.scope_id else None,
        }

    study = db.query(ResearchStudy).filter(
        ResearchStudy.id == study_id,
        ResearchStudy.user_id == user.id,
    ).first()
    if study is None:
        raise HTTPException(404, "Étude de recherche introuvable.")
    if (study.status or "draft").strip().lower() != "analysis":
        raise HTTPException(409, "La résolution structurale exige une étude approuvée pour analyse.")
    target = db.query(ResearchTarget).filter(
        ResearchTarget.id == target_id,
        ResearchTarget.study_id == study_id,
    ).first()
    if target is None:
        raise HTTPException(404, "Cible de recherche introuvable.")

    job = ResearchStructureJob(
        study_id=study_id,
        target_id=target_id,
        provider=body.provider,
        status="pending",
        progress=0,
        metadata_json={
            "sequence_length": len(target.protein_sequence or ""),
            "requested_provider": body.provider,
            "input_sequence_sha256": hashlib.sha256(
                (target.protein_sequence or "").encode("ascii")
            ).hexdigest(),
        },
    )
    db.add(job)
    db.flush()
    target.structure_status = "pending"
    target.structure_validation = {}
    try:
        run, replay = create_workflow_run(
            db,
            _RESEARCH_STRUCTURE_DEFINITION,
            owner_user_id=user.id,
            scope_type="research_structure_job",
            scope_id=job.id,
            context={
                "study_id": study_id,
                "target_id": target_id,
                "structure_job_id": job.id,
                "provider": body.provider,
            },
            input_manifest={
                "protein_length": len(target.protein_sequence or ""),
                "protein_sha256": hashlib.sha256(
                    (target.protein_sequence or "").encode("ascii")
                ).hexdigest(),
            },
            step_inputs={"resolve_target_structure": {"args": [job.id]}},
            idempotency_key=_namespaced_idempotency_key(namespace, idempotency_key),
        )
        if replay:
            db.delete(job)
            db.flush()
        else:
            job.metadata_json = {
                **(job.metadata_json or {}),
                "scientific_workflow_run_id": run.id,
            }
        db.commit()
        db.refresh(run)
        return {
            **_run_response(run, replay=replay),
            "structure_job_id": int(run.scope_id),
            "study_id": study_id,
            "target_id": target_id,
            "provider": body.provider,
            "dispatch": "scientific_workflow_dispatcher",
        }
    except (InvalidWorkflow, ValueError) as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.get("/{run_id}")
def get_scientific_workflow(
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = _owned_run(db, run_id, user)
    steps = db.query(ScientificWorkflowStep).filter(
        ScientificWorkflowStep.workflow_run_id == run.id
    ).order_by(ScientificWorkflowStep.id.asc()).all()
    events = db.query(ScientificWorkflowEvent).filter(
        ScientificWorkflowEvent.workflow_run_id == run.id
    ).order_by(ScientificWorkflowEvent.id.asc()).limit(500).all()

    return {
        **_run_response(run),
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "failure": {
            "code": run.failure_code,
            "message": run.failure_message,
        } if run.failure_code or run.failure_message else None,
        "steps": [
            {
                "id": step.id,
                "key": step.step_key,
                "task_name": step.task_name,
                "queue": step.queue_name,
                "state": step.state,
                "attempts": step.attempts,
                "max_attempts": step.max_attempts,
                "error_code": step.error_code,
                "error_message": step.error_message,
                "output": step.output,
                "started_at": step.started_at.isoformat() if step.started_at else None,
                "completed_at": step.completed_at.isoformat() if step.completed_at else None,
            }
            for step in steps
        ],
        "events": [
            {
                "id": event.id,
                "step_id": event.step_id,
                "type": event.event_type,
                "from_state": event.from_state,
                "to_state": event.to_state,
                "action": event.action,
                "reason": event.reason,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in events
        ],
    }


@router.post("/{run_id}/actions")
def act_on_scientific_workflow(
    run_id: int,
    body: WorkflowActionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    run = _owned_run(db, run_id, user, lock=True)
    action = body.action.strip().lower()
    if action not in {"cancel", "block", "resume", "approve", "reject", "needs_user_action", "resume_validation"}:
        raise HTTPException(422, "Action de workflow non prise en charge.")
    try:
        run, replay = transition_workflow_run(
            db,
            run.id,
            action=action,
            actor_user_id=user.id,
            reason=body.reason,
            expected_state_version=body.expected_state_version,
            idempotency_key=idempotency_key,
        )
        if action == "approve" and not replay:
            run, _ = transition_workflow_run(
                db, run.id,
                action="finalize_review",
                actor_user_id=user.id,
                reason="Quality review approved.",
                expected_state_version=run.state_version,
            )
        db.commit()
        db.refresh(run)
        return _run_response(run, replay=replay)
    except InvalidWorkflow as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except WorkflowConflict as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
