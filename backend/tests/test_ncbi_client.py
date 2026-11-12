from __future__ import annotations

import json

import httpx
import pytest

from app.services import ncbi_client


class FakeResponse:
    def __init__(self, status_code=200, *, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = text.encode("utf-8") if isinstance(text, str) else text
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, url, params=None):
        self.calls.append((url, params or {}))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _fake_http(monkeypatch, responses):
    fake = FakeClient(responses)
    monkeypatch.setattr(ncbi_client.httpx, "Client", lambda **kwargs: fake)
    monkeypatch.setattr(ncbi_client, "MIN_REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(ncbi_client.time, "sleep", lambda seconds: None)
    return fake


def test_search_sequences_uses_eutils_json_and_normalizes_summary(monkeypatch):
    fake = _fake_http(monkeypatch, [
        FakeResponse(payload={"esearchresult": {"idlist": ["101", "102"], "count": "37"}}),
        FakeResponse(payload={"result": {
            "uids": ["101", "102"],
            "101": {"caption": "NC_000001.11", "title": "Chromosome 1", "organism": "Homo sapiens", "slen": "248956422"},
            "102": {"caption": "NC_000002.12", "title": "Chromosome 2", "organism": "Homo sapiens", "slen": 242193529},
        }}),
    ])
    monkeypatch.setattr(ncbi_client.settings, "NCBI_EMAIL", "research@example.org")
    monkeypatch.setattr(ncbi_client.settings, "NCBI_API_KEY", "test-key")

    result = ncbi_client.search_sequences("Homo sapiens[Organism]", "nucleotide", 2)

    assert result["total_count"] == 37
    assert [item["accession"] for item in result["results"]] == ["NC_000001.11", "NC_000002.12"]
    assert result["results"][0]["length"] == 248956422
    assert len(fake.calls) == 2
    assert fake.calls[0][1]["tool"] == "nexora_v2"
    assert fake.calls[0][1]["email"] == "research@example.org"
    assert fake.calls[0][1]["api_key"] == "test-key"
    assert fake.calls[1][1]["id"] == "101,102"


def test_search_sequences_returns_empty_result_without_summary_call(monkeypatch):
    fake = _fake_http(monkeypatch, [
        FakeResponse(payload={"esearchresult": {"idlist": [], "count": "0"}})
    ])
    result = ncbi_client.search_sequences("no matches", "protein", 10)
    assert result == {"results": [], "total_count": 0}
    assert len(fake.calls) == 1


def test_fetch_sequence_retries_rate_limit_and_parses_fasta(monkeypatch):
    fake = _fake_http(monkeypatch, [
        FakeResponse(429, headers={"Retry-After": "0"}),
        FakeResponse(text=">NP_000001.1 Example protein [Example organism]\nMKWVTFISLLFLFSSAYS\n"),
    ])
    record = ncbi_client.fetch_sequence("NP_000001.1", "protein")
    assert record["accession"] == "NP_000001.1"
    assert record["sequence_data"] == "MKWVTFISLLFLFSSAYS"
    assert record["length"] == 18
    assert len(fake.calls) == 2


def test_fetch_sequence_returns_none_for_non_fasta_response(monkeypatch):
    _fake_http(monkeypatch, [FakeResponse(text="")])
    assert ncbi_client.fetch_sequence("NM_000001.1", "nucleotide") is None


def test_fetch_sequence_rejects_unexpected_fasta_symbols(monkeypatch):
    _fake_http(monkeypatch, [FakeResponse(text=">NM_000001.1 example\nATGC$TAA\n")])
    with pytest.raises(ncbi_client.NCBIClientError, match="caractères inattendus"):
        ncbi_client.fetch_sequence("NM_000001.1")


@pytest.mark.parametrize("database", ["pubmed", "sra", "../nucleotide", ""])
def test_sequence_routes_reject_non_sequence_entrez_databases(database):
    with pytest.raises(ncbi_client.NCBIClientError) as error:
        ncbi_client.validate_database(database)
    assert error.value.status_code == 422


@pytest.mark.parametrize("accession", ["../etc/passwd", "NM_000001/2", "NM 000001", ""])
def test_accession_validator_rejects_path_or_control_characters(accession):
    with pytest.raises(ncbi_client.NCBIClientError) as error:
        ncbi_client.validate_accession(accession)
    assert error.value.status_code == 422


def test_transient_ncbi_failure_is_retryable_after_attempts_exhausted(monkeypatch):
    fake = _fake_http(monkeypatch, [
        FakeResponse(503),
        FakeResponse(503),
        FakeResponse(503),
        FakeResponse(503),
    ])
    with pytest.raises(ncbi_client.NCBIClientError) as error:
        ncbi_client.fetch_sequence("NM_000001.1")
    assert error.value.status_code == 503
    assert error.value.retryable is True
    assert len(fake.calls) == ncbi_client.MAX_ATTEMPTS


def test_invalid_database_is_rejected_before_network_call(monkeypatch):
    fake = _fake_http(monkeypatch, [])
    with pytest.raises(ncbi_client.NCBIClientError):
        ncbi_client.search_sequences("BRCA1", "pubmed", 5)
    assert fake.calls == []
