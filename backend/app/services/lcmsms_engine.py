"""Transactional execution service for OpenMS + matchms LC-MS/MS processing.

One processing run consumes one centroided mzML injection. Cross-injection
alignment and quantitative normalisation are intentionally outside this first
engine slice; the original mzML remains the lossless source of all scans/peaks.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.evidence import ResearchEvidence
from app.models.lcmsms import (
    LcmsCompoundAnnotation,
    LcmsDataArtifact,
    LcmsFeature,
    LcmsFeatureObservation,
    LcmsInjection,
    LcmsMs2Spectrum,
    LcmsProcessingRun,
    LcmsQualityControlObservation,
    LcmsSpectralLibrary,
)
from app.models.phytochemistry import PhytochemicalCandidate
from app.services.lcmsms_algorithms import (
    FeatureRecord,
    LcmsEngineError,
    Ms2ScanRecord,
    associate_ms2_scans,
    detect_ms1_features,
    extract_ms2_scans,
    load_msp_library,
    score_library_spectra,
    validate_mzml,
)
from app.services.scientific_provenance import seal_manifest, sha256_json

logger = logging.getLogger(__name__)
OPENMS_VERSION = "3.6.0"
MATCHMS_VERSION = "0.33.1"


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _safe_storage_file(relative_path: str, *, max_bytes: int, expected_sha256: str, label: str) -> tuple[Path, int]:
    root = Path(settings.LCMS_STORAGE_DIR).resolve()
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise LcmsEngineError("ARTIFACT_PATH_INVALID", f"{label} storage path is invalid.")
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise LcmsEngineError("ARTIFACT_NOT_FOUND", f"{label} source file is missing from external storage.")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise LcmsEngineError("ARTIFACT_READ_FAILED", f"Cannot stat {label}: {exc}") from exc
    if size <= 0:
        raise LcmsEngineError("ARTIFACT_EMPTY", f"{label} source file is empty.")
    if size > max_bytes:
        raise LcmsEngineError(
            "PROCESSING_INPUT_TOO_LARGE",
            f"{label} is {size} bytes; configured processing limit is {max_bytes} bytes.",
        )
    try:
        actual_sha, actual_size = _sha256_file(path)
    except OSError as exc:
        raise LcmsEngineError("ARTIFACT_READ_FAILED", f"Cannot read {label}: {exc}") from exc
    if actual_size != size or actual_sha.lower() != (expected_sha256 or "").lower():
        raise LcmsEngineError("ARTIFACT_CHECKSUM_MISMATCH", f"{label} SHA-256 does not match its artefact registry record.")
    return path, size


def _json_safe(value: Any) -> Any:
    """Remove non-finite/NumPy scalar values before JSON persistence and hashing."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    try:
        scalar = value.item()
        return _json_safe(scalar)
    except Exception:
        return str(value)


def _metric(
    db: Session,
    *,
    run: LcmsProcessingRun,
    injection_id: int,
    metric_name: str,
    metric_value: float,
    unit: str,
    status: str,
    rationale: str,
    criteria: dict[str, Any] | None = None,
) -> None:
    db.add(LcmsQualityControlObservation(
        study_id=run.study_id,
        batch_id=run.batch_id,
        injection_id=injection_id,
        qc_type="other",
        metric_name=metric_name,
        metric_value=metric_value,
        metric_unit=unit,
        status=status,
        assessment_method="calculated",
        acceptance_criteria=criteria or {},
        rationale=rationale,
    ))


