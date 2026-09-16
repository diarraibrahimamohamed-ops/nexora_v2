from app.services.amr_analysis import analyze_amr
from app.services.genomic_analysis import interpret_sequence_pro, genomic_signature, GENE_DB
from app.core.hsa.stability_analyzer import analyze_stability


def test_amr_is_deterministic_and_no_percent_prediction():
    r1 = analyze_amr("ATGC" * 100, "bacterium")
    r2 = analyze_amr("ATGC" * 100, "bacterium")
    assert r1 == r2
    assert r1["phenotype_prediction"] is None
    assert r1["confidence"] is None


def test_virus_antibiotic_scope_guard():
    r = analyze_amr("ATGC" * 100, "virus")
    assert r["applicability"] == "not_applicable"
    assert r["phenotype_prediction"] is None


def test_legacy_short_motifs_are_not_called_genes():
    sig = genomic_signature("ATGC" * 100, GENE_DB)
    assert not any("Gène identifié" in x for x in sig)


def test_interpretation_exposes_evidence_not_fake_confidence():
    r = interpret_sequence_pro("ATGC" * 150)
    assert "evidence_score" in r
    assert r["confidence_score"] is None
    assert "scientific_validation" in r
    assert r["mutations"] if "mutations" in r else True


def test_hsa_never_estimates_mutation_probability_and_rejects_protein():
    r = analyze_stability("ATGC" * 30, {"stress_factor": 1.2})
    assert r["mutation_prob"] is None
    assert r["environmental"]["ph_used_in_formula"] is False
    assert r["validity_domain"]["clinical_validation"] is False
    bad = analyze_stability("MKTLLILAVV")
    assert bad["success"] is False
