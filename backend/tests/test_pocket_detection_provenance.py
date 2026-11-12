from pathlib import Path
from types import SimpleNamespace

from app.core.docking import pocket_detector as detector_module
from app.core.docking.pocket_detector import (
    PocketDetectorEnrichedFixed,
    detect_binding_pockets_enriched_fixed,
)


def _pqr_line(serial: int, x: float, radius: float) -> str:
    # fpocket alpha-sphere PQR output: ATOM serial atom residue resid x y z charge radius
    return (
        f"ATOM {serial} C STP 1 {x:.3f} 2.000 3.000 0.000 {radius:.3f}\n"
    )


def test_fpocket_info_parser_maps_one_based_sections_to_same_pocket_ids(tmp_path):
    info = tmp_path / "protein_info.txt"
    info.write_text(
        "Pocket 1 :\n"
        "\tScore :\t1.250\n"
        "\tDruggability Score :\t0.300\n"
        "Pocket 2 :\n"
        "\tScore :\t4.500\n"
        "\tDruggability Score :\t0.900\n"
        "Pocket 10 :\n"
        "\tScore :\t9.750\n"
        "\tDruggability Score :\t0.600\n",
        encoding="utf-8",
    )
    detector = PocketDetectorEnrichedFixed.__new__(PocketDetectorEnrichedFixed)

    metrics = detector._parse_fpocket_metrics_by_id(str(info))

    assert metrics[1] == {"fpocket_score": 1.25, "druggability_score": 0.3}
    assert metrics[2] == {"fpocket_score": 4.5, "druggability_score": 0.9}
    assert metrics[10] == {"fpocket_score": 9.75, "druggability_score": 0.6}
    assert detector._parse_fpocket_scores(str(info)) == [1.25, 4.5, 9.75]


def test_fpocket_info_mapping_detects_zero_based_pocket_files(tmp_path):
    info = tmp_path / "protein_info.txt"
    info.write_text(
        "Pocket 1 :\n\tScore : 1.0\n\tDruggability Score : 0.2\n"
        "Pocket 2 :\n\tScore : 2.0\n\tDruggability Score : 0.8\n"
        "Pocket 10 :\n\tScore : 10.0\n\tDruggability Score : 0.6\n",
        encoding="utf-8",
    )
    detector = PocketDetectorEnrichedFixed.__new__(PocketDetectorEnrichedFixed)

    metrics = detector._parse_fpocket_metrics_by_id(
        str(info), pocket_file_ids={0, 1, 9}
    )

    assert detector._last_fpocket_id_offset == 1
    assert metrics[0]["druggability_score"] == 0.2
    assert metrics[1]["druggability_score"] == 0.8
    assert metrics[9]["druggability_score"] == 0.6


def test_fpocket_info_mapping_detects_one_based_pocket_files(tmp_path):
    info = tmp_path / "protein_info.txt"
    info.write_text(
        "Pocket 1 :\n\tScore : 1.0\n\tDruggability Score : 0.2\n"
        "Pocket 2 :\n\tScore : 2.0\n\tDruggability Score : 0.8\n"
        "Pocket 10 :\n\tScore : 10.0\n\tDruggability Score : 0.6\n",
        encoding="utf-8",
    )
    detector = PocketDetectorEnrichedFixed.__new__(PocketDetectorEnrichedFixed)

    metrics = detector._parse_fpocket_metrics_by_id(
        str(info), pocket_file_ids={1, 2, 10}
    )

    assert detector._last_fpocket_id_offset == 0
    assert metrics[1]["druggability_score"] == 0.2
    assert metrics[2]["druggability_score"] == 0.8
    assert metrics[10]["druggability_score"] == 0.6


def test_fpocket_pqr_parser_handles_current_ten_field_format(tmp_path):
    pqr = tmp_path / "pocket1_vert.pqr"
    pqr.write_text(
        "HEADER This file contains fpocket alpha spheres\n"
        + _pqr_line(1, 10.0, 3.5)
        + _pqr_line(2, 12.0, 4.5),
        encoding="utf-8",
    )
    detector = PocketDetectorEnrichedFixed.__new__(PocketDetectorEnrichedFixed)

    pocket = detector._parse_pocket_vert_file(str(pqr), pocket_id=1)

    assert pocket is not None
    assert pocket["alpha_sphere_count"] == 2
    assert pocket["coordinates"] == [[10.0, 2.0, 3.0], [12.0, 2.0, 3.0]]
    assert pocket["mean_alpha_sphere_radius_A"] == 4.0
    assert pocket["max_alpha_sphere_radius_A"] == 4.5
    assert pocket["center_x"] == 11.0


