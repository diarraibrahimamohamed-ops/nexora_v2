"""Reviewed adapters for existing scientific engines.

Adapters are the only place where the generic workflow core calls domain
workers. A workflow definition selects only allow-listed task contracts.
Large scientific inputs are loaded from persisted domain records, not copied
into Celery arguments.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable

from app.config import settings
from app.database import SessionLocal
from app.models.docking import DockingJob, JobStatus
from app.models.hsa import HSAJob
from app.models.research import ResearchStructureJob, ResearchTarget
from app.models.rna_structure_job import RnaStructureJob
from app.models.sequence import Analysis, AnalysisStatus


class WorkflowAdapterError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = (code or "WORKFLOW_ADAPTER_ERROR")[:120]
        self.retryable = retryable


def _json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _status(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _sha256_path(path: Path) -> tuple[str, int]:
    """Hash an artifact incrementally so large files do not need to fit in memory."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _run_task_eager(task, *, args: list, kwargs: dict | None = None) -> dict:
    """Run an existing Celery task in this worker process without joining a queue."""
    result = task.apply(args=args, kwargs=kwargs or {}, throw=False)
    payload = result.get(propagate=True)
    if not isinstance(payload, dict):
        raise WorkflowAdapterError(
            "INVALID_ENGINE_RESPONSE",
            f"Scientific task {task.name} did not return a JSON object.",
        )
    completed = payload.get("status") == "completed" or payload.get("success") is True
    if not completed:
        raise WorkflowAdapterError(
            "SCIENTIFIC_ENGINE_FAILED",
            str(payload.get("error") or payload.get("error_message") or
                f"Scientific task {task.name} returned status {payload.get('status')!r}."),
            retryable=False,
        )
    return payload


def run_sequence_analysis(args: list, kwargs: dict) -> dict:
    if len(args) != 1:
        raise WorkflowAdapterError("INVALID_STEP_INPUT", "analysis.run expects one persisted analysis ID.")
    analysis_id = int(args[0])
    db = SessionLocal()
    try:
        analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
        if analysis is None:
            raise WorkflowAdapterError("ANALYSIS_NOT_FOUND", "Sequence analysis record was not found.")
        if _status(analysis.status) == AnalysisStatus.completed.value:
            result = (analysis.data or {}).get("async_sequence_result")
            if isinstance(result, dict):
                return {"analysis_id": analysis_id, "status": "completed", "reused": True}
        if _status(analysis.status) == AnalysisStatus.failed.value:
            raise WorkflowAdapterError(
                "ANALYSIS_PREVIOUSLY_FAILED",
                (analysis.data or {}).get("error") or "The sequence analysis already failed.",
                retryable=True,
            )
    finally:
        db.close()

    from app.workers.analysis_tasks import run_analysis_task
    return _run_task_eager(run_analysis_task, args=[analysis_id, None], kwargs=kwargs)


def validate_sequence_result(args: list, kwargs: dict) -> dict:
    if len(args) != 1:
        raise WorkflowAdapterError(
            "INVALID_STEP_INPUT", "workflow.validate_sequence_result expects one analysis ID."
        )
    analysis_id = int(args[0])
    db = SessionLocal()
    try:
        analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
        if analysis is None:
            raise WorkflowAdapterError("ANALYSIS_NOT_FOUND", "Sequence analysis record was not found.")
        if _status(analysis.status) != AnalysisStatus.completed.value:
            raise WorkflowAdapterError(
                "SEQUENCE_OUTPUT_NOT_COMPLETED",
                "The sequence analysis record is not marked completed.",
            )
        result = (analysis.data or {}).get("async_sequence_result")
        if not isinstance(result, dict) or not isinstance(result.get("fragments"), list):
            raise WorkflowAdapterError(
                "SEQUENCE_OUTPUT_INVALID", "Persisted sequence result has an invalid structure."
            )
        if int(result.get("input_length") or 0) <= 0 or not result["fragments"]:
            raise WorkflowAdapterError(
                "SEQUENCE_OUTPUT_EMPTY", "Persisted sequence output is empty."
            )
        return {
            "analysis_id": analysis_id,
            "status": "validated",
            "input_length": int(result["input_length"]),
            "chunks": int(result.get("chunks") or len(result["fragments"])),
            "output_sha256": _json_sha256(result),
        }
    finally:
        db.close()


