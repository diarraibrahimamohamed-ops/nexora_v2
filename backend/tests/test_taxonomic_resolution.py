"""Tests for WCVP-backed botanical-name resolution and independent review gates."""
import httpx
import pytest

from app.config import settings
from app.database import Base
from app.models.phytochemistry import PhytochemicalCandidate, TaxonResolution
from app.services.phytochemistry_catalog import (
    CurationError, IMPORTED, OCCURRENCE_VERIFIED, verify_occurrence,
)
from app.services.taxonomic_resolution import (
    ACCEPTABLE_PLANT_RANKS, WCVP_CHECKLIST_KEY, TaxonomyResolutionError,
    resolve_botanical_taxon, review_taxon_resolution,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def wcvp_payload(kingdom="Plantae"):
    return {
        "usage": {
            "key": "wcvp-syn-1",
            "name": "Acacia mollissima Willd.",
            "canonicalName": "Acacia mollissima",
            "rank": "SPECIES",
            "status": "SYNONYM",
        },
        "acceptedUsage": {
            "key": "wcvp-accepted-1",
            "name": "Acacia mearnsii De Wild.",
            "canonicalName": "Acacia mearnsii",
            "rank": "SPECIES",
            "status": "ACCEPTED",
        },
        "classification": [
            {"name": kingdom, "rank": "KINGDOM"},
            {"name": "Fabaceae", "rank": "FAMILY"},
        ],
        "diagnostics": {"matchType": "EXACT", "confidence": 99},
    }


def candidate_row(db):
    row = PhytochemicalCandidate(
        name_raw="Caffeine", library_source="MANUAL", source_row="taxonomy-test",
        source_plant="Acacia mollissima", identity_status=IMPORTED,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_taxon_resolution_model_is_registered_in_sqlalchemy_metadata():
    assert "phytochem_taxon_resolutions" in Base.metadata.tables
    assert TaxonResolution.__table__.name == "phytochem_taxon_resolutions"


def test_explicit_wcvp_checklist_and_synonym_preservation(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    observed = {}

    def fake_get(url, *, params, timeout, headers):
        observed.update(url=url, params=params, timeout=timeout, headers=headers)
        return FakeResponse(wcvp_payload())

    monkeypatch.setattr("app.services.taxonomic_resolution.httpx.get", fake_get)
    candidate = candidate_row(db)
    row = resolve_botanical_taxon(db, "Acacia mollissima", candidate_id=candidate.id)
    db.commit()
    db.refresh(row)

    assert observed["url"].endswith("/v2/species/match")
    assert observed["params"]["checklistKey"] == WCVP_CHECKLIST_KEY
    assert observed["params"]["kingdom"] == "Plantae"
    assert row.matched_status == "SYNONYM"
    assert row.matched_name == "Acacia mollissima Willd."
    assert row.accepted_name == "Acacia mearnsii De Wild."
    assert row.accepted_status == "ACCEPTED"
    assert row.review_status == "pending_review"
    assert len(row.response_sha256) == 64
    assert row.dataset_doi == "10.15468/6h8ucr"
    assert candidate.accepted_taxon is None
    assert candidate.occurrence_status != OCCURRENCE_VERIFIED


def test_accepted_species_response_uses_usage_when_no_separate_accepted_usage(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    payload = {
        "usage": {
            "key": "wcvp-accepted-2",
            "name": "Acacia mearnsii De Wild.",
            "canonicalName": "Acacia mearnsii",
            "rank": "SPECIES",
            "status": "ACCEPTED",
            "kingdom": "Plantae",
            "family": "Fabaceae",
        },
        "diagnostics": {"matchType": "EXACT", "confidence": 100},
    }
    monkeypatch.setattr(
        "app.services.taxonomic_resolution.httpx.get",
        lambda *args, **kwargs: FakeResponse(payload),
    )
    candidate = candidate_row(db)
    row = resolve_botanical_taxon(db, "Acacia mearnsii", candidate_id=candidate.id)
    assert row.review_status == "pending_review"
    assert row.accepted_name == "Acacia mearnsii De Wild."
    assert row.accepted_identifier == "wcvp-accepted-2"


def test_curator_acceptance_updates_taxon_only_and_never_occurrence(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.services.taxonomic_resolution.httpx.get",
        lambda *args, **kwargs: FakeResponse(wcvp_payload()),
    )
    candidate = candidate_row(db)
    row = resolve_botanical_taxon(db, "Acacia mollissima", candidate_id=candidate.id)
    db.commit()

    review_taxon_resolution(
        db, row, decision="accept", reviewer="curator@example.org",
        rationale="Synonym and accepted name reviewed against the WCVP checklist record.",
    )
    db.commit()
    db.refresh(candidate)

    assert row.review_status == "accepted"
    assert candidate.accepted_taxon == "Acacia mearnsii De Wild."
    assert candidate.taxon_identifier == "WCVP:wcvp-accepted-1"
    assert candidate.taxonomy_resolution_source == "WCVP_VIA_GBIF_CHECKLISTBANK"
    assert candidate.taxonomy_cache_sha256 == row.response_sha256
    assert candidate.identity_status == IMPORTED
    assert candidate.occurrence_status == "A_VÉRIFIER"


def test_fuzzy_higher_rank_and_doubtful_results_cannot_be_accepted(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    base = wcvp_payload()
    for match_type, status in (
        ("FUZZY", "ACCEPTED"),
        ("HIGHERRANK", "ACCEPTED"),
        ("EXACT", "DOUBTFUL"),
    ):
        payload = {
            **base,
            "usage": {**base["usage"], "status": status},
            "diagnostics": {"matchType": match_type, "confidence": 72},
        }
        monkeypatch.setattr(
            "app.services.taxonomic_resolution.httpx.get",
            lambda *args, payload=payload, **kwargs: FakeResponse(payload),
        )
        candidate = candidate_row(db)
        row = resolve_botanical_taxon(db, "Acacia", candidate_id=candidate.id)
        assert row.review_status == "unresolved"
        with pytest.raises(TaxonomyResolutionError):
            review_taxon_resolution(
                db, row, decision="accept", reviewer="curator",
                rationale="This result does not meet the strict WCVP acceptance gate.",
            )


def test_non_plant_match_is_preserved_but_not_acceptable(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.services.taxonomic_resolution.httpx.get",
        lambda *args, **kwargs: FakeResponse(wcvp_payload(kingdom="Animalia")),
    )
    candidate = candidate_row(db)
    row = resolve_botanical_taxon(db, "Acacia mollissima", candidate_id=candidate.id)
    assert row.review_status == "unresolved"
    assert row.accepted_name is None
    with pytest.raises(TaxonomyResolutionError):
        review_taxon_resolution(
            db, row, decision="accept", reviewer="curator",
            rationale="Attempted acceptance of a non-plant record.",
        )
    assert candidate.accepted_taxon is None


def test_genus_rank_result_cannot_be_accepted(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    payload = wcvp_payload()
    payload["acceptedUsage"]["rank"] = "GENUS"
    monkeypatch.setattr(
        "app.services.taxonomic_resolution.httpx.get",
        lambda *args, **kwargs: FakeResponse(payload),
    )
    candidate = candidate_row(db)
    row = resolve_botanical_taxon(db, "Acacia", candidate_id=candidate.id)
    assert "SPECIES" in ACCEPTABLE_PLANT_RANKS
    assert row.review_status == "unresolved"
    with pytest.raises(TaxonomyResolutionError):
        review_taxon_resolution(
            db, row, decision="accept", reviewer="curator",
            rationale="The result is at genus rank and cannot be accepted.",
        )


def test_exact_query_cache_is_reused_after_network_error(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    calls = {"count": 0}

    def fake_get(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] > 1:
            raise httpx.ConnectError("offline")
        return FakeResponse(wcvp_payload())

    monkeypatch.setattr("app.services.taxonomic_resolution.httpx.get", fake_get)
    candidate = candidate_row(db)
    first = resolve_botanical_taxon(db, "Acacia mollissima", candidate_id=candidate.id)
    second = resolve_botanical_taxon(db, "Acacia mollissima", candidate_id=candidate.id)
    assert first.source_mode == "LIVE"
    assert second.source_mode == "CACHE"
    assert second.response_sha256 == first.response_sha256


def test_synonym_without_accepted_usage_stays_unresolved(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    payload = wcvp_payload()
    payload.pop("acceptedUsage")
    monkeypatch.setattr(
        "app.services.taxonomic_resolution.httpx.get",
        lambda *args, **kwargs: FakeResponse(payload),
    )
    candidate = candidate_row(db)
    row = resolve_botanical_taxon(db, "Acacia mollissima", candidate_id=candidate.id)
    assert row.review_status == "unresolved"
    assert row.accepted_name is None
