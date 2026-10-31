"""Versioned workflow definitions, hierarchical state transitions and DAG policy."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class TaskContract:
    task_name: str
    queue_name: str
    description: str


# Explicit allow-list. A workflow definition cannot publish arbitrary Celery task names.
TASK_CONTRACTS: dict[str, TaskContract] = {
    "analysis.run": TaskContract("analysis.run", "analysis", "Chunked sequence analysis"),
    "analysis.run_afribio_alignment": TaskContract(
        "analysis.run_afribio_alignment", "analysis", "AfriBio local alignment"
    ),
    "analysis.run_afribio_phylo": TaskContract(
        "analysis.run_afribio_phylo", "analysis", "AfriBio phylogenetic tree"
    ),
    "docking.run_vina": TaskContract("docking.run_vina", "docking", "AutoDock Vina docking"),
    "hsa.run": TaskContract("hsa.run", "qsa", "Heuristic Stability Analysis"),
    "rna_structure.predict_secondary": TaskContract(
        "rna_structure.predict_secondary", "structure", "RNA secondary-structure prediction"
    ),
    "research.resolve_structure": TaskContract(
        "research.resolve_structure", "structure", "Research target structure resolution"
    ),
    "workflow.validate_sequence_result": TaskContract(
        "workflow.validate_sequence_result", "workflow", "Validate persisted sequence analysis output"
    ),
    "workflow.validate_docking_input": TaskContract(
        "workflow.validate_docking_input", "workflow", "Validate persisted docking inputs"
    ),
    "workflow.validate_docking_result": TaskContract(
        "workflow.validate_docking_result", "workflow", "Validate persisted docking result and artifact"
    ),
}


@dataclass(frozen=True)
class WorkflowStepDefinition:
    key: str
    task_name: str
    queue_name: str
    depends_on: tuple[str, ...] = ()
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if not self.key or len(self.key) > 120:
            raise ValueError("step key must contain 1 to 120 characters")
        if self.max_attempts < 1 or self.max_attempts > 20:
            raise ValueError("max_attempts must be between 1 and 20")
        contract = TASK_CONTRACTS.get(self.task_name)
        if contract is None:
            raise ValueError(f"task is not registered: {self.task_name}")
        if self.queue_name != contract.queue_name:
            raise ValueError(
                f"queue mismatch for {self.task_name}: expected {contract.queue_name}"
            )


@dataclass(frozen=True)
class WorkflowDefinition:
    key: str
    version: str
    steps: tuple[WorkflowStepDefinition, ...]
    requires_quality_review: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if not self.key or len(self.key) > 120:
            raise ValueError("workflow key must contain 1 to 120 characters")
        if not self.version or len(self.version) > 40:
            raise ValueError("workflow version must contain 1 to 40 characters")
        validate_dag(self.steps)


@dataclass(frozen=True)
class TransitionDecision:
    allowed: bool
    from_state: str
    to_state: str | None
    action: str
    reason: str | None = None


# Leaf states include hierarchical paths. Parent states are derived, not persisted.
ACTIVE_STATES = frozenset({
    "created", "validating", "ready", "queued",
    "running.preparing", "running.executing", "running.postprocessing",
    "quality_review.pending", "quality_review.accepted",
    "retry_wait", "blocked", "needs_user_action",
})
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})

TRANSITIONS: dict[str, dict[str, str]] = {
    "created": {"validate": "validating", "cancel": "cancelled"},
    "validating": {
        "validation_passed": "ready",
        "validation_failed": "failed",
        "block": "blocked",
        "needs_user_action": "needs_user_action",
        "cancel": "cancelled",
    },
    "ready": {"queue": "queued", "block": "blocked", "cancel": "cancelled"},
    "queued": {
        "worker_started": "running.preparing",
        "block": "blocked",
        "cancel": "cancelled",
    },
    "running.preparing": {
        "begin_execution": "running.executing",
        "retry": "retry_wait",
        "fail": "failed",
        "block": "blocked",
        "cancel": "cancelled",
    },
    "running.executing": {
        "begin_postprocessing": "running.postprocessing",
        "retry": "retry_wait",
        "fail": "failed",
        "block": "blocked",
        "cancel": "cancelled",
    },
    "running.postprocessing": {
        "require_review": "quality_review.pending",
        "finalize": "succeeded",
        "fail": "failed",
        "block": "blocked",
        "cancel": "cancelled",
    },
    "quality_review.pending": {
        "approve": "quality_review.accepted",
        "reject": "failed",
        "needs_user_action": "needs_user_action",
        "cancel": "cancelled",
    },
    "quality_review.accepted": {"finalize_review": "succeeded"},
    "retry_wait": {"retry_ready": "queued", "fail": "failed", "cancel": "cancelled"},
    "blocked": {"resume": "__resume__", "cancel": "cancelled"},
    "needs_user_action": {
        "resume_validation": "validating",
        "cancel": "cancelled",
    },
}

ACTION_DESCRIPTIONS = {
    "validate": "Begin workflow input validation",
    "validation_passed": "Workflow inputs passed validation",
    "validation_failed": "Workflow input validation failed",
    "queue": "Queue the workflow for execution",
    "worker_started": "A worker started preparing the execution",
    "begin_execution": "Begin scientific step execution",
    "begin_postprocessing": "All required steps finished; begin postprocessing",
    "require_review": "Submit outputs for quality review",
    "approve": "Approve workflow quality review",
    "reject": "Reject workflow quality review",
    "finalize": "Finalize workflow without a separate quality review",
    "finalize_review": "Finalize workflow after quality approval",
    "retry": "Schedule workflow for retry",
    "retry_ready": "Workflow retry is ready",
    "block": "Block workflow pending intervention",
    "resume": "Resume workflow from its previous active state",
    "needs_user_action": "Pause workflow until user action is provided",
    "resume_validation": "Resume validation after user action",
    "fail": "Mark workflow as failed",
    "cancel": "Cancel workflow",
}


def parent_state(state: str) -> str:
    """Return the HFSM parent state for a persisted leaf state."""
    if state.startswith("running."):
        return "running"
    if state.startswith("quality_review."):
        return "quality_review"
    return state


def resolve_transition(
    state: str,
    action: str,
    *,
    resume_state: str | None = None,
) -> TransitionDecision:
    current = (state or "").strip().lower()
    requested = (action or "").strip().lower()
    target = TRANSITIONS.get(current, {}).get(requested)

    if target == "__resume__":
        if resume_state not in ACTIVE_STATES or resume_state in {"blocked", "needs_user_action"}:
            return TransitionDecision(
                False, current, None, requested,
                "No valid active state is available for resume.",
            )
        target = resume_state

    if target is None:
        return TransitionDecision(
            False, current, None, requested,
            f"Transition '{requested}' is not allowed from '{current}'.",
        )
    return TransitionDecision(True, current, target, requested)


def allowed_actions(state: str) -> list[str]:
    return sorted(TRANSITIONS.get((state or "").strip().lower(), {}).keys())


def validate_dag(steps: tuple[WorkflowStepDefinition, ...] | list[WorkflowStepDefinition]) -> list[str]:
    """Validate a workflow DAG and return a deterministic topological ordering."""
    definitions = list(steps)
    keys = [step.key for step in definitions]
    if not keys:
        raise ValueError("workflow must contain at least one step")
    if len(keys) != len(set(keys)):
        raise ValueError("workflow step keys must be unique")
    for step in definitions:
        if len(step.depends_on) != len(set(step.depends_on)):
            raise ValueError(f"duplicate dependency declared for step '{step.key}'")
    known = set(keys)
    incoming: dict[str, set[str]] = {key: set() for key in keys}
    outgoing: dict[str, set[str]] = {key: set() for key in keys}

    for step in definitions:
        for dependency in step.depends_on:
            if dependency not in known:
                raise ValueError(f"unknown dependency '{dependency}' for step '{step.key}'")
            if dependency == step.key:
                raise ValueError(f"step '{step.key}' cannot depend on itself")
            incoming[step.key].add(dependency)
            outgoing[dependency].add(step.key)

    ready = sorted(key for key in keys if not incoming[key])
    order: list[str] = []
    while ready:
        key = ready.pop(0)
        order.append(key)
        for dependent in sorted(outgoing[key]):
            incoming[dependent].remove(key)
            if not incoming[dependent]:
                ready.append(dependent)
                ready.sort()

    if len(order) != len(keys):
        raise ValueError("workflow dependencies contain a cycle")
    return order


def snapshot_definition(definition: WorkflowDefinition) -> dict[str, Any]:
    """Return a JSON-safe, immutable-at-creation snapshot of the workflow definition."""
    return {
        "workflow_key": definition.key,
        "version": definition.version,
        "description": definition.description,
        "requires_quality_review": definition.requires_quality_review,
        "steps": [
            {
                "key": step.key,
                "task_name": step.task_name,
                "queue_name": step.queue_name,
                "depends_on": list(step.depends_on),
                "max_attempts": step.max_attempts,
            }
            for step in definition.steps
        ],
        "topological_order": validate_dag(definition.steps),
    }


class WorkflowRegistry:
    """In-process registry: only code-reviewed definitions may be registered."""

    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], WorkflowDefinition] = {}

    def register(self, definition: WorkflowDefinition) -> None:
        key = (definition.key, definition.version)
        if key in self._definitions:
            raise ValueError(f"workflow definition already registered: {key[0]}@{key[1]}")
        self._definitions[key] = definition

    def get(self, key: str, version: str) -> WorkflowDefinition:
        try:
            return self._definitions[(key, version)]
        except KeyError as exc:
            raise KeyError(f"unknown workflow definition: {key}@{version}") from exc

    def list_definitions(self) -> list[dict[str, str]]:
        return [
            {"key": key, "version": version, "description": definition.description}
            for (key, version), definition in sorted(self._definitions.items())
        ]
