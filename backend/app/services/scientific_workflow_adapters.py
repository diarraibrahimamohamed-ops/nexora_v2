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


def _run_task_eager(task, *, args: list, kwargs: dict | None = None) -> dict:
    """Run an existing Celery task in this worker process without joining a queue."""
    result = task.apply(args=args, kwargs=kwargs or {}, throw=False)
    payload = result.get(propagate=True)
    if not isinstance(payload, dict):
        raise WorkflowAdapterError(
            "INVALID_ENGINE_RESPONSE",
            f"Scientific task {task.name} did not return a JSON object.",
        )
    if payload.get("status") != "completed":
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


ADAPTERS: dict[str, Callable[[list, dict], dict]] = {
    "analysis.run": run_sequence_analysis,
    "workflow.validate_sequence_result": validate_sequence_result,
    "workflow.validate_docking_input": validate_docking_input,
    "docking.run_vina": run_vina_docking,
    "workflow.validate_docking_result": validate_docking_result,
}


def execute_adapter(task_name: str, args: list, kwargs: dict) -> dict:
    adapter = ADAPTERS.get(task_name)
    if adapter is None:
        raise WorkflowAdapterError(
            "UNREGISTERED_WORKFLOW_ADAPTER",
            f"No reviewed scientific workflow adapter is registered for {task_name!r}.",
        )
    return adapter(args, kwargs)
