"""Regression tests for the African natural-products curation workflow."""
import csv
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.config import settings
from app.models.phytochemistry import PhytochemicalCandidate
from app.services import phytochemistry_catalog as catalog
from app.services.phytochemistry_catalog import (
    CurationError,
    IDENTITY_REJECTED,
    IDENTITY_VERIFIED,
    IMPORTED,
    OCCURRENCE_VERIFIED,
    import_from_afrodb,
    import_from_anpdb,
    import_sahel_50,
    ready_for_docking,
    verify_identity,
    verify_occurrence,
)
from app.services.virtual_screening import ScreeningOptions, standardize_and_profile, prepare_library


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_sahel_seed_has_exactly_50_unresolved_candidates(db):
    fixture = Path(__file__).resolve().parents[1] / "app" / "fixtures" / "sahel_50_ligands.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    assert len(payload["records"]) == 50
    result = import_sahel_50(None, db)
    assert result["total_source_records"] == 50
    assert result["status"] == IMPORTED
    rows = db.query(PhytochemicalCandidate).filter(
        PhytochemicalCandidate.library_source == "SAHEL_50"
    ).all()
    assert len(rows) == 50
    assert all(row.identity_status == IMPORTED for row in rows)
    assert all(row.smiles_raw is None and row.smiles_canonical is None for row in rows)
    assert all(row.pubchem_cid is None and row.inchikey is None for row in rows)
    assert all(row.accepted_taxon is None for row in rows)
    assert all(row.occurrence_status != OCCURRENCE_VERIFIED for row in rows)


def test_sahel_seed_import_is_idempotent(db):
    first = import_sahel_50(None, db)
    second = import_sahel_50(None, db)
    assert first["imported"] == 50
    assert second["imported"] == 0
    assert second["skipped_or_duplicate"] == 50
    assert db.query(PhytochemicalCandidate).filter(
        PhytochemicalCandidate.library_source == "SAHEL_50"
    ).count() == 50


def test_anpdb_csv_import_normalizes_smiles_but_leaves_identity_unverified(db, tmp_path):
    source = tmp_path / "anpdb.csv"
    source.write_text(
        "molecule_id,mol_name,smiles,class_name,species,part_org\n"
        "ANP-1,Ethanol,CCO,small molecule,Coffea arabica,fruit\n",
        encoding="utf-8",
    )
    result = import_from_anpdb(str(source), db, max_import=10)
    assert result["imported"] == 1
    row = db.query(PhytochemicalCandidate).filter(
        PhytochemicalCandidate.library_source == "ANPDB"
    ).one()
    assert row.name_raw == "Ethanol"
    assert row.source_row == "ANP-1"
    assert row.smiles_canonical == "CCO"
    assert row.inchikey
    assert row.descriptors["molecular_weight"] > 40
    assert row.identity_status == IMPORTED
    assert row.occurrence_status != OCCURRENCE_VERIFIED
    assert row.source_file_sha256 == result["source_file_sha256"]
    assert row.source_file_path == result["source_file_path"]
    archived = Path(settings.AFRICAN_LIBS_DIR) / row.source_file_path
    assert archived.is_file()
    assert catalog.sha256_file(archived) == row.source_file_sha256


def test_anpdb_csv_reimport_does_not_duplicate_same_source_record(db, tmp_path):
    source = tmp_path / "anpdb.csv"
    source.write_text(
        "molecule_id,mol_name,smiles\nANP-1,Ethanol,CCO\n",
        encoding="utf-8",
    )
    assert import_from_anpdb(str(source), db)["imported"] == 1
    again = import_from_anpdb(str(source), db)
    assert again["imported"] == 0
    assert again["skipped_or_duplicate"] == 1


