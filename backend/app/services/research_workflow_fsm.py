"""Finite-state workflow for auditable research-study progression.

This module contains transition policy only. Database readiness checks live
in the API layer so the pure transition graph remains easy to test.
"""
from __future__ import annotations

from dataclasses import dataclass


ACTIVE_STATES = frozenset({"draft", "data_review", "analysis", "results_review"})
TERMINAL_STATES = frozenset({"completed", "cancelled"})

TRANSITIONS: dict[str, dict[str, str]] = {
    "draft": {"submit_for_review": "data_review"},
    "data_review": {"approve_data": "analysis"},
    "analysis": {"submit_results": "results_review"},
    "results_review": {"complete_computational": "completed"},
    "blocked": {"resume": "__resume__"},
}

ACTION_DESCRIPTIONS = {
    "submit_for_review": "Soumettre l'objectif et l'hypothèse à la revue des données",
    "approve_data": "Valider les éléments d'entrée et ouvrir la phase d'analyse",
    "submit_results": "Soumettre des résultats calculés ou des preuves documentées à la revue",
    "complete_computational": "Clôturer l'étude comme étude computationnelle",
    "block": "Bloquer temporairement l'étude avec une justification",
    "resume": "Reprendre l'étude à son état précédent",
    "cancel": "Annuler l'étude",
}


@dataclass(frozen=True)
class TransitionDecision:
    allowed: bool
    from_state: str
    to_state: str | None
    action: str
    reason: str | None = None


def resolve_transition(state: str, action: str, resume_state: str | None = None) -> TransitionDecision:
    """Resolve a requested transition without mutating persistent state."""
    current = (state or "draft").strip().lower()
    requested = (action or "").strip().lower()

    if requested == "block":
        if current not in ACTIVE_STATES:
            return TransitionDecision(False, current, None, requested, "Seule une étude active peut être bloquée.")
        return TransitionDecision(True, current, "blocked", requested)

    if requested == "cancel":
        if current in TERMINAL_STATES:
            return TransitionDecision(False, current, None, requested, "Une étude terminée ou annulée ne peut pas être annulée à nouveau.")
        return TransitionDecision(True, current, "cancelled", requested)

    target = TRANSITIONS.get(current, {}).get(requested)
    if target == "__resume__":
        if resume_state not in ACTIVE_STATES:
            return TransitionDecision(False, current, None, requested, "Aucun état actif précédent n'est disponible pour la reprise.")
        target = resume_state

    if target is None:
        return TransitionDecision(False, current, None, requested, f"Transition '{requested}' interdite depuis l'état '{current}'.")
    return TransitionDecision(True, current, target, requested)


def allowed_actions(state: str) -> list[str]:
    current = (state or "draft").strip().lower()
    actions = list(TRANSITIONS.get(current, {}).keys())
    if current in ACTIVE_STATES:
        actions.append("block")
    if current not in TERMINAL_STATES:
        actions.append("cancel")
    return actions