def validate_docking_input(args: list, kwargs: dict) -> dict:
    if len(args) != 1:
        raise WorkflowAdapterError(
            "INVALID_STEP_INPUT", "workflow.validate_docking_input expects one docking job ID."
        )
    job_id = int(args[0])
    db = SessionLocal()
    try:
        job = db.query(DockingJob).filter(DockingJob.id == job_id).first()
        if job is None:
            raise WorkflowAdapterError("DOCKING_JOB_NOT_FOUND", "Docking job was not found.")
        protein = (job.protein_seq or "").strip().upper()
        smiles = (job.ligand_smiles or "").strip()
        if len(protein) < 3 or any(letter not in "ACDEFGHIKLMNPQRSTVWY" for letter in protein):
            raise WorkflowAdapterError(
                "INVALID_PROTEIN_SEQUENCE", "The stored target sequence is empty or contains invalid amino-acid symbols."
            )
        try:
            from rdkit import Chem
            molecule = Chem.MolFromSmiles(smiles)
            if molecule is None:
                raise ValueError("SMILES parser returned no molecule")
            canonical = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
        except Exception as exc:
            raise WorkflowAdapterError("INVALID_LIGAND_SMILES", f"Invalid ligand SMILES: {exc}") from exc
        return {
            "docking_job_id": job_id,
            "protein_length": len(protein),
            "protein_sha256": hashlib.sha256(protein.encode("ascii")).hexdigest(),
            "ligand_smiles_canonical": canonical,
            "ligand_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "max_pockets": int(kwargs.get("max_pockets", 3)),
        }
    finally:
        db.close()


def run_vina_docking(args: list, kwargs: dict) -> dict:
    if len(args) != 2:
        raise WorkflowAdapterError(
            "INVALID_STEP_INPUT",
            "docking.run_vina workflow adapter expects docking_job_id and max_pockets.",
        )
    job_id, max_pockets = int(args[0]), int(args[1])
    db = SessionLocal()
    try:
        job = db.query(DockingJob).filter(DockingJob.id == job_id).first()
        if job is None:
            raise WorkflowAdapterError("DOCKING_JOB_NOT_FOUND", "Docking job was not found.")
        if job.status == JobStatus.completed and job.result_path:
            result_path = Path(job.result_path).resolve()
            results_root = Path(settings.RESULTS_DIR).resolve()
            if result_path.is_relative_to(results_root) and result_path.is_file():
                with result_path.open("r", encoding="utf-8") as handle:
                    existing = json.load(handle)
                if isinstance(existing, dict) and existing.get("success"):
                    return {
                        "job_id": job_id,
                        "status": "completed",
                        "reused": True,
                    }
    finally:
        db.close()

    from app.workers.docking_tasks import run_vina_task
    return _run_task_eager(
        run_vina_task,
        args=[job_id, None, None, max_pockets, None],
        kwargs=kwargs,
    )