def test_afrodb_sdf_import_preserves_candidate_status(db, tmp_path):
    from rdkit import Chem

    path = tmp_path / "afrodb.sdf"
    mol = Chem.MolFromSmiles("CCO")
    mol.SetProp("PUBCHEM_IUPAC_TRADITIONAL_NAME", "Ethanol")
    mol.SetProp("PUBCHEM_COMPOUND_CID", "702")
    writer = Chem.SDWriter(str(path))
    writer.write(mol)
    writer.close()

    result = import_from_afrodb(str(path), db, max_import=10)
    assert result["imported"] == 1
    row = db.query(PhytochemicalCandidate).filter(
        PhytochemicalCandidate.library_source == "AFRODB"
    ).one()
    assert row.pubchem_cid == 702
    assert row.smiles_canonical == "CCO"
    assert row.identity_status == IMPORTED


def test_pubchem_identity_verification_requires_name_structure_and_cid_agreement(monkeypatch):
    from rdkit import Chem
    from rdkit.Chem import inchi

    expected_key = inchi.MolToInchiKey(Chem.MolFromSmiles("CCO"))

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self.data

    def fake_get(url, **kwargs):
        if "/synonyms/" in url:
            return Response({"InformationList": {"Information": [{"Synonym": ["Ethanol", "ethyl alcohol"]}]}})
        return Response({"PropertyTable": {"Properties": [{
            "CID": 702,
            "InChIKey": expected_key,
            "ConnectivitySMILES": "CCO",
            "MolecularFormula": "C2H6O",
        }]}})

    monkeypatch.setattr(catalog.httpx, "get", fake_get)
    candidate = PhytochemicalCandidate(
        name_raw="Ethanol", library_source="MANUAL", source_row="test-1",
        identity_status=IMPORTED, occurrence_status="A_VÉRIFIER",
    )
    result = verify_identity(candidate, 702, expected_key, "OCC")
    assert result["verified"] is True
    assert candidate.identity_status == IDENTITY_VERIFIED
    assert candidate.inchikey == expected_key
    assert candidate.smiles_canonical == "CCO"
    assert candidate.formula == "C2H6O"


def test_cryptolepine_identity_uses_confirmed_pubchem_cid_82143(monkeypatch):
    pubchem_key = "KURWKDDWCJELSV-UHFFFAOYSA-N"
    pubchem_smiles = "CN1C2=CC=CC=C2C=C3C1=C4C=CC=CC4=N3"

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self.data

    def fake_get(url, **kwargs):
        if "/synonyms/" in url:
            return Response({"InformationList": {"Information": [{"Synonym": ["Cryptolepine", "480-26-2"]}]}})
        return Response({"PropertyTable": {"Properties": [{
            "CID": 82143, "InChIKey": pubchem_key,
            "ConnectivitySMILES": pubchem_smiles, "MolecularFormula": "C16H12N2",
        }]}})

    monkeypatch.setattr(catalog.httpx, "get", fake_get)
    candidate = PhytochemicalCandidate(
        name_raw="Cryptolepine", library_source="SAHEL_50", source_row="8",
        identity_status=IMPORTED,
    )
    result = verify_identity(candidate, 82143, pubchem_key, pubchem_smiles)
    assert result["verified"] is True
    assert candidate.pubchem_cid == 82143
    assert candidate.inchikey == pubchem_key
    assert candidate.formula == "C16H12N2"
    assert candidate.identity_status == IDENTITY_VERIFIED


def test_pubchem_identity_mismatch_cannot_be_verified(monkeypatch):
    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self.data

    def fake_get(url, **kwargs):
        if "/synonyms/" in url:
            return Response({"InformationList": {"Information": [{"Synonym": ["Cryptolepine"]}]}})
        return Response({"PropertyTable": {"Properties": [{
            "CID": 702, "InChIKey": "DIFFERENT-INCHIKEY", "ConnectivitySMILES": "CCO",
            "MolecularFormula": "C2H6O",
        }]}})

    from rdkit import Chem
    from rdkit.Chem import inchi
    ethanol_key = inchi.MolToInchiKey(Chem.MolFromSmiles("CCO"))
    monkeypatch.setattr(catalog.httpx, "get", fake_get)
    candidate = PhytochemicalCandidate(
        name_raw="Cryptolepine", library_source="SAHEL_50", source_row="8",
        identity_status=IMPORTED,
    )
    result = verify_identity(candidate, 702, ethanol_key, "CCO")
    assert result["verified"] is False
    assert candidate.identity_status == IDENTITY_REJECTED


