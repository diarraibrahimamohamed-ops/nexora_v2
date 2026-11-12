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
    assert provider == "openbabel"
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