def validate_docking_result(args: list, kwargs: dict) -> dict:
    if len(args) != 1:
        raise WorkflowAdapterError(
            "INVALID_STEP_INPUT", "workflow.validate_docking_result expects one docking job ID."
        )
    job_id = int(args[0])
    db = SessionLocal()
    try:
        job = db.query(DockingJob).filter(DockingJob.id == job_id).first()
        if job is None:
            raise WorkflowAdapterError("DOCKING_JOB_NOT_FOUND", "Docking job was not found.")
        if job.status != JobStatus.completed or not job.result_path:
            raise WorkflowAdapterError(
                "DOCKING_OUTPUT_NOT_COMPLETED",
                job.error or "The docking worker did not persist a completed result.",
            )
        result_path = Path(job.result_path).resolve()
        results_root = Path(settings.RESULTS_DIR).resolve()
        if not result_path.is_relative_to(results_root):
            raise WorkflowAdapterError(
                "DOCKING_ARTIFACT_OUTSIDE_STORAGE",
                "Docking result path is outside the configured results directory.",
            )
        if not result_path.is_file():
            raise WorkflowAdapterError(
                "DOCKING_ARTIFACT_MISSING", "Docking result artifact is missing."
            )
        raw = result_path.read_bytes()
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict) or not result.get("success"):
            raise WorkflowAdapterError(
                "DOCKING_ARTIFACT_INVALID",
                "Docking result artifact does not contain a successful result.",
            )
        score = result.get("vina_best_score", result.get("docking_score"))
        pose_count = int(result.get("num_poses") or len(result.get("poses") or []))
        try:
            finite_score = math.isfinite(float(score))
        except (TypeError, ValueError):
            finite_score = False
        if not finite_score or pose_count < 1:
            raise WorkflowAdapterError(
                "DOCKING_RESULT_QUALITY_FAILED",
                "Docking artefact must contain at least one pose and a finite Vina score.",
            )
        return {
            "docking_job_id": job_id,
            "status": "validated",
            "result_sha256": hashlib.sha256(raw).hexdigest(),
            "vina_best_score": float(score),
            "num_poses": pose_count,
        }
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowAdapterError("DOCKING_ARTIFACT_READ_ERROR", str(exc)) from exc
    finally:
        db.close()


def run_qsa_analysis(args: list, kwargs: dict) -> dict:
    """Compatibility adapter for the historical QSA task alias."""
    if len(args) != 1:
        raise WorkflowAdapterError("INVALID_STEP_INPUT", "qsa.run expects one persisted HSA/QSA job ID.")
    job_id = int(args[0])
    db = SessionLocal()
    try:
        job = db.query(HSAJob).filter(HSAJob.id == job_id).first()
        if job is None:
            raise WorkflowAdapterError("HSA_JOB_NOT_FOUND", "QSA compatibility job was not found.")
        if job.status in {"failed", "error"}:
            raise WorkflowAdapterError(
                "HSA_PREVIOUSLY_FAILED", job.error or "The QSA compatibility job already failed.",
                retryable=True,
            )
        sequence = job.protein_sequence
        env_factors = dict(job.env_factors or {})
    finally:
        db.close()

    from app.workers.hsa_tasks import run_qsa_task
    _run_task_eager(run_qsa_task, args=[job_id, sequence, env_factors], kwargs=kwargs)

    db = SessionLocal()
    try:
        job = db.query(HSAJob).filter(HSAJob.id == job_id).first()
        if job is None:
            raise WorkflowAdapterError("HSA_JOB_NOT_FOUND", "QSA job disappeared after execution.")
        result = job.results if isinstance(job.results, dict) else {}
        if job.status not in {"completed", "done"} or result.get("success") is not True:
            raise WorkflowAdapterError(
                "HSA_RESULT_NOT_VALID", job.error or result.get("error") or
                "The QSA compatibility task did not persist a successful result.",
            )
        return {
            "hsa_job_id": job_id, "status": "completed", "method": result.get("method", "HSA"),
            "formula_version": result.get("formula_version"),
            "sequence_length": int(result.get("sequence_length") or len(sequence)),
            "global_score": result.get("global_score"),
            "vulnerability_index": result.get("vulnerability_index"),
            "result_sha256": _json_sha256(result),
            "interpretation_limit": result.get("disclaimer"),
            "legacy_task_alias": "qsa.run",
        }
    finally:
        db.close()


