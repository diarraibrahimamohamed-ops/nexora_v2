"""Pooled-QC robust LOESS normalization unit tests."""
import math

from app.services.lcmsms_quantification import quantify_aligned_feature_matrix


def batch_inputs(*, constant=False, outside=False):
    injections = []
    members = []
    for order in range(1, 13 + int(outside)):
        injection_type = "pooled_qc" if order in {1, 2, 4, 5, 7, 8, 10, 12} else "sample"
        if outside and order == 13:
            injection_type = "sample"
        injections.append({
            "injection_id": order,
            "injection_order": order,
            "injection_type": injection_type,
        })
        if order == 9 or (outside and order == 13):
            continue
        baseline = 100.0
        drift = 1.0 if constant else math.exp(-0.075 * (order - 1))
        members.append({
            "aligned_feature_id": 501,
            "injection_id": order,
            "processing_run_id": 200 + order,
            "feature_id": 900 + order,
            "raw_intensity": baseline * drift,
        })
    if not outside:
        injections = injections[:12]
    return (
        [{"aligned_feature_id": 501, "polarity": "positive"}],
        injections,
        members,
    )


def quant_params():
    return {
        "minimum_qc_injections": 8,
        "minimum_qc_span_ratio": 0.6,
        "loess_span_fraction": 0.75,
        "robust_iterations": 2,
        "minimum_correction_factor": 0.2,
        "maximum_correction_factor": 5.0,
        "maximum_qc_rsd_worsening_pct": 0.0,
        "no_correction_threshold_pct": 2.0,
    }


def test_qc_rlsc_corrects_log_linear_drift_and_keeps_raw_values():
    features, injections, members = batch_inputs()
    original_raw = [row["raw_intensity"] for row in members]
    result = quantify_aligned_feature_matrix(
        features, injections, members, parameters=quant_params()
    )
    summary = result["feature_summaries"][0]
    corrected = [
        cell for cell in result["cells"]
        if cell["normalization_status"] == "corrected"
    ]
    assert summary["status"] == "corrected"
    assert summary["qc_rsd_after_pct"] < summary["qc_rsd_before_pct"]
    assert corrected
    assert all(cell["corrected_intensity"] is not None for cell in corrected)
    assert [row["raw_intensity"] for row in members] == original_raw
    assert result["method"] == "pooled_qc_robust_loess_log_intensity"


def test_constant_qc_signal_does_not_receive_unnecessary_correction():
    features, injections, members = batch_inputs(constant=True)
    result = quantify_aligned_feature_matrix(
        features, injections, members, parameters=quant_params()
    )
    summary = result["feature_summaries"][0]
    assert summary["status"] == "no_correction_needed"
    assert all(
        cell["correction_factor"] == 1.0
        for cell in result["cells"]
        if cell["raw_intensity"] is not None
    )


def test_non_detected_features_are_missing_not_zero_imputed():
    features, injections, members = batch_inputs()
    members = [
        row for row in members
        if row["injection_id"] != 3
    ]
    result = quantify_aligned_feature_matrix(
        features, injections, members, parameters=quant_params()
    )
    missing = next(cell for cell in result["cells"] if cell["injection_id"] == 3)
    assert missing["normalization_status"] == "not_detected"
    assert missing["raw_intensity"] is None
    assert missing["corrected_intensity"] is None


def test_insufficient_qc_coverage_is_not_normalized():
    features, injections, members = batch_inputs()
    members = [
        row for row in members
        if row["injection_id"] not in {1, 2}
    ]
    result = quantify_aligned_feature_matrix(
        features, injections, members, parameters=quant_params()
    )
    summary = result["feature_summaries"][0]
    assert summary["status"] == "insufficient_qc"
    assert all(
        cell["corrected_intensity"] is None
        for cell in result["cells"]
        if cell["raw_intensity"] is not None
    )


def test_no_loess_extrapolation_past_last_pooled_qc():
    features, injections, members = batch_inputs(outside=True)
    # No measurement for the last sample; add a genuine detected value outside QC range.
    members.append({
        "aligned_feature_id": 501,
        "injection_id": 13,
        "processing_run_id": 213,
        "feature_id": 913,
        "raw_intensity": 42.0,
    })
    result = quantify_aligned_feature_matrix(
        features, injections, members, parameters=quant_params()
    )
    outside_cell = next(cell for cell in result["cells"] if cell["injection_id"] == 13)
    assert outside_cell["normalization_status"] == "outside_qc_range"
    assert outside_cell["corrected_intensity"] is None
