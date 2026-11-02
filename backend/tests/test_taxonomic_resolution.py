"""Tests for documented nested GBIF Species Match v2 responses and curation gates."""
import httpx
import pytest

from app.config import settings
from app.database import Base
from app.models.phytochemistry import PhytochemicalCandidate, TaxonResolution
from app.services.phytochemistry_catalog import CurationError, IMPORTED, OCCURRENCE_VERIFIED, verify_occurrence
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


def accepted_match():
    # Documented GBIF v2 shape: name usage, classification and match diagnostics.
    return {
        "usage": {
            "key": 200,
            "name": "Acacia mearnsii De Wild.",
            "canonicalName": "Acacia mearnsii",
            "rank": "SPECIES",
            "status": "ACCEPTED",
        },
        "classification": [
            {"key": "6", "name": "Plantae", "rank": "KINGDOM"},
            {"key": "5386", "name": "Fabaceae", "rank": "FAMILY"},
        ],
        "diagnostics": {"matchType": "EXACT", "confidence": 100},
        "synonym": False,
    }


def synonym_match():
    return {
        "usage": {
            "key": 100,
            "acceptedUsageKey": 200,
            "name": "Acacia mollissima Willd.",
            "canonicalName": "Acacia mollissima",
            "rank": "SPECIES",
            "status": "SYNONYM",
        },
        "classification": [
            {"key": "6", "name": "Plantae", "rank": "KINGDOM"},
            {"key": "5386", "name": "Fabaceae", "rank": "FAMILY"},
        ],
        "diagnostics": {"matchType": "EXACT", "confidence": 99},
        "synonym": True,
    }