def run_experimental_4pl(args: list, kwargs: dict) -> dict:
    """Execute the shared 4PL service for an already-approved assay dossier."""
    if len(args) != 3:
        raise WorkflowAdapterError(
            "INVALID_STEP_INPUT",
            "experimental.run_4pl expects study_id, assay_id and owner_user_id.",
        )
    try:
        study_id, assay_id, user_id = (int(value) for value in args)
    except (TypeError, ValueError) as exc:
        raise WorkflowAdapterError("INVALID_STEP_INPUT", "4PL identifiers must be integers.") from exc
    parameters = kwargs.get("parameters")
    if not isinstance(parameters, dict):
        raise WorkflowAdapterError("INVALID_STEP_INPUT", "4PL parameters must be a JSON object.")

    from app.services.assay_analytics import (
        AssayAnalysisNotReady,
        execute_dose_response_analysis,
    )
    from app.services.assay_statistics import AssayStatisticsError

    db = SessionLocal()
    try:
        try:
            response = execute_dose_response_analysis(
                db,
                study_id=study_id,
                assay_id=assay_id,
                user_id=user_id,
                parameters=parameters,
            )
            db.commit()
        except (AssayAnalysisNotReady, AssayStatisticsError) as exc:
            db.rollback()
            raise WorkflowAdapterError(
                "EXPERIMENTAL_4PL_REJECTED", str(exc), retryable=False
            ) from exc
        except Exception:
            db.rollback()
            raise

        analysis = response.get("analysis") or {}
        result = analysis.get("result") or {}
        fit = result.get("fit") or {}
        quality = result.get("quality") or {}
        confidence = result.get("confidence_interval") or {}
        if response.get("success") is not True or analysis.get("status") != "completed":
            raise WorkflowAdapterError(
                "EXPERIMENTAL_4PL_NOT_COMPLETED",
                "4PL service returned without a completed persisted analysis.",
            )
        input_sha256 = analysis.get("input_sha256")
        output_sha256 = analysis.get("output_sha256")
        if not input_sha256 or not output_sha256:
            raise WorkflowAdapterError(
                "EXPERIMENTAL_4PL_PROVENANCE_MISSING",
                "Persisted 4PL analysis is missing input/output SHA-256 provenance.",
            )
        return {
            "study_id": study_id,
            "assay_id": assay_id,
            "analysis_id": analysis.get("id"),
            "status": "completed",
            "idempotent_replay": bool(response.get("idempotent_replay")),
            "algorithm": "four_parameter_logistic",
            "algorithm_version": analysis.get("algorithm_version"),
            "analysis_key": analysis.get("analysis_key"),
            "input_sha256": input_sha256,
            "output_sha256": output_sha256,
            "quality_status": quality.get("status"),
            "endpoint_name": parameters.get("endpoint_name"),
            "midpoint_concentration": fit.get("midpoint_concentration"),
            "concentration_unit": parameters.get("concentration_unit"),
            "confidence_interval": confidence,
            "interpretation_limit": (
                "Exploratory computation on curator-accepted declared observations; "
                "not independent replication or clinical validation."
            ),
        }
    finally:
        db.close()


def run_hsa_analysis(args: list, kwargs: dict) -> dict:
    """Run the existing HSA task from its persisted job and verify its stored result."""
    if len(args) != 1:
        raise WorkflowAdapterError("INVALID_STEP_INPUT", "hsa.run expects one persisted HSA job ID.")
    job_id = int(args[0])
    db = SessionLocal()
    try:
        job = db.query(HSAJob).filter(HSAJob.id == job_id).first()
        if job is None:
            raise WorkflowAdapterError("HSA_JOB_NOT_FOUND", "HSA job was not found.")
        if job.status in {"failed", "error"}:
            raise WorkflowAdapterError(
                "HSA_PREVIOUSLY_FAILED", job.error or "The HSA job already failed.", retryable=True
            )
        sequence = job.protein_sequence
        env_factors = dict(job.env_factors or {})
    finally:
        db.close()

    from app.workers.hsa_tasks import run_hsa_task
    _run_task_eager(run_hsa_task, args=[job_id, sequence, env_factors], kwargs=kwargs)

    db = SessionLocal()
    try:
        job = db.query(HSAJob).filter(HSAJob.id == job_id).first()
        if job is None:
            raise WorkflowAdapterError("HSA_JOB_NOT_FOUND", "HSA job disappeared after execution.")
        result = job.results if isinstance(job.results, dict) else {}
        if job.status not in {"completed", "done"} or result.get("success") is not True:
            raise WorkflowAdapterError(
                "HSA_RESULT_NOT_VALID",
                job.error or result.get("error") or "HSA did not persist a successful result.",
            )
        return {
            "hsa_job_id": job_id,
            "status": "completed",
            "method": result.get("method", "HSA"),
            "formula_version": result.get("formula_version"),
            "sequence_length": int(result.get("sequence_length") or len(sequence)),
            "global_score": result.get("global_score"),
            "vulnerability_index": result.get("vulnerability_index"),
            "result_sha256": _json_sha256(result),
            "interpretation_limit": result.get("disclaimer"),
        }
    finally:
        db.close()


