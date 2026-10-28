"""Tests for versioned research profiles, offline manifests, and evidence retrieval."""
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models.evidence import ResearchEvidence
from app.models.research import ResearchStudy
from app.models.user import User
from app.models.phytochemistry import PhytochemicalCandidate
from app.services.local_research_assistant import retrieve_sources, answer_question
from app.services.offline_packages import (
    OfflinePackageError, create_manifest, verify_manifest, list_manifests,
)
from app.services.research_profiles import list_profiles, get_profile, ResearchProfileError


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_profiles_are_versioned_and_planned_profiles_are_labelled():
    registry = list_profiles()
    by_id = {item["id"]: item for item in registry["profiles"]}
    malaria = by_id["plasmodium_falciparum_sahel_v1"]
    assert malaria["version"] == "1.0.0"
    assert malaria["status"] == "available_needs_case_validation"
    assert by_id["bacterial_amr_v1"]["status"] == "conditional_on_amrfinderplus"
    assert by_id["viral_genotyping_v1"]["status"] == "planned_needs_pathogen_specific_reference"
    copy = get_profile("plasmodium_falciparum_sahel_v1")
    copy["name"] = "changed only in test"
    assert get_profile("plasmodium_falciparum_sahel_v1")["name"] != copy["name"]


def test_unknown_profile_is_not_silently_accepted():
    with pytest.raises(ResearchProfileError):
        get_profile("not-a-profile")


def test_offline_manifest_integrity_and_mutation_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OFFLINE_PACKAGES_DIR", str(tmp_path))
    source = tmp_path / "malaria" / "reference.md"
    source.parent.mkdir(parents=True)
    source.write_text("Plasmodium falciparum marker reference v1.\n", encoding="utf-8")
    manifest = create_manifest(
        package_id="malaria-sahel-v1",
        profile_id="plasmodium_falciparum_sahel_v1",
        profile_version="1.0.0",
        reference_set_version="test-reference-2026-01",
        relative_files=["malaria/reference.md"],
        references=[{"title": "Reference source", "url": "https://example.org/reference"}],
        licenses=[{"name": "test source", "license": "verify before redistribution"}],
    )
    assert manifest["files"][0]["sha256"]
    assert verify_manifest("malaria-sahel-v1")["ok"] is True
    assert any(item["package_id"] == "malaria-sahel-v1" for item in list_manifests())

    source.write_text("modified after manifest creation\n", encoding="utf-8")
    report = verify_manifest("malaria-sahel-v1")
    assert report["ok"] is False
    assert report["manifest_sha256_ok"] is True
    assert report["files_failed"]


def test_offline_manifest_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OFFLINE_PACKAGES_DIR", str(tmp_path))
    with pytest.raises(OfflinePackageError):
        create_manifest(
            package_id="bad-path",
            profile_id="plasmodium_falciparum_sahel_v1",
            profile_version="1.0.0",
            reference_set_version="test",
            relative_files=["../outside.txt"],
        )


def test_evidence_retrieval_is_scoped_to_user_and_study(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OFFLINE_PACKAGES_DIR", str(tmp_path))
    user = User(username="evidence_user", email="evidence-user@nexora.test", password="not-a-real-hash", status="active", is_admin=False)
    db.add(user)
    db.flush()
    study = ResearchStudy(
        user_id=user.id, name="Evidence retrieval test",
        disease="malaria", pathogen="Plasmodium falciparum",
        country_focus="Mali", region_scope="Sahel",
        metadata_json={},
    )
    db.add(study)
    db.flush()
    db.add(ResearchEvidence(
        study_id=study.id, object_type="variant", object_id="pfcrt:K76T",
        evidence_kind="published_association", evidence_status="documented_association",
        evidence_grade="B", claim="Published association between pfcrt K76T and antimalarial resistance marker context.",
        organism="Plasmodium falciparum", source_id="PMID:123456",
        source_title="Example resistance marker study", source_url="https://example.org/paper",
        study_reference="PMID:123456", created_by=user.id,
    ))
    db.commit()

    found = retrieve_sources("pfcrt K76T antimalarial resistance", db, user.id, study.id)
    assert found
    assert found[0]["source_id"] == "evidence:1"
    assert found[0]["evidence_status"] == "documented_association"

    unrelated_user = User(username="other_user", email="other-user@nexora.test", password="not-a-real-hash", status="active", is_admin=False)
    db.add(unrelated_user)
    db.commit()
    assert retrieve_sources("pfcrt K76T antimalarial resistance", db, unrelated_user.id) == []


def test_local_assistant_returns_no_generated_answer_without_relevant_sources(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OFFLINE_PACKAGES_DIR", str(tmp_path))
    result = answer_question("Comment interpréter ce marqueur biologique ?", db, user_id=99999)
    assert result["success"] is True
    assert result["answered"] is False
    assert result["sources"] == []

def test_local_retrieval_includes_phyto_candidates_without_upgrading_evidence(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OFFLINE_PACKAGES_DIR", str(tmp_path))
    candidate = PhytochemicalCandidate(
        name_raw="Guieranone A",
        source_plant="Guiera senegalensis",
        library_source="SAHEL_50",
        source_row="24",
        identity_status="CANDIDAT_IMPORTÉ",
        occurrence_status="A_VÉRIFIER",
        evidence_grade="D",
        import_warnings=["source botanique à vérifier"],
    )
    db.add(candidate)
    db.commit()

    found = retrieve_sources("Guiera senegalensis phytochemical", db, user_id=99999)
    phyto = next(item for item in found if item["source_kind"] == "phytochemical_candidate")
    assert phyto["source_id"] == "phyto:candidate:" + str(candidate.id)
    assert phyto["evidence_status"] == "catalogue_candidate"
    assert phyto["evidence_grade"] == "D"
    assert phyto["identity_status"] == "CANDIDAT_IMPORTÉ"
    assert "pas une preuve d'efficacité" in phyto["text"]

