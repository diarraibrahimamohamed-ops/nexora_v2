from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.v1.docking import _extract_protein_from_analysis
from app.core.docking import vina_runner
from app.core.protein.colabfold_runner import _parse_pdb_native, pdb_to_atom_dicts
from app.core.protein.sequence_validation import (
    ProteinSequenceValidationError,
    normalize_protein_sequence,
)


AA3 = {"A": "ALA", "C": "CYS", "D": "ASP", "E": "GLU", "G": "GLY"}


def _write_multichain_pdb(path: Path):
    lines = []
    serial = 1
    for chain_id, sequence in [("A", "AC"), ("B", "GGG")]:
        for residue_id, aa in enumerate(sequence, start=1):
            lines.append(
                f"ATOM  {serial:5d}  CA  {AA3[aa]} {chain_id}{residue_id:4d}    "
                f"{float(serial):8.3f}{0.0:8.3f}{0.0:8.3f}  1.00{80.0:6.2f}           C  "
            )
            serial += 1
    lines.append("END")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_normalize_protein_sequence_only_removes_whitespace_and_case():
    assert normalize_protein_sequence("mkai\n fvlkgt") == "MKAIFVLKGT"


@pytest.mark.parametrize("symbol", ["X", "B", "Z", "J", "U", "O", ">", "*", "1"])
def test_normalize_protein_sequence_rejects_ambiguous_or_invalid_symbols(symbol):
    with pytest.raises(ProteinSequenceValidationError, match="position"):
        normalize_protein_sequence(f"MKA{symbol}FVLKGT")


def test_normalize_protein_sequence_enforces_length():
    with pytest.raises(ProteinSequenceValidationError, match="maximum 1000"):
        normalize_protein_sequence("A" * 1001)


def test_analysis_extraction_does_not_mistake_dna_for_protein():
    analysis = SimpleNamespace(
        type="DNA",
        data={"sequence": "ATGCGATCGATCG"},
        sequences=[],
    )
    assert _extract_protein_from_analysis(analysis) is None


def test_analysis_extraction_rejects_ambiguous_protein_data():
    analysis = SimpleNamespace(
        type="protein",
        data={"protein_data": {"sequence": "MKAIXVLKGT"}},
        sequences=[],
    )
    with pytest.raises(ProteinSequenceValidationError):
        _extract_protein_from_analysis(analysis)


def test_pdb_atom_parser_selects_only_validated_chain(tmp_path):
    pdb = tmp_path / "two_chains.pdb"
    _write_multichain_pdb(pdb)

    atoms_a, error_a = pdb_to_atom_dicts(str(pdb), chain_id="A")
    atoms_b, error_b = pdb_to_atom_dicts(str(pdb), chain_id="B")

    assert error_a is None and error_b is None
    assert len(atoms_a) == 2
    assert len(atoms_b) == 3


def test_native_pdb_parser_selects_only_requested_chain(tmp_path):
    pdb = tmp_path / "two_chains.pdb"
    _write_multichain_pdb(pdb)

    atoms, error = _parse_pdb_native(str(pdb), chain_id="A")
    assert error is None
    assert len(atoms) == 2


def test_pdbqt_temporary_directory_is_removed_after_preparation(tmp_path, monkeypatch):
    observed = {}

    def fake_ligand(smiles, out_dir):
        path = Path(out_dir) / "ligand.tmp"
        path.write_text("ligand", encoding="utf-8")
        observed["dir"] = Path(out_dir)
        return "LIGAND PDBQT", None

    def fake_receptor(atoms, out_dir):
        path = Path(out_dir) / "receptor.tmp"
        path.write_text("receptor", encoding="utf-8")
        return "RECEPTOR PDBQT", None

    monkeypatch.setattr(vina_runner, "_pdbqt_ligand_via_obabel", fake_ligand)
    monkeypatch.setattr(vina_runner, "_pdbqt_receptor_via_obabel", fake_receptor)

    receptor, ligand, provider, error = vina_runner._prepare_pdbqt_inputs(
        [{"element": "C"}], [{"element": "C"}], "CCO", 0
    )

    assert error is None
    assert receptor == "RECEPTOR PDBQT"
    assert ligand == "LIGAND PDBQT"
    assert provider["provider"] == "openbabel"
    assert provider["ligand_flexible"] is True
    assert not observed["dir"].exists()


def test_invalid_sequence_fails_before_vina_or_structure_lookup(monkeypatch):
    monkeypatch.setattr(vina_runner, "_resolve_vina_executable", lambda _: "/fake/vina")
    result = vina_runner.run_scientific_docking("MKAIXVLKGT", "CCO")
    assert result["success"] is False
    assert result["stage"] == "protein_validation"
    assert "position" in result["error_message"]


