from app.services.variant_annotation import apply_protein_substitution, build_protein_consequence, parse_protein_change


def test_parse_k76t():
    assert parse_protein_change("K76T") == ("K", 76, "T")


def test_apply_substitution_preserves_length():
    seq = "ACDEFGHIKLMNPQRSTVWY"
    out = apply_protein_substitution(seq, ref_aa="K", position=9, alt_aa="T")
    assert len(out) == len(seq)
    assert out[8] == "T"
    assert out[:8] == seq[:8]
    assert out[9:] == seq[9:]


def test_wrong_reference_is_rejected():
    try:
        apply_protein_substitution("MKT", ref_aa="A", position=2, alt_aa="T")
    except ValueError as exc:
        assert "Discordance WT" in str(exc)
    else:
        raise AssertionError("Expected WT mismatch to be rejected")


def test_consequence_is_deterministic_and_explicit():
    seq = "M" + "A" * 74 + "K" + "G" * 5
    result = build_protein_consequence(seq, notation="K76T")
    assert result.notation == "K76T"
    assert result.protein_position == 76
    assert result.ref_aa == "K"
    assert result.alt_aa == "T"
    assert result.variant_protein_sequence[75] == "T"
    assert result.variant_protein_length == result.reference_protein_length


def test_parse_rejects_non_substitution_notation():
    try:
        parse_protein_change("K76")
    except ValueError as exc:
        assert "Notation protéique attendue" in str(exc)
    else:
        raise AssertionError("Expected invalid notation to be rejected")
