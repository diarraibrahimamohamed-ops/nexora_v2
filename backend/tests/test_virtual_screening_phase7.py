from app.services.virtual_screening import ScreeningOptions, prepare_library, standardize_and_profile


def test_rdkit_profile_is_deterministic_and_not_activity_score():
    a = standardize_and_profile("CCO", ScreeningOptions())
    b = standardize_and_profile("CCO", ScreeningOptions())
    assert a["accepted"] is True
    assert a["canonical_smiles"] == b["canonical_smiles"]
    assert a["descriptors"] == b["descriptors"]
    assert "activity_score" not in a


def test_invalid_and_duplicate_smiles_are_handled():
    result = prepare_library(["CCO", "OCC", "not-a-smiles"], ScreeningOptions(), max_input=10)
    assert result["input_count"] == 3
    assert result["unique_count"] == 1
    assert result["duplicates_removed"] == 1
    assert result["accepted_count"] == 1
    assert result["rejected_count"] == 0


def test_explicit_filter_can_reject_without_fabricating_activity():
    result = standardize_and_profile("CCCCCCCCCCCC", ScreeningOptions(max_logp=1.0))
    assert result["accepted"] is False
    assert "max_logp" in result["rejected_by"]
    assert "activity_score" not in result