def test_vina_binary_resolution_uses_configured_executable(monkeypatch):
    monkeypatch.setattr(vina_runner.shutil, "which", lambda binary: "/opt/bin/vina" if binary == "vina-custom" else None)
    assert vina_runner._resolve_vina_executable("vina-custom") == "/opt/bin/vina"


def test_permissive_rcsb_retry_is_skipped_for_transient_provider_failures():
    assert vina_runner._should_retry_permissive_rcsb({"failure_category": "no_experimental_hit"}) is True
    assert vina_runner._should_retry_permissive_rcsb({"failure_category": "no_candidate_passed_sequence_validation"}) is True
    assert vina_runner._should_retry_permissive_rcsb({"failure_category": "provider_unavailable"}) is False
    assert vina_runner._should_retry_permissive_rcsb({"failure_category": "provider_timeout"}) is False
    assert vina_runner._should_retry_permissive_rcsb({"failure_category": "invalid_provider_response"}) is False


def test_fpocket_readiness_only_reports_executable_presence(monkeypatch):
    from app.services import tool_status

    monkeypatch.setattr(tool_status.shutil, "which", lambda name: "/usr/local/bin/fpocket" if name == "fpocket" else None)
    report = tool_status.fpocket_status()

    assert report["available"] is True
    assert report["path"] == "/usr/local/bin/fpocket"
    assert report["probe"] == "binary_lookup_only"
    assert "version" not in report


def test_internal_pdbqt_generator_outputs_valid_rigid_ligand_and_receptor():
    atoms = [{
        "atom_id": 1, "atom_name": "C", "residue_name": "LIG",
        "residue_id": 1, "x": 1.25, "y": -2.5, "z": 3.75,
        "element": "C", "charge": 0.0,
    }]
    receptor, receptor_error = vina_runner.create_pdbqt_from_atoms(atoms, "receptor")
    ligand, ligand_error = vina_runner.create_pdbqt_from_atoms(atoms, "ligand", torsdof=4)

    assert receptor_error is None and ligand_error is None
    receptor_report, receptor_validation_error = vina_runner.validate_pdbqt_content(
        receptor, "receptor"
    )
    ligand_report, ligand_validation_error = vina_runner.validate_pdbqt_content(
        ligand, "ligand"
    )
    assert receptor_validation_error is None
    assert ligand_validation_error is None
    assert receptor_report["atom_count"] == 1
    assert receptor_report["torsdof"] is None
    assert ligand_report["atom_count"] == 1
    assert ligand_report["torsdof"] == 0
    assert ligand_report["branch_count"] == 0


def test_pdbqt_validator_rejects_ligand_torsdof_branch_mismatch():
    atoms = [{
        "atom_id": 1, "atom_name": "C", "residue_name": "LIG",
        "residue_id": 1, "x": 0.0, "y": 0.0, "z": 0.0,
        "element": "C", "charge": 0.0,
    }]
    ligand, error = vina_runner.create_pdbqt_from_atoms(atoms, "ligand", torsdof=0)
    assert error is None
    invalid = ligand.replace("TORSDOF 0", "BRANCH 1 2\nENDBRANCH 1 2\nTORSDOF 0")
    report, validation_error = vina_runner.validate_pdbqt_content(invalid, "ligand")
    assert report is None
    assert "TORSDOF incohérent" in validation_error


def test_pdbqt_validator_rejects_nonfinite_coordinates_and_unknown_atom_types():
    atoms = [{
        "atom_id": 1, "atom_name": "C", "residue_name": "LIG",
        "residue_id": 1, "x": 0.0, "y": 0.0, "z": 0.0,
        "element": "C", "charge": 0.0,
    }]
    receptor, error = vina_runner.create_pdbqt_from_atoms(atoms, "receptor")
    assert error is None
    invalid_coords = receptor.replace("   0.000", "     nan", 1)
    report, validation_error = vina_runner.validate_pdbqt_content(invalid_coords, "receptor")
    assert report is None
    assert "non finies" in validation_error

    invalid_type = receptor.rstrip()[:-1] + "Q"
    report, validation_error = vina_runner.validate_pdbqt_content(invalid_type, "receptor")
    assert report is None
    assert "Type AutoDock/Vina" in validation_error


def test_vina_environment_preserves_host_paths_and_library_paths(monkeypatch):
    monkeypatch.setenv("PATH", "/home/user/.local/bin:/opt/vina/bin")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/rdkit/lib:/opt/openbabel/lib")
    monkeypatch.setenv("LD_PRELOAD", "/tmp/untrusted-preload.so")

    env = vina_runner._build_vina_environment()

    assert "/home/user/.local/bin" in env["PATH"]
    assert "/opt/vina/bin" in env["PATH"]
    assert "/opt/rdkit/lib" in env["LD_LIBRARY_PATH"]
    assert "/opt/openbabel/lib" in env["LD_LIBRARY_PATH"]
    assert "LD_PRELOAD" not in env


