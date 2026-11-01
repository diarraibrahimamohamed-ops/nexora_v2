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
    parse_tsv_text,
    match_archive_reference,
    build_inventory,
    sha256_bytes,
    _normalise_file_catalogue,
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
        "Sample Name\tMS Assay Name\tInjection Order\tRaw Spectral Data File\tDerived Spectral Data File\n"
        "POOL_01\tLC-MS positive mode\t1\tFILES/POOL_01.raw\tFILES/POOL_01.mzML\n"
        "SERUM_01\tLC-MS negative mode\t2\tFILES/SERUM_01.wiff\tFILES/SERUM_01.mzML\n"
    )
    rows = parse_assay_rows([("a_MTBLS00000_lc-ms.txt", assay_text)], roles)
    assert len(rows) == 2
    assert rows[0]["sample_role_candidate"] == "pooled_qc_candidate"
    assert rows[0]["polarity_candidate"] == "positive"
    assert rows[0]["derived_extension"] == ".mzml"
    assert rows[0]["injection_order_candidate"] == "1"
    assert rows[1]["polarity_candidate"] == "negative"
    assert rows[1]["raw_extension"] == ".wiff"


def test_eligibility_requires_repository_inventory_license_and_human_review():
    args = {
        "study_id": "MTBLS12345",
        "catalogue_available": True,
        "archive_available": True,
        "archive_listing_complete": True,
        "metadata_types": {
            "metadata_investigation",
            "metadata_sample",
            "metadata_assay",
        },
        "downloaded_metadata_types": {
            "metadata_investigation",
            "metadata_sample",
            "metadata_assay",
        },
        "assay_rows": [{
            "derived_extension": ".mzml",
            "derived_data_file": "FILES/sample.mzML",
            "derived_archive_match": {"status": "matched_exact_path"},
        }],
        "archive_files": [{"path": "FILES/sample.mzML"}],
        "review": None,
    }
    blocked = assess_eligibility(**args)
    assert blocked["eligible_for_processing"] is False
    assert "reuse_and_license_review_approved" in blocked["blocking_checks"]

    approved_review = {
        "study_id": "MTBLS12345",
        "reviewed_by": "reviewer",
        "reviewed_at_utc": "2026-11-01T00:00:00Z",
        "reuse_approved": True,
        "license_identifier": "CC0-1.0",
        "license_evidence_url": "https://example.org/study-license",
        "assay_mapping_approved": True,
        "qc_role_mapping_approved": True,
        "injection_order_approved": True,
        "injection_order_evidence_url": "https://example.org/injection-order",
    }
    accepted = assess_eligibility(**{**args, "review": approved_review})
    assert accepted["eligible_for_processing"] is True
    assert accepted["status"] == "eligible_for_controlled_pilot"


