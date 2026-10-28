"""Read-only, versioned profile registry for organism-specific research workflows."""
from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path


class ResearchProfileError(ValueError):
    pass


@lru_cache(maxsize=1)
def _load_profiles() -> dict:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "research_profiles.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Research-profile registry is unavailable: {type(exc).__name__}") from exc
    rows = data.get("profiles")
    if not isinstance(rows, list):
        raise RuntimeError("Research-profile registry must contain a profiles list.")
    indexed = {}
    for profile in rows:
        profile_id = profile.get("id")
        if not profile_id or profile_id in indexed:
            raise RuntimeError("Research-profile registry has missing or duplicate profile IDs.")
        if not profile.get("version") or not profile.get("workflow"):
            raise RuntimeError(f"Incomplete research profile: {profile_id}")
        indexed[profile_id] = profile
    return {"schema_version": data.get("schema_version", "unknown"),
            "platform": data.get("platform", "N3XORA-Sahel BioEvidence"),
            "description": data.get("description", ""),
            "profiles": indexed}


def list_profiles() -> dict:
    registry = _load_profiles()
    return {
        "schema_version": registry["schema_version"],
        "platform": registry["platform"],
        "profiles": [deepcopy(p) for p in registry["profiles"].values()],
    }


def get_profile(profile_id: str) -> dict:
    profile = _load_profiles()["profiles"].get(profile_id)
    if profile is None:
        raise ResearchProfileError(f"Profil de recherche inconnu: {profile_id}")
    return deepcopy(profile)
