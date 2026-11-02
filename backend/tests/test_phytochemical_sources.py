"""Regression tests for the licence-aware PhytoEvidence source registry."""
from types import SimpleNamespace

from app.main import app
from app.services.auth_service import get_current_user
from app.services.phytochemical_sources import get_source_registry


def _by_id():
    return {source["source_id"]: source for source in get_source_registry()["sources"]}


def test_registry_has_unique_ids_and_explicit_non_automatic_ingestion():
    registry = get_source_registry()
    ids = [source["source_id"] for source in registry["sources"]]

    assert registry["schema_version"] == "1.0.0"
    assert registry["registry_id"] == "N3XORA_PHYTOEVIDENCE_SOURCE_REGISTRY"
    assert len(ids) == len(set(ids))
    assert registry["total"] == len(ids)

    for source in registry["sources"]:
        assert source["name"]
        assert source["domains"]
        assert source["rights"]["status"] in {
            "verified", "record_specific", "permission_required", "unverified"
        }
        assert source["ingestion"]["automatic_download"] is False
        assert source["ingestion"]["decision"]
        assert source["ingestion"]["requirements"]


def test_reusable_core_sources_keep_their_license_and_attribution_rules():
    sources = _by_id()

    assert sources["DR_DUKE"]["rights"]["license_expression"] == "CC0-1.0"
    assert sources["COCONUT"]["rights"]["license_expression"] == "CC0-1.0"
    assert sources["CHEBI"]["rights"]["license_expression"] == "CC-BY-4.0"
    assert sources["CHEBI"]["rights"]["commercial_reuse"] == "allowed_with_attribution"


def test_noncommercial_sources_are_not_marked_commercially_compatible():
    sources = _by_id()

    for source_id in ("ANPDB", "FOODB", "IMPPAT"):
        assert sources[source_id]["rights"]["commercial_reuse"].startswith("prohibited")
        assert sources[source_id]["ingestion"]["commercial_deployment"].startswith("blocked")


def test_record_scoped_rights_are_not_collapsed_to_one_global_license():
    sources = _by_id()

    for source_id in ("GBIF", "POWO_WCVP"):
        source = sources[source_id]
        assert source["rights"]["status"] == "record_specific"
        assert source["rights"]["commercial_reuse"] == "record_specific"
        assert source["ingestion"]["decision"] == "record_level_rights_gate"


def test_unknown_or_permission_required_sources_are_blocked_from_ingestion():
    for source in get_source_registry()["sources"]:
        if source["rights"]["status"] in {"unverified", "permission_required"}:
            assert source["ingestion"]["automatic_download"] is False
            assert source["ingestion"]["decision"] in {
                "blocked_pending_rights_review",
                "reference_only_until_permission",
                "manual_upload_rights_review",
            }


def test_registry_domain_filter_is_case_insensitive_and_does_not_mutate_cache():
    registry = get_source_registry(domain="BOTANICAL_TAXONOMY")
    assert registry["total"] == 2
    assert {source["source_id"] for source in registry["sources"]} == {
        "POWO_WCVP", "TROPICOS"
    }

    registry["sources"][0]["name"] = "MUTATED"
    assert all(source["name"] != "MUTATED" for source in get_source_registry()["sources"])


def test_registry_api_requires_authentication_and_is_read_only(client):
    unauthenticated = client.get("/api/v1/phytochem/sources")
    assert unauthenticated.status_code == 401

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    try:
        response = client.get("/api/v1/phytochem/sources?domain=botanical_taxonomy")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["total"] == 2
    assert {source["source_id"] for source in payload["sources"]} == {
        "POWO_WCVP", "TROPICOS"
    }
