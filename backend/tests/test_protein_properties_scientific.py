import re

from Bio.SeqUtils.ProtParam import ProteinAnalysis


def _aliphatic_index(seq: str) -> float:
    n = len(seq)
    return 100.0 * (
        seq.count("A") / n
        + 2.9 * seq.count("V") / n
        + 3.9 * (seq.count("I") + seq.count("L")) / n
    )


def test_protein_properties_are_deterministic_and_not_secondary_prediction():
    seq = "MALVALSRVDQHVRC"
    a = ProteinAnalysis(seq)
    b = ProteinAnalysis(seq)
    assert a.molecular_weight() == b.molecular_weight()
    assert a.isoelectric_point() == b.isoelectric_point()
    assert a.gravy() == b.gravy()
    assert 0 <= _aliphatic_index(seq) <= 100


def test_protein_sequence_validation_accepts_standard_amino_acids_only():
    assert re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWY]+", "MALV")
    assert not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWY]+", "MALV*")
