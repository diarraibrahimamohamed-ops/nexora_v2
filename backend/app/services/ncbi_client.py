"""Small, bounded and retry-aware client for NCBI E-utilities.

The client deliberately avoids Biopython Entrez module-level mutable settings.
It applies a conservative per-process request pace (3 req/s) and retries only
transient transport/service failures. A deployment with several replicas
should centralize this throttle (e.g. Redis) to enforce the aggregate IP limit.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
REQUEST_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
MAX_ATTEMPTS = 4
MAX_FETCH_BYTES = 10 * 1024 * 1024
MAX_ACCESSION_LENGTH = 100
ALLOWED_SEQUENCE_DATABASES = frozenset({"nucleotide", "protein"})
ACCESSION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
MIN_REQUEST_INTERVAL_SECONDS = 1.0 / 3.0

_rate_lock = threading.Lock()
_last_request_started = 0.0


class NCBIClientError(RuntimeError):
    """A categorized NCBI failure safe to map to an HTTP API response."""

    def __init__(self, message: str, *, status_code: int = 502, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def validate_database(database: str) -> str:
    value = (database or "").strip().lower()
    if value not in ALLOWED_SEQUENCE_DATABASES:
        raise NCBIClientError(
            "Base NCBI non prise en charge. Utilisez 'nucleotide' ou 'protein'.",
            status_code=422,
        )
    return value


def validate_accession(accession: str) -> str:
    value = (accession or "").strip()
    if not ACCESSION_PATTERN.fullmatch(value):
        raise NCBIClientError("Identifiant NCBI invalide.", status_code=422)
    return value


def _base_params() -> Dict[str, str]:
    params = {"tool": settings.NCBI_TOOL or "nexora_v2"}
    email = (settings.NCBI_EMAIL or "").strip()
    if email and "your_email" not in email.lower() and not email.lower().endswith("@example.com"):
        params["email"] = email
    api_key = (settings.NCBI_API_KEY or "").strip()
    if api_key:
        params["api_key"] = api_key
    return params


def _pace_requests() -> None:
    """Serialize request starts within this Python process at <= 3 per second."""
    global _last_request_started
    with _rate_lock:
        now = time.monotonic()
        delay = MIN_REQUEST_INTERVAL_SECONDS - (now - _last_request_started)
        if delay > 0:
            time.sleep(delay)
        _last_request_started = time.monotonic()


def _retry_after_seconds(response: httpx.Response) -> Optional[float]:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return min(max(float(value), 0.0), 15.0)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            remaining = (date - datetime.now(timezone.utc)).total_seconds()
            return min(max(remaining, 0.0), 15.0)
        except (TypeError, ValueError, OverflowError):
            return None


def _get(client: httpx.Client, endpoint: str, params: Dict[str, Any]) -> httpx.Response:
    url = f"{EUTILS_BASE}/{endpoint}.fcgi"
    last_error: Optional[BaseException] = None

    for attempt in range(MAX_ATTEMPTS):
        _pace_requests()
        try:
            response = client.get(url, params=params)
            if response.status_code in TRANSIENT_HTTP_STATUSES:
                last_error = NCBIClientError(
                    f"NCBI a répondu HTTP {response.status_code}.",
                    status_code=response.status_code,
                    retryable=True,
                )
                if attempt < MAX_ATTEMPTS - 1:
                    retry_after = _retry_after_seconds(response)
                    delay = retry_after if retry_after is not None else min(0.75 * (2 ** attempt), 6.0)
                    time.sleep(delay)
                    continue
                break
            if response.status_code >= 400:
                raise NCBIClientError(
                    f"Requête NCBI rejetée (HTTP {response.status_code}).",
                    status_code=response.status_code,
                )

            # Some E-utilities errors have historically been returned as JSON
            # error payloads despite HTTP 200. Retry explicit quota responses.
            content_type = response.headers.get("Content-Type", "").lower()
            if "json" in content_type:
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                error_message = str(payload.get("error", "")) if isinstance(payload, dict) else ""
                if "rate limit" in error_message.lower() or "quota exceeded" in error_message.lower():
                    last_error = NCBIClientError(
                        "NCBI a signalé une limite de débit dans sa réponse JSON.",
                        status_code=429,
                        retryable=True,
                    )
                    if attempt < MAX_ATTEMPTS - 1:
                        time.sleep(min(0.75 * (2 ** attempt), 6.0))
                        continue
                    break
            return response
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(min(0.75 * (2 ** attempt), 6.0))
                continue
            break

    if isinstance(last_error, NCBIClientError):
        raise last_error
    if last_error is not None:
        raise NCBIClientError(
            f"NCBI temporairement inaccessible ({type(last_error).__name__}).",
            status_code=504 if isinstance(last_error, httpx.TimeoutException) else 503,
            retryable=True,
        ) from last_error
    raise NCBIClientError("NCBI temporairement inaccessible.", status_code=503, retryable=True)


def _json(response: httpx.Response, endpoint: str) -> Dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise NCBIClientError(f"Réponse JSON NCBI invalide ({endpoint}).", status_code=502) from exc
    if not isinstance(payload, dict):
        raise NCBIClientError(f"Format de réponse NCBI inattendu ({endpoint}).", status_code=502)
    if payload.get("error"):
        message = str(payload["error"])[:300]
        raise NCBIClientError(f"Erreur NCBI : {message}", status_code=502)
    return payload


def search_sequences(query: str, database: str = "nucleotide", max_results: int = 10) -> Dict[str, Any]:
    """Search nucleotide/protein Entrez records and return normalized summaries."""
    db_name = validate_database(database)
    term = (query or "").strip()
    if len(term) < 3 or len(term) > 300 or any(ord(char) < 32 for char in term):
        raise NCBIClientError("La requête NCBI doit contenir entre 3 et 300 caractères imprimables.", status_code=422)
    if not 1 <= int(max_results) <= 50:
        raise NCBIClientError("max_results doit être compris entre 1 et 50.", status_code=422)

    params = {
        **_base_params(),
        "db": db_name,
        "term": term,
        "retmax": int(max_results),
        "retmode": "json",
    }
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            search_payload = _json(_get(client, "esearch", params), "esearch")
            result = search_payload.get("esearchresult") or {}
            if not isinstance(result, dict):
                raise NCBIClientError("Structure ESearch NCBI inattendue.", status_code=502)
            ids = result.get("idlist") or []
            total_count = int(result.get("count") or 0)
            if not ids:
                return {"results": [], "total_count": total_count}

            summary_params = {
                **_base_params(),
                "db": db_name,
                "id": ",".join(str(item) for item in ids),
                "retmode": "json",
            }
            summary_payload = _json(_get(client, "esummary", summary_params), "esummary")
    except NCBIClientError:
        raise
    except httpx.HTTPError as exc:
        raise NCBIClientError(f"Erreur de transport NCBI ({type(exc).__name__}).", status_code=503, retryable=True) from exc

    summary = summary_payload.get("result") or {}
    if not isinstance(summary, dict):
        raise NCBIClientError("Structure ESummary NCBI inattendue.", status_code=502)

    results: List[Dict[str, Any]] = []
    for uid in ids:
        item = summary.get(str(uid))
        if not isinstance(item, dict):
            continue
        try:
            length = int(item.get("slen") or item.get("length") or 0)
        except (TypeError, ValueError):
            length = 0
        results.append({
            "accession": str(item.get("caption") or item.get("accessionversion") or uid),
            "title": str(item.get("title") or ""),
            "organism": str(item.get("organism") or ""),
            "length": length,
        })

    return {"results": results, "total_count": total_count}


def fetch_sequence(accession: str, database: str = "nucleotide") -> Optional[Dict[str, Any]]:
    """Fetch one FASTA record. Return None for a genuine no-record response."""
    accession_value = validate_accession(accession)
    db_name = validate_database(database)
    params = {
        **_base_params(),
        "db": db_name,
        "id": accession_value,
        "rettype": "fasta",
        "retmode": "text",
    }
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = _get(client, "efetch", params)
    except NCBIClientError:
        raise
    except httpx.HTTPError as exc:
        raise NCBIClientError(f"Erreur de transport NCBI ({type(exc).__name__}).", status_code=503, retryable=True) from exc

    content = response.text
    if len(response.content) > MAX_FETCH_BYTES:
        raise NCBIClientError("Séquence NCBI trop volumineuse pour cette route (limite 10 MiB).", status_code=413)
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines or not lines[0].startswith(">"):
        return None

    header = lines[0][1:].strip()
    sequence_parts = []
    for line in lines[1:]:
        if line.startswith(">"):
            break
        sequence_parts.append(re.sub(r"\s+", "", line))
    sequence = "".join(sequence_parts).upper()
    if not sequence:
        return None
    if not re.fullmatch(r"[A-Z*.-]+", sequence):
        raise NCBIClientError("La séquence FASTA retournée par NCBI contient des caractères inattendus.", status_code=502)

    accession_from_header = header.split()[0] if header else accession_value
    return {
        "accession": accession_from_header,
        "header": header,
        "sequence_data": sequence,
        "organism": None,
        "length": len(sequence),
        "database": db_name,
    }
