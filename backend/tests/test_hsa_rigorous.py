from app.core.hsa.stability_analyzer import analyze_stability
from app.core.protein.structure_resolver import resolve_experimental_pdb


def test_hsa_returns_bounded_transparent_index_without_fake_probability():
    result = analyze_stability("ATGCGCATATCGGCGTATCG" * 3)
    assert result["success"] is True
    assert result["formula_version"] == "HSA-2.0-transparent-index"
    assert 0 <= result["global_score"] <= 100
    assert 0 <= result["vulnerability_index"] <= 100
    assert result["mutation_prob"] is None
    assert result["environmental"]["ph_used_in_formula"] is False


def test_structure_resolver_rejects_invalid_identity_cutoff_without_network():
    path, error, metadata = resolve_experimental_pdb("MKT", "/tmp", identity_cutoff=1.5)
    assert path is None
    assert "identity_cutoff" in error
    assert metadata == {}