def candidate_row(db):
    row = PhytochemicalCandidate(
        name_raw="Caffeine",
        library_source="MANUAL",
        source_row="taxonomy-test",
        source_plant="Acacia mollissima",
        identity_status=IMPORTED,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_taxon_resolution_model_is_registered_in_sqlalchemy_metadata():
    assert "phytochem_taxon_resolutions" in Base.metadata.tables
    assert TaxonResolution.__table__.name == "phytochem_taxon_resolutions"


def test_nested_v2_synonym_response_preserves_matched_and_accepted_usages(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    observed = []

    def fake_get(url, *, params, timeout, headers):
        observed.append((url, params))
        if url.endswith("/v2/species/match"):
            return FakeResponse(synonym_match())
        if url.endswith("/v2/species/match/id/200"):
            assert params["checklistKey"] == WCVP_CHECKLIST_KEY
            return FakeResponse(accepted_match())
        raise AssertionError("Unexpected GBIF endpoint: " + url)

    monkeypatch.setattr("app.services.taxonomic_resolution.httpx.get", fake_get)
    candidate = candidate_row(db)
    row = resolve_botanical_taxon(db, "Acacia mollissima", candidate_id=candidate.id)
    db.commit()
    db.refresh(row)

    assert observed[0][1]["checklistKey"] == WCVP_CHECKLIST_KEY
    assert observed[1][0].endswith("/id/200")
    assert row.matched_status == "SYNONYM"
    assert row.matched_name == "Acacia mollissima Willd."
    assert row.matched_identifier == "100"
    assert row.accepted_name == "Acacia mearnsii De Wild."
    assert row.accepted_identifier == "200"
    assert row.accepted_status == "ACCEPTED"
    assert row.review_status == "pending_review"
    assert len(row.response_sha256) == 64
    assert row.dataset_doi == "10.15468/6h8ucr"
    assert candidate.accepted_taxon is None
    assert candidate.occurrence_status != OCCURRENCE_VERIFIED


def test_nested_accepted_species_uses_match_without_second_lookup(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.services.taxonomic_resolution.httpx.get",
        lambda *args, **kwargs: FakeResponse(accepted_match()),
    )
    candidate = candidate_row(db)
    row = resolve_botanical_taxon(db, "Acacia mearnsii", candidate_id=candidate.id)
    assert row.review_status == "pending_review"
    assert row.accepted_name == "Acacia mearnsii De Wild."
    assert row.accepted_identifier == "200"


def test_accept_updates_taxon_only_and_never_occurrence(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))

    def fake_get(url, **kwargs):
        if url.endswith("/v2/species/match"):
            return FakeResponse(synonym_match())
        return FakeResponse(accepted_match())

    monkeypatch.setattr("app.services.taxonomic_resolution.httpx.get", fake_get)
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
    assert candidate.taxon_identifier == "WCVP:200"
    assert candidate.taxonomy_resolution_source == "WCVP_VIA_GBIF_CHECKLISTBANK"
    assert candidate.taxonomy_cache_sha256 == row.response_sha256
    assert candidate.identity_status == IMPORTED
    assert candidate.occurrence_status == "A_VÉRIFIER"


def test_fuzzy_higher_rank_and_doubtful_matches_cannot_be_accepted(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    for match_type, status in (
        ("FUZZY", "ACCEPTED"),
        ("HIGHERRANK", "ACCEPTED"),
        ("EXACT", "DOUBTFUL"),
        ("AGGREGATE", "ACCEPTED"),
    ):
        payload = accepted_match()
        payload["diagnostics"]["matchType"] = match_type
        payload["usage"]["status"] = status
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
    payload = synonym_match()
    payload["classification"][0]["name"] = "Animalia"
    monkeypatch.setattr(
        "app.services.taxonomic_resolution.httpx.get",
        lambda url, **kwargs: FakeResponse(payload if url.endswith("/match") else accepted_match()),
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
    payload = accepted_match()
    payload["usage"]["rank"] = "GENUS"
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


def test_exact_query_cache_reuses_both_responses_after_network_error(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    calls = {"count": 0}

    def fake_get(url, **kwargs):
        calls["count"] += 1
        if calls["count"] > 2:
            raise httpx.ConnectError("offline")
        if url.endswith("/v2/species/match"):
            return FakeResponse(synonym_match())
        return FakeResponse(accepted_match())

    monkeypatch.setattr("app.services.taxonomic_resolution.httpx.get", fake_get)
    candidate = candidate_row(db)
    first = resolve_botanical_taxon(db, "Acacia mollissima", candidate_id=candidate.id)
    second = resolve_botanical_taxon(db, "Acacia mollissima", candidate_id=candidate.id)
    assert first.source_mode == "LIVE"
    assert second.source_mode == "CACHE"
    assert second.response_sha256 == first.response_sha256


def test_synonym_without_accepted_usage_key_stays_unresolved(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    payload = synonym_match()
    payload["usage"].pop("acceptedUsageKey")
    monkeypatch.setattr(
        "app.services.taxonomic_resolution.httpx.get",
        lambda *args, **kwargs: FakeResponse(payload),
    )
    candidate = candidate_row(db)
    row = resolve_botanical_taxon(db, "Acacia mollissima", candidate_id=candidate.id)
    assert row.review_status == "unresolved"
    assert row.accepted_name is None


def test_flat_match_payload_remains_backward_compatible(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    flat = {
        "usageKey": 200,
        "scientificName": "Acacia mearnsii De Wild.",
        "canonicalName": "Acacia mearnsii",
        "rank": "SPECIES",
        "status": "ACCEPTED",
        "confidence": 100,
        "matchType": "EXACT",
        "kingdom": "Plantae",
        "family": "Fabaceae",
    }
    monkeypatch.setattr(
        "app.services.taxonomic_resolution.httpx.get",
        lambda *args, **kwargs: FakeResponse(flat),
    )
    candidate = candidate_row(db)
    row = resolve_botanical_taxon(db, "Acacia mearnsii", candidate_id=candidate.id)
    assert row.review_status == "pending_review"
    assert row.accepted_name == "Acacia mearnsii De Wild."
    assert row.accepted_identifier == "200"


def test_v2_match_embedded_accepted_usage_avoids_unnecessary_id_request(monkeypatch, db, tmp_path):
    monkeypatch.setattr(settings, "AFRICAN_LIBS_DIR", str(tmp_path))
    payload = synonym_match()
    payload["acceptedUsage"] = accepted_match()["usage"]
    calls = []
    monkeypatch.setattr(
        "app.services.taxonomic_resolution.httpx.get",
        lambda url, **kwargs: (calls.append(url) or FakeResponse(payload)),
    )
    candidate = candidate_row(db)
    row = resolve_botanical_taxon(db, "Acacia mollissima", candidate_id=candidate.id)
    assert len(calls) == 1
    assert row.review_status == "pending_review"
    assert row.accepted_name == "Acacia mearnsii De Wild."
    assert row.accepted_identifier == "200"
