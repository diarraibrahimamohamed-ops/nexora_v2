"""AMRFinderPlus readiness must include both binary and reference database."""
from pathlib import Path

from app.config import settings
from app.services.amr_analysis import amrfinder_status


def test_amrfinder_is_not_ready_when_reference_database_is_missing(tmp_path, monkeypatch):
    binary = tmp_path / "amrfinder"
    binary.write_text("#!/bin/sh\necho AMRFinderPlus version 4.2.7\n", encoding="utf-8")
    binary.chmod(0o755)
    db_root = tmp_path / "reference-db"
    db_root.mkdir()
    monkeypatch.setattr(settings, "AMRFINDER_PATH", str(binary))
    monkeypatch.setattr(settings, "AMRFINDER_DB_DIR", str(db_root))

    report = amrfinder_status()
    assert report["executable_available"] is True
    assert report["version"]
    assert report["database_available"] is False
    assert report["ready"] is False


def test_amrfinder_is_ready_only_when_binary_and_database_exist(tmp_path, monkeypatch):
    binary = tmp_path / "amrfinder"
    binary.write_text("#!/bin/sh\necho AMRFinderPlus version 4.2.7\n", encoding="utf-8")
    binary.chmod(0o755)
    db_root = tmp_path / "reference-db"
    latest = db_root / "latest"
    latest.mkdir(parents=True)
    for name in (
        "version.txt", "AMR.LIB", "AMRProt.fa", "AMRProt-mutation.tsv",
        "fam.tsv", "taxgroup.tsv", "database_format_version.txt",
    ):
        (latest / name).write_text("test marker", encoding="utf-8")

    monkeypatch.setattr(settings, "AMRFINDER_PATH", str(binary))
    monkeypatch.setattr(settings, "AMRFINDER_DB_DIR", str(db_root))
    report = amrfinder_status()
    assert report["executable_available"] is True
    assert report["database_available"] is True
    assert report["ready"] is True


def test_amrfinder_does_not_accept_nonempty_but_incomplete_database(tmp_path, monkeypatch):
    binary = tmp_path / "amrfinder"
    binary.write_text("#!/bin/sh\\necho AMRFinderPlus version 4.2.7\\n", encoding="utf-8")
    binary.chmod(0o755)
    db_root = tmp_path / "reference-db"
    latest = db_root / "latest"
    latest.mkdir(parents=True)
    (latest / "README").write_text("not a valid database", encoding="utf-8")

    monkeypatch.setattr(settings, "AMRFINDER_PATH", str(binary))
    monkeypatch.setattr(settings, "AMRFINDER_DB_DIR", str(db_root))
    report = amrfinder_status()
    assert report["executable_available"] is True
    assert report["database_available"] is False
    assert report["ready"] is False
    assert "AMR.LIB" in report["database_missing_files"]
