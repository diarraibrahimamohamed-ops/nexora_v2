"""Compatibility facade for the ResearchStudy lifecycle transition policy.

ResearchStudy and scientific DAG workflows share one HFSM transition registry.
The legacy API keeps its action names and status values while the generic
engine owns the authoritative transition graph.
"""
from __future__ import annotations

from app.services.scientific_workflow_policy import (
    ACTION_DESCRIPTIONS as _HFSM_ACTION_DESCRIPTIONS,
    allowed_actions as _hfsm_allowed_actions,
    resolve_transition as _hfsm_resolve_transition,
)

RESEARCH_ACTIONS = frozenset({
    "submit_for_review",
    "approve_data",
    "submit_results",
    "complete_computational",
    "block",
    "resume",
    "cancel",
})

ACTION_DESCRIPTIONS = {
    action: _HFSM_ACTION_DESCRIPTIONS[action]
    for action in RESEARCH_ACTIONS
}


def resolve_transition(state: str, action: str, resume_state: str | None = None):
    """Keep the historic import path, but delegate to the shared HFSM policy."""
    return _hfsm_resolve_transition(state, action, resume_state=resume_state)


def allowed_actions(state: str) -> list[str]:
    """Return the shared HFSM policy's permitted actions for a ResearchStudy state."""
    return [action for action in _hfsm_allowed_actions(state) if action in RESEARCH_ACTIONS]