def run_rna_secondary_structure(args: list, kwargs: dict) -> dict:
    """Run RNA-FM through the existing worker and validate its contained artifact."""
    if len(args) != 1:
        raise WorkflowAdapterError(
            "INVALID_STEP_INPUT", "rna_structure.predict_secondary expects one persisted job ID."
        )
    job_id = int(args[0])
    db = SessionLocal()
    try:
        job = db.query(RnaStructureJob).filter(RnaStructureJob.id == job_id).first()
        if job is None:
            raise WorkflowAdapterError("RNA_JOB_NOT_FOUND", "RNA secondary-structure job was not found.")
        if job.status == "failed":
            raise WorkflowAdapterError("RNA_JOB_PREVIOUSLY_FAILED", job.error or "RNA-FM job failed.")
    finally:
        db.close()

    from app.workers.rna_structure_tasks import predict_rna_secondary_structure_task
    _run_task_eager(predict_rna_secondary_structure_task, args=[job_id], kwargs=kwargs)

    db = SessionLocal()
    try:
        job = db.query(RnaStructureJob).filter(RnaStructureJob.id == job_id).first()
        if job is None or job.status != "completed" or not job.artifact_path:
            raise WorkflowAdapterError(
                "RNA_OUTPUT_NOT_COMPLETED",
                (job.error if job else None) or "RNA-FM did not persist a completed artifact.",
            )
        root = (Path(settings.TEMP_DIR) / "rna_secondary" / ("job_" + str(job_id))).resolve()
        artifact = Path(job.artifact_path).resolve()
        if not artifact.is_relative_to(root) or not artifact.is_file():
            raise WorkflowAdapterError(
                "RNA_ARTIFACT_INVALID_PATH",
                "RNA-FM artifact is missing or outside its job-specific storage directory.",
            )
        artifact_sha256, artifact_size = _sha256_path(artifact)
        if artifact_size <= 0:
            raise WorkflowAdapterError("RNA_ARTIFACT_EMPTY", "RNA-FM artifact is empty.")
        return {
            "rna_structure_job_id": job_id,
            "status": "completed",
            "artifact_name": artifact.name,
            "artifact_size_bytes": artifact_size,
            "artifact_sha256": artifact_sha256,
            "metadata": job.metadata_json if isinstance(job.metadata_json, dict) else {},
        }
    except OSError as exc:
        raise WorkflowAdapterError("RNA_ARTIFACT_READ_ERROR", str(exc)) from exc
    finally:
        db.close()