def _load_run_context(processing_run_id: int) -> dict[str, Any]:
    db = SessionLocal()
    try:
        run = db.query(LcmsProcessingRun).filter(
            LcmsProcessingRun.id == processing_run_id
        ).with_for_update().first()
        if run is None:
            raise LcmsEngineError("PROCESSING_RUN_NOT_FOUND", "LC-MS/MS processing run does not exist.")
        if run.status == "completed" and run.output_sha256:
            return {"already_completed": True, "output_sha256": run.output_sha256}
        if run.status == "failed":
            raise LcmsEngineError("PROCESSING_RUN_FAILED", "This processing run is already marked failed.")
        parameters = dict(run.parameters or {})
        artifact_id = parameters.get("mzml_artifact_id")
        injection_id = parameters.get("mzml_injection_id")
        if not isinstance(artifact_id, int) or not isinstance(injection_id, int):
            raise LcmsEngineError("PROCESSING_RUN_INPUT_INVALID", "Processing run is missing mzML artefact/injection identifiers.")
        artifact = db.query(LcmsDataArtifact).filter(
            LcmsDataArtifact.id == artifact_id,
            LcmsDataArtifact.study_id == run.study_id,
            LcmsDataArtifact.injection_id == injection_id,
            LcmsDataArtifact.artifact_role in {"raw_data", "converted_data"},
            LcmsDataArtifact.data_format == "mzml",
        ).first()
        if artifact is None:
            raise LcmsEngineError("MZML_ARTEFACT_NOT_FOUND", "Registered mzML artefact does not belong to this processing run.")
        injection = db.query(LcmsInjection).filter(
            LcmsInjection.id == injection_id,
            LcmsInjection.batch_id == run.batch_id,
            LcmsInjection.study_id == run.study_id,
        ).first()
        if injection is None:
            raise LcmsEngineError("MZML_INJECTION_MISMATCH", "Registered mzML injection is outside the processing batch.")
        library = None
        if run.spectral_library_id is not None:
            library = db.query(LcmsSpectralLibrary).filter(
                LcmsSpectralLibrary.id == run.spectral_library_id,
                LcmsSpectralLibrary.curation_status == "reviewed",
                LcmsSpectralLibrary.data_format == "msp",
            ).first()
            if library is None or not library.reference_set_storage_path or not library.reference_set_sha256:
                raise LcmsEngineError("MSP_LIBRARY_NOT_READY", "The selected MSP library has not been uploaded and reviewed.")
        mzml_path, mzml_size = _safe_storage_file(
            artifact.storage_path,
            max_bytes=settings.LCMS_MAX_PROCESSING_BYTES,
            expected_sha256=artifact.sha256,
            label="mzML",
        )
        library_path = None
        library_size = 0
        if library is not None:
            library_path, library_size = _safe_storage_file(
                library.reference_set_storage_path,
                max_bytes=settings.LCMS_MAX_LIBRARY_BYTES,
                expected_sha256=library.reference_set_sha256,
                label="MSP library",
            )
            if library.reference_set_size_bytes and library_size != library.reference_set_size_bytes:
                raise LcmsEngineError("MSP_LIBRARY_SIZE_MISMATCH", "MSP library size does not match its curated registry metadata.")

        run.status = "processing"
        run.started_at = run.started_at or datetime.utcnow()
        run.provenance_manifest = {
            **(run.provenance_manifest or {}),
            "execution": {
                "status": "processing",
                "engine": "OpenMS + matchms",
                "pyopenms_version": OPENMS_VERSION,
                "matchms_version": MATCHMS_VERSION if library is not None else None,
                "started_at": datetime.utcnow().isoformat(),
            },
        }
        db.commit()
        return {
            "already_completed": False,
            "processing_run_id": run.id,
            "study_id": run.study_id,
            "batch_id": run.batch_id,
            "injection_id": injection.id,
            "artifact_id": artifact.id,
            "artifact_sha256": artifact.sha256,
            "artifact_size_bytes": mzml_size,
            "mzml_path": str(mzml_path),
            "library_id": library.id if library else None,
            "library_name": library.name if library else None,
            "library_version": library.version if library else None,
            "library_sha256": library.reference_set_sha256 if library else None,
            "library_path": str(library_path) if library_path else None,
            "library_source_license": library.source_license if library else None,
            "library_citation": library.citation if library else None,
            "parameters": parameters,
            "input_sha256": run.input_sha256,
        }
    except LcmsEngineError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _persist_results(
    context: dict[str, Any],
    *,
    validation: dict[str, Any],
    feature_records: list[FeatureRecord],
    scan_records: list[Ms2ScanRecord],
    associations: list[Any],
    library_parse: dict[str, Any] | None,
    annotation_matches: dict[str, list[dict[str, Any]]],
    processing_summary: dict[str, Any],
) -> dict[str, Any]:
    processing_run_id = int(context["processing_run_id"])
    db = SessionLocal()
    try:
        run = db.query(LcmsProcessingRun).filter(
            LcmsProcessingRun.id == processing_run_id
        ).with_for_update().first()
        if run is None:
            raise LcmsEngineError("PROCESSING_RUN_NOT_FOUND", "LC-MS/MS processing run disappeared before persistence.")
        if run.status == "completed" and run.output_sha256:
            return {"status": "completed", "idempotent_replay": True, "output_sha256": run.output_sha256}

        feature_payload = [{
            "feature_key": item.feature_key,
            "precursor_mz": item.precursor_mz,
            "retention_time": item.retention_time_seconds,
            "retention_time_unit": "second",
            "polarity": item.polarity,
            "charge": item.charge,
            "quality": item.overall_quality,
        } for item in feature_records]
        scan_payload = [{
            "scan_identifier": scan.scan_identifier,
            "precursor_mz": scan.precursor_mz,
            "rt_seconds": scan.retention_time_seconds,
            "polarity": scan.polarity,
            "association_status": association.status,
            "associated_feature_key": association.feature_key,
            "association_candidates": list(association.candidates),
        } for scan, association in zip(scan_records, associations)]
        annotations_payload = {
            key: items for key, items in sorted(annotation_matches.items())
        }
        output_payload = _json_safe({
            "processing_run_id": run.id,
            "input_sha256": run.input_sha256,
            "feature_count": len(feature_payload),
            "ms2_scan_count": len(scan_payload),
            "feature_results": feature_payload,
            "ms2_associations": scan_payload,
            "library_matches": annotations_payload,
            "summary": processing_summary,
        })
        output_sha = sha256_json(output_payload)
        output_manifest = seal_manifest(
            {
                **output_payload,
                "output_sha256": output_sha,
                "engine": "OpenMS.FeatureFindingMetabo + matchms.CosineGreedy",
                "pyopenms_version": OPENMS_VERSION,
                "matchms_version": MATCHMS_VERSION if context["library_id"] is not None else None,
                "semantic_validation": validation,
                "algorithm_parameters": run.parameters,
                "interpretation_limit": (
                    "MS1 features are analytical signals. MS/MS library matches are putative MSI level 2 candidates; "
                    "they are not authentic-standard identifications or evidence of biological activity."
                ),
            },
            manifest_type="lcms_openms_matchms_output",
        )

        feature_by_key: dict[str, LcmsFeature] = {}
        for item in feature_records:
            row = LcmsFeature(
                processing_run_id=run.id,
                feature_key=item.feature_key,
                precursor_mz=item.precursor_mz,
                retention_time=item.retention_time_seconds,
                retention_time_unit="second",
                polarity=item.polarity,
                charge=item.charge,
                adduct=item.adduct,
                quality_flags=["openms_feature_finding_metabo"],
            )
            db.add(row)
            feature_by_key[item.feature_key] = row
        db.flush()

        for item in feature_records:
            feature_row = feature_by_key[item.feature_key]
            db.add(LcmsFeatureObservation(
                feature_id=feature_row.id,
                injection_id=context["injection_id"],
                observed_mz=item.precursor_mz,
                retention_time=item.retention_time_seconds,
                peak_area=None,
                peak_height=None,
                intensity=None,
                signal_to_noise=None,
                quality_flag="not_assessed",
                metadata_json={"source": "OpenMS FeatureFindingMetabo", "overall_quality": item.overall_quality},
            ))

        scan_rows: list[LcmsMs2Spectrum] = []
        scan_feature_ids: list[int | None] = []
        for scan, association in zip(scan_records, associations):
            feature = feature_by_key.get(association.feature_key) if association.feature_key else None
            fragment_items = [dict(item) for item in scan.fragments]
            row = LcmsMs2Spectrum(
                feature_id=feature.id if feature is not None else None,
                injection_id=context["injection_id"],
                artifact_id=context["artifact_id"],
                scan_identifier=scan.scan_identifier,
                association_status=association.status,
                association_candidates=list(association.candidates),
                precursor_mz=scan.precursor_mz,
                isolation_window=scan.isolation_window,
                collision_energy=scan.collision_energy,
                collision_energy_unit="eV" if scan.collision_energy is not None else None,
                polarity=scan.polarity,
                charge=scan.charge,
                fragments=fragment_items,
            )
            db.add(row)
            scan_rows.append(row)
            scan_feature_ids.append(feature.id if feature is not None else None)
        db.flush()

        annotation_count = 0
        feature_map_by_key = feature_by_key
        for feature_key, match_items in annotation_matches.items():
            feature = feature_map_by_key.get(feature_key)
            if feature is None:
                continue
            for match in match_items:
                candidate_id = None
                inchikey = match.get("inchikey")
                if isinstance(inchikey, str) and inchikey.strip():
                    candidates = db.query(PhytochemicalCandidate).filter(
                        PhytochemicalCandidate.inchikey == inchikey.strip()
                    ).limit(2).all()
                    if len(candidates) == 1:
                        candidate_id = candidates[0].id
                evidence = {
                    "spectrum_match": True,
                    "ms2_match": True,
                    "orthogonal_matches": ["ms2_spectrum"],
                    "score_type": "CosineGreedy",
                    "cosine_score": match["cosine_score"],
                    "matched_peaks": match["matched_peaks"],
                    "precursor_mass_error_ppm": match["precursor_mass_error_ppm"],
                    "fragment_mz_tolerance_da": run.parameters["fragment_mz_tolerance_da"],
                    "precursor_mass_tolerance_ppm": run.parameters["precursor_mass_tolerance_ppm"],
                    "minimum_cosine_score": run.parameters["minimum_cosine_score"],
                    "minimum_matched_peaks": run.parameters["minimum_matched_peaks"],
                    "library_identifier": match.get("library_identifier"),
                    "library_source": match.get("library_source"),
                    "reference_metadata": match.get("reference_metadata") or {},
                }
                annotation = LcmsCompoundAnnotation(
                    feature_id=feature.id,
                    phytochem_candidate_id=candidate_id,
                    spectral_library_id=run.spectral_library_id,
                    annotation_method="library_match",
                    annotation_name=match.get("annotation_name"),
                    molecular_formula=match.get("molecular_formula"),
                    inchikey=inchikey,
                    smiles=match.get("smiles"),
                    msi_level=2,
                    score_type="matchms.CosineGreedy",
                    score_value=match["cosine_score"],
                    evidence=evidence,
                    status="candidate",
                    created_by=run.created_by,
                )
                db.add(annotation)
                db.flush()
                db.add(ResearchEvidence(
                    study_id=run.study_id,
                    object_type="lcms_compound_annotation",
                    object_id=str(annotation.id),
                    evidence_kind="msms_library_match",
                    evidence_status="computational_hypothesis",
                    evidence_grade="D",
                    claim=(
                        f"Annotation spectrale candidate de {match.get('annotation_name') or feature_key}; "
                        f"score CosineGreedy={match['cosine_score']}, MSI niveau 2."
                    ),
                    source_type="msp_spectral_library",
                    source_id=f"library:{run.spectral_library_id}:{match.get('library_identifier') or match.get('library_index')}",
                    source_title=match.get("annotation_name"),
                    study_reference=context.get("library_citation"),
                    method="OpenMS.FeatureFindingMetabo + matchms.CosineGreedy",
                    tool_name="N3XORA LC-MS/MS Engine",
                    tool_version=f"pyOpenMS {OPENMS_VERSION} / matchms {MATCHMS_VERSION}",
                    reference_set_version=f"{context.get('library_name')}@{context.get('library_version')}",
                    input_sha256=run.input_sha256,
                    output_sha256=output_sha,
                    limitations=(
                        "Putative spectral-library annotation only. No authentic-standard co-analysis, "
                        "quantification, pharmacological activity, efficacy or clinical interpretation is established."
                    ),
                    metadata_json={
                        "processing_run_id": run.id,
                        "feature_id": feature.id,
                        "annotation_id": annotation.id,
                        "msi_level": 2,
                        "cosine_score": match["cosine_score"],
                        "matched_peaks": match["matched_peaks"],
                        "library_sha256": context["library_sha256"],
                        "annotation_status": "candidate",
                    },
                    created_by=run.created_by,
                ))
                annotation_count += 1

        _metric(
            db, run=run, injection_id=context["injection_id"],
            metric_name="mzml_semantic_validation", metric_value=1.0, unit="boolean",
            status="pass",
            rationale="OpenMS MzMLFile.isSemanticallyValid accepted the mzML input.",
        )
        _metric(
            db, run=run, injection_id=context["injection_id"],
            metric_name="ms1_scan_count", metric_value=float(processing_summary["ms1_scan_count"]), unit="scans",
            status="not_assessed",
            rationale="Descriptive acquisition metric; acceptance threshold is protocol-specific.",
        )
        _metric(
            db, run=run, injection_id=context["injection_id"],
            metric_name="feature_count", metric_value=float(len(feature_records)), unit="features",
            status="not_assessed",
            rationale="Descriptive result count; not a measure of analytical validity.",
        )
        _metric(
            db, run=run, injection_id=context["injection_id"],
            metric_name="ms2_scan_count", metric_value=float(len(scan_records)), unit="scans",
            status="not_assessed",
            rationale="Descriptive acquisition metric; acceptance threshold is protocol-specific.",
        )
        _metric(
            db, run=run, injection_id=context["injection_id"],
            metric_name="ms2_unassigned_scan_count",
            metric_value=float(sum(1 for item in associations if item.status != "matched")),
            unit="scans",
            status="not_assessed",
            rationale="Ambiguous and unmatched scans are retained; no nearest-feature guess is made.",
        )

        run.output_sha256 = output_sha
        run.provenance_manifest = {
            **(run.provenance_manifest or {}),
            "output_manifest": output_manifest,
            "execution": {
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
                "feature_count": len(feature_records),
                "ms2_scan_count": len(scan_records),
                "ms2_matched_scan_count": sum(1 for item in associations if item.status == "matched"),
                "ms2_ambiguous_scan_count": sum(1 for item in associations if item.status == "ambiguous"),
                "ms2_unmatched_scan_count": sum(1 for item in associations if item.status == "unmatched"),
                "annotation_candidate_count": annotation_count,
            },
        }
        run.status = "completed"
        run.completed_at = datetime.utcnow()
        db.commit()
        return {
            "status": "completed",
            "processing_run_id": run.id,
            "output_sha256": output_sha,
            "feature_count": len(feature_records),
            "ms2_scan_count": len(scan_records),
            "annotation_candidate_count": annotation_count,
            "ms2_matched_scan_count": sum(1 for item in associations if item.status == "matched"),
            "ms2_ambiguous_scan_count": sum(1 for item in associations if item.status == "ambiguous"),
            "ms2_unmatched_scan_count": sum(1 for item in associations if item.status == "unmatched"),
            "quality_review_required": True,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def execute_lcmsms_processing(processing_run_id: int) -> dict[str, Any]:
    """Execute one persisted LC-MS/MS run idempotently from its immutable input records."""
    context = _load_run_context(processing_run_id)
    if context.get("already_completed"):
        return {
            "status": "completed",
            "processing_run_id": processing_run_id,
            "output_sha256": context["output_sha256"],
            "idempotent_replay": True,
        }

    try:
        mzml_path = Path(context["mzml_path"])
        validation = validate_mzml(mzml_path)
        parameters = dict(context["parameters"])
        feature_records, feature_summary = detect_ms1_features(mzml_path, parameters)
        scan_records, scan_summary = extract_ms2_scans(
            mzml_path,
            fragment_storage_limit=int(parameters.get("fragment_storage_limit", 500)),
        )
        associations = associate_ms2_scans(
            feature_records,
            scan_records,
            precursor_mass_tolerance_ppm=float(parameters["precursor_mass_tolerance_ppm"]),
            retention_time_tolerance_seconds=float(parameters["retention_time_tolerance_seconds"]),
        )

        reference_spectra = []
        library_parse = None
        if context["library_path"]:
            reference_spectra, library_parse = load_msp_library(Path(context["library_path"]))
        feature_key_matches: dict[str, list[dict[str, Any]]] = {}
        if reference_spectra:
            max_per_feature = int(parameters.get("max_annotations_per_feature", 3))
            if max_per_feature < 1 or max_per_feature > 10:
                raise LcmsEngineError("INVALID_PROCESSING_PARAMETER", "max_annotations_per_feature must be 1..10.")
            for scan, association in zip(scan_records, associations):
                if association.status != "matched" or not association.feature_key:
                    continue
                if len(scan.fragments) < 2:
                    continue
                matches = score_library_spectra(
                    scan,
                    reference_spectra,
                    precursor_mass_tolerance_ppm=float(parameters["precursor_mass_tolerance_ppm"]),
                    fragment_mz_tolerance_da=float(parameters["fragment_mz_tolerance_da"]),
                    minimum_cosine_score=float(parameters["minimum_cosine_score"]),
                    minimum_matched_peaks=int(parameters["minimum_matched_peaks"]),
                    max_candidates=max_per_feature,
                )
                current = feature_key_matches.setdefault(association.feature_key, [])
                for match in matches:
                    dedupe_key = (match.get("library_identifier"), match.get("annotation_name"))
                    existing_keys = {
                        (item.get("library_identifier"), item.get("annotation_name"))
                        for item in current
                    }
                    if dedupe_key in existing_keys:
                        for existing in current:
                            if (
                                existing.get("library_identifier"), existing.get("annotation_name")
                            ) == dedupe_key and match["cosine_score"] > existing["cosine_score"]:
                                existing.update(match)
                        continue
                    current.append(match)
                current.sort(key=lambda item: (-item["cosine_score"], -item["matched_peaks"]))
                feature_key_matches[association.feature_key] = current[:max_per_feature]

        summary = {
            **feature_summary,
            **scan_summary,
            "semantic_validation_warning_count": len(validation.get("warnings") or []),
            "library_reference_spectrum_count": (library_parse or {}).get("reference_spectrum_count", 0),
            "library_rejected_reference_spectrum_count": (library_parse or {}).get("rejected_reference_spectrum_count", 0),
            "ms2_matched_scan_count": sum(1 for item in associations if item.status == "matched"),
            "ms2_ambiguous_scan_count": sum(1 for item in associations if item.status == "ambiguous"),
            "ms2_unmatched_scan_count": sum(1 for item in associations if item.status == "unmatched"),
            "annotation_candidate_count": sum(len(items) for items in feature_key_matches.values()),
        }
        return _persist_results(
            context,
            validation=validation,
            feature_records=feature_records,
            scan_records=scan_records,
            associations=associations,
            library_parse=library_parse,
            annotation_matches=feature_key_matches,
            processing_summary=summary,
        )
    except LcmsEngineError as exc:
        _mark_processing_failed(processing_run_id, exc.code, str(exc))
        raise
    except Exception as exc:
        _mark_processing_failed(processing_run_id, type(exc).__name__, str(exc))
        raise LcmsEngineError("LCMS_PROCESSING_FAILED", f"Unexpected LC-MS/MS engine failure: {exc}") from exc


def _mark_processing_failed(processing_run_id: int, error_code: str, message: str) -> None:
    db = SessionLocal()
    try:
        run = db.query(LcmsProcessingRun).filter(
            LcmsProcessingRun.id == processing_run_id
        ).first()
        if run is None or run.status == "completed":
            return
        run.status = "failed"
        run.completed_at = datetime.utcnow()
        run.provenance_manifest = {
            **(run.provenance_manifest or {}),
            "execution": {
                "status": "failed",
                "failed_at": datetime.utcnow().isoformat(),
                "error_code": error_code[:120],
                "error_message": message[:2000],
            },
        }
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not persist LC-MS/MS processing failure for run %s", processing_run_id)
    finally:
        db.close()
