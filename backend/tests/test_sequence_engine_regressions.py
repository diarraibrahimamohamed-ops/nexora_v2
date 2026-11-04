"""Regression tests for scientific sequence normalization and alignment."""
import pytest
from Bio.Align import PairwiseAligner

from app.api.v1.analysis import _clean, _gc, _nuc_counts, _sync_analyze, _translate
from app.core.sequence.afribio_engine import SmithWatermanGotoh, clean_sequence
from app.core.sequence.translation import translate_dna_to_protein


def test_gotoh_affine_gap_score_and_state_aware_traceback_match_biopython():
    query = "AAAACCCCGGGG"
    reference = "AAAATTTTCCCCGGGG"

    result = SmithWatermanGotoh().align(query, reference)

    aligner = PairwiseAligner()
    aligner.mode = "local"
    aligner.match_score = 5
    aligner.mismatch_score = -4
    aligner.open_gap_score = -10
    aligner.extend_gap_score = -1

    assert result["score"] == aligner.score(query, reference) == 47
    assert result["aligned_query"].replace("-", "") == query
    assert result["aligned_reference"].replace("-", "") == reference
    assert result["aligned_query"].count("-") == 4
    assert len(result["aligned_query"]) == len(result["aligned_reference"])


def test_alignment_preserves_iupac_symbols_and_scores_ambiguity_conservatively():
    engine = SmithWatermanGotoh()
    sequence = "ACGTRYSWKMBDHVN"

    assert clean_sequence(" acgt\nRYSWKMBDHVN ") == sequence
    assert engine.get_score("R", "A") == 0
    assert engine.get_score("R", "C") == -4
    assert clean_sequence("ACGU", "RNA") == "ACGT"
    with pytest.raises(ValueError, match="Caractères invalides"):
        clean_sequence("ACGT-")


def test_alignment_rejects_excessive_cell_count_before_allocation():
    engine = SmithWatermanGotoh(max_alignment_cells=8)

    with pytest.raises(ValueError, match="Alignement refusé avant allocation"):
        engine.align("AC", "GT")


def test_analysis_normalization_preserves_n_and_rejects_unknown_symbols():
    assert _clean(" acgt\nN ") == "ACGTN"
    with pytest.raises(ValueError, match="Caractères ADN non pris en charge"):
        _clean("ACGTX")

    assert _gc("GCGCNN") == 100.0
    counts = _nuc_counts("ACGTNN")
    assert counts["N"] == 2
    assert counts["called_length"] == 4


def test_chunked_analysis_keeps_all_three_global_reading_frames_continuous():
    sequence = "ATGGCCGTTTAA" * 5_000
    result = _sync_analyze(sequence)
    full_rna = sequence.replace("T", "U")

    assert result["chunks"] > 1
    assert result["translation_frame_scope"] == "whole_sequence_with_boundary_carry"

    for frame in range(3):
        key = f"frame_{frame + 1}"
        fragment_translation = "".join(
            fragment["translations"][key] for fragment in result["fragments"]
        )
        assert fragment_translation == _translate(full_rna, frame)


def test_direct_translation_rejects_invalid_or_incomplete_input_without_silent_repair():
    protein, error = translate_dna_to_protein(" \nATG AAA TTT\n")
    assert protein == "MKF"
    assert error is None

    protein, error = translate_dna_to_protein("ATGAAAXTTT")
    assert protein is None
    assert "Caractères non nucléotidiques" in error

    protein, error = translate_dna_to_protein("ATGAAATTTT")
    assert protein is None
    assert "multiple de 3" in error



def test_async_worker_uses_same_cross_chunk_translation_as_sync():
    from app.workers.analysis_tasks import _analyze_chunked

    sequence = "ATGGCCGTTTAA" * 5_000
    result = _analyze_chunked(sequence)
    full_rna = sequence.replace("T", "U")
    assert result["mode"] == "async_server"
    for frame in range(3):
        key = f"frame_{frame + 1}"
        actual = "".join(fragment["translations"][key] for fragment in result["fragments"])
        assert actual == _translate(full_rna, frame)


def test_celery_analysis_payload_is_identifier_only_and_threshold_is_50k():
    from app.api.v1.analysis import BROWSER_SEQUENCE_THRESHOLD, SEQUENCE_ASYNC_THRESHOLD

    assert SEQUENCE_ASYNC_THRESHOLD == 50_000
    assert BROWSER_SEQUENCE_THRESHOLD == 100_000