def test_calculation_manifest_hashes_inputs_and_records_tool_fingerprints(tmp_path, monkeypatch):
    structure = tmp_path / "receptor.pdb"
    structure.write_text("ATOM test structure\\n", encoding="utf-8")
    receptor_pdbqt = "RECEPTOR PDBQT"
    ligand_pdbqt = "LIGAND PDBQT"
    fake_vina = tmp_path / "vina"
    fake_vina.write_text("#!/bin/sh\\necho AutoDock Vina 1.2.5\\n", encoding="utf-8")
    fake_vina.chmod(0o755)

    monkeypatch.setattr(vina_runner.shutil, "which", lambda name: None)
    manifest = vina_runner.build_calculation_manifest(
        protein_sequence="ACDEFGHIK",
        ligand_smiles="CCO",
        ligand_data={"name": "CCO", "atoms": [{"element": "C"}]},
        structure_path=str(structure),
        structure_metadata={"source_type": "experimental_pdb", "entry_id": "1ABC"},
        vina_executable=str(fake_vina),
        receptor_pdbqt=receptor_pdbqt,
        ligand_pdbqt=ligand_pdbqt,
        pdbqt_metadata={"provider": "internal_fallback"},
        parameters={"vina_cpu": 1, "random_seed": 42},
        pockets=[{"candidate_index": 0, "source_pocket_id": 2, "detection_source": "fpocket"}],
    )

    assert manifest["schema"] == "nexora.docking-manifest.v1"
    assert len(manifest["inputs"]["protein_sequence_sha256"]) == 64
    assert manifest["inputs"]["receptor_structure_sha256"] is not None
    assert manifest["pdbqt"]["ligand_sha256"] == vina_runner._sha256_text(ligand_pdbqt)
    assert manifest["pdbqt"]["receptor_sha256"] == vina_runner._sha256_text(receptor_pdbqt)
    assert manifest["pdbqt"]["provider"] == "internal_fallback"
    assert manifest["toolchain"]["autodock_vina"]["sha256"] is not None
    assert manifest["parameters"]["random_seed"] == 42
    assert manifest["pocket_candidates"][0]["source_pocket_id"] == 2


def test_pdbqt_validator_accepts_internal_rigid_ligand_and_receptor():
    atoms = [{
        "atom_id": 1, "atom_name": "C", "residue_name": "LIG",
        "residue_id": 1, "x": 1.25, "y": -2.5, "z": 3.75,
        "element": "C", "charge": 0.0,
    }]
    receptor, receptor_error = vina_runner.create_pdbqt_from_atoms(atoms, "receptor")
    ligand, ligand_error = vina_runner.create_pdbqt_from_atoms(atoms, "ligand", torsdof=4)
    assert receptor_error is None and ligand_error is None

    receptor_report, receptor_validation_error = vina_runner.validate_pdbqt_content(
        receptor, "receptor"
    )
    ligand_report, ligand_validation_error = vina_runner.validate_pdbqt_content(
        ligand, "ligand"
    )

    assert receptor_validation_error is None
    assert ligand_validation_error is None
    assert receptor_report["atom_count"] == 1
    assert receptor_report["torsdof"] is None
    assert ligand_report["atom_count"] == 1
    assert ligand_report["torsdof"] == 0
    assert ligand_report["branch_count"] == 0


def test_pdbqt_validator_rejects_ligand_torsdof_branch_mismatch():
    atoms = [{
        "atom_id": 1, "atom_name": "C", "residue_name": "LIG",
        "residue_id": 1, "x": 0.0, "y": 0.0, "z": 0.0,
        "element": "C", "charge": 0.0,
    }]
    ligand, error = vina_runner.create_pdbqt_from_atoms(atoms, "ligand", torsdof=0)
    assert error is None
    invalid = ligand.replace("TORSDOF 0", "TORSDOF 1")
    report, validation_error = vina_runner.validate_pdbqt_content(invalid, "ligand")
    assert report is None
    assert "TORSDOF incohérent" in validation_error


def test_pdbqt_validator_rejects_nonfinite_coordinates_and_unknown_atom_types():
    atoms = [{
        "atom_id": 1, "atom_name": "C", "residue_name": "LIG",
        "residue_id": 1, "x": 0.0, "y": 0.0, "z": 0.0,
        "element": "C", "charge": 0.0,
    }]
    receptor, error = vina_runner.create_pdbqt_from_atoms(atoms, "receptor")
    assert error is None
    invalid_coords = receptor.replace("   0.000", "     nan", 1)
    report, validation_error = vina_runner.validate_pdbqt_content(invalid_coords, "receptor")
    assert report is None
    assert "non finies" in validation_error

    invalid_type = receptor.rstrip()[:-1] + "Q"
    report, validation_error = vina_runner.validate_pdbqt_content(invalid_type, "receptor")
    assert report is None
    assert "Type AutoDock/Vina" in validation_error


