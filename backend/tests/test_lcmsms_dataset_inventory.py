from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from lcmsms_dataset_inventory import (  # noqa: E402
    assess_eligibility,
    classify_sample_role,
    parse_assay_rows,
    parse_sample_roles,
    sha256_bytes,
)


def test_sample_role_classifier_marks_candidates_without_claiming_final_identity():
    assert classify_sample_role(["Pool_QC_01", "Pooled quality control"]) == "pooled_qc_candidate"
    assert classify_sample_role(["Solvent blank 01"]) == "blank_candidate"
    assert classify_sample_role(["QC"]) == "qc_unspecified_candidate"
    assert classify_sample_role(["serum sample 01"]) == "unclassified"


def test_sample_and_assay_metadata_are_linked_by_sample_name():
    sample_text = (
        "Sample Name\tCharacteristics[Sample type]\n"
        "POOL_01\tPooled QC\n"
        "SERUM_01\tserum\n"
    )
    roles = parse_sample_roles(sample_text)
    assay_text = (
        "Sample Name\tMS Assay Name\tRaw Spectral Data File\tDerived Spectral Data File\n"
        "POOL_01\tLC-MS positive mode\tFILES/POOL_01.raw\tFILES/POOL_01.mzML\n"
        "SERUM_01\tLC-MS negative mode\tFILES/SERUM_01.wiff\tFILES/SERUM_01.mzML\n"
    )
    rows = parse_assay_rows([("a_MTBLS00000_lc-ms.txt", assay_text)], roles)
    assert len(rows) == 2
    assert rows[0]["sample_role_candidate"] == "pooled_qc_candidate"
    assert rows[0]["polarity_candidate"] == "positive"
    assert rows[0]["derived_extension"] == ".mzml"
    assert rows[1]["polarity_candidate"] == "negative"
    assert rows[1]["raw_extension"] == ".wiff"


def test_eligibility_requires_repository_inventory_license_and_human_review():
    args = {
        "catalogue_available": True,
        "archive_available": True,
        "metadata_types": {
            "metadata_investigation",
            "metadata_sample",
            "metadata_assay",
        },
        "assay_rows": [{"derived_extension": ".mzml"}],
        "archive_files": [{"path": "FILES/sample.mzML"}],
        "review": None,
    }
    blocked = assess_eligibility(**args)
    assert blocked["eligible_for_processing"] is False
    assert "reuse_and_license_review_approved" in blocked["blocking_checks"]

    approved_review = {
        "reviewed_by": "reviewer",
        "reviewed_at_utc": "2026-11-01T00:00:00Z",
        "reuse_approved": True,
        "license_identifier": "CC0-1.0",
        "license_evidence_url": "https://example.org/study-license",
        "assay_mapping_approved": True,
        "qc_role_mapping_approved": True,
    }
    accepted = assess_eligibility(**{**args, "review": approved_review})
    assert accepted["eligible_for_processing"] is True
    assert accepted["status"] == "eligible_for_controlled_pilot"


def test_license_and_conversion_decision_cannot_be_inferred_from_file_formats():
    base = {
        "catalogue_available": True,
        "archive_available": True,
        "metadata_types": {
            "metadata_investigation",
            "metadata_sample",
            "metadata_assay",
        },
        "assay_rows": [{"derived_extension": ".raw"}],
        "archive_files": [{"path": "FILES/sample.raw"}],
        "review": {
            "reviewed_by": "reviewer",
            "reviewed_at_utc": "2026-11-01T00:00:00Z",
            "reuse_approved": True,
            "license_identifier": "CC0-1.0",
            "license_evidence_url": "https://example.org/study-license",
            "assay_mapping_approved": True,
            "qc_role_mapping_approved": True,
            "conversion_plan": {
                "approved": False,
                "tool_and_version": "",
                "parameters_or_sop": "",
            },
        },
    }
    result = assess_eligibility(**base)
    assert result["eligible_for_processing"] is False
    assert "mzml_available_or_conversion_plan_approved" in result["blocking_checks"]


def test_sha256_is_stable_and_content_sensitive():
    assert sha256_bytes(b"mzML-test") == sha256_bytes(b"mzML-test")
    assert sha256_bytes(b"mzML-test") != sha256_bytes(b"mzML-test-2")