def test_license_and_conversion_decision_cannot_be_inferred_from_file_formats():
    base = {
        "study_id": "MTBLS12345",
        "catalogue_available": True,
        "archive_available": True,
        "archive_listing_complete": True,
        "metadata_types": {
            "metadata_investigation",
            "metadata_sample",
            "metadata_assay",
        },
        "downloaded_metadata_types": {
            "metadata_investigation",
            "metadata_sample",
            "metadata_assay",
        },
        "assay_rows": [{
            "derived_extension": ".raw",
            "derived_data_file": "FILES/sample.raw",
            "derived_archive_match": {"status": "matched_exact_path"},
        }],
        "archive_files": [{"path": "FILES/sample.raw"}],
        "review": {
            "study_id": "MTBLS12345",
            "reviewed_by": "reviewer",
            "reviewed_at_utc": "2026-11-01T00:00:00Z",
            "reuse_approved": True,
            "license_identifier": "CC0-1.0",
            "license_evidence_url": "https://example.org/study-license",
            "assay_mapping_approved": True,
            "qc_role_mapping_approved": True,
            "injection_order_approved": True,
            "injection_order_evidence_url": "https://example.org/injection-order",
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


def test_archive_mapping_distinguishes_exact_unique_ambiguous_and_missing():
    files = [
        {"path": "FILES/POOL_01.raw"},
        {"path": "OTHER/POOL_02.raw"},
        {"path": "SECOND/POOL_02.raw"},
    ]
    assert match_archive_reference("FILES/POOL_01.raw", files)["status"] == "matched_exact_path"
    assert match_archive_reference("POOL_01.raw", files)["status"] == "matched_unique_basename"
    assert match_archive_reference("POOL_02.raw", files)["status"] == "ambiguous_basename"
    assert match_archive_reference("missing.mzML", files)["status"] == "not_found_in_public_index"


def test_inventory_report_preserves_unverified_state_without_approved_review():
    report = build_inventory(
        "MTBLS15740",
        {
            "available": True,
            "files": [
                {"file": "i_Investigation.txt", "type": "metadata_investigation"},
                {"file": "s_MTBLS15740.txt", "type": "metadata_sample"},
                {"file": "a_MTBLS15740.txt", "type": "metadata_assay"},
            ],
        },
        {
            "available": True,
            "files": [{"path": "FILES/sample.mzML", "url": "https://example.org/sample.mzML"}],
        },
        [{"file": "s_MTBLS15740.txt", "downloaded": True, "sha256": "a" * 64}],
        [{
            "sample_role_candidate": "pooled_qc_candidate",
            "polarity_candidate": "positive",
            "raw_data_file": "",
            "derived_data_file": "sample.mzML",
            "derived_extension": ".mzml",
        }],
        None,
    )
    assert report["eligibility"]["eligible_for_processing"] is False
    assert report["license_review"]["status"] == "not_verified"
    assert report["processing"]["raw_data_downloaded"] is False
    assert report["assay_rows"][0]["derived_archive_match"]["status"] == "matched_unique_basename"



def test_isa_tab_parser_accepts_large_protocol_fields():
    long_value = "x" * 200_000
    parsed = parse_tsv_text(
        "Sample Name\\tProtocol Description\\nS1\\t" + long_value
    )
    assert parsed[0]["Protocol Description"] == long_value


def test_assay_polarity_parser_reads_ion_mode_and_rejects_conflicts():
    text = (
        "Sample Name\\tParameter Value[Ion mode]\\tMS Assay Name\\n"
        "S1\\tpositive\\tLC-MS\\n"
        "S2\\tnegative\\tLC-MS\\n"
        "S3\\tpositive and negative\\tLC-MS\\n"
    )
    rows = parse_assay_rows([("a_MTBLS12345_lcms.txt", text)], {})
    assert [row["polarity_candidate"] for row in rows] == [
        "positive", "negative", "unknown"
    ]


def test_catalogue_parser_accepts_string_file_entries():
    rows = _normalise_file_catalogue([
        "i_Investigation.txt",
        "s_MTBLS12345.txt",
        "a_MTBLS12345_lc-ms.txt",
    ])
    assert [row["type"] for row in rows] == [
        "metadata_investigation", "metadata_sample", "metadata_assay"
    ]


def test_incomplete_archive_listing_blocks_k0():
    args = {
        "study_id": "MTBLS12345",
        "catalogue_available": True,
        "archive_available": True,
        "archive_listing_complete": False,
        "metadata_types": {
            "metadata_investigation", "metadata_sample", "metadata_assay"
        },
        "downloaded_metadata_types": {
            "metadata_investigation", "metadata_sample", "metadata_assay"
        },
        "assay_rows": [{
            "derived_extension": ".mzml",
            "derived_data_file": "FILES/sample.mzML",
            "derived_archive_match": {"status": "matched_exact_path"},
        }],
        "archive_files": [{"path": "FILES/sample.mzML"}],
        "review": {
            "study_id": "MTBLS12345",
            "reviewed_by": "reviewer",
            "reviewed_at_utc": "2026-11-01T00:00:00Z",
            "reuse_approved": True,
            "license_identifier": "CC0-1.0",
            "license_evidence_url": "https://example.org/license",
            "assay_mapping_approved": True,
            "qc_role_mapping_approved": True,
            "injection_order_approved": True,
            "injection_order_evidence_url": "https://example.org/injection-order",
        },
    }
    result = assess_eligibility(**args)
    assert result["eligible_for_processing"] is False
    assert "public_archive_listing_complete" in result["blocking_checks"]


def test_review_record_cannot_be_reused_for_another_accession():
    args = {
        "study_id": "MTBLS12345",
        "catalogue_available": True,
        "archive_available": True,
        "archive_listing_complete": True,
        "metadata_types": {
            "metadata_investigation", "metadata_sample", "metadata_assay"
        },
        "downloaded_metadata_types": {
            "metadata_investigation", "metadata_sample", "metadata_assay"
        },
        "assay_rows": [{
            "derived_extension": ".mzml",
            "derived_data_file": "FILES/sample.mzML",
            "derived_archive_match": {"status": "matched_exact_path"},
        }],
        "archive_files": [{"path": "FILES/sample.mzML"}],
        "review": {
            "study_id": "MTBLS99999",
            "reviewed_by": "reviewer",
            "reviewed_at_utc": "2026-11-01T00:00:00Z",
            "reuse_approved": True,
            "license_identifier": "CC0-1.0",
            "license_evidence_url": "https://example.org/license",
            "assay_mapping_approved": True,
            "qc_role_mapping_approved": True,
            "injection_order_approved": True,
            "injection_order_evidence_url": "https://example.org/injection-order",
        },
    }
    result = assess_eligibility(**args)
    assert result["eligible_for_processing"] is False
    assert "review_record_matches_study_accession" in result["blocking_checks"]
