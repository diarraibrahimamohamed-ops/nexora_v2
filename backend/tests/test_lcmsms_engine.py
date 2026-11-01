"""Tests for deterministic LC-MS/MS preprocessing and conservative MS2 matching."""
from pathlib import Path

import numpy as np
import pytest
from matchms import Spectrum

from app.services.lcmsms_algorithms import (
    FeatureRecord,
    LcmsEngineError,
    Ms2ScanRecord,
    associate_ms2_scans,
    load_msp_library,
    mass_error_ppm,
    score_library_spectra,
)


def feature(key, mz, rt, polarity="positive"):
    return FeatureRecord(
        feature_key=key,
        precursor_mz=mz,
        retention_time_seconds=rt,
        polarity=polarity,
        intensity=1000.0,
        charge=1,
    )


def scan(identifier, mz, rt, polarity="positive"):
    return Ms2ScanRecord(
        scan_identifier=identifier,
        precursor_mz=mz,
        retention_time_seconds=rt,
        polarity=polarity,
        charge=1,
        isolation_window=1.0,
        collision_energy=20.0,
        fragments=(
            {"mz": 50.0, "intensity": 100.0},
            {"mz": 100.0, "intensity": 60.0},
            {"mz": 150.0, "intensity": 30.0},
        ),
        total_fragment_count=3,
        fragments_truncated=False,
    )


def test_mass_error_ppm_is_absolute_and_symmetric():
    assert mass_error_ppm(100.0005, 100.0) == pytest.approx(5.0)
    assert mass_error_ppm(99.9995, 100.0) == pytest.approx(5.0)


def test_ms2_association_requires_unique_mass_rt_and_polarity_match():
    features = [
        feature("feature-a", 301.1234, 100.0),
        feature("feature-b", 301.1234, 100.0),
        feature("feature-negative", 301.1234, 100.0, "negative"),
        feature("feature-far-rt", 301.1234, 200.0),
    ]
    scans = [
        scan("scan-ambiguous", 301.1234, 100.0),
        scan("scan-unique", 301.1234, 200.0),
        scan("scan-unmatched-polarity", 301.1234, 100.0, "unknown"),
    ]

    results = associate_ms2_scans(
        features,
        scans,
        precursor_mass_tolerance_ppm=5.0,
        retention_time_tolerance_seconds=2.0,
    )

    assert [item.status for item in results] == ["ambiguous", "matched", "unmatched"]
    assert results[0].feature_key is None
    assert {item["feature_key"] for item in results[0].candidates} == {"feature-a", "feature-b"}
    assert results[1].feature_key == "feature-far-rt"
    assert results[2].candidates == ()


def test_ms2_association_rejects_invalid_tolerances():
    with pytest.raises(LcmsEngineError, match="precursor_mass_tolerance_ppm"):
        associate_ms2_scans(
            [feature("a", 301.0, 10.0)],
            [scan("s", 301.0, 10.0)],
            precursor_mass_tolerance_ppm=0.0,
            retention_time_tolerance_seconds=1.0,
        )


def test_msp_loader_and_cosine_greedy_identify_synthetic_reference_only_as_candidate(tmp_path):
    msp_path = tmp_path / "synthetic.msp"
    msp_path.write_text(
        "Name: Synthetic flavonoid\n"
        "PrecursorMZ: 301.1234\n"
        "IonMode: Positive\n"
        "Num Peaks: 3\n"
        "50.0 100.0\n"
        "100.0 60.0\n"
        "150.0 30.0\n\n",
        encoding="utf-8",
    )

    library, summary = load_msp_library(msp_path)
    assert len(library) == 1
    assert summary["reference_spectrum_count"] == 1
    assert summary["rejected_reference_spectrum_count"] == 0

    matches = score_library_spectra(
        scan("scan-1", 301.1234, 100.0),
        library,
        precursor_mass_tolerance_ppm=5.0,
        fragment_mz_tolerance_da=0.01,
        minimum_cosine_score=0.8,
        minimum_matched_peaks=3,
    )

    assert len(matches) == 1
    assert matches[0]["annotation_name"] == "Synthetic flavonoid"
    assert matches[0]["cosine_score"] == pytest.approx(1.0)
    assert matches[0]["matched_peaks"] == 3
    # This result is a score and an annotation candidate, never an MSI-1 identity.
    assert matches[0]["precursor_mass_error_ppm"] == pytest.approx(0.0)


def test_score_library_spectra_does_not_accept_precursor_outside_ppm_tolerance():
    reference = Spectrum(
        mz=np.asarray([50.0, 100.0, 150.0]),
        intensities=np.asarray([100.0, 60.0, 30.0]),
        metadata={"precursor_mz": 500.0, "compound_name": "Different precursor", "ionmode": "positive"},
    )
    matches = score_library_spectra(
        scan("scan-1", 301.1234, 100.0),
        [reference],
        precursor_mass_tolerance_ppm=5.0,
        fragment_mz_tolerance_da=0.01,
        minimum_cosine_score=0.0,
        minimum_matched_peaks=2,
    )
    assert matches == []