def test_fpocket_pqr_parser_handles_no_chain_with_trailing_element(tmp_path):
    pqr = tmp_path / "pocket2_vert.pqr"
    pqr.write_text(
        "ATOM 1 C STP 1 10.000 20.000 30.000 0.000 2.500 C\n",
        encoding="utf-8",
    )
    detector = PocketDetectorEnrichedFixed.__new__(PocketDetectorEnrichedFixed)

    pocket = detector._parse_pocket_vert_file(str(pqr), pocket_id=2)

    assert pocket is not None
    assert pocket["coordinates"] == [[10.0, 20.0, 30.0]]
    assert pocket["max_alpha_sphere_radius_A"] == 2.5


def test_fpocket_pqr_parser_handles_optional_chain_column(tmp_path):
    pqr = tmp_path / "pocket2_vert.pqr"
    pqr.write_text(
        "ATOM 1 C STP A 1 10.000 20.000 30.000 0.000 2.500\n",
        encoding="utf-8",
    )
    detector = PocketDetectorEnrichedFixed.__new__(PocketDetectorEnrichedFixed)

    pocket = detector._parse_pocket_vert_file(str(pqr), pocket_id=2)

    assert pocket is not None
    assert pocket["coordinates"] == [[10.0, 20.0, 30.0]]
    assert pocket["max_alpha_sphere_radius_A"] == 2.5


def test_fpocket_detects_and_ranks_numeric_ids_by_druggability_score(tmp_path, monkeypatch):
    def fake_run(command, *, capture_output, text, timeout, cwd):
        output = Path(cwd) / "protein_out"
        pockets = output / "pockets"
        pockets.mkdir(parents=True)
        # Deliberately create in non-numeric order.
        for pocket_id, x in [(10, 10.0), (2, 2.0), (1, 1.0)]:
            (pockets / f"pocket{pocket_id}_vert.pqr").write_text(
                _pqr_line(1, x, 3.5), encoding="utf-8"
            )
        (output / "protein_info.txt").write_text(
            "Pocket 1 :\n\tScore :\t1.000\n\tDruggability Score :\t0.300\n"
            "Pocket 2 :\n\tScore :\t2.000\n\tDruggability Score :\t0.900\n"
            "Pocket 10 :\n\tScore :\t10.000\n\tDruggability Score :\t0.600\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(detector_module.subprocess, "run", fake_run)
    detector = PocketDetectorEnrichedFixed.__new__(PocketDetectorEnrichedFixed)
    detector.fpocket_available = True
    detector.methods_available = ["fpocket"]

    pockets, error = detector.detect_pockets_fpocket(
        [{"element": "C", "atom_name": "CA", "residue_name": "ALA",
          "residue_id": 1, "x": 0.0, "y": 0.0, "z": 0.0}]
    )

    assert error is None
    assert [p["pocket_id"] for p in pockets] == [2, 10, 1]
    assert [p["fpocket_pocket_number"] for p in pockets] == [2, 10, 1]
    assert [p["fpocket_druggability_score"] for p in pockets] == [0.9, 0.6, 0.3]
    assert pockets[0]["selection_score"] == 0.9
    assert pockets[0]["confidence"] is None
    assert "not a probability" in pockets[0]["score_semantics"]


def test_heuristic_pockets_are_disabled_unless_explicitly_enabled(monkeypatch):
    detector = PocketDetectorEnrichedFixed.__new__(PocketDetectorEnrichedFixed)
    detector.fpocket_available = False
    detector.methods_available = []
    monkeypatch.setattr(
        detector_module,
        "pocket_detector_enriched_fixed",
        detector,
    )
    monkeypatch.setattr(
        detector,
        "detect_pockets_alternative_methods",
        lambda atoms: ([{
            "pocket_id": 7,
            "score": 0.85,
            "center_x": 1.0,
            "center_y": 2.0,
            "center_z": 3.0,
            "size_x": 16.0,
            "size_y": 16.0,
            "size_z": 16.0,
        }], None),
    )

    pockets, error = detect_binding_pockets_enriched_fixed([{"element": "C"}])
    assert pockets is None
    assert error and "fallback heuristique est désactivé" in error

    pockets, error = detect_binding_pockets_enriched_fixed(
        [{"element": "C"}],
        allow_heuristic_fallback=True,
    )
    assert error is None
    assert pockets[0]["detection_source"] == "heuristic_fallback"
    assert pockets[0]["quality_class"] == "exploratory_heuristic_candidate"
    assert pockets[0]["confidence"] is None
    assert "non calibré" in pockets[0]["quality_warning"]
