"""Tests for source-backed plant-compound occurrence evidence."""
from app.database import Base
from app.models.phytochemistry import (
    PhytochemicalCandidate, PhytochemicalOccurrenceEvidence, TaxonResolution,
)
from app.services.phytochemistry_catalog import IMPORTED, OCCURRENCE_VERIFIED
from app.services.phytochemical_occurrence import (
    OccurrenceEvidenceError, create_occurrence_evidence, review_occurrence_evidence,
)
import pytest


def candidate_row(db):
    row = PhytochemicalCandidate(
        name_raw="Caffeine", library_source="MANUAL", source_row="occurrence-test",
        source_plant="Coffea arabica", accepted_taxon="Coffea arabica",
        taxon_identifier="WCVP:coffee-1",
        taxonomy_resolution_source="WCVP_VIA_GBIF_CHECKLISTBANK",
        taxonomy_cache_sha256="a" * 64, identity_status=IMPORTED,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def accepted_resolution(db, candidate):
    row = TaxonResolution(
        candidate_id=candidate.id,
        requested_name="Coffea arabica",
        matched_name="Coffea arabica",
        matched_identifier="coffee-1",
        matched_status="ACCEPTED",
        matched_rank="SPECIES",
        accepted_name="Coffea arabica",
        accepted_identifier="coffee-1",
        accepted_status="ACCEPTED",
        accepted_rank="SPECIES",
        kingdom="Plantae",
        family="Rubiaceae",
        match_type="EXACT",
        confidence=100,
        provider="GBIF ChecklistBank Species Match API v2 / WCVP",
        checklist_key="f382f0ce-323a-4091-bb9f-add557f3a9a2",
        dataset_doi="10.15468/6h8ucr",
        dataset_version="WCVP v16 (2026-06-04)",
        source_mode="LIVE",
        source_url="https://api.gbif.org/v2/species/match?scientificName=Coffea%20arabica",
        response_sha256="b" * 64,
        response_payload={"match_response": {"status": "ACCEPTED"}},
        review_status="accepted",
        reviewed_by="curator@example.org",
        review_note="Accepted after WCVP nomenclature review.",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_occurrence_model_is_registered_in_sqlalchemy_metadata():
    assert "phytochem_occurrence_evidence" in Base.metadata.tables
    assert PhytochemicalOccurrenceEvidence.__table__.name == "phytochem_occurrence_evidence"


def test_literature_occurrence_is_pending_and_does_not_change_candidate_status(db):
    candidate = candidate_row(db)
    resolution = accepted_resolution(db, candidate)
    evidence = create_occurrence_evidence(
        db, candidate_id=candidate.id, taxon_resolution_id=resolution.id,
        evidence_type="literature_report", plant_part="feuilles",
        source_reference="https://doi.org/10.1234/example",
        source_title="Chemical composition of Coffea arabica leaves",
        source_locator="Table 2, page 5",
        evidence_statement="The paper reports caffeine in the dried leaves of Coffea arabica.",
        created_by=None,
    )
    db.commit()
    db.refresh(evidence)
    db.refresh(candidate)
    assert evidence.review_status == "pending_review"
    assert len(evidence.evidence_sha256) == 64
    assert candidate.occurrence_status != OCCURRENCE_VERIFIED
    assert candidate.identity_status == IMPORTED


def test_accepting_evidence_updates_occurrence_but_not_identity(db):
    candidate = candidate_row(db)
    resolution = accepted_resolution(db, candidate)
    evidence = create_occurrence_evidence(
        db, candidate_id=candidate.id, taxon_resolution_id=resolution.id,
        evidence_type="literature_report", plant_part="graines",
        source_reference="https://doi.org/10.1234/seed-report",
        evidence_statement="A primary source documents caffeine in the seeds of this plant.",
        created_by=None,
    )
    db.commit()
    review_occurrence_evidence(
        db, evidence, decision="accept", reviewer_id=None,
        reviewer_label="curator@example.org",
        rationale="Primary source and reported plant part reviewed and accepted.",
    )
    db.commit()
    db.refresh(candidate)
    db.refresh(evidence)
    assert evidence.review_status == "accepted"
    assert candidate.occurrence_status == OCCURRENCE_VERIFIED
    assert candidate.plant_part == "graines"
    assert candidate.identity_status == IMPORTED
    assert candidate.taxon_identifier == "WCVP:coffee-1"


def test_pending_taxon_resolution_cannot_support_occurrence(db):
    candidate = candidate_row(db)
    resolution = accepted_resolution(db, candidate)
    resolution.review_status = "pending_review"
    db.commit()
    with pytest.raises(OccurrenceEvidenceError):
        create_occurrence_evidence(
            db, candidate_id=candidate.id, taxon_resolution_id=resolution.id,
            evidence_type="literature_report", plant_part="feuilles",
            source_reference="https://doi.org/10.1234/example",
            evidence_statement="A primary paper reports an occurrence.",
            created_by=None,
        )


def test_rejection_does_not_change_candidate_occurrence_status(db):
    candidate = candidate_row(db)
    resolution = accepted_resolution(db, candidate)
    evidence = create_occurrence_evidence(
        db, candidate_id=candidate.id, taxon_resolution_id=resolution.id,
        evidence_type="literature_report", plant_part="racines",
        source_reference="https://doi.org/10.1234/roots",
        evidence_statement="The report mentions this plant compound.",
        created_by=None,
    )
    db.commit()
    review_occurrence_evidence(
        db, evidence, decision="reject", reviewer_id=None, reviewer_label="curator",
        rationale="The cited source does not support the stated plant part.",
    )
    db.commit()
    db.refresh(candidate)
    assert evidence.review_status == "rejected"
    assert candidate.occurrence_status != OCCURRENCE_VERIFIED


def test_evidence_content_is_integrity_checked_before_review(db):
    candidate = candidate_row(db)
    resolution = accepted_resolution(db, candidate)
    evidence = create_occurrence_evidence(
        db, candidate_id=candidate.id, taxon_resolution_id=resolution.id,
        evidence_type="literature_report", plant_part="feuilles",
        source_reference="https://doi.org/10.1234/example",
        evidence_statement="The paper supports this occurrence claim.",
        created_by=None,
    )
    db.commit()
    evidence.evidence_statement = "Altered after evidence registration."
    with pytest.raises(OccurrenceEvidenceError):
        review_occurrence_evidence(
            db, evidence, decision="accept", reviewer_id=None, reviewer_label="curator",
            rationale="An attempted integrity check of the evidence statement.",
        )


def test_lcms_occurrence_cannot_be_created_without_sample_and_annotation(db):
    candidate = candidate_row(db)
    resolution = accepted_resolution(db, candidate)
    with pytest.raises(OccurrenceEvidenceError):
        create_occurrence_evidence(
            db, candidate_id=candidate.id, taxon_resolution_id=resolution.id,
            evidence_type="lcms_annotation", plant_part="feuilles",
            source_reference=None, evidence_statement="LC-MS/MS detected this candidate compound.",
            created_by=None,
        )
