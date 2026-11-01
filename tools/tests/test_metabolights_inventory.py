from tools.metabolights_inventory import InventoryError, safe_isa_filename, sample_role, parse_isa


def test_path_traversal_rejected():
    for value in ("../i_Investigation.txt", "/tmp/s_MTBLS1.txt", "FILES/a_MTBLS1.txt"):
        try:
            safe_isa_filename(value)
        except InventoryError:
            continue
        raise AssertionError(f"unsafe filename accepted: {value}")


def test_expected_isa_filenames_accepted():
    assert safe_isa_filename("i_Investigation.txt") == "i_Investigation.txt"
    assert safe_isa_filename("s_MTBLS15740.txt") == "s_MTBLS15740.txt"
    assert safe_isa_filename("a_MTBLS15740_pos_lcms.txt") == "a_MTBLS15740_pos_lcms.txt"


def test_sample_roles_remain_candidates():
    assert sample_role({"Sample Name": "QC pooled 01"}) == "pooled_qc_candidate"
    assert sample_role({"Sample Name": "Extraction blank 02"}) == "extraction_blank_candidate"
    assert sample_role({"Sample Name": "Serum A1"}) == "sample_or_unclassified"


def test_assay_parser_maps_samples_and_file_references():
    metadata = {
        "s_MTBLS15740.txt": {"text": "Sample Name\tCharacteristics[Sample type]\nQC pool 01\tpooled quality control\nSerum 01\tbiological sample\n"},
        "a_MTBLS15740_pos.txt": {"text": "Sample Name\tParameter Value[Ion mode]\tRaw Spectral Data File\tDerived Spectral Data File\tParameter Value[Injection Order]\nQC pool 01\tpositive\tFILES/qc.raw\tFILES/qc.mzML\t1\nSerum 01\tpositive\tFILES/s1.raw\tFILES/s1.mzML\t2\n"},
    }
    files = [{"name": name} for name in ("FILES/qc.raw", "FILES/qc.mzML", "FILES/s1.raw", "FILES/s1.mzML")]
    parsed = parse_isa(metadata, files)
    assert parsed["sample_count"] == 2
    assert parsed["sample_role_candidate_counts"]["pooled_qc_candidate"] == 1
    assert len(parsed["assay_file_references"]) == 4
    assert all(row["resolves_in_archive"] for row in parsed["assay_file_references"])
    assert all(row["manual_confirmation_required"] for row in parsed["assay_file_references"])


def test_mixed_polarity_is_not_guessed():
    metadata = {
        "s_MTBLS11.txt": {"text": "Sample Name\nA\n"},
        "a_MTBLS11_mix.txt": {"text": "Sample Name\tAssay description\tDerived Spectral Data File\nA\tpositive and negative references\tFILES/a.mzML\n"},
    }
    parsed = parse_isa(metadata, [{"name": "FILES/a.mzML"}])
    assert parsed["assay_file_references"][0]["polarity_candidate"] == "unknown"


def test_injection_order_is_not_inferred():
    metadata = {
        "s_MTBLS9.txt": {"text": "Sample Name\nA\n"},
        "a_MTBLS9_lcms.txt": {"text": "Sample Name\tDerived Spectral Data File\nA\tFILES/a.mzML\n"},
    }
    parsed = parse_isa(metadata, [{"name": "FILES/a.mzML"}])
    assert parsed["assay_file_references"][0]["injection_order_candidate"] is None


def test_parser_accepts_isa_fields_larger_than_csv_default_limit():
    from tools.metabolights_inventory import parse_tsv

    long_value = "x" * 150_000
    headers, rows = parse_tsv("Sample Name\\tProtocol Description\\nS1\\t" + long_value)
    assert headers == ["Sample Name", "Protocol Description"]
    assert rows[0]["Protocol Description"] == long_value