def test_pdbqt_preparation_fallback_records_rigid_ligand_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(
        vina_runner,
        "_pdbqt_ligand_via_obabel",
        lambda smiles, out_dir: (None, "obabel CLI unavailable"),
    )
    monkeypatch.setattr(
        vina_runner,
        "_pdbqt_receptor_via_obabel",
        lambda atoms, out_dir: ("native receptor should be discarded", None),
    )
    protein_atoms = [{
        "atom_id": 1, "atom_name": "CA", "residue_name": "ALA",
        "residue_id": 1, "x": 0.0, "y": 0.0, "z": 0.0,
        "element": "C", "charge": 0.0,
    }]
    ligand_atoms = [{
        "atom_id": 1, "atom_name": "C", "residue_name": "LIG",
        "residue_id": 1, "x": 1.0, "y": 0.0, "z": 0.0,
        "element": "C", "charge": 0.0,
    }]

    receptor, ligand, provider, error = vina_runner._prepare_pdbqt_inputs(
        protein_atoms, ligand_atoms, "CCO", 3
    )

    assert error is None
    assert provider["provider"] == "internal_fallback"
    assert provider["ligand_provider"] == "internal_fallback"
    assert provider["receptor_provider"] == "internal_fallback"
    assert provider["ligand_flexible"] is False
    assert provider["ligand_torsdof_requested"] == 3
    assert provider["ligand_torsdof_effective"] == 0
    assert "obabel CLI unavailable" in provider["fallback_reason"]["ligand"]
    assert vina_runner.validate_pdbqt_content(receptor, "receptor")[1] is None
    assert vina_runner.validate_pdbqt_content(ligand, "ligand")[1] is None


def test_vina_environment_preserves_host_path_and_library_paths(monkeypatch):
    monkeypatch.setenv("PATH", "/home/user/.local/bin:/opt/vina/bin")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/rdkit/lib:/opt/openbabel/lib")
    monkeypatch.setenv("LD_PRELOAD", "/tmp/untrusted-preload.so")

    env = vina_runner._build_vina_environment()

    assert "/home/user/.local/bin" in env["PATH"]
    assert "/opt/vina/bin" in env["PATH"]
    assert "/opt/rdkit/lib" in env["LD_LIBRARY_PATH"]
    assert "/opt/openbabel/lib" in env["LD_LIBRARY_PATH"]
    assert "LD_PRELOAD" not in env


def test_calculation_manifest_hashes_inputs_and_records_toolchain(tmp_path, monkeypatch):
    structure = tmp_path / "receptor.pdb"
    structure.write_text("ATOM test structure\\n", encoding="utf-8")
    receptor_pdbqt = "RECEPTOR PDBQT"
    ligand_pdbqt = "LIGAND PDBQT"
    fake_vina = tmp_path / "vina"
    fake_vina.write_text("#!/bin/sh\\necho AutoDock Vina 1.2.5\\n", encoding="utf-8")
    fake_vina.chmod(0o755)

    monkeypatch.setattr(vina_runner.shutil, "which", lambda name: None)
    manifest = vina_runner.build_calculation_manifest(
        protein_sequence="ACDEFGHIK",
        ligand_smiles="CCO",
        ligand_data={"name": "CCO", "atoms": [{"element": "C"}]},
        structure_path=str(structure),
        structure_metadata={"source_type": "experimental_pdb", "entry_id": "1ABC"},
        vina_executable=str(fake_vina),
        receptor_pdbqt=receptor_pdbqt,
        ligand_pdbqt=ligand_pdbqt,
        pdbqt_metadata={"provider": "internal_fallback"},
        parameters={"vina_cpu": 1, "random_seed": 42},
        pockets=[{"candidate_index": 0, "source_pocket_id": 2, "detection_source": "fpocket"}],
    )

    assert manifest["schema"] == "nexora.docking-manifest.v1"
    assert len(manifest["inputs"]["protein_sequence_sha256"]) == 64
    assert manifest["inputs"]["receptor_structure_sha256"] is not None
    assert manifest["pdbqt"]["ligand_sha256"] == vina_runner._sha256_text(ligand_pdbqt)
    assert manifest["pdbqt"]["receptor_sha256"] == vina_runner._sha256_text(receptor_pdbqt)
    assert manifest["pdbqt"]["provider"] == "internal_fallback"
    assert manifest["toolchain"]["autodock_vina"]["sha256"] is not None
    assert manifest["parameters"]["random_seed"] == 42
    assert manifest["pocket_candidates"][0]["source_pocket_id"] == 2
