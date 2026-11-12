from uuid import uuid4

from app.api.v1 import sequences
from app.config import settings
from app.models.ncbi import NcbiSequence
from app.services.ncbi_client import NCBIClientError


def _authenticated_headers(client):
    suffix = uuid4().hex[:10]
    email = f"ncbi-{suffix}@nexora.test"
    created = client.post("/api/v1/auth/register", json={
        "username": f"ncbi_{suffix}",
        "email": email,
        "password": "password123",
    })
    assert created.status_code == 201
    login = client.post("/api/v1/auth/login", json={
        "email": email,
        "password": "password123",
    }, headers={"X-Real-IP": f"198.51.100.{int(suffix[:2], 16) % 200 + 1}"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_ncbi_search_maps_rate_limit_to_service_unavailable(client, monkeypatch):
    headers = _authenticated_headers(client)

    def rate_limited(*args, **kwargs):
        raise NCBIClientError("NCBI quota exceeded.", status_code=429, retryable=True)

    monkeypatch.setattr(sequences, "ncbi_search_sequences", rate_limited)
    response = client.get(
        "/api/v1/sequences/ncbi?query=BRCA1&database=protein&max_results=5",
        headers=headers,
    )
    assert response.status_code == 503
    assert "quota" in response.json()["detail"]


def test_ncbi_fetch_serves_fresh_database_scoped_cache(client, db, monkeypatch):
    headers = _authenticated_headers(client)
    monkeypatch.setattr(settings, "NCBI_CACHE_TTL_SECONDS", 3600)
    db.add(NcbiSequence(
        accession="NM_000001.1",
        database_name="nucleotide",
        title="Example record",
        sequence="ATGCGT",
        length=6,
    ))
    db.commit()

    def no_network(*args, **kwargs):
        raise AssertionError("A fresh cache entry must prevent an external NCBI request.")

    monkeypatch.setattr(sequences, "ncbi_fetch_sequence", no_network)
    response = client.get(
        "/api/v1/sequences/ncbi/fetch?accession=NM_000001.1&database=nucleotide",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["from_cache"] is True
    assert response.json()["data"]["sequence_data"] == "ATGCGT"

    # The same accession in a different database is not a cache hit.
    monkeypatch.setattr(
        sequences,
        "ncbi_fetch_sequence",
        lambda accession, database: {
            "accession": accession, "header": "protein record",
            "sequence_data": "MKWV", "length": 4,
            "organism": None, "database": database,
        },
    )
    response = client.get(
        "/api/v1/sequences/ncbi/fetch?accession=NM_000001.1&database=protein",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["from_cache"] is False
    assert response.json()["data"]["database"] == "protein"


def test_ncbi_invalid_accession_is_rejected_without_external_request(client, monkeypatch):
    headers = _authenticated_headers(client)

    def no_network(*args, **kwargs):
        raise AssertionError("Invalid accessions must be rejected before network access.")

    monkeypatch.setattr(sequences, "ncbi_fetch_sequence", no_network)
    response = client.get(
        "/api/v1/sequences/ncbi/fetch?accession=../../etc/passwd",
        headers=headers,
    )
    assert response.status_code == 422
