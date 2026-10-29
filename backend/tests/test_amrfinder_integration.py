from pathlib import Path
from types import SimpleNamespace

import pytest
from app.services import amr_analysis as amr


def test_normalize_amr_fasta_preserves_ambiguous_bases_and_contig_boundaries():
    fasta, records, length = amr.normalize_amr_fasta(">contig-one\nACGTNN\n>contig-two\nRYSWKMBDHV\n")
    assert records == 2
    assert length == 16
    assert ">N3XORA_contig_1\nACGTNN" in fasta
    assert ">N3XORA_contig_2\nRYSWKMBDHV" in fasta


def test_normalize_amr_fasta_rejects_gaps_and_protein_letters():
    with pytest.raises(ValueError, match="Séquence invalide"):
        amr.normalize_amr_fasta("ACGT--ACGT")
    with pytest.raises(ValueError, match="Séquence invalide"):
        amr.normalize_amr_fasta("MKWVTF")


def test_virus_and_human_are_not_sent_to_amrfinder(monkeypatch):
    monkeypatch.setattr(amr, "_run_amrfinder", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not run")))
    assert amr.analyze_amr("not bacterial DNA", "virus")["applicability"] == "not_applicable"
    assert amr.analyze_amr("not bacterial DNA", "human")["applicability"] == "not_applicable"


def test_taxonomic_group_is_passed_as_an_argument(tmp_path, monkeypatch):
    database = tmp_path / "db"
    database.mkdir()
    monkeypatch.setattr(amr, "amrfinder_status", lambda: {
        "ready": True, "executable_path": "/usr/bin/amrfinder", "database_path": str(database),
        "version": "AMRFinderPlus 4.2.7", "database_version": "test-db", "database_release": "2026-08-07.1",
    })
    monkeypatch.setattr(amr, "list_amrfinder_organisms", lambda: ["Escherichia coli"])
    observed = {}
    def fake_run(command, **kwargs):
        observed["command"] = list(command)
        output = Path(command[command.index("-o") + 1])
        output.write_text(
            "Protein identifier\tContig id\tElement symbol\tMethod\t% Identity\t% Coverage\tSubclass\tAMR Gene Family\n"
            "N3XORA_contig_1\tN3XORA_contig_1\tblaKPC\tEXACT\t100\t100\tcarbapenemase\tbeta-lactamase\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(amr.subprocess, "run", fake_run)
    result = amr._run_amrfinder(">contig\nACGT\n", "Escherichia coli")
    assert result["available"] is True
    assert result["hits"][0]["gene_symbol"] == "blaKPC"
    assert observed["command"][observed["command"].index("--organism") + 1] == "Escherichia coli"
    assert "--plus" not in observed["command"]


def test_unknown_taxonomic_group_is_not_submitted(monkeypatch, tmp_path):
    monkeypatch.setattr(amr, "amrfinder_status", lambda: {
        "ready": True, "executable_path": "/usr/bin/amrfinder", "database_path": str(tmp_path),
        "version": "AMRFinderPlus 4.2.7"
    })
    monkeypatch.setattr(amr, "list_amrfinder_organisms", lambda: ["Escherichia coli"])
    result = amr._run_amrfinder(">contig\nACGT\n", "not-a-real-taxon")
    assert result["available"] is False
    assert "Groupe taxonomique non reconnu" in result["reason"]
