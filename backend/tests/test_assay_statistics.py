"""Deterministic tests for N3XORA's biological-replicate-aware 4PL statistics."""
from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

from app.services.assay_statistics import (
    AssayStatisticsError,
    fit_dose_response_4pl,
    prepare_biological_profiles,
    spearman_rank_comparison,
)


def _curve(concentration, midpoint=3.2, hill=1.15, bottom=1.5, top=98.0):
    return bottom + (top - bottom) / (1.0 + (concentration / midpoint) ** hill)


def _synthetic_measurements(*, include_technical=True, extra_sample=False):
    rows = []
    concentrations = [0.01, 0.1, 0.3, 1.0, 3.0, 10.0, 100.0]
    for replicate_index, replicate_id in enumerate(("bio-A", "bio-B", "bio-C")):
        biological_shift = (-0.8, 0.0, 0.8)[replicate_index]
        for concentration in concentrations:
            response = _curve(concentration) + biological_shift
            rows.append(SimpleNamespace(
                id=len(rows) + 1,
                sample_role="test_sample",
                sample_label="Compound A",
                replicate_id=replicate_id,
                replicate_kind="biological",
                biological_sample_id=None,
                concentration_value=concentration,
                concentration_unit="uM",
                response_name="growth_inhibition",
                response_value=response,
                response_unit="% inhibition",
                qualifier="exact",
                timepoint_value=48.0,
                timepoint_unit="h",
                well_id=None,
                record_key=None,
                metadata_json={},
            ))
            if include_technical:
                for offset in (-0.25, 0.25):
                    rows.append(SimpleNamespace(
                        id=len(rows) + 1,
                        sample_role="test_sample",
                        sample_label="Compound A",
                        replicate_id=f"well-{replicate_id}-{concentration}-{offset}",
                        replicate_kind="technical",
                        biological_sample_id=replicate_id,
                        concentration_value=concentration,
                        concentration_unit="uM",
                        response_name="growth_inhibition",
                        response_value=response + offset,
                        response_unit="% inhibition",
                        qualifier="exact",
                        timepoint_value=48.0,
                        timepoint_unit="h",
                        well_id=f"{replicate_id}-{concentration}-{offset}",
                        record_key=None,
                        metadata_json={},
                    ))
    rows.append(SimpleNamespace(
        id=len(rows) + 1,
        sample_role="test_sample",
        sample_label="Compound A",
        replicate_id="independent-run-1",
        replicate_kind="independent_run",
        biological_sample_id=None,
        concentration_value=1.0,
        concentration_unit="uM",
        response_name="growth_inhibition",
        response_value=55.0,
        response_unit="% inhibition",
        qualifier="exact",
        timepoint_value=48.0,
        timepoint_unit="h",
        well_id=None,
        record_key=None,
        metadata_json={},
    ))
    if extra_sample:
        for replicate_id in ("extra-A", "extra-B", "extra-C"):
            for concentration in concentrations:
                rows.append(SimpleNamespace(
                    id=len(rows) + 1,
                    sample_role="test_sample",
                    sample_label="Compound B",
                    replicate_id=replicate_id,
                    replicate_kind="biological",
                    biological_sample_id=None,
                    concentration_value=concentration,
                    concentration_unit="uM",
                    response_name="growth_inhibition",
                    response_value=_curve(concentration, midpoint=12.0),
                    response_unit="% inhibition",
                    qualifier="exact",
                    timepoint_value=48.0,
                    timepoint_unit="h",
                    well_id=None,
                    record_key=None,
                    metadata_json={},
                ))
    return rows


