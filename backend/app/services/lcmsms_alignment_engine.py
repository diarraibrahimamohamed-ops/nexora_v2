"""Durable alignment and quantification engines for LC-MS/MS batches."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.database import SessionLocal
from app.models.lcmsms import (
    LcmsAlignedFeature,
    LcmsAlignedFeatureMember,
    LcmsDataArtifact,
    LcmsFeature,
    LcmsFeatureAlignmentRun,
    LcmsFeatureObservation,
    LcmsInjection,
    LcmsNormalizedFeatureQuantification,
    LcmsProcessingRun,
    LcmsQuantificationRun,
)
from app.services.scientific_provenance import seal_manifest, sha256_json
from app.services.lcmsms_alignment import (
    FeatureInput,
    InjectionInput,
    LcmsAlignmentError,
    align_lcms_features,
)
from app.services.lcmsms_quantification import (
    LcmsQuantificationError,
    quantify_aligned_feature_matrix,
)


class LcmsAlignmentEngineError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = (code or "LCMS_ALIGNMENT_ERROR")[:120]
        self.retryable = retryable


def _feature_snapshot(feature: LcmsFeature, observation: LcmsFeatureObservation | None) -> dict[str, Any]:
    return {
        "feature_id": int(feature.id),
        "processing_run_id": int(feature.processing_run_id),
        "feature_key": feature.feature_key,
        "precursor_mz": float(feature.precursor_mz),
        "retention_time": float(feature.retention_time),
        "retention_time_unit": feature.retention_time_unit,
        "polarity": feature.polarity,
        "charge": feature.charge,
        "adduct": feature.adduct,
        "observation_id": int(observation.id) if observation is not None else None,
        "observed_mz": (
            float(observation.observed_mz)
            if observation is not None and observation.observed_mz is not None else None
        ),
        "observed_retention_time": (
            float(observation.retention_time)
            if observation is not None and observation.retention_time is not None else None
        ),
        "raw_intensity": (
            float(observation.intensity)
            if observation is not None and observation.intensity is not None
            else (
                float(observation.peak_area)
                if observation is not None and observation.peak_area is not None else None
            )
        ),
    }


def collect_alignment_inputs(
    db,
    *,
    study_id: int,
    batch_id: int,
    processing_run_ids: list[int],
) -> tuple[list[FeatureInput], list[InjectionInput], list[dict[str, Any]], list[int]]:
    runs = db.query(LcmsProcessingRun).filter(
        LcmsProcessingRun.id.in_(processing_run_ids),
        LcmsProcessingRun.study_id == study_id,
        LcmsProcessingRun.batch_id == batch_id,
        LcmsProcessingRun.status == "completed",
        LcmsProcessingRun.output_sha256.isnot(None),
    ).order_by(LcmsProcessingRun.id.asc()).all()
    if len(runs) != len(processing_run_ids):
        raise LcmsAlignmentEngineError(
            "ALIGNMENT_SOURCE_RUN_INVALID",
            "Un ou plusieurs traitements source n'existent pas, ne sont pas terminés ou appartiennent à un autre lot.",
        )

    injections: list[InjectionInput] = []
    feature_inputs: list[FeatureInput] = []
    snapshots: list[dict[str, Any]] = []
    injection_ids: list[int] = []
    seen_injections: set[int] = set()

    for run in runs:
        parameters = dict(run.parameters or {})
        injection_id = parameters.get("mzml_injection_id")
        if not isinstance(injection_id, int) or injection_id < 1:
            raise LcmsAlignmentEngineError(
                "ALIGNMENT_SOURCE_INJECTION_MISSING",
                f"Le traitement {run.id} ne référence pas une injection mzML valide.",
            )
        if injection_id in seen_injections:
            raise LcmsAlignmentEngineError(
                "ALIGNMENT_DUPLICATE_INJECTION_RUN",
                f"Plusieurs traitements ont été sélectionnés pour l'injection {injection_id}.",
            )
        seen_injections.add(injection_id)
        injection = db.query(LcmsInjection).filter(
            LcmsInjection.id == injection_id,
            LcmsInjection.study_id == study_id,
            LcmsInjection.batch_id == batch_id,
        ).first()
        if injection is None:
            raise LcmsAlignmentEngineError(
                "ALIGNMENT_SOURCE_INJECTION_MISMATCH",
                f"L'injection {injection_id} n'appartient pas à l'étude et au lot sélectionnés.",
            )
        if injection.injection_type not in {"sample", "pooled_qc"}:
            raise LcmsAlignmentEngineError(
                "ALIGNMENT_INJECTION_TYPE_UNSUPPORTED",
                f"L'injection {injection_id} ({injection.injection_type}) est exclue de l'alignement 1J.",
            )

        injections.append(InjectionInput(
            injection_id=int(injection.id),
            injection_order=int(injection.injection_order),
            injection_type=injection.injection_type,
        ))
        injection_ids.append(int(injection.id))
        source_features = db.query(LcmsFeature).filter(
            LcmsFeature.processing_run_id == run.id
        ).order_by(LcmsFeature.id.asc()).all()
        if not source_features:
            raise LcmsAlignmentEngineError(
                "ALIGNMENT_SOURCE_RUN_EMPTY",
                f"Le traitement {run.id} ne contient aucune feature MS1.",
            )
        observation_rows = db.query(LcmsFeatureObservation).filter(
            LcmsFeatureObservation.feature_id.in_([int(item.id) for item in source_features]),
            LcmsFeatureObservation.injection_id == injection_id,
        ).all()
        observations_by_feature = {int(item.feature_id): item for item in observation_rows}
        for feature in source_features:
            observation = observations_by_feature.get(int(feature.id))
            snapshot = _feature_snapshot(feature, observation)
            if feature.retention_time_unit not in {"second", "seconds"}:
                raise LcmsAlignmentEngineError(
                    "ALIGNMENT_RT_UNIT_UNSUPPORTED",
                    f"La feature {feature.id} n'est pas exprimée en secondes.",
                )
            snapshots.append(snapshot)
            raw_intensity = snapshot["raw_intensity"]
            feature_inputs.append(FeatureInput(
                feature_id=int(feature.id),
                processing_run_id=int(run.id),
                injection_id=int(injection.id),
                injection_order=int(injection.injection_order),
                feature_key=feature.feature_key,
                precursor_mz=float(feature.precursor_mz),
                retention_time_seconds=float(feature.retention_time),
                polarity=feature.polarity,
                charge=feature.charge,
                adduct=feature.adduct,
                raw_intensity=raw_intensity,
                source_observation_id=snapshot["observation_id"],
            ))
    if len(set(injection_ids)) != len(injection_ids):
        raise LcmsAlignmentEngineError(
            "ALIGNMENT_DUPLICATE_INJECTION_RUN",
            "La sélection ne peut contenir qu'un traitement terminé par injection.",
        )
    return feature_inputs, injections, snapshots, injection_ids


def build_alignment_input_manifest(
    *,
    study_id: int,
    batch_id: int,
    processing_runs: list[LcmsProcessingRun],
    injections: list[InjectionInput],
    feature_snapshots: list[dict[str, Any]],
    parameters: dict[str, Any],
    reference_injection_ids: dict[str, int],
) -> dict[str, Any]:
    return {
        "study_id": int(study_id),
        "batch_id": int(batch_id),
        "source_processing_runs": [
            {
                "processing_run_id": int(run.id),
                "input_sha256": run.input_sha256,
                "output_sha256": run.output_sha256,
            }
            for run in sorted(processing_runs, key=lambda row: int(row.id))
        ],
        "source_injections": [
            {
                "injection_id": item.injection_id,
                "injection_order": item.injection_order,
                "injection_type": item.injection_type,
            }
            for item in sorted(injections, key=lambda item: (item.injection_order, item.injection_id))
        ],
        "source_features": sorted(feature_snapshots, key=lambda row: row["feature_id"]),
        "reference_injection_ids": dict(sorted(reference_injection_ids.items())),
        "parameters": parameters,
        "alignment_protocol_version": "1.0.0",
    }


def execute_lcms_feature_alignment(alignment_run_id: int) -> dict[str, Any]:
    context = _load_alignment_context(alignment_run_id)
    if context["already_completed"]:
        return {
            "status": "completed",
            "alignment_run_id": alignment_run_id,
            "output_sha256": context["output_sha256"],
            "idempotent_replay": True,
        }
    try:
        output = align_lcms_features(
            context["features"],
            context["injections"],
            parameters=context["parameters"],
            reference_injection_ids=context["reference_injection_ids"],
        )
        return _persist_alignment(
            context,
            groups=list(output.groups),
            diagnostics=output.diagnostics,
        )
    except LcmsAlignmentError as exc:
        _mark_alignment_failed(alignment_run_id, exc.code, str(exc))
        raise LcmsAlignmentEngineError(exc.code, str(exc)) from exc
    except LcmsAlignmentEngineError as exc:
        _mark_alignment_failed(alignment_run_id, exc.code, str(exc))
        raise
    except Exception as exc:
        _mark_alignment_failed(alignment_run_id, type(exc).__name__, str(exc))
        raise LcmsAlignmentEngineError(
            "ALIGNMENT_EXECUTION_FAILED",
            f"Échec du calcul d'alignement : {exc}",
        ) from exc


def _load_alignment_context(alignment_run_id: int) -> dict[str, Any]:
    db = SessionLocal()
    try:
        run = db.query(LcmsFeatureAlignmentRun).filter(
            LcmsFeatureAlignmentRun.id == alignment_run_id
        ).with_for_update().first()
        if run is None:
            raise LcmsAlignmentEngineError("ALIGNMENT_RUN_NOT_FOUND", "L'exécution d'alignement n'existe pas.")
        if run.status == "completed" and run.output_sha256:
            return {"already_completed": True, "output_sha256": run.output_sha256}
        if run.status == "failed":
            raise LcmsAlignmentEngineError("ALIGNMENT_RUN_FAILED", "Cette exécution est déjà marquée en échec.")
        processing_ids = [int(value) for value in (run.source_processing_run_ids or [])]
        features, injections, snapshots, injection_ids = collect_alignment_inputs(
            db,
            study_id=run.study_id,
            batch_id=run.batch_id,
            processing_run_ids=processing_ids,
        )
        processing_runs = db.query(LcmsProcessingRun).filter(
            LcmsProcessingRun.id.in_(processing_ids)
        ).order_by(LcmsProcessingRun.id.asc()).all()
        parameters = dict(run.parameters or {})
        reference_ids = dict(parameters.pop("reference_injection_ids", {}) or {})
        manifest = build_alignment_input_manifest(
            study_id=run.study_id,
            batch_id=run.batch_id,
            processing_runs=processing_runs,
            injections=injections,
            feature_snapshots=snapshots,
            parameters=parameters,
            reference_injection_ids=reference_ids,
        )
        current_hash = sha256_json(manifest)
        if current_hash != run.input_sha256:
            raise LcmsAlignmentEngineError(
                "ALIGNMENT_SOURCE_CHANGED",
                "Les données source ont changé depuis la création de l'exécution d'alignement.",
            )
        run.status = "processing"
        run.started_at = run.started_at or datetime.utcnow()
        run.alignment_manifest = {
            **(run.alignment_manifest or {}),
            "execution": {
                "status": "processing",
                "started_at": datetime.utcnow().isoformat(),
                "input_sha256": current_hash,
            },
        }
        db.commit()
        return {
            "already_completed": False,
            "alignment_run_id": int(run.id),
            "study_id": int(run.study_id),
            "batch_id": int(run.batch_id),
            "input_sha256": run.input_sha256,
            "parameters": parameters,
            "reference_injection_ids": reference_ids,
            "processing_run_ids": processing_ids,
            "injection_ids": injection_ids,
            "features": features,
            "injections": injections,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _persist_alignment(
    context: dict[str, Any],
    *,
    groups: list[dict[str, Any]],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    run_id = int(context["alignment_run_id"])
    db = SessionLocal()
    try:
        run = db.query(LcmsFeatureAlignmentRun).filter(
            LcmsFeatureAlignmentRun.id == run_id
        ).with_for_update().first()
        if run is None:
            raise LcmsAlignmentEngineError("ALIGNMENT_RUN_NOT_FOUND", "L'exécution d'alignement a disparu.")
        if run.status == "completed" and run.output_sha256:
            return {
                "status": "completed",
                "alignment_run_id": run_id,
                "output_sha256": run.output_sha256,
                "idempotent_replay": True,
            }

        output_groups = []
        for group in groups:
            consensus = LcmsAlignedFeature(
                alignment_run_id=run_id,
                polarity=group["polarity"],
                consensus_mz=group["consensus_mz"],
                aligned_retention_time=group["aligned_retention_time"],
                retention_time_unit="second",
                member_count=group["member_count"],
                injection_count=group["injection_count"],
                alignment_status=group["alignment_status"],
                quality_metrics={
                    "expected_injection_count": group["expected_injection_count"],
                    "member_count": group["member_count"],
                },
            )
            db.add(consensus)
            db.flush()
            member_payloads = []
            for member in group["members"]:
                feature = member["feature"]
                row = LcmsAlignedFeatureMember(
                    alignment_run_id=run_id,
                    aligned_feature_id=consensus.id,
                    processing_run_id=feature.processing_run_id,
                    injection_id=feature.injection_id,
                    feature_id=feature.feature_id,
                    source_observation_id=feature.source_observation_id,
                    original_mz=feature.precursor_mz,
                    original_retention_time=feature.retention_time_seconds,
                    aligned_retention_time=member["aligned_retention_time"],
                    retention_time_unit="second",
                    delta_retention_time_seconds=member["delta_retention_time_seconds"],
                    delta_mz_ppm=member["delta_mz_ppm"],
                    raw_peak_area=None,
                    raw_intensity=feature.raw_intensity,
                    charge=feature.charge,
                    adduct=feature.adduct,
                    metadata_json={
                        "feature_key": feature.feature_key,
                        "is_rt_aligned": member["is_rt_aligned"],
                    },
                )
                db.add(row)
                member_payloads.append({
                    "feature_id": feature.feature_id,
                    "processing_run_id": feature.processing_run_id,
                    "injection_id": feature.injection_id,
                    "source_observation_id": feature.source_observation_id,
                    "original_mz": feature.precursor_mz,
                    "original_retention_time": feature.retention_time_seconds,
                    "aligned_retention_time": member["aligned_retention_time"],
                    "delta_retention_time_seconds": member["delta_retention_time_seconds"],
                    "delta_mz_ppm": member["delta_mz_ppm"],
                    "raw_intensity": feature.raw_intensity,
                    "is_rt_aligned": member["is_rt_aligned"],
                })
            output_groups.append({
                "aligned_feature_id": int(consensus.id),
                "polarity": group["polarity"],
                "consensus_mz": group["consensus_mz"],
                "aligned_retention_time": group["aligned_retention_time"],
                "member_count": group["member_count"],
                "injection_count": group["injection_count"],
                "expected_injection_count": group["expected_injection_count"],
                "alignment_status": group["alignment_status"],
                "members": member_payloads,
            })
        db.flush()
        output_payload = {
            "alignment_run_id": run_id,
            "input_sha256": run.input_sha256,
            "groups": output_groups,
            "diagnostics": diagnostics,
        }
        output_sha = sha256_json(output_payload)
        run.output_sha256 = output_sha
        run.status = "completed"
        run.completed_at = datetime.utcnow()
        run.alignment_manifest = seal_manifest({
            **(run.alignment_manifest or {}),
            "status": "completed",
            "input_sha256": run.input_sha256,
            "output_sha256": output_sha,
            "parameters": run.parameters or {},
            "source_processing_run_ids": context["processing_run_ids"],
            "source_injection_ids": context["injection_ids"],
            "diagnostics": diagnostics,
            "output_summary": {
                "aligned_feature_count": len(output_groups),
                "member_count": sum(item["member_count"] for item in output_groups),
                "singleton_count": sum(item["alignment_status"] == "singleton" for item in output_groups),
                "partial_count": sum(item["alignment_status"] == "partial" for item in output_groups),
            },
        }, manifest_type="lcms_feature_alignment_run")
        db.commit()
        return {
            "status": "completed",
            "alignment_run_id": run_id,
            "output_sha256": output_sha,
            "aligned_feature_count": len(output_groups),
            "idempotent_replay": False,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _mark_alignment_failed(alignment_run_id: int, code: str, message: str) -> None:
    db = SessionLocal()
    try:
        run = db.query(LcmsFeatureAlignmentRun).filter(
            LcmsFeatureAlignmentRun.id == alignment_run_id
        ).with_for_update().first()
        if run is not None and run.status != "completed":
            run.status = "failed"
            run.completed_at = datetime.utcnow()
            run.alignment_manifest = {
                **(run.alignment_manifest or {}),
                "execution": {
                    "status": "failed",
                    "error_code": code,
                    "error_message": str(message)[:4000],
                    "completed_at": datetime.utcnow().isoformat(),
                },
            }
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def build_quantification_input_manifest(
    *,
    alignment_run: LcmsFeatureAlignmentRun,
    injections: list[LcmsInjection],
    members: list[LcmsAlignedFeatureMember],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    return {
        "alignment_run_id": int(alignment_run.id),
        "alignment_output_sha256": alignment_run.output_sha256,
        "injections": [
            {
                "injection_id": int(item.id),
                "injection_order": int(item.injection_order),
                "injection_type": item.injection_type,
            }
            for item in sorted(injections, key=lambda row: (row.injection_order, row.id))
        ],
        "members": [
            {
                "aligned_feature_id": int(item.aligned_feature_id),
                "feature_id": int(item.feature_id),
                "processing_run_id": int(item.processing_run_id),
                "injection_id": int(item.injection_id),
                "raw_intensity": float(item.raw_intensity) if item.raw_intensity is not None else None,
            }
            for item in sorted(members, key=lambda row: (row.aligned_feature_id, row.injection_id))
        ],
        "parameters": parameters,
        "quantification_protocol_version": "1.0.0",
    }


def execute_lcms_feature_quantification(quantification_run_id: int) -> dict[str, Any]:
    context = _load_quantification_context(quantification_run_id)
    if context["already_completed"]:
        return {
            "status": "completed",
            "quantification_run_id": quantification_run_id,
            "output_sha256": context["output_sha256"],
            "idempotent_replay": True,
        }
    try:
        output = quantify_aligned_feature_matrix(
            context["features"],
            context["injections"],
            context["members"],
            parameters=context["parameters"],
        )
        return _persist_quantification(context, output)
    except LcmsQuantificationError as exc:
        _mark_quantification_failed(quantification_run_id, exc.code, str(exc))
        raise LcmsAlignmentEngineError(exc.code, str(exc)) from exc
    except LcmsAlignmentEngineError as exc:
        _mark_quantification_failed(quantification_run_id, exc.code, str(exc))
        raise
    except Exception as exc:
        _mark_quantification_failed(quantification_run_id, type(exc).__name__, str(exc))
        raise LcmsAlignmentEngineError(
            "QUANTIFICATION_EXECUTION_FAILED",
            f"Échec de la correction QC-RLSC : {exc}",
        ) from exc


def _load_quantification_context(quantification_run_id: int) -> dict[str, Any]:
    db = SessionLocal()
    try:
        run = db.query(LcmsQuantificationRun).filter(
            LcmsQuantificationRun.id == quantification_run_id
        ).with_for_update().first()
        if run is None:
            raise LcmsAlignmentEngineError("QUANTIFICATION_RUN_NOT_FOUND", "L'exécution de quantification n'existe pas.")
        if run.status == "completed" and run.output_sha256:
            return {"already_completed": True, "output_sha256": run.output_sha256}
        if run.status == "failed":
            raise LcmsAlignmentEngineError("QUANTIFICATION_RUN_FAILED", "Cette exécution est déjà marquée en échec.")

        alignment = db.query(LcmsFeatureAlignmentRun).filter(
            LcmsFeatureAlignmentRun.id == run.alignment_run_id,
            LcmsFeatureAlignmentRun.study_id == run.study_id,
            LcmsFeatureAlignmentRun.batch_id == run.batch_id,
            LcmsFeatureAlignmentRun.status == "completed",
            LcmsFeatureAlignmentRun.output_sha256.isnot(None),
        ).first()
        if alignment is None:
            raise LcmsAlignmentEngineError(
                "QUANTIFICATION_ALIGNMENT_NOT_READY",
                "La quantification exige une exécution d'alignement terminée et scellée.",
            )
        injection_ids = [int(value) for value in (alignment.source_injection_ids or [])]
        injections = db.query(LcmsInjection).filter(
            LcmsInjection.id.in_(injection_ids),
            LcmsInjection.study_id == run.study_id,
            LcmsInjection.batch_id == run.batch_id,
        ).order_by(LcmsInjection.injection_order.asc()).all()
        if len(injections) != len(injection_ids):
            raise LcmsAlignmentEngineError(
                "QUANTIFICATION_INJECTION_MISMATCH",
                "Une injection source de l'alignement n'existe plus dans le lot.",
            )
        features = db.query(LcmsAlignedFeature).filter(
            LcmsAlignedFeature.alignment_run_id == alignment.id
        ).order_by(LcmsAlignedFeature.id.asc()).all()
        members = db.query(LcmsAlignedFeatureMember).filter(
            LcmsAlignedFeatureMember.alignment_run_id == alignment.id
        ).order_by(
            LcmsAlignedFeatureMember.aligned_feature_id.asc(),
            LcmsAlignedFeatureMember.injection_id.asc(),
        ).all()
        input_manifest = build_quantification_input_manifest(
            alignment_run=alignment,
            injections=injections,
            members=members,
            parameters=dict(run.parameters or {}),
        )
        input_sha = sha256_json(input_manifest)
        if input_sha != run.input_sha256:
            raise LcmsAlignmentEngineError(
                "QUANTIFICATION_SOURCE_CHANGED",
                "Les membres ou intensités alignés ont changé depuis la création de la quantification.",
            )
        run.status = "processing"
        run.started_at = run.started_at or datetime.utcnow()
        run.quantification_manifest = {
            **(run.quantification_manifest or {}),
            "execution": {
                "status": "processing",
                "started_at": datetime.utcnow().isoformat(),
                "input_sha256": input_sha,
            },
        }
        db.commit()
        return {
            "already_completed": False,
            "quantification_run_id": int(run.id),
            "alignment_run_id": int(alignment.id),
            "study_id": int(run.study_id),
            "batch_id": int(run.batch_id),
            "input_sha256": run.input_sha256,
            "parameters": dict(run.parameters or {}),
            "features": [
                {"aligned_feature_id": int(item.id), "polarity": item.polarity}
                for item in features
            ],
            "injections": [
                {
                    "injection_id": int(item.id),
                    "injection_order": int(item.injection_order),
                    "injection_type": item.injection_type,
                }
                for item in injections
            ],
            "members": [
                {
                    "aligned_feature_id": int(item.aligned_feature_id),
                    "injection_id": int(item.injection_id),
                    "processing_run_id": int(item.processing_run_id),
                    "feature_id": int(item.feature_id),
                    "raw_intensity": float(item.raw_intensity) if item.raw_intensity is not None else None,
                }
                for item in members
            ],
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _persist_quantification(context: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    run_id = int(context["quantification_run_id"])
    db = SessionLocal()
    try:
        run = db.query(LcmsQuantificationRun).filter(
            LcmsQuantificationRun.id == run_id
        ).with_for_update().first()
        if run is None:
            raise LcmsAlignmentEngineError("QUANTIFICATION_RUN_NOT_FOUND", "L'exécution de quantification a disparu.")
        if run.status == "completed" and run.output_sha256:
            return {
                "status": "completed",
                "quantification_run_id": run_id,
                "output_sha256": run.output_sha256,
                "idempotent_replay": True,
            }
        rows = [
            LcmsNormalizedFeatureQuantification(
                quantification_run_id=run_id,
                alignment_run_id=context["alignment_run_id"],
                aligned_feature_id=item["aligned_feature_id"],
                injection_id=item["injection_id"],
                processing_run_id=item["processing_run_id"],
                feature_id=item["feature_id"],
                qc_type=item["qc_type"],
                raw_intensity=item["raw_intensity"],
                corrected_intensity=item["corrected_intensity"],
                correction_factor=item["correction_factor"],
                normalization_status=item["normalization_status"],
                qc_rsd_before_pct=item["qc_rsd_before_pct"],
                qc_rsd_after_pct=item["qc_rsd_after_pct"],
                quality_metrics=item["quality_metrics"],
            )
            for item in output["cells"]
        ]
        db.add_all(rows)
        db.flush()
        output_payload = {
            "quantification_run_id": run_id,
            "alignment_run_id": context["alignment_run_id"],
            "input_sha256": run.input_sha256,
            "method": output["method"],
            "parameters": output["parameters"],
            "feature_count": output["feature_count"],
            "injection_count": output["injection_count"],
            "cell_count": output["cell_count"],
            "status_counts": output["status_counts"],
            "feature_summaries": output["feature_summaries"],
        }
        output_sha = sha256_json(output_payload)
        run.output_sha256 = output_sha
        run.status = "completed"
        run.completed_at = datetime.utcnow()
        run.quantification_manifest = seal_manifest({
            **(run.quantification_manifest or {}),
            **output_payload,
            "status": "completed",
            "output_sha256": output_sha,
            "source_injection_ids": [item["injection_id"] for item in context["injections"]],
            "limitations": output["limitations"],
        }, manifest_type="lcms_quantification_run")
        db.commit()
        return {
            "status": "completed",
            "quantification_run_id": run_id,
            "output_sha256": output_sha,
            "cell_count": len(rows),
            "status_counts": output["status_counts"],
            "idempotent_replay": False,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _mark_quantification_failed(quantification_run_id: int, code: str, message: str) -> None:
    db = SessionLocal()
    try:
        run = db.query(LcmsQuantificationRun).filter(
            LcmsQuantificationRun.id == quantification_run_id
        ).with_for_update().first()
        if run is not None and run.status != "completed":
            run.status = "failed"
            run.completed_at = datetime.utcnow()
            run.quantification_manifest = {
                **(run.quantification_manifest or {}),
                "execution": {
                    "status": "failed",
                    "error_code": code,
                    "error_message": str(message)[:4000],
                    "completed_at": datetime.utcnow().isoformat(),
                },
            }
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