def run_research_structure_resolution(args: list, kwargs: dict) -> dict:
    """Resolve a research target structure and enforce the existing sequence/QC gate."""
    if len(args) != 1:
        raise WorkflowAdapterError(
            "INVALID_STEP_INPUT", "research.resolve_structure expects one persisted structure-job ID."
        )
    job_id = int(args[0])
    db = SessionLocal()
    try:
        job = db.query(ResearchStructureJob).filter(ResearchStructureJob.id == job_id).first()
        if job is None:
            raise WorkflowAdapterError(
                "RESEARCH_STRUCTURE_JOB_NOT_FOUND", "Research structure-resolution job was not found."
            )
        if job.status == "failed":
            raise WorkflowAdapterError(
                "RESEARCH_STRUCTURE_PREVIOUSLY_FAILED", job.error or "Structure resolution previously failed."
            )
    finally:
        db.close()

    from app.workers.research_structure_tasks import resolve_research_structure_task
    _run_task_eager(resolve_research_structure_task, args=[job_id], kwargs=kwargs)

    db = SessionLocal()
    try:
        job = db.query(ResearchStructureJob).filter(ResearchStructureJob.id == job_id).first()
        if job is None or job.status != "completed":
            raise WorkflowAdapterError(
                "RESEARCH_STRUCTURE_NOT_COMPLETED",
                (job.error if job else None) or "Structure resolver did not complete the persisted job.",
            )
        target = db.query(ResearchTarget).filter(ResearchTarget.id == job.target_id).first()
        if target is None or not target.structure_path:
            raise WorkflowAdapterError(
                "RESEARCH_STRUCTURE_MISSING", "No structure path was persisted for the target."
            )
        root = (
            Path(settings.TEMP_DIR) / "research_structures"
            / f"study_{job.study_id}" / f"target_{target.id}"
        ).resolve()
        structure = Path(target.structure_path).resolve()
        if not structure.is_relative_to(root) or not structure.is_file():
            raise WorkflowAdapterError(
                "RESEARCH_STRUCTURE_INVALID_PATH",
                "Resolved structure is missing or outside its study/target storage directory.",
            )
        report = target.structure_validation if isinstance(target.structure_validation, dict) else {}
        validation_status = report.get("status")
        if not report or report.get("eligible_for_docking_review") is not True:
            raise WorkflowAdapterError(
                "RESEARCH_STRUCTURE_QC_REJECTED",
                f"Structure exists but did not pass the sequence/coverage gate ({validation_status or 'no report'}).",
            )
        structure_sha256, structure_size = _sha256_path(structure)
        if structure_size <= 0:
            raise WorkflowAdapterError("RESEARCH_STRUCTURE_EMPTY", "Resolved structure artifact is empty.")
        best_chain = ((report.get("sequence_match") or {}).get("best_chain") or {})
        return {
            "structure_job_id": job_id,
            "target_id": target.id,
            "status": "validated",
            "structure_source": target.structure_source,
            "validation_status": validation_status,
            "eligible_for_docking_review": True,
            "sequence_identity": best_chain.get("identity"),
            "sequence_coverage": best_chain.get("coverage"),
            "structure_size_bytes": structure_size,
            "structure_sha256": structure_sha256,
        }
    except OSError as exc:
        raise WorkflowAdapterError("RESEARCH_STRUCTURE_READ_ERROR", str(exc)) from exc
    finally:
        db.close()


ADAPTERS: dict[str, Callable[[list, dict], dict]] = {
    "analysis.run": run_sequence_analysis,
    "workflow.validate_sequence_result": validate_sequence_result,
    "workflow.validate_docking_input": validate_docking_input,
    "docking.run_vina": run_vina_docking,
    "workflow.validate_docking_result": validate_docking_result,
    "hsa.run": run_hsa_analysis,
    "qsa.run": run_qsa_analysis,
    "rna_structure.predict_secondary": run_rna_secondary_structure,
    "research.resolve_structure": run_research_structure_resolution,
    "experimental.run_4pl": run_experimental_4pl,
}


def execute_adapter(task_name: str, args: list, kwargs: dict) -> dict:
    adapter = ADAPTERS.get(task_name)
    if adapter is None:
        raise WorkflowAdapterError(
            "UNREGISTERED_WORKFLOW_ADAPTER",
            f"No reviewed scientific workflow adapter is registered for {task_name!r}.",
        )
    return adapter(args, kwargs)
