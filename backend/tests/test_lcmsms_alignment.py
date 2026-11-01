"""Deterministic polarity-aware LC-MS feature alignment tests."""
import pytest

from app.services.lcmsms_alignment import (
    FeatureInput,
    InjectionInput,
    LcmsAlignmentError,
    align_lcms_features,
)


def make_injections():
    return [
        InjectionInput(1, 1, "pooled_qc"),
        InjectionInput(2, 2, "sample"),
        InjectionInput(3, 3, "pooled_qc"),
    ]


def make_feature(feature_id, injection_id, order, index, polarity="positive", rt_offset=0.0):
    reference_rt = 40.0 + index * 30.0
    target_rt = (reference_rt - 6.0) / 1.03
    rt = reference_rt if injection_id == 1 else target_rt + rt_offset
    return FeatureInput(
        feature_id=feature_id,
        processing_run_id=100 + injection_id,
        injection_id=injection_id,
        injection_order=order,
        feature_key=f"openms:{polarity}:{index}",
        precursor_mz=150.0 + index * 11.0 + (0.0001 if injection_id == 2 else 0.0),
        retention_time_seconds=rt,
        polarity=polarity,
        charge=1,
        adduct="[M+H]+" if polarity == "positive" else "[M-H]-",
        raw_intensity=1000.0 + index,
        source_observation_id=feature_id + 1000,
    )


def make_feature_maps(*, out_of_range=False):
    features = []
    next_id = 1
    for injection_id, order in [(1, 1), (2, 2), (3, 3)]:
        for polarity in ("positive", "negative"):
            for index in range(12):
                features.append(make_feature(
                    next_id, injection_id, order, index,
                    polarity=polarity,
                ))
                next_id += 1
    if out_of_range:
        features.append(FeatureInput(
            feature_id=next_id,
            processing_run_id=102,
            injection_id=2,
            injection_order=2,
            feature_key="outside-anchor-range",
            precursor_mz=999.0,
            retention_time_seconds=5.0,
            polarity="positive",
            charge=1,
            adduct="[M+H]+",
            raw_intensity=50.0,
            source_observation_id=next_id + 1000,
        ))
    return features


def test_aligns_positive_and_negative_polarities_separately():
    output = align_lcms_features(
        make_feature_maps(),
        make_injections(),
        parameters={
            "minimum_anchors": 8,
            "minimum_injections": 3,
            "minimum_anchor_rt_coverage": 0.5,
            "group_mass_tolerance_ppm": 15,
            "group_rt_tolerance_seconds": 2,
        },
    )
    groups = list(output.groups)
    assert len(groups) == 24
    assert {item["polarity"] for item in groups} == {"positive", "negative"}
    assert all(item["member_count"] == 3 for item in groups)
    assert all(item["alignment_status"] == "aligned" for item in groups)
    assert all(
        len({member["feature"].injection_id for member in item["members"]}) == 3
        for item in groups
    )
    assert output.diagnostics["reference_injection_ids"] == {
        "negative": 1,
        "positive": 1,
    }
    injection_two_members = [
        member
        for group in groups
        for member in group["members"]
        if member["feature"].injection_id == 2
    ]
    assert len(injection_two_members) == 24
    for member in injection_two_members:
        source_rt = member["feature"].retention_time_seconds
        expected_reference_rt = 1.03 * source_rt + 6.0
        assert member["is_rt_aligned"] is True
        assert member["aligned_retention_time"] == pytest.approx(
            expected_reference_rt, abs=1e-6
        )
        assert member["delta_retention_time_seconds"] == pytest.approx(
            expected_reference_rt - source_rt, abs=1e-6
        )


def test_preserves_out_of_anchor_range_feature_as_singleton_without_extrapolation():
    output = align_lcms_features(
        make_feature_maps(out_of_range=True),
        make_injections(),
        parameters={
            "minimum_anchors": 8,
            "minimum_injections": 3,
            "minimum_anchor_rt_coverage": 0.5,
            "group_mass_tolerance_ppm": 15,
            "group_rt_tolerance_seconds": 2,
        },
    )
    singleton = next(
        group for group in output.groups
        if group["members"][0]["feature"].feature_key == "outside-anchor-range"
    )
    member = singleton["members"][0]
    assert singleton["alignment_status"] == "singleton"
    assert member["is_rt_aligned"] is False
    assert member["aligned_retention_time"] == 5.0
    assert member["delta_retention_time_seconds"] == 0.0
    assert output.diagnostics["unaligned_feature_count"] == 1


def test_consensus_status_is_partial_when_an_injection_has_no_member():
    features = make_feature_maps()
    # Remove one positive-mode feature from the third injection only.
    features = [
        row for row in features
        if not (
            row.injection_id == 3
            and row.polarity == "positive"
            and row.feature_key.endswith(":0")
        )
    ]
    output = align_lcms_features(
        features,
        make_injections(),
        parameters={
            "minimum_anchors": 8,
            "minimum_injections": 3,
            "minimum_anchor_rt_coverage": 0.5,
            "group_mass_tolerance_ppm": 15,
            "group_rt_tolerance_seconds": 2,
        },
    )
    partial = next(
        group for group in output.groups
        if group["polarity"] == "positive"
        and abs(group["consensus_mz"] - 150.00005) < 0.01
    )
    assert partial["member_count"] == 2
    assert partial["expected_injection_count"] == 3
    assert partial["alignment_status"] == "partial"


def test_rejects_unknown_polarity_instead_of_cross_mode_matching():
    features = make_feature_maps()
    first = features[0]
    features[0] = FeatureInput(
        **{
            **first.__dict__,
            "polarity": "unknown",
        }
    )
    with pytest.raises(LcmsAlignmentError) as error:
        align_lcms_features(
            features,
            make_injections(),
            parameters={"minimum_anchors": 8},
        )
    assert error.value.code == "ALIGNMENT_UNKNOWN_POLARITY"


def test_rejects_ambiguous_anchor_candidates():
    injections = make_injections()
    features = []
    for injection_id, order in [(1, 1), (2, 2), (3, 3)]:
        for index in range(10):
            base = make_feature(
                injection_id * 100 + index,
                injection_id,
                order,
                index,
            )
            features.append(base)
        if injection_id == 1:
            for duplicate_index in range(9):
                duplicate = make_feature(
                    900 + duplicate_index, 1, 1, duplicate_index
                )
                features.append(duplicate)
    with pytest.raises(LcmsAlignmentError) as error:
        align_lcms_features(
            features,
            injections,
            parameters={
                "minimum_anchors": 8,
                "minimum_injections": 3,
                "minimum_anchor_rt_coverage": 0.5,
            },
        )
    assert error.value.code in {
        "ALIGNMENT_INSUFFICIENT_ANCHORS",
        "ALIGNMENT_ANCHOR_OUTLIERS",
    }


def test_rejects_inadequate_injection_count():
    with pytest.raises(LcmsAlignmentError) as error:
        align_lcms_features(
            make_feature_maps(),
            make_injections()[:2],
            parameters={"minimum_injections": 3},
        )
    assert error.value.code == "ALIGNMENT_INSUFFICIENT_INJECTIONS"