def test_4pl_recovers_relative_midpoint_and_cluster_bootstrap_ci():
    measurements = _synthetic_measurements()
    result = fit_dose_response_4pl(
        measurements,
        response_name="growth_inhibition",
        response_unit="% inhibition",
        concentration_unit="uM",
        response_direction="decreasing",
        endpoint_name="IC50",
        sample_label="Compound A",
        timepoint_value=48.0,
        timepoint_unit="h",
        bootstrap_iterations=500,
        random_seed=11,
    )
    assert result["analysis_type"] == "dose_response_4pl"
    assert result["fit"]["midpoint_concentration"] == pytest.approx(3.2, rel=0.10)
    assert result["confidence_interval"]["lower"] < result["fit"]["midpoint_concentration"]
    assert result["confidence_interval"]["upper"] > result["fit"]["midpoint_concentration"]
    assert result["sample"]["n_biological_replicates"] == 3
    assert result["sample"]["n_technical_measurements_used"] == 42
    assert result["sample"]["n_independent_run_rows_not_used"] == 1
    assert result["quality"]["status"] == "interpolated"
    assert result["model"]["endpoint_semantics"].startswith("The reported concentration is the relative midpoint")


def test_4pl_requires_explicit_sample_label_when_multiple_compounds_are_present():
    with pytest.raises(AssayStatisticsError, match="Plusieurs échantillons"):
        fit_dose_response_4pl(
            _synthetic_measurements(extra_sample=True),
            response_name="growth_inhibition",
            response_unit="% inhibition",
            concentration_unit="uM",
            response_direction="decreasing",
            endpoint_name="IC50",
            timepoint_value=48.0,
            timepoint_unit="h",
            bootstrap_iterations=500,
        )


def test_4pl_rejects_censored_measurements():
    measurements = _synthetic_measurements(include_technical=False)
    measurements[0].qualifier = "below"
    with pytest.raises(AssayStatisticsError, match="censurées"):
        fit_dose_response_4pl(
            measurements,
            response_name="growth_inhibition",
            response_unit="% inhibition",
            concentration_unit="uM",
            response_direction="decreasing",
            endpoint_name="IC50",
            sample_label="Compound A",
            timepoint_value=48.0,
            timepoint_unit="h",
            bootstrap_iterations=500,
        )


def test_4pl_rejects_unlinked_technical_replicates():
    measurements = _synthetic_measurements()
    technical = next(row for row in measurements if row.replicate_kind == "technical")
    technical.biological_sample_id = None
    with pytest.raises(AssayStatisticsError, match="biological_sample_id"):
        prepare_biological_profiles(
            measurements,
            response_name="growth_inhibition",
            response_unit="% inhibition",
            concentration_unit="uM",
            sample_label="Compound A",
            timepoint_value=48.0,
            timepoint_unit="h",
        )


def test_4pl_requires_minimum_number_of_biological_replicates():
    measurements = _synthetic_measurements(include_technical=False)
    measurements = [
        row for row in measurements
        if row.replicate_id != "bio-C" and row.replicate_id != "independent-run-1"
    ]
    with pytest.raises(AssayStatisticsError, match="Au moins 3 réplicats biologiques"):
        prepare_biological_profiles(
            measurements,
            response_name="growth_inhibition",
            response_unit="% inhibition",
            concentration_unit="uM",
            sample_label="Compound A",
            timepoint_value=48.0,
            timepoint_unit="h",
        )


def test_spearman_rank_comparison_uses_exact_permutation_for_five_pairs():
    # Both lower Vina scores and lower IC50 midpoints are the preferred ranks.
    result = spearman_rank_comparison(
        [-10.0, -9.0, -8.0, -7.0, -6.0],
        np.log10([0.5, 1.0, 2.0, 4.0, 8.0]),
        random_seed=7,
    )
    assert result["rho"] == pytest.approx(1.0)
    assert result["permutation_method"] == "exact_all_pairings"
    assert result["permutation_resamples"] == 120
    assert 0.0 < result["permutation_p_value_two_sided"] <= 1.0
    assert result["bootstrap_ci_95"] is not None
    assert result["n_compounds"] == 5


def test_spearman_rejects_small_or_constant_comparisons():
    with pytest.raises(AssayStatisticsError, match="cinq paires"):
        spearman_rank_comparison([-8, -7, -6, -5], [1, 2, 3, 4])
    with pytest.raises(AssayStatisticsError, match="constant"):
        spearman_rank_comparison([-8, -8, -8, -8, -8], [1, 2, 3, 4, 5])
