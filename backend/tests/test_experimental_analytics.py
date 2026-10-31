from types import SimpleNamespace

import numpy as np
import pytest

from app.services.experimental_analytics import (
    TabularImportError, fit_dose_response, parse_measurement_table,
)


def _measurement(role, replicate, concentration, response, *, kind="biological",
                 concentration_unit="uM", response_unit="% inhibition", qualifier="exact"):
    return SimpleNamespace(
        id=None, sample_role=role, sample_label="Compound A", replicate_id=replicate,
        replicate_kind=kind, concentration_value=concentration,
        concentration_unit=concentration_unit, response_name="growth_inhibition",
        response_value=response, response_unit=response_unit, qualifier=qualifier,
        timepoint_value=None, timepoint_unit=None, metadata_json={},
    )


def test_csv_parser_preserves_rows_and_returns_source_hash():
    csv_data = (
        "sample_role,sample_label,replicate_id,replicate_kind,concentration_value,"
        "concentration_unit,response_name,response_value,response_unit\n"
        "test_sample,Compound A,bio-1,biological,1,uM,growth_inhibition,80,% inhibition\n"
        "vehicle_control,Vehicle,control-1,biological,,,growth_inhibition,100,% inhibition\n"
    ).encode("utf-8")
    result = parse_measurement_table("plate.csv", csv_data)
    assert result["row_count"] == 2
    assert result["error_count"] == 0
    assert len(result["source_sha256"]) == 64
    assert result["measurements"][0]["concentration_value"] == 1.0
    assert result["measurements"][1]["sample_role"] == "vehicle_control"


def test_csv_parser_reports_invalid_numeric_rows_without_silent_coercion():
    csv_data = (
        "sample_role,sample_label,replicate_id,concentration_value,concentration_unit,"
        "response_name,response_value,response_unit\n"
        "test_sample,Compound A,bio-1,1,uM,growth_inhibition,not-a-number,%\n"
    ).encode("utf-8")
    result = parse_measurement_table("plate.csv", csv_data)
    assert result["row_count"] == 0
    assert result["error_count"] == 1
    assert "numérique" in result["errors"][0]["message"]


def test_csv_parser_rejects_missing_required_columns():
    with pytest.raises(TabularImportError, match="Colonnes obligatoires manquantes"):
        parse_measurement_table("plate.csv", b"sample,response\nA,50\n")


def test_4pl_recovers_ic50_and_reproducible_cluster_bootstrap_interval():
    concentrations = [0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30, 100, 300, 1000]
    rows = []
    replicate_offsets = {"bio-1": -0.7, "bio-2": 0.0, "bio-3": 0.8, "bio-4": 0.2}
    for replicate, offset in replicate_offsets.items():
        for concentration in concentrations:
            response = 100.0 / (1.0 + (10.0 / concentration) ** 1.25) + offset
            rows.append(_measurement("test_sample", replicate, concentration, response))
    result = fit_dose_response(
        rows, response_name="growth_inhibition", direction="increasing",
        endpoint_name="IC50", bootstrap_iterations=100, random_seed=42,
    )
    assert result["algorithm"] == "four_parameter_logistic"
    assert result["estimate"] == pytest.approx(10.0, rel=0.12)
    assert result["confidence_interval"]["status"] == "available"
    assert result["confidence_interval"]["lower"] < result["estimate"] < result["confidence_interval"]["upper"]
    assert result["data_summary"]["biological_replicates_total"] == 4
    assert result["data_summary"]["complete_biological_replicates"] == 4
    assert len(result["fitted_curve"]) == 100
    assert len(result["input_sha256"]) == 64


def test_4pl_decreasing_response_is_supported():
    concentrations = [0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30, 100, 300]
    rows = []
    for replicate, offset in (("bio-1", -0.2), ("bio-2", 0), ("bio-3", 0.2)):
        for concentration in concentrations:
            response = 100.0 / (1.0 + (concentration / 10.0) ** 1.1) + offset
            rows.append(_measurement("test_sample", replicate, concentration, response))
    result = fit_dose_response(
        rows, response_name="growth_inhibition", direction="decreasing",
        bootstrap_iterations=100, random_seed=7,
    )
    assert result["estimate"] == pytest.approx(10.0, rel=0.15)


def test_4pl_rejects_mixed_units_instead_of_converting_silently():
    rows = []
    for replicate in ("bio-1", "bio-2", "bio-3"):
        for index, concentration in enumerate((0.1, 1, 10, 100, 1000)):
            unit = "uM" if index < 3 else "nM"
            rows.append(_measurement(
                "test_sample", replicate, concentration, 100 / (1 + 10 / concentration),
                concentration_unit=unit,
            ))
    with pytest.raises(ValueError, match="Plusieurs unités de concentration"):
        fit_dose_response(rows, response_name="growth_inhibition", direction="increasing")


def test_4pl_rejects_censored_observations():
    rows = []
    for replicate in ("bio-1", "bio-2", "bio-3"):
        for concentration in (0.1, 1, 10, 100, 1000):
            rows.append(_measurement(
                "test_sample", replicate, concentration, 50,
                qualifier="below" if concentration == 1000 else "exact",
            ))
    with pytest.raises(ValueError, match="censurées"):
        fit_dose_response(rows, response_name="growth_inhibition", direction="increasing")


def test_4pl_does_not_treat_technical_replicates_as_biological_units():
    rows = []
    for replicate in ("tech-1", "tech-2", "tech-3"):
        for concentration in (0.1, 1, 10, 100, 1000):
            rows.append(_measurement(
                "test_sample", replicate, concentration, 100 / (1 + 10 / concentration),
                kind="technical",
            ))
    with pytest.raises(ValueError, match="Aucun réplicat biologique"):
        fit_dose_response(rows, response_name="growth_inhibition", direction="increasing")