def test_taxonomy_lookup_does_not_verify_occurrence_without_explicit_curator_attestation(monkeypatch):
    from app.services.phytochemistry_catalog import CurationError

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"usageKey": 123, "rank": "SPECIES", "status": "ACCEPTED",
                    "scientificName": "Coffea arabica"}

    monkeypatch.setattr(catalog.httpx, "get", lambda *args, **kwargs: Response())
    candidate = PhytochemicalCandidate(
        name_raw="Caffeine", library_source="MANUAL", source_row="test-2",
        source_plant="Coffea arabica", identity_status=IMPORTED,
    )
    with pytest.raises(CurationError):
        verify_occurrence(
            candidate, "Coffea arabica", "https://doi.org/10.1234/example",
            False, "curator", "An explicit review note for the occurrence record.",
        )
    assert candidate.occurrence_status != OCCURRENCE_VERIFIED


def test_taxonomy_and_occurrence_attestation_are_separate_from_identity(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"usageKey": 123, "rank": "SPECIES", "status": "ACCEPTED",
                    "scientificName": "Coffea arabica"}

    monkeypatch.setattr(catalog.httpx, "get", lambda *args, **kwargs: Response())
    candidate = PhytochemicalCandidate(
        name_raw="Caffeine", library_source="MANUAL", source_row="test-3",
        source_plant="Coffea arabica", identity_status=IMPORTED,
    )
    result = verify_occurrence(
        candidate, "Coffea arabica", "https://doi.org/10.1234/example",
        True, "curator", "Reviewed source for the reported plant-compound occurrence.",
    )
    assert result["verified"] is True
    assert candidate.occurrence_status == OCCURRENCE_VERIFIED
    assert candidate.identity_status == IMPORTED  # taxonomy does not verify chemistry


def test_candidate_without_verified_identity_and_occurrence_is_blocked():
    candidate = PhytochemicalCandidate(
        name_raw="Erinacine A (Plante)", library_source="SAHEL_50", source_row="36",
        identity_status=IMPORTED, occurrence_status="A_VÉRIFIER",
    )
    ready, blockers = ready_for_docking(candidate)
    assert ready is False
    assert any("identité chimique" in item for item in blockers)
    assert any("occurrence botanique" in item for item in blockers)


def test_african_natural_product_mode_relaxes_default_mw_gate_without_claiming_activity():
    from rdkit import Chem
    from rdkit.Chem import Descriptors

    smiles = "O" + "C(O)" * 18 + "O"
    mol = Chem.MolFromSmiles(smiles)
    assert 500 < Descriptors.MolWt(mol) < 600
    standard = standardize_and_profile(
        smiles,
        ScreeningOptions(max_hbd=None, max_hba=None, max_rotatable_bonds=30, max_tpsa=None),
    )
    african = standardize_and_profile(
        smiles,
        ScreeningOptions(
            max_hbd=None, max_hba=None, max_rotatable_bonds=30, max_tpsa=None,
            african_natural_product_mode=True,
        ),
    )
    assert standard["accepted"] is False
    assert "max_mw" in standard["rejected_by"]
    assert african["accepted"] is True
    assert african["natural_product_mode"] is True
    assert "activity_score" not in african


def test_screening_library_preserves_duplicate_source_indices():
    prepared = prepare_library(
        ["CCO", "OCC", "CC(=O)O"],
        ScreeningOptions(),
    )
    assert prepared["duplicates_removed"] == 1
    assert len(prepared["duplicate_records"]) == 1
    duplicate = prepared["duplicate_records"][0]
    assert duplicate["input_index"] == 1
    assert duplicate["duplicate_of_input_index"] == 0
    assert len(prepared["compounds"]) == 2
