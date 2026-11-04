"""Compatibility facade for the ResearchStudy lifecycle transition policy.

ResearchStudy and scientific DAG workflows share one HFSM transition registry.
The legacy API keeps its action names and status values while the generic
engine owns the authoritative transition graph.
"""
from __future__ import annotations

from app.services.scientific_workflow_policy import (
    ACTION_DESCRIPTIONS as _HFSM_ACTION_DESCRIPTIONS,
    ACTIVE_STATES as _HFSM_ACTIVE_STATES,
    TERMINAL_STATES as _HFSM_TERMINAL_STATES,
    TRANSITIONS as _HFSM_TRANSITIONS,
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

ACTIVE_STATES = frozenset(_HFSM_ACTIVE_STATES.intersection({
    "draft", "data_review", "analysis", "results_review", "blocked",
}))
TERMINAL_STATES = frozenset(_HFSM_TERMINAL_STATES.intersection({"completed", "cancelled"}))
TRANSITIONS = {
    state: {
        action: target for action, target in transitions.items()
        if action in RESEARCH_ACTIONS
    }
    for state, transitions in _HFSM_TRANSITIONS.items()
    if state in ACTIVE_STATES or state in TERMINAL_STATES
}
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
