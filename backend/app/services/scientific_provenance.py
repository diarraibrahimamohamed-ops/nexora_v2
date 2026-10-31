"""Canonical, versioned provenance manifests for Nexora scientific computations.

Hashes are calculated over deterministic UTF-8 JSON (sorted keys, compact
separators, no NaN/Infinity). A manifest hash excludes its own hash field.
Scientific result hashes exclude the provenance envelope to avoid recursion.
"""
from __future__ import annotations

import hashlib
import json
import platform
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Mapping, Sequence

PROVENANCE_SCHEMA_VERSION = "1.0.0"
PROVENANCE_SCHEMA = "nexora.scientific-provenance/v1"


class ProvenanceError(ValueError):
    """A provenance payload cannot be represented as strict canonical JSON."""


def canonical_json_bytes(payload: Any) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ProvenanceError(f"Payload is not canonical JSON: {exc}") from exc


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_json(payload: Any) -> str:
    return sha256_bytes(canonical_json_bytes(payload))


def seal_manifest(payload: Mapping[str, Any], *, manifest_type: str) -> dict[str, Any]:
    """Add schema/type and a deterministic hash to a JSON-compatible manifest."""
    if not manifest_type or not isinstance(manifest_type, str):
        raise ProvenanceError("manifest_type must be a non-empty string.")
    body = dict(payload)
    body.pop("manifest_sha256", None)
    body.pop("schema", None)
    body.pop("schema_version", None)
    body.pop("manifest_type", None)
    sealed_body = {
        "schema": PROVENANCE_SCHEMA,
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "manifest_type": manifest_type,
        **body,
    }
    digest = sha256_json(sealed_body)
    return {**sealed_body, "manifest_sha256": digest}


def software_fingerprint(
    engine_module: str,
    dependencies: Sequence[str] = ("numpy", "scipy"),
) -> dict[str, Any]:
    """Capture the Python and scientific-library versions affecting a run."""
    installed = {}
    for package in dependencies:
        try:
            installed[package] = version(package)
        except PackageNotFoundError:
            installed[package] = None
    return {
        "application": "Nexora v2",
        "engine_module": engine_module,
        "python_version": platform.python_version(),
        "dependencies": installed,
    }


def build_analysis_manifest(
    *,
    algorithm: str,
    algorithm_version: str,
    inputs: Sequence[Mapping[str, Any]],
    parameters: Mapping[str, Any],
    output_sha256: str,
    execution: Mapping[str, Any] | None = None,
    software: Mapping[str, Any] | None = None,
    interpretation_limit: str | None = None,
) -> dict[str, Any]:
    """Build a shared manifest for either historical or advanced analysis engines."""
    if len(output_sha256) != 64:
        raise ProvenanceError("output_sha256 must be a 64-character SHA-256 digest.")
    parameter_payload = dict(parameters)
    manifest = {
        "algorithm": {
            "name": algorithm,
            "version": algorithm_version,
        },
        "inputs": [dict(item) for item in inputs],
        "parameters": parameter_payload,
        "parameters_sha256": sha256_json(parameter_payload),
        "output": {
            "format": "application/json",
            "sha256": output_sha256,
        },
        "execution": dict(execution or {"mode": "unspecified"}),
        "software": dict(software or {}),
        "interpretation_limit": interpretation_limit,
    }
    return seal_manifest(manifest, manifest_type="scientific_analysis")


def build_workflow_input_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Seal an existing workflow input manifest without renaming legacy fields."""
    return seal_manifest(payload, manifest_type="scientific_workflow_input")


def build_workflow_output_manifest(
    *,
    workflow_run_id: int,
    workflow_key: str,
    definition_version: str,
    steps: Sequence[Mapping[str, Any]],
    requires_quality_review: bool,
) -> dict[str, Any]:
    """Create a compact output manifest from completed DAG step summaries."""
    return seal_manifest(
        {
            "workflow": {
                "run_id": workflow_run_id,
                "key": workflow_key,
                "definition_version": definition_version,
            },
            "steps": [dict(item) for item in steps],
            "completion": {
                "all_steps_succeeded": True,
                "quality_review_required": bool(requires_quality_review),
                "state": "steps_succeeded",
            },
        },
        manifest_type="scientific_workflow_output",
    )
