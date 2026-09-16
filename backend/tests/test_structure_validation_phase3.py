from pathlib import Path

from app.core.protein.structure_validation import validate_structure


def _pdb_for_sequence(sequence: str, b=80.0):
    lines = []
    serial = 1
    for i, aa in enumerate(sequence, start=1):
        # Only CA atoms are sufficient for sequence correspondence tests.
        aa3 = {"A":"ALA","C":"CYS","D":"ASP","E":"GLU","F":"PHE","G":"GLY","H":"HIS","I":"ILE","K":"LYS","L":"LEU","M":"MET","N":"ASN","P":"PRO","Q":"GLN","R":"ARG","S":"SER","T":"THR","V":"VAL","W":"TRP","Y":"TYR"}[aa]
        lines.append(f"ATOM  {serial:5d}  CA  {aa3} A{i:4d}    {float(i):8.3f}{0.0:8.3f}{0.0:8.3f}  1.00{b:6.2f}           C  ")
        serial += 1
    lines.append("END")
    return "\n".join(lines) + "\n"


def test_experimental_structure_accepts_matching_chain(tmp_path: Path):
    sequence = "ACDEFGHIKLMNPQRSTVWYACDE"
    pdb = tmp_path / "wt.pdb"
    pdb.write_text(_pdb_for_sequence(sequence), encoding="utf-8")
    report = validate_structure(str(pdb), sequence, "experimental_pdb")
    assert report["status"] == "accepted_for_docking_review"
    assert report["eligible_for_docking_review"] is True
    assert report["sequence_match"]["best_chain"]["identity"] == 1.0
    assert report["sequence_match"]["best_chain"]["coverage"] == 1.0
    assert report["predicted_confidence"]["available"] is True


def test_structure_sequence_mismatch_is_not_accepted(tmp_path: Path):
    pdb = tmp_path / "wrong.pdb"
    pdb.write_text(_pdb_for_sequence("ACDEFGHIKLMNPQRSTVWY"), encoding="utf-8")
    report = validate_structure(str(pdb), "AAAAAAAAAAAAAAAAAAAAAAAA", "experimental_pdb")
    assert report["status"] == "sequence_mismatch"
    assert report["eligible_for_docking_review"] is False


def test_predicted_structure_exposes_plddt_but_not_biological_truth(tmp_path: Path):
    sequence = "ACDEFGHIKLMNPQRSTVWYACDE"
    pdb = tmp_path / "predicted.pdb"
    pdb.write_text(_pdb_for_sequence(sequence, b=92.0), encoding="utf-8")
    report = validate_structure(str(pdb), sequence, "predicted_esmfold")
    assert report["status"] == "predicted_reviewable"
    assert report["predicted_confidence"]["mean"] == 92.0
    assert report["predicted_confidence"]["normalized_mean"] == 0.92
    assert "ne constitue pas une validation biologique" in report["note"]
