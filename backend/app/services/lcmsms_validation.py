"""Deterministic batch-level LC-MS/MS QC assessment.

The service evaluates only explicitly recorded QC observations. It does not
align features between independent mzML processing runs or infer missing data.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any, Mapping, Sequence

from app.services.scientific_provenance import sha256_json

VALIDATION_PROTOCOL_VERSION = "1.0.0"
SUPPORTED_QC_TYPES = {
    "pooled_qc", "solvent_blank", "extraction_blank", "internal_standard",
    "system_suitability", "carryover", "mass_accuracy", "retention_time_drift",
    "calibration", "other",
}
ALLOWED_METRIC_LIMIT_KEYS = {
    "min", "max", "max_abs", "max_rsd_pct",
    "max_blank_to_pooled_qc_pct",
    "max_abs_drift_pct_per_10_injections",
}


class LcmsValidationError(ValueError):
    """Raised when the validation protocol or its evidence is malformed."""


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _limits_for(metric_name: str, metric_limits: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    raw = metric_limits.get(metric_name, {})
    if not isinstance(raw, Mapping):
        raise LcmsValidationError(f"Les limites de « {metric_name} » doivent être un objet.")
    unknown = set(raw) - ALLOWED_METRIC_LIMIT_KEYS
    if unknown:
        raise LcmsValidationError(
            f"Limites non reconnues pour « {metric_name} » : {', '.join(sorted(unknown))}."
        )
    result: dict[str, float] = {}
    for key, value in raw.items():
        number = _finite_number(value)
        nonnegative_keys = {
            "max_abs", "max_rsd_pct", "max_blank_to_pooled_qc_pct",
            "max_abs_drift_pct_per_10_injections",
        }
        if number is None or (key in nonnegative_keys and number < 0):
            raise LcmsValidationError(f"La limite « {key} » de « {metric_name} » doit être finie et non négative.")
        result[key] = number
    if "min" in result and "max" in result and result["min"] > result["max"]:
        raise LcmsValidationError(f"La limite minimale dépasse la limite maximale pour « {metric_name} ».")
    return result


def _linear_drift(values: Sequence[tuple[int, float]]) -> dict[str, float] | None:
    """OLS trend over injection order, expressed as % of mean per 10 injections."""
    if len(values) < 3:
        return None
    xs = [float(item[0]) for item in values]
    ys = [float(item[1]) for item in values]
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    if mean_y <= 0:
        return None
    ss_x = sum((x - mean_x) ** 2 for x in xs)
    if ss_x <= 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / ss_x
    ss_y = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (mean_y + slope * (x - mean_x))) ** 2 for x, y in zip(xs, ys))
    r_squared = 1.0 if ss_y == 0 else max(0.0, min(1.0, 1.0 - ss_res / ss_y))
    return {
        "slope_per_injection": slope,
        "drift_pct_per_10_injections": slope * 10.0 / mean_y * 100.0,
        "r_squared": r_squared,
    }


def _status_for_limits(
    value: float,
    limits: Mapping[str, float],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if "min" in limits and value < limits["min"]:
        reasons.append(f"value_below_min:{limits['min']}")
    if "max" in limits and value > limits["max"]:
        reasons.append(f"value_above_max:{limits['max']}")
    if "max_abs" in limits and abs(value) > limits["max_abs"]:
        reasons.append(f"absolute_value_above_max:{limits['max_abs']}")
    return ("fail", reasons) if reasons else ("pass", ["within_configured_limits"])


def assess_lcms_batch_quality(
    observations: Sequence[Any],
    *,
    injection_orders: Mapping[int, int],
    criteria: Mapping[str, Any],
) -> dict[str, Any]:
    """Create a deterministic, evidence-linked QC assessment for one batch.

    The 30% pooled-QC RSD default is a configurable screening threshold, not a
    universal analytical acceptance standard. Method-specific limits should
    override it where validated.
    """
    min_qc = int(criteria.get("min_pooled_qc_injections", 3))
    max_rsd = _finite_number(criteria.get("max_pooled_qc_rsd_pct", 30.0))
    if min_qc < 3 or min_qc > 500:
        raise LcmsValidationError("min_pooled_qc_injections doit être compris entre 3 et 500.")
    if max_rsd is None or max_rsd <= 0 or max_rsd > 500:
        raise LcmsValidationError("max_pooled_qc_rsd_pct doit être strictement positif et ≤ 500.")
    raw_limits = criteria.get("metric_limits", {})
    if not isinstance(raw_limits, Mapping):
        raise LcmsValidationError("metric_limits doit être un objet indexé par nom de métrique.")
    metric_limits = {
        str(name): _limits_for(str(name), raw_limits)
        for name in sorted(raw_limits)
    }

    normalized: list[dict[str, Any]] = []
    source_ids: list[int] = []
    null_value_count = 0
    unknown_injection_count = 0
    for row in observations:
        qc_type = str(getattr(row, "qc_type", ""))
        metric_name = str(getattr(row, "metric_name", "")).strip()
        injection_id = getattr(row, "injection_id", None)
        metric_value = _finite_number(getattr(row, "metric_value", None))
        if not metric_name or qc_type not in SUPPORTED_QC_TYPES:
            continue
        row_id = int(getattr(row, "id"))
        source_ids.append(row_id)
        if metric_value is None:
            null_value_count += 1
        if injection_id is not None and int(injection_id) not in injection_orders:
            unknown_injection_count += 1
        normalized.append({
            "id": row_id,
            "qc_type": qc_type,
            "metric_name": metric_name,
            "metric_value": metric_value,
            "metric_unit": str(getattr(row, "metric_unit", "") or ""),
            "injection_id": int(injection_id) if injection_id is not None else None,
            "status": str(getattr(row, "status", "not_assessed")),
            "assessment_method": str(getattr(row, "assessment_method", "manual")),
            "acceptance_criteria": getattr(row, "acceptance_criteria", None) or {},
        })
    normalized.sort(key=lambda x: x["id"])

    metrics: list[dict[str, Any]] = []
    pooled_injection_ids = sorted({
        item["injection_id"] for item in normalized
        if item["qc_type"] == "pooled_qc" and item["injection_id"] is not None
    })
    blank_injection_ids = sorted({
        item["injection_id"] for item in normalized
        if item["qc_type"] in {"solvent_blank", "extraction_blank"}
        and item["injection_id"] is not None
    })

    # Per-metric pooled-QC repeatability. Duplicates within one injection are
    # excluded rather than silently averaged.
    repeat_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in normalized:
        if item["qc_type"] in {"pooled_qc", "internal_standard"} and item["metric_value"] is not None:
            repeat_groups[(item["qc_type"], item["metric_name"], item["metric_unit"])].append(item)

    for (qc_type, metric_name, unit), rows in sorted(repeat_groups.items()):
        by_injection: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for item in rows:
            if item["injection_id"] is not None:
                by_injection[item["injection_id"]].append(item)
        duplicate_injections = sorted(key for key, value in by_injection.items() if len(value) != 1)
        usable = [
            values[0] for key, values in by_injection.items()
            if len(values) == 1 and key in injection_orders
        ]
        values = [float(item["metric_value"]) for item in usable]
        metric: dict[str, Any] = {
            "metric_type": "repeatability",
            "qc_type": qc_type,
            "metric_name": metric_name,
            "unit": unit,
            "n": len(values),
            "source_observation_ids": sorted(item["id"] for item in usable),
            "duplicate_injection_ids": duplicate_injections,
        }
        if len(values) < min_qc:
            metric.update({
                "status": "insufficient_data",
                "reason": f"minimum_{min_qc}_distinct_qc_injections_required",
            })
            if configured_drift_limit := metric_limits.get(metric_name, {}).get("max_abs_drift_pct_per_10_injections"):
                metric["drift_status"] = "insufficient_data"
                metric["drift_threshold_pct_per_10_injections"] = configured_drift_limit
        elif statistics.mean(values) <= 0:
            metric.update({
                "status": "insufficient_data",
                "reason": "non_positive_mean_prevents_relative_standard_deviation",
            })
        else:
            mean_value = statistics.mean(values)
            sd_value = statistics.stdev(values)
            rsd_pct = sd_value / mean_value * 100.0
            configured = metric_limits.get(metric_name, {})
            threshold = configured.get("max_rsd_pct")
            if threshold is None and qc_type == "pooled_qc":
                threshold = float(max_rsd)
            metric.update({
                "mean": mean_value,
                "sample_standard_deviation": sd_value,
                "rsd_pct": rsd_pct,
                "threshold_rsd_pct": threshold,
            })
            if threshold is None:
                metric.update({
                    "status": "not_assessed",
                    "reason": "no_method_specific_rsd_limit_configured",
                })
            else:
                metric.update({
                    "status": "pass" if rsd_pct <= threshold else "fail",
                    "reason": "within_configured_rsd_limit" if rsd_pct <= threshold else "rsd_exceeds_configured_limit",
                })
            ordered = sorted(
                (injection_orders[item["injection_id"]], float(item["metric_value"]))
                for item in usable
            )
            drift = _linear_drift(ordered)
            drift_limit = configured.get("max_abs_drift_pct_per_10_injections")
            if drift is not None:
                metric["drift"] = drift
                if drift_limit is not None:
                    drift_status = abs(drift["drift_pct_per_10_injections"]) <= drift_limit
                    metric["drift_status"] = "pass" if drift_status else "fail"
                    metric["drift_threshold_pct_per_10_injections"] = drift_limit
                    if not drift_status:
                        metric["status"] = "fail"
                        metric["reason"] = "drift_exceeds_configured_limit"
            elif drift_limit is not None:
                metric["drift_status"] = "insufficient_data"
                metric["drift_reason"] = "at_least_three_distinct_injection_orders_required"
                if metric["status"] == "pass":
                    metric["status"] = "insufficient_data"
        metrics.append(metric)

    # Blank-to-pooled-QC ratio is computed only where a method-specific limit
    # has been supplied; otherwise the metric remains transparently unassessed.
    values_by_type_name: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for item in normalized:
        if item["metric_value"] is not None:
            values_by_type_name[(item["qc_type"], item["metric_name"], item["metric_unit"])].append(
                float(item["metric_value"])
            )
    blank_names = sorted({
        (name, unit)
        for (qc_type, name, unit) in values_by_type_name
        if qc_type in {"solvent_blank", "extraction_blank"}
    })
    for metric_name, unit in blank_names:
        pooled_values = values_by_type_name.get(("pooled_qc", metric_name, unit), [])
        blank_values = (
            values_by_type_name.get(("solvent_blank", metric_name, unit), [])
            + values_by_type_name.get(("extraction_blank", metric_name, unit), [])
        )
        configured = metric_limits.get(metric_name, {})
        threshold = configured.get("max_blank_to_pooled_qc_pct")
        metric: dict[str, Any] = {
            "metric_type": "blank_contamination",
            "metric_name": metric_name,
            "unit": unit,
            "blank_observation_count": len(blank_values),
            "pooled_qc_observation_count": len(pooled_values),
            "threshold_pct": threshold,
        }
        if threshold is None:
            metric.update({"status": "not_assessed", "reason": "no_blank_ratio_limit_configured"})
        elif not pooled_values or statistics.median(pooled_values) <= 0:
            metric.update({"status": "insufficient_data", "reason": "positive_pooled_qc_reference_required"})
        else:
            ratio = max(abs(value) for value in blank_values) / statistics.median(pooled_values) * 100.0
            metric["max_blank_to_pooled_qc_pct"] = ratio
            metric["status"] = "pass" if ratio <= threshold else "fail"
            metric["reason"] = "within_configured_blank_limit" if ratio <= threshold else "blank_signal_exceeds_configured_limit"
        metrics.append(metric)

    # Generic configured limits for mass accuracy, RT drift, carryover and
    # system-suitability observations. No universal instrument limit is assumed.
    measurement_qc_types = {
        "mass_accuracy", "retention_time_drift", "carryover",
        "system_suitability", "calibration",
    }
    units_by_metric: dict[str, set[str]] = defaultdict(set)
    for item in normalized:
        if item["qc_type"] in measurement_qc_types and item["metric_value"] is not None:
            units_by_metric[item["metric_name"]].add(item["metric_unit"].strip())

    for item in normalized:
        if item["qc_type"] not in measurement_qc_types or item["metric_value"] is None:
            continue
        limits = metric_limits.get(item["metric_name"], {})
        simple_limits = {key: limits[key] for key in ("min", "max", "max_abs") if key in limits}
        metric = {
            "metric_type": "configured_measurement",
            "qc_type": item["qc_type"],
            "metric_name": item["metric_name"],
            "value": item["metric_value"],
            "unit": item["metric_unit"],
            "source_observation_ids": [item["id"]],
        }
        observed_units = units_by_metric[item["metric_name"]]
        if "" in observed_units:
            metric.update({
                "status": "not_assessed",
                "reason": "metric_unit_missing; explicit comparable units are required",
            })
        elif len(observed_units) > 1:
            metric.update({
                "status": "not_assessed",
                "reason": "mixed_metric_units; automatic unit conversion is not performed",
                "observed_units": sorted(observed_units),
            })
        elif not simple_limits:
            metric.update({
                "status": "not_assessed",
                "reason": "no_method_specific_measurement_limits_configured",
            })
        else:
            status, reasons = _status_for_limits(item["metric_value"], simple_limits)
            metric.update({"status": status, "limits": simple_limits, "reasons": reasons})
        metrics.append(metric)

    # Preserve manually assessed observations as immutable findings.
    for item in normalized:
        if item["status"] in {"warning", "fail"}:
            metrics.append({
                "metric_type": "recorded_qc_assessment",
                "qc_type": item["qc_type"],
                "metric_name": item["metric_name"],
                "value": item["metric_value"],
                "unit": item["metric_unit"],
                "status": item["status"],
                "source_observation_ids": [item["id"]],
                "reason": "pre_existing_manual_or_imported_assessment",
            })

    statuses = [item["status"] for item in metrics]
    if "fail" in statuses:
        overall = "fail"
    elif "warning" in statuses:
        overall = "warning"
    elif ("insufficient_data" in statuses or "not_assessed" in statuses) and "pass" in statuses:
        overall = "warning"
    elif "insufficient_data" in statuses or "not_assessed" in statuses:
        overall = "insufficient_data"
    elif "pass" in statuses:
        overall = "pass"
    else:
        overall = "insufficient_data"

    return {
        "protocol_version": VALIDATION_PROTOCOL_VERSION,
        "status": overall,
        "criteria": {
            "min_pooled_qc_injections": min_qc,
            "max_pooled_qc_rsd_pct": float(max_rsd),
            "metric_limits": metric_limits,
        },
        "coverage": {
            "quality_control_observation_count": len(normalized),
            "non_numeric_observation_count": null_value_count,
            "unknown_injection_reference_count": unknown_injection_count,
            "pooled_qc_injection_count": len(pooled_injection_ids),
            "blank_injection_count": len(blank_injection_ids),
            "source_observation_ids": source_ids,
        },
        "metrics": metrics,
        "limitations": [
            "No feature alignment is performed between separate mzML processing runs.",
            "Only explicitly recorded QC observations are assessed.",
            "No missing-feature rate can be calculated without a declared aligned feature universe.",
            "The configured RSD limit is method-specific; the default is a screening threshold, not a universal pass criterion.",
            "Blank ratios and instrument-specific mass/retention limits are assessed only when method-specific criteria are supplied.",
        ],
    }


def build_validation_report_payload(
    assessment: Mapping[str, Any],
    *,
    batch_id: int,
    study_id: int,
    processing_runs: Sequence[Any],
    observations: Sequence[Any],
    injection_orders: Mapping[int, int],
    created_at: datetime,
) -> dict[str, Any]:
    run_rows = sorted(processing_runs, key=lambda row: int(row.id))
    observation_ids = sorted(int(row.id) for row in observations)
    observation_snapshot = [
        {
            "id": int(row.id),
            "qc_type": str(row.qc_type),
            "metric_name": str(row.metric_name),
            "metric_value": _finite_number(row.metric_value),
            "metric_unit": row.metric_unit,
            "injection_id": int(row.injection_id) if row.injection_id is not None else None,
            "status": row.status,
            "assessment_method": row.assessment_method,
            "acceptance_criteria": row.acceptance_criteria or {},
        }
        for row in sorted(observations, key=lambda item: int(item.id))
    ]
    injection_order_snapshot = {
        str(key): int(value) for key, value in sorted(injection_orders.items())
    }
    payload = {
        "batch_id": int(batch_id),
        "study_id": int(study_id),
        "created_at": created_at.isoformat(),
        "assessment": dict(assessment),
        "source_injection_orders": injection_order_snapshot,
        "source_quality_control_observations": observation_snapshot,
        "source_processing_runs": [
            {
                "processing_run_id": int(row.id),
                "input_sha256": row.input_sha256,
                "output_sha256": row.output_sha256,
                "status": row.status,
            }
            for row in run_rows
        ],
        "source_quality_control_observation_ids": observation_ids,
    }
    payload["input_sha256"] = sha256_json({
        "batch_id": payload["batch_id"],
        "source_processing_runs": payload["source_processing_runs"],
        "source_quality_control_observation_ids": observation_ids,
        "source_quality_control_observations": observation_snapshot,
        "source_injection_orders": injection_order_snapshot,
        "criteria": payload["assessment"]["criteria"],
    })
    payload["report_sha256"] = sha256_json({
        "input_sha256": payload["input_sha256"],
        "assessment": payload["assessment"],
        "source_processing_runs": payload["source_processing_runs"],
        "source_quality_control_observation_ids": observation_ids,
    })
    return payload
