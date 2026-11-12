from pathlib import Path
import shutil

from Bio.PDB import MMCIFIO, PDBParser

from app.core.protein import esmfold_runner, tamarind_runner
from app.core.protein.structure_validation import validate_structure


AA3 = {
    "A": "ALA", "C": "CYS", "D": "ASP", "E": "GLU", "F": "PHE",
    "G": "GLY", "H": "HIS", "I": "ILE", "K": "LYS", "L": "LEU",
    "M": "MET", "N": "ASN", "P": "PRO", "Q": "GLN", "R": "ARG",
    "S": "SER", "T": "THR", "V": "VAL", "W": "TRP", "Y": "TYR",
}


def _write_pdb(path: Path, sequence: str, b_factor: float = 88.0) -> None:
    lines = []
    for serial, (resid, aa) in enumerate(enumerate(sequence, start=1), start=1):
        lines.append(
            f"ATOM  {serial:5d}  CA  {AA3[aa]} A{resid:4d}    "
            f"{float(resid):8.3f}{0.0:8.3f}{0.0:8.3f}  1.00{b_factor:6.2f}           C  "
        )
    lines.append("END")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_mmcif(path: Path, sequence: str, b_factor: float = 88.0) -> None:
    pdb_path = path.with_suffix(".source.pdb")
    _write_pdb(pdb_path, sequence, b_factor)
    structure = PDBParser(QUIET=True).get_structure("source", str(pdb_path))
    writer = MMCIFIO()
    writer.set_structure(structure)
    writer.save(str(path))


def test_tamarind_mmcif_artifact_is_normalized_to_pdb(tmp_path, monkeypatch):
    sequence = "ACDEFGHIKLMNPQRSTVWYACDE"
    source_cif = tmp_path / "provider-output.cif"
    _write_mmcif(source_cif, sequence, b_factor=88.0)

    def fake_run_job(**kwargs):
        destination = Path(kwargs["output_dir"]) / "tamarind-model.cif"
        shutil.copyfile(source_cif, destination)
        return str(destination), None, {"job_name": "test-job"}

    monkeypatch.setattr(tamarind_runner, "_run_job", fake_run_job)

    path, error, metadata = tamarind_runner.build_tamarind_structure(
        sequence,
        str(tmp_path),
        tamarind_api_key="test-api-key",
    )

    assert error is None
    assert path is not None
    assert Path(path).suffix == ".pdb"
    assert Path(path).is_file()
    assert metadata["source_format"] == "cif"
    assert metadata["normalized_format"] == "pdb"

    report = validate_structure(path, sequence, "predicted_tamarind_esmfold")
    assert report["status"] == "predicted_reviewable"
    assert report["sequence_match"]["best_chain"]["identity"] == 1.0
    assert report["predicted_confidence"]["mean"] == 88.0


def test_long_sequence_skips_atlas_but_attempts_tamarind(tmp_path, monkeypatch):
    sequence = "A" * 401
    prediction = tmp_path / "tamarind-long.pdb"
    prediction.write_text("ATOM  fake\n", encoding="utf-8")
    calls = {}

    monkeypatch.setattr(esmfold_runner.settings, "ENABLE_DEPRECATED_NVIDIA_ESMFOLD", False)
    monkeypatch.setattr(
        esmfold_runner,
        "_via_atlas",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Atlas must be skipped >400 aa")),
    )

    def fake_tamarind(received_sequence, output_dir, timeout_seconds, tamarind_api_key):
        calls["sequence_length"] = len(received_sequence)
        calls["timeout"] = timeout_seconds
        return str(prediction), None, {"provider": "Tamarind Bio"}

    monkeypatch.setattr(esmfold_runner, "build_tamarind_structure", fake_tamarind)

    path, error, metadata = esmfold_runner.build_esmfold_structure(
        sequence,
        str(tmp_path),
    )

    assert error is None
    assert path == str(prediction)
    assert calls["sequence_length"] == 401
    assert calls["timeout"] == 600.0
    assert metadata["provider"] == "Tamarind Bio"
