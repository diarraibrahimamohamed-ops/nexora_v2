"""Pooled-QC LOESS signal correction over an aligned LC-MS feature matrix.

Raw measurements are never overwritten. Correction is feature-wise, uses
log-intensity LOESS, requires QC coverage, and does not extrapolate beyond the
observed pooled-QC injection-order range.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Mapping, Sequence


QUANTIFICATION_PROTOCOL_VERSION = "1.0.0"
VALID_QC_TYPES = {"pooled_qc"}
VALID_SAMPLE_TYPES = {"sample"}


class LcmsQuantificationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LcmsQuantificationError("INVALID_QUANTIFICATION_INPUT", f"{label} doit être numérique.") from exc
    if not math.isfinite(number):
        raise LcmsQuantificationError("INVALID_QUANTIFICATION_INPUT", f"{label} doit être fini.")
    return number


def _sample_rsd(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean_value = statistics.mean(values)
    if mean_value <= 0:
        return None
    return statistics.stdev(values) / mean_value * 100.0


def _weighted_local_linear_predict(
    x_train: Sequence[float],
    y_train: Sequence[float],
    x_eval: float,
    *,
    span_fraction: float,
    robust_weights: Sequence[float],
) -> float:
    count = len(x_train)
    neighbors = min(count, max(4, int(math.ceil(span_fraction * count))))
    nearest = sorted(range(count), key=lambda index: (abs(x_train[index] - x_eval), index))[:neighbors]
    bandwidth = max(abs(x_train[index] - x_eval) for index in nearest)
    weighted: list[tuple[float, float, float]] = []
    for index in nearest:
        distance = abs(x_train[index] - x_eval)
        if bandwidth <= 1e-12:
            kernel = 1.0
        else:
            ratio = min(1.0, distance / bandwidth)
            kernel = (1.0 - ratio ** 3) ** 3
        weight = kernel * robust_weights[index]
        if weight > 1e-12:
            weighted.append((x_train[index] - x_eval, y_train[index], weight))
    if not weighted:
        nearest_index = min(range(count), key=lambda index: abs(x_train[index] - x_eval))
        return y_train[nearest_index]

    sum_w = sum(item[2] for item in weighted)
    sum_x = sum(item[0] * item[2] for item in weighted)
    sum_y = sum(item[1] * item[2] for item in weighted)
    sum_xx = sum(item[0] * item[0] * item[2] for item in weighted)
    sum_xy = sum(item[0] * item[1] * item[2] for item in weighted)
    determinant = sum_w * sum_xx - sum_x * sum_x
    if abs(determinant) <= 1e-14:
        return sum_y / sum_w
    intercept = (sum_y * sum_xx - sum_x * sum_xy) / determinant
    return intercept


def _lowess_predict(
    x_train: Sequence[float],
    y_train: Sequence[float],
    x_eval: Sequence[float],
    *,
    span_fraction: float,
    robust_iterations: int,
) -> list[float]:
    if len(x_train) < 4:
        raise LcmsQuantificationError(
            "QUANTIFICATION_INSUFFICIENT_QC",
            "Au moins quatre pooled-QC positifs sont nécessaires au calcul LOESS.",
        )
    if len(x_train) != len(y_train):
        raise LcmsQuantificationError("INVALID_QUANTIFICATION_INPUT", "Les séries QC x/y sont de tailles différentes.")
    robust = [1.0] * len(x_train)
    for _ in range(robust_iterations):
        fitted = [
            _weighted_local_linear_predict(
                x_train, y_train, x_value,
                span_fraction=span_fraction,
                robust_weights=robust,
            )
            for x_value in x_train
        ]
        residuals = [observed - predicted for observed, predicted in zip(y_train, fitted)]
        center = statistics.median(residuals)
        scale = statistics.median(abs(value - center) for value in residuals)
        if scale <= 1e-12:
            break
        cutoff = 6.0 * scale
        updated = []
        for residual in residuals:
            ratio = abs(residual - center) / cutoff
            updated.append((1.0 - ratio * ratio) ** 2 if ratio < 1.0 else 0.0)
        # Keep at least one local fit viable even under an extreme outlier pattern.
        if sum(value > 1e-8 for value in updated) < 4:
            break
        robust = updated

    return [
        _weighted_local_linear_predict(
            x_train, y_train, x_value,
            span_fraction=span_fraction,
            robust_weights=robust,
        )
        for x_value in x_eval
    ]


def quantify_aligned_feature_matrix(
    aligned_features: Sequence[Mapping[str, Any]],
    injections: Sequence[Mapping[str, Any]],
    members: Sequence[Mapping[str, Any]],
    *,
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    """Return an immutable cell matrix plus per-feature QC correction metrics."""
    minimum_qc = int(parameters.get("minimum_qc_injections", 8))
    minimum_span = _finite(parameters.get("minimum_qc_span_ratio", 0.6), "minimum_qc_span_ratio")
    span_fraction = _finite(parameters.get("loess_span_fraction", 0.75), "loess_span_fraction")
    robust_iterations = int(parameters.get("robust_iterations", 2))
    factor_min = _finite(parameters.get("minimum_correction_factor", 0.2), "minimum_correction_factor")
    factor_max = _finite(parameters.get("maximum_correction_factor", 5.0), "maximum_correction_factor")
    max_rsd_worsening = _finite(parameters.get("maximum_qc_rsd_worsening_pct", 0.0), "maximum_qc_rsd_worsening_pct")
    no_correction_threshold = _finite(parameters.get("no_correction_threshold_pct", 2.0), "no_correction_threshold_pct")
    if minimum_qc < 8 or minimum_qc > 500:
        raise LcmsQuantificationError("INVALID_QUANTIFICATION_PARAMETER", "minimum_qc_injections doit être dans [8, 500].")
    if not 0.1 <= minimum_span <= 1.0:
        raise LcmsQuantificationError("INVALID_QUANTIFICATION_PARAMETER", "minimum_qc_span_ratio doit être dans [0.1, 1].")
    if not 0.3 <= span_fraction <= 1.0:
        raise LcmsQuantificationError("INVALID_QUANTIFICATION_PARAMETER", "loess_span_fraction doit être dans [0.3, 1].")
    if robust_iterations < 0 or robust_iterations > 5:
        raise LcmsQuantificationError("INVALID_QUANTIFICATION_PARAMETER", "robust_iterations doit être dans [0, 5].")
    if factor_min <= 0 or factor_max <= factor_min or factor_max > 20:
        raise LcmsQuantificationError("INVALID_QUANTIFICATION_PARAMETER", "Bornes de facteur de correction invalides.")
    if max_rsd_worsening < 0 or max_rsd_worsening > 20:
        raise LcmsQuantificationError("INVALID_QUANTIFICATION_PARAMETER", "maximum_qc_rsd_worsening_pct doit être dans [0, 20].")
    if no_correction_threshold < 0 or no_correction_threshold > 25:
        raise LcmsQuantificationError("INVALID_QUANTIFICATION_PARAMETER", "no_correction_threshold_pct doit être dans [0, 25].")

    injection_rows = sorted(injections, key=lambda row: (int(row["injection_order"]), int(row["injection_id"])))
    selected_types = {str(row["injection_type"]) for row in injection_rows}
    if not injection_rows or not selected_types.issubset(VALID_QC_TYPES | VALID_SAMPLE_TYPES):
        raise LcmsQuantificationError(
            "QUANTIFICATION_INVALID_INJECTIONS",
            "Seules les injections sample et pooled_qc sont admises dans cette matrice.",
        )
    if not any(row["injection_type"] == "pooled_qc" for row in injection_rows):
        raise LcmsQuantificationError("QUANTIFICATION_NO_POOLED_QC", "Aucune injection pooled_qc dans la sélection.")
    order_by_id = {int(row["injection_id"]): int(row["injection_order"]) for row in injection_rows}
    type_by_id = {int(row["injection_id"]): str(row["injection_type"]) for row in injection_rows}
    all_orders = [int(row["injection_order"]) for row in injection_rows]
    sequence_span = max(all_orders) - min(all_orders)
    if sequence_span <= 0:
        raise LcmsQuantificationError("QUANTIFICATION_INVALID_INJECTION_ORDER", "L'ordre d'injection ne couvre aucune séquence.")

    member_by_feature_injection: dict[tuple[int, int], Mapping[str, Any]] = {}
    for member in members:
        feature_id = int(member["aligned_feature_id"])
        injection_id = int(member["injection_id"])
        if injection_id not in order_by_id:
            raise LcmsQuantificationError(
                "QUANTIFICATION_MEMBER_INJECTION_MISMATCH",
                f"L'injection {injection_id} du membre n'appartient pas à la sélection.",
            )
        key = (feature_id, injection_id)
        if key in member_by_feature_injection:
            raise LcmsQuantificationError(
                "QUANTIFICATION_DUPLICATE_MEMBER",
                f"Plusieurs membres de la feature {feature_id} existent dans l'injection {injection_id}.",
            )
        member_by_feature_injection[key] = member

    cells: list[dict[str, Any]] = []
    feature_summaries: list[dict[str, Any]] = []
    for feature in sorted(aligned_features, key=lambda row: int(row["aligned_feature_id"])):
        aligned_id = int(feature["aligned_feature_id"])
        observed: dict[int, float | None] = {}
        for injection in injection_rows:
            injection_id = int(injection["injection_id"])
            member = member_by_feature_injection.get((aligned_id, injection_id))
            if member is None or member.get("raw_intensity") is None:
                observed[injection_id] = None
            else:
                value = _finite(member["raw_intensity"], "raw_intensity")
                if value < 0:
                    raise LcmsQuantificationError("INVALID_QUANTIFICATION_INPUT", "L'intensité brute ne peut pas être négative.")
                observed[injection_id] = value if value > 0 else None

        qc_injections = [
            row for row in injection_rows
            if row["injection_type"] == "pooled_qc"
            and observed[int(row["injection_id"])] is not None
        ]
        qc_values = [float(observed[int(row["injection_id"])]) for row in qc_injections]
        qc_orders = [int(row["injection_order"]) for row in qc_injections]
        rsd_before = _sample_rsd(qc_values)
        qc_span_ratio = (
            (max(qc_orders) - min(qc_orders)) / sequence_span
            if len(qc_orders) >= 2 else 0.0
        )

        feature_status: str | None = None
        correction_by_injection: dict[int, float] = {}
        corrected_qc_rsd: float | None = None
        qc_log_center: float | None = None
        if len(qc_injections) < minimum_qc:
            feature_status = "insufficient_qc"
        elif qc_span_ratio < minimum_span:
            feature_status = "insufficient_qc_span"
        elif rsd_before is None:
            feature_status = "insufficient_qc"
        else:
            x_train = [float(order) for order in qc_orders]
            y_train = [math.log(value) for value in qc_values]
            eval_injections = [
                row for row in injection_rows
                if min(qc_orders) <= int(row["injection_order"]) <= max(qc_orders)
            ]
            x_eval = [float(row["injection_order"]) for row in eval_injections]
            predicted = _lowess_predict(
                x_train, y_train, x_eval,
                span_fraction=span_fraction,
                robust_iterations=robust_iterations,
            )
            qc_log_center = statistics.median(y_train)
            for injection, predicted_log_signal in zip(eval_injections, predicted):
                injection_id = int(injection["injection_id"])
                factor = math.exp(qc_log_center - predicted_log_signal)
                correction_by_injection[injection_id] = factor

            qc_factors = [
                correction_by_injection[int(row["injection_id"])]
                for row in qc_injections
            ]
            if any(factor < factor_min or factor > factor_max for factor in qc_factors):
                feature_status = "correction_factor_out_of_bounds"
            else:
                corrected_qc = [
                    value * correction_by_injection[int(row["injection_id"])]
                    for row, value in zip(qc_injections, qc_values)
                ]
                corrected_qc_rsd = _sample_rsd(corrected_qc)
                if (
                    corrected_qc_rsd is None
                    or corrected_qc_rsd > rsd_before * (1.0 + max_rsd_worsening / 100.0) + 1e-9
                ):
                    feature_status = "correction_not_improving_qc_rsd"
                else:
                    max_factor_delta_pct = max(
                        abs(factor - 1.0) * 100.0 for factor in qc_factors
                    )
                    if max_factor_delta_pct <= no_correction_threshold:
                        feature_status = "no_correction_needed"
                    else:
                        feature_status = "corrected"

        for injection in injection_rows:
            injection_id = int(injection["injection_id"])
            raw = observed[injection_id]
            base_cell = {
                "aligned_feature_id": aligned_id,
                "injection_id": injection_id,
                "processing_run_id": (
                    int(member_by_feature_injection[(aligned_id, injection_id)]["processing_run_id"])
                    if (aligned_id, injection_id) in member_by_feature_injection else None
                ),
                "feature_id": (
                    int(member_by_feature_injection[(aligned_id, injection_id)]["feature_id"])
                    if (aligned_id, injection_id) in member_by_feature_injection else None
                ),
                "qc_type": type_by_id[injection_id],
                "raw_intensity": raw,
                "corrected_intensity": None,
                "correction_factor": None,
                "normalization_status": "not_detected" if raw is None else str(feature_status),
                "qc_rsd_before_pct": rsd_before,
                "qc_rsd_after_pct": corrected_qc_rsd,
                "quality_metrics": {
                    "qc_observation_count": len(qc_injections),
                    "qc_span_ratio": qc_span_ratio,
                    "qc_injection_orders": qc_orders,
                    "loess_span_fraction": span_fraction,
                    "robust_iterations": robust_iterations,
                },
            }
            if raw is not None and feature_status in {"corrected", "no_correction_needed"}:
                if injection_id not in correction_by_injection:
                    base_cell["normalization_status"] = "outside_qc_range"
                else:
                    factor = correction_by_injection[injection_id]
                    if factor < factor_min or factor > factor_max:
                        base_cell["normalization_status"] = "correction_factor_out_of_bounds"
                        base_cell["quality_metrics"]["proposed_correction_factor"] = factor
                    elif feature_status == "no_correction_needed":
                        base_cell["normalization_status"] = "no_correction_needed"
                        base_cell["corrected_intensity"] = raw
                        base_cell["correction_factor"] = 1.0
                    else:
                        base_cell["normalization_status"] = "corrected"
                        base_cell["corrected_intensity"] = raw * factor
                        base_cell["correction_factor"] = factor
            elif raw is not None:
                base_cell["normalization_status"] = str(feature_status)
            cells.append(base_cell)

        feature_summaries.append({
            "aligned_feature_id": aligned_id,
            "polarity": feature["polarity"],
            "qc_observation_count": len(qc_injections),
            "qc_span_ratio": qc_span_ratio,
            "qc_rsd_before_pct": rsd_before,
            "qc_rsd_after_pct": corrected_qc_rsd,
            "status": feature_status or "insufficient_qc",
            "corrected_cell_count": sum(
                1 for cell in cells
                if cell["aligned_feature_id"] == aligned_id
                and cell["normalization_status"] in {"corrected", "no_correction_needed"}
            ),
        })

    status_counts = defaultdict(int)
    for cell in cells:
        status_counts[cell["normalization_status"]] += 1
    return {
        "protocol_version": QUANTIFICATION_PROTOCOL_VERSION,
        "method": "pooled_qc_robust_loess_log_intensity",
        "parameters": {
            "minimum_qc_injections": minimum_qc,
            "minimum_qc_span_ratio": minimum_span,
            "loess_span_fraction": span_fraction,
            "robust_iterations": robust_iterations,
            "minimum_correction_factor": factor_min,
            "maximum_correction_factor": factor_max,
            "maximum_qc_rsd_worsening_pct": max_rsd_worsening,
            "no_correction_threshold_pct": no_correction_threshold,
        },
        "feature_count": len(aligned_features),
        "injection_count": len(injection_rows),
        "cell_count": len(cells),
        "status_counts": dict(sorted(status_counts.items())),
        "feature_summaries": feature_summaries,
        "cells": cells,
        "limitations": [
            "QC-RLSC is feature-wise and based only on explicitly detected pooled-QC intensities.",
            "No zero imputation is performed; non-detected values remain missing.",
            "No extrapolation is performed outside the pooled-QC injection-order range.",
            "Features with insufficient pooled-QC observations or sequence coverage remain uncorrected.",
            "This phase does not apply sample-wise total-signal, median, probabilistic quotient or batch-effect normalization.",
            "Raw intensities and raw feature observations remain unchanged.",
        ],
    }
