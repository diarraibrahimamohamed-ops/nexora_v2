"""Reproducible algorithms for the first LC-MS/MS engine slice.

OpenMS FeatureFindingMetabo is used only on centroided MS1 spectra.
MS2-to-feature association is deliberately conservative: more than one
candidate inside both declared tolerances is marked ambiguous, never guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any


class LcmsEngineError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = (code or "LCMS_ENGINE_ERROR")[:120]
        self.retryable = retryable


@dataclass(frozen=True)
class FeatureRecord:
    feature_key: str
    precursor_mz: float
    retention_time_seconds: float
    polarity: str
    intensity: float
    charge: int | None = None
    adduct: str | None = None
    overall_quality: float | None = None


@dataclass(frozen=True)
class Ms2ScanRecord:
    scan_identifier: str
    precursor_mz: float
    retention_time_seconds: float
    polarity: str
    charge: int | None
    isolation_window: float | None
    collision_energy: float | None
    fragments: tuple[dict[str, float], ...]
    total_fragment_count: int
    fragments_truncated: bool
    spectrum_type: str = "unknown"


@dataclass(frozen=True)
class Ms2Association:
    status: str
    feature_key: str | None
    candidates: tuple[dict[str, Any], ...]


def _finite_positive(value: Any, label: str) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise LcmsEngineError("INVALID_PROCESSING_PARAMETER", f"{label} must be a number.") from exc
    if not math.isfinite(converted) or converted <= 0:
        raise LcmsEngineError("INVALID_PROCESSING_PARAMETER", f"{label} must be finite and > 0.")
    return converted


def mass_error_ppm(observed_mz: float, reference_mz: float) -> float:
    if not math.isfinite(observed_mz) or not math.isfinite(reference_mz) or reference_mz <= 0:
        raise ValueError("m/z values must be finite and reference_mz > 0")
    return abs(observed_mz - reference_mz) / reference_mz * 1_000_000.0


def associate_ms2_scans(
    features: list[FeatureRecord],
    scans: list[Ms2ScanRecord],
    *,
    precursor_mass_tolerance_ppm: float,
    retention_time_tolerance_seconds: float,
) -> list[Ms2Association]:
    """Associate each MS2 scan only when exactly one feature meets both tolerances."""
    ppm_tolerance = _finite_positive(precursor_mass_tolerance_ppm, "precursor_mass_tolerance_ppm")
    rt_tolerance = _finite_positive(retention_time_tolerance_seconds, "retention_time_tolerance_seconds")
    results: list[Ms2Association] = []

    for scan in scans:
        candidates: list[dict[str, Any]] = []
        for feature in features:
            if feature.polarity != scan.polarity:
                continue
            ppm = mass_error_ppm(scan.precursor_mz, feature.precursor_mz)
            rt_delta = abs(scan.retention_time_seconds - feature.retention_time_seconds)
            if ppm <= ppm_tolerance and rt_delta <= rt_tolerance:
                candidates.append({
                    "feature_key": feature.feature_key,
                    "mass_error_ppm": round(ppm, 8),
                    "retention_time_delta_seconds": round(rt_delta, 8),
                })
        candidates.sort(key=lambda item: (
            item["mass_error_ppm"] / ppm_tolerance
            + item["retention_time_delta_seconds"] / rt_tolerance,
            item["feature_key"],
        ))
        if len(candidates) == 1:
            results.append(Ms2Association(
                status="matched",
                feature_key=candidates[0]["feature_key"],
                candidates=tuple(candidates),
            ))
        elif len(candidates) > 1:
            results.append(Ms2Association(
                status="ambiguous",
                feature_key=None,
                candidates=tuple(candidates[:20]),
            ))
        else:
            results.append(Ms2Association(status="unmatched", feature_key=None, candidates=()))
    return results


def _decode_messages(values: list[Any]) -> list[str]:
    result = []
    for item in values:
        if isinstance(item, bytes):
            result.append(item.decode("utf-8", errors="replace"))
        else:
            result.append(str(item))
    return result


def _spectrum_polarity(oms: Any, spectrum: Any) -> str:
    polarity = spectrum.getInstrumentSettings().getPolarity()
    if polarity == oms.IonSource.Polarity.POSITIVE:
        return "positive"
    if polarity == oms.IonSource.Polarity.NEGATIVE:
        return "negative"
    return "unknown"


def _spectrum_type_name(oms: Any, spectrum: Any) -> str:
    try:
        return str(oms.MSSpectrum.spectrumTypeToString(spectrum.getType(True))).lower()
    except Exception:
        return str(spectrum.getType(True)).lower()


def _load_spectra(path: Path, ms_levels: list[int]) -> tuple[Any, list[Any]]:
    try:
        import pyopenms as oms
    except ImportError as exc:
        raise LcmsEngineError(
            "OPENMS_UNAVAILABLE",
            "pyOpenMS is not installed in this worker; install the pinned backend dependencies.",
        ) from exc
    options = oms.PeakFileOptions()
    options.setMSLevels(ms_levels)
    mzml_file = oms.MzMLFile()
    mzml_file.setOptions(options)
    experiment = oms.MSExperiment()
    try:
        mzml_file.load(str(path), experiment)
    except Exception as exc:
        raise LcmsEngineError("MZML_PARSE_FAILED", f"OpenMS could not parse the mzML file: {exc}") from exc
    return oms, list(experiment.getSpectra())


def _classify_spectrum_type(oms: Any, spectrum: Any) -> str:
    """Return an explicit centroiding classification; never infer centroiding."""
    name = _spectrum_type_name(oms, spectrum)
    if "centroid" in name:
        return "centroided"
    if "profile" in name or "raw" in name:
        return "profile"
    return "unknown"


def validate_mzml(path: Path) -> dict[str, Any]:
    """Run OpenMS semantic validation before interpreting the raw XML payload."""
    try:
        import pyopenms as oms
    except ImportError as exc:
        raise LcmsEngineError("OPENMS_UNAVAILABLE", "pyOpenMS is not installed in this worker.") from exc
    errors: list[Any] = []
    warnings: list[Any] = []
    try:
        valid = bool(oms.MzMLFile().isSemanticallyValid(str(path), errors, warnings))
    except Exception as exc:
        raise LcmsEngineError("MZML_VALIDATION_FAILED", f"OpenMS mzML validation failed: {exc}") from exc
    error_messages = _decode_messages(errors)
    warning_messages = _decode_messages(warnings)
    if not valid:
        detail = "; ".join(error_messages[:5]) or "No validator detail was returned."
        raise LcmsEngineError("MZML_SEMANTIC_INVALID", f"The mzML file is not semantically valid: {detail}")
    return {
        "validator": "OpenMS MzMLFile.isSemanticallyValid",
        "valid": True,
        "errors": error_messages,
        "warnings": warning_messages,
    }


def detect_ms1_features(path: Path, parameters: dict[str, Any]) -> tuple[list[FeatureRecord], dict[str, Any]]:
    """Detect untargeted metabolite features from centroided MS1 scans."""
    import numpy as np

    mass_error = _finite_positive(parameters.get("mass_error_ppm"), "mass_error_ppm")
    noise_threshold = _finite_positive(parameters.get("noise_threshold_int"), "noise_threshold_int")

    oms, spectra = _load_spectra(path, [1])
    if not spectra:
        raise LcmsEngineError("MZML_NO_MS1", "The mzML file contains no MS1 spectra.")

    polarity_groups: dict[str, list[Any]] = {"positive": [], "negative": [], "unknown": []}
    profile_count = 0
    unknown_type_count = 0
    for spectrum in spectra:
        spectrum_type = _spectrum_type_name(oms, spectrum)
        if "profile" in spectrum_type or "raw" in spectrum_type:
            profile_count += 1
            continue
        if "unknown" in spectrum_type:
            unknown_type_count += 1
            continue
        polarity_groups[_spectrum_polarity(oms, spectrum)].append(spectrum)

    if profile_count:
        raise LcmsEngineError(
            "MZML_PROFILE_MS1_UNSUPPORTED",
            f"{profile_count} MS1 scans contain profile data. Centroid them with a documented preprocessing step before FeatureFindingMetabo.",
        )
    if unknown_type_count:
        raise LcmsEngineError(
            "MZML_UNKNOWN_MS1_CENTROIDING",
            f"{unknown_type_count} MS1 scans have unknown peak type; the engine refuses to assume centroiding.",
        )

    features: list[FeatureRecord] = []
    polarity_counts: dict[str, int] = {}
    for polarity, group in polarity_groups.items():
        if not group:
            continue
        experiment = oms.MSExperiment()
        for spectrum in group:
            experiment.addSpectrum(spectrum)
        experiment.sortSpectra(True)

        mtd = oms.MassTraceDetection()
        mtd_params = mtd.getDefaults()
        mtd_params.setValue("mass_error_ppm", mass_error)
        mtd_params.setValue("noise_threshold_int", noise_threshold)
        mtd.setParameters(mtd_params)
        try:
            mass_traces = mtd.run(experiment)
        except Exception as exc:
            raise LcmsEngineError(
                "OPENMS_MASS_TRACE_FAILED", f"MassTraceDetection failed for {polarity} data: {exc}"
            ) from exc

        epd = oms.ElutionPeakDetection()
        epd_params = epd.getDefaults()
        # OpenMS fixed mode uses its documented FWHM interval [1.0, 60.0] seconds.
        epd_params.setValue("width_filtering", "fixed")
        epd.setParameters(epd_params)
        try:
            mass_traces_split = epd.detectPeaks(mass_traces)
        except Exception as exc:
            raise LcmsEngineError(
                "OPENMS_ELUTION_PEAK_FAILED", f"ElutionPeakDetection failed for {polarity} data: {exc}"
            ) from exc
        mass_traces_final = (
            epd.filterByPeakWidth(mass_traces_split)
            if epd.getParameters().getValue("width_filtering") == "auto"
            else mass_traces_split
        )

        feature_map = oms.FeatureMap()
        ffm = oms.FeatureFindingMetabo()
        ffm_params = ffm.getDefaults()
        ffm_params.setValue("isotope_filtering_model", "none")
        ffm_params.setValue("remove_single_traces", "true")
        ffm_params.setValue("mz_scoring_by_elements", "false")
        ffm_params.setValue("report_convex_hulls", "true")
        ffm.setParameters(ffm_params)
        try:
            ffm.run(mass_traces_final, feature_map)
            feature_map.setUniqueIds()
        except Exception as exc:
            raise LcmsEngineError(
                "OPENMS_FEATURE_FINDING_FAILED", f"FeatureFindingMetabo failed for {polarity} data: {exc}"
            ) from exc

        polarity_counts[polarity] = int(feature_map.size())
        for feature in feature_map:
            mz = float(feature.getMZ())
            rt = float(feature.getRT())
            intensity = float(feature.getIntensity())
            if not (math.isfinite(mz) and mz > 0 and math.isfinite(rt) and rt >= 0 and math.isfinite(intensity) and intensity >= 0):
                continue
            try:
                charge_value = int(feature.getCharge())
                charge = charge_value if charge_value > 0 else None
            except Exception:
                charge = None
            try:
                quality = float(feature.getOverallQuality())
                if not math.isfinite(quality):
                    quality = None
            except Exception:
                quality = None
            feature_id = str(feature.getUniqueId())
            features.append(FeatureRecord(
                feature_key=f"openms:{polarity}:{feature_id}",
                precursor_mz=mz,
                retention_time_seconds=rt,
                polarity=polarity,
                intensity=intensity,
                charge=charge,
                overall_quality=quality,
            ))

    return features, {
        "ms1_scan_count": len(spectra),
        "feature_count_by_polarity": polarity_counts,
        "feature_count": len(features),
        "centroided_ms1_required": True,
        "feature_finder": "OpenMS.FeatureFindingMetabo",
        "mass_trace_detection": "OpenMS.MassTraceDetection",
        "elution_peak_detection": "OpenMS.ElutionPeakDetection",
        "mass_error_ppm": mass_error,
        "noise_threshold_int": noise_threshold,
        "elution_peak_width_mode": "fixed",
        "elution_peak_fwhm_min_seconds": 1.0,
        "elution_peak_fwhm_max_seconds": 60.0,
    }


def extract_ms2_scans(
    path: Path,
    *,
    fragment_storage_limit: int = 500,
) -> tuple[list[Ms2ScanRecord], dict[str, Any]]:
    """Extract MS2 precursor metadata and a bounded fragment summary per scan.

    The original mzML artefact remains the lossless source of all fragment peaks.
    The JSON field stores up to fragment_storage_limit strongest peaks per scan.
    """
    import numpy as np

    if fragment_storage_limit < 20 or fragment_storage_limit > 5000:
        raise LcmsEngineError("INVALID_PROCESSING_PARAMETER", "fragment_storage_limit must be 20..5000.")

    oms, spectra = _load_spectra(path, [2])
    scans: list[Ms2ScanRecord] = []
    missing_precursor = 0
    missing_fragments = 0
    spectrum_type_counts = {"centroided": 0, "profile": 0, "unknown": 0}

    for index, spectrum in enumerate(spectra):
        spectrum_type = _classify_spectrum_type(oms, spectrum)
        spectrum_type_counts[spectrum_type] += 1
        precursors = list(spectrum.getPrecursors())
        if not precursors:
            missing_precursor += 1
            continue
        precursor = precursors[0]
        precursor_mz = float(precursor.getMZ())
        if not math.isfinite(precursor_mz) or precursor_mz <= 0:
            missing_precursor += 1
            continue

        mz_array, intensity_array = spectrum.get_peaks()
        mz_values = np.asarray(mz_array, dtype=float)
        intensities = np.asarray(intensity_array, dtype=float)
        valid = np.isfinite(mz_values) & np.isfinite(intensities) & (mz_values > 0) & (intensities > 0)
        mz_values = mz_values[valid]
        intensities = intensities[valid]
        if len(mz_values) == 0:
            missing_fragments += 1

        total_count = int(len(mz_values))
        truncated = total_count > fragment_storage_limit
        if truncated:
            strongest = np.argpartition(intensities, -fragment_storage_limit)[-fragment_storage_limit:]
            mz_values = mz_values[strongest]
            intensities = intensities[strongest]
        order = np.argsort(mz_values)
        fragments = tuple({
            "mz": float(mz_values[i]),
            "intensity": float(intensities[i]),
        } for i in order)

        try:
            charge_value = int(precursor.getCharge())
            charge = charge_value if charge_value else None
        except Exception:
            charge = None
        try:
            isolation = float(precursor.getIsolationWindowLowerOffset()) + float(precursor.getIsolationWindowUpperOffset())
            if not math.isfinite(isolation) or isolation < 0:
                isolation = None
        except Exception:
            isolation = None
        try:
            collision_energy = float(precursor.getActivationEnergy())
            if not math.isfinite(collision_energy) or collision_energy < 0:
                collision_energy = None
        except Exception:
            collision_energy = None

        scan_id = str(spectrum.getNativeID() or f"ms2-index-{index + 1}")
        scans.append(Ms2ScanRecord(
            scan_identifier=scan_id[:120],
            precursor_mz=precursor_mz,
            retention_time_seconds=max(0.0, float(spectrum.getRT())),
            polarity=_spectrum_polarity(oms, spectrum),
            charge=charge,
            isolation_window=isolation,
            collision_energy=collision_energy,
            fragments=fragments,
            total_fragment_count=total_count,
            fragments_truncated=truncated,
            spectrum_type=spectrum_type,
        ))
    return scans, {
        "ms2_scan_count": len(spectra),
        "ms2_centroided_scan_count": spectrum_type_counts["centroided"],
        "ms2_profile_scan_count": spectrum_type_counts["profile"],
        "ms2_unknown_spectrum_type_scan_count": spectrum_type_counts["unknown"],
        "ms2_scans_with_precursor": len(scans),
        "ms2_scans_missing_precursor": missing_precursor,
        "ms2_scans_without_fragments": missing_fragments,
        "stored_fragments_per_spectrum_max": fragment_storage_limit,
        "fragment_storage_note": "The mzML artefact remains the lossless source; fragments JSON is a bounded summary.",
    }


def load_msp_library(path: Path) -> tuple[list[Any], dict[str, Any]]:
    """Load curated reference spectra from a reviewed MSP file."""
    try:
        from matchms.importing import load_from_msp
    except ImportError as exc:
        raise LcmsEngineError("MATCHMS_UNAVAILABLE", "matchms is not installed in this worker.") from exc
    spectra = []
    rejected = 0
    try:
        for spectrum in load_from_msp(str(path)):
            metadata = spectrum.metadata
            precursor_mz = metadata.get("precursor_mz")
            try:
                precursor_value = float(precursor_mz)
            except (TypeError, ValueError):
                rejected += 1
                continue
            mz_values = spectrum.peaks.mz
            intensity_values = spectrum.peaks.intensities
            if (
                not math.isfinite(precursor_value) or precursor_value <= 0
                or len(mz_values) < 2 or len(mz_values) != len(intensity_values)
            ):
                rejected += 1
                continue
            name = metadata.get("compound_name") or metadata.get("name")
            if not name:
                rejected += 1
                continue
            spectra.append(spectrum)
    except Exception as exc:
        raise LcmsEngineError("MSP_LIBRARY_PARSE_FAILED", f"matchms could not parse the MSP library: {exc}") from exc
    if not spectra:
        raise LcmsEngineError("MSP_LIBRARY_EMPTY", "The MSP library contains no usable spectra with precursor m/z and compound name.")
    return spectra, {
        "reference_spectrum_count": len(spectra),
        "rejected_reference_spectrum_count": rejected,
        "parser": "matchms.importing.load_from_msp",
    }


def score_library_spectra(
    scan: Ms2ScanRecord,
    reference_spectra: list[Any],
    *,
    precursor_mass_tolerance_ppm: float,
    fragment_mz_tolerance_da: float,
    minimum_cosine_score: float,
    minimum_matched_peaks: int,
    max_candidates: int = 3,
) -> list[dict[str, Any]]:
    """Return scored reference candidates; no candidate is auto-accepted."""
    if scan.spectrum_type != "centroided":
        return []
    try:
        import numpy as np
        from matchms import Spectrum
        from matchms.similarity import CosineGreedy
    except ImportError as exc:
        raise LcmsEngineError("MATCHMS_UNAVAILABLE", "matchms is not installed in this worker.") from exc

    precursor_tolerance = _finite_positive(precursor_mass_tolerance_ppm, "precursor_mass_tolerance_ppm")
    fragment_tolerance = _finite_positive(fragment_mz_tolerance_da, "fragment_mz_tolerance_da")
    if not math.isfinite(minimum_cosine_score) or not 0 <= minimum_cosine_score <= 1:
        raise LcmsEngineError("INVALID_PROCESSING_PARAMETER", "minimum_cosine_score must be between 0 and 1.")
    if minimum_matched_peaks < 2 or minimum_matched_peaks > 100:
        raise LcmsEngineError("INVALID_PROCESSING_PARAMETER", "minimum_matched_peaks must be between 2 and 100.")

    query = Spectrum(
        mz=np.asarray([item["mz"] for item in scan.fragments], dtype=float),
        intensities=np.asarray([item["intensity"] for item in scan.fragments], dtype=float),
        metadata={"precursor_mz": scan.precursor_mz},
    )
    similarity = CosineGreedy(tolerance=fragment_tolerance)
    matches: list[dict[str, Any]] = []
    for index, reference in enumerate(reference_spectra):
        metadata = reference.metadata
        reference_precursor = metadata.get("precursor_mz")
        if reference_precursor is None:
            continue
        try:
            reference_precursor_value = float(reference_precursor)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(reference_precursor_value) or reference_precursor_value <= 0:
            continue
        precursor_error = mass_error_ppm(scan.precursor_mz, reference_precursor_value)
        if precursor_error > precursor_tolerance:
            continue
        reference_mode = str(metadata.get("ionmode") or metadata.get("polarity") or "").lower()
        if reference_mode:
            if "positive" in reference_mode or reference_mode in {"+", "pos"}:
                if scan.polarity != "positive":
                    continue
            elif "negative" in reference_mode or reference_mode in {"-", "neg"}:
                if scan.polarity != "negative":
                    continue
        score, matched_peaks = similarity.pair(reference, query)
        score_value = float(score)
        matched_count = int(matched_peaks)
        if not math.isfinite(score_value) or score_value < minimum_cosine_score or matched_count < minimum_matched_peaks:
            continue
        matches.append({
            "library_index": index,
            "annotation_name": str(metadata.get("compound_name") or metadata.get("name") or "Unknown compound"),
            "molecular_formula": metadata.get("molecular_formula") or metadata.get("formula"),
            "inchikey": metadata.get("inchikey"),
            "smiles": metadata.get("smiles"),
            "precursor_mz": reference_precursor_value,
            "precursor_mass_error_ppm": round(precursor_error, 8),
            "cosine_score": round(score_value, 8),
            "matched_peaks": matched_count,
            "library_identifier": metadata.get("spectrumid") or metadata.get("id"),
            "library_source": metadata.get("source") or metadata.get("database"),
            "reference_metadata": {
                key: metadata.get(key)
                for key in ("instrument", "instrument_type", "ionmode", "collision_energy", "spectrum_type")
                if metadata.get(key) is not None
            },
        })
    matches.sort(key=lambda item: (
        -item["cosine_score"], -item["matched_peaks"],
        item["precursor_mass_error_ppm"], item["annotation_name"].lower(),
    ))
    return matches[:max_candidates]
