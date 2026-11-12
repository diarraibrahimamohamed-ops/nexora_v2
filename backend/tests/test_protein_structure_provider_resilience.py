from pathlib import Path
import shutil

from Bio.PDB import MMCIFIO, PDBParser

from app.core.protein import esmfold_runner, tamarind_runner
from app.core.protein.structure_resolver import (
    _afdb_accession_from_csm_id,
    resolve_computed_alphafold_model,
)
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


class _FakeProviderResponse:
    def __init__(self, *, payload=None, text="", status_code=200):
        self._payload = payload
        if payload is not None and not text:
            import json
            text = json.dumps(payload)
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("No JSON payload")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            request = httpx.Request("GET", "https://test.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("provider error", request=request, response=response)


class _FakeAlphaFoldClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)


def test_computed_rcsb_hit_downloads_current_alphafold_db_cif_and_validates_chain(tmp_path, monkeypatch):
    import httpx

    sequence = "ACDEFGHIKLMNPQRSTVWYACDE"
    model_cif = tmp_path / "AF-B3EWR1-F1-model_v6.cif"
    _write_mmcif(model_cif, sequence, b_factor=91.0)
    search_response = _FakeProviderResponse(payload={
        "result_set": [{"identifier": "AF_AFB3EWR1F1", "score": 1.0}]
    })
    prediction_response = _FakeProviderResponse(payload=[{
        "modelEntityId": "AF-B3EWR1-F1",
        "sequence": sequence,
        "sequenceStart": 1,
        "sequenceEnd": len(sequence),
        "latestVersion": 6,
        "globalMetricValue": 91.0,
        "cifUrl": "https://alphafold.ebi.ac.uk/files/AF-B3EWR1-F1-model_v6.cif",
    }])
    cif_response = _FakeProviderResponse(text=model_cif.read_text(encoding="utf-8"))
    fake_client = _FakeAlphaFoldClient([search_response, prediction_response, cif_response])
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake_client)
    monkeypatch.setattr("app.core.protein.structure_resolver.time.sleep", lambda seconds: None)

    path, error, metadata = resolve_computed_alphafold_model(
        sequence,
        str(tmp_path),
        identity_cutoff=0.7,
        coverage_cutoff=0.7,
    )

    assert error is None
    assert path is not None and Path(path).suffix == ".pdb"
    assert metadata["source_type"] == "predicted_alphafold_db"
    assert metadata["model_entity_id"] == "AF-B3EWR1-F1"
    assert metadata["model_version"] == 6
    assert metadata["global_plddt"] == 91.0
    assert metadata["sequence_validation"]["eligible_for_docking_review"] is True
    assert metadata["sequence_validation"]["predicted_confidence"]["mean"] == 91.0
    assert fake_client.calls[0][2]["json"]["request_options"]["results_content_type"] == ["computational"]
    assert fake_client.calls[0][2]["json"]["return_type"] == "entry"


def test_computed_rcsb_hit_skips_modelarchive_instead_of_misclassifying_it(tmp_path, monkeypatch):
    import httpx

    fake_client = _FakeAlphaFoldClient([
        _FakeProviderResponse(payload={
            "result_set": [{"identifier": "MA_MODELARCHIVE123", "score": 1.0}]
        })
    ])
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake_client)

    path, error, metadata = resolve_computed_alphafold_model(
        "ACDEFGHIKLMNPQRSTVWYACDE",
        str(tmp_path),
    )

    assert path is None
    assert error is not None and "ModelArchive" in error
    assert metadata["modelarchive_candidates_skipped"] == 1
    assert metadata["candidates_checked"] == []
    assert len(fake_client.calls) == 1


def test_research_structure_schemas_allow_explicit_alphafold_db_provider():
    from app.api.v1.research import StructureResolveCreate
    from app.api.v1.scientific_workflows import ResearchStructureWorkflowRequest

    assert StructureResolveCreate(provider="alphafold_db").provider == "alphafold_db"
    assert ResearchStructureWorkflowRequest(provider="alphafold_db").provider == "alphafold_db"


def test_research_structure_schemas_still_reject_unknown_providers():
    import pytest
    from pydantic import ValidationError
    from app.api.v1.research import StructureResolveCreate

    with pytest.raises(ValidationError):
        StructureResolveCreate(provider="made_up_provider")


def test_alphafold_csm_identifier_parser_accepts_entry_and_polymer_entity_ids():
    assert _afdb_accession_from_csm_id("AF_AFB3EWR1F1") == ("B3EWR1", "AF-B3EWR1-F1")
    assert _afdb_accession_from_csm_id("AF_AFB3EWR1F1_1") == ("B3EWR1", "AF-B3EWR1-F1")
    assert _afdb_accession_from_csm_id("MA_MODELARCHIVE123") is None
