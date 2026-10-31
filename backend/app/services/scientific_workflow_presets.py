"""Reviewed built-in workflows binding persisted domain jobs to allowed engines."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.services.scientific_workflow_engine import create_workflow_run
from app.services.scientific_workflow_policy import (
    TASK_CONTRACTS,
    WorkflowDefinition,
    WorkflowStepDefinition,
)

BUILTIN_TASK_WORKFLOWS = {
    "analysis.run_persisted": {
        "key": "sequence-analysis",
        "version": "1.0.0",
        "description": "Chunked analysis of a persisted sequence-analysis job.",
        "max_attempts": 3,
    },
    "docking.run_vina_persisted": {
        "key": "vina-docking",
        "version": "1.0.0",
        "description": "AutoDock Vina execution for a persisted docking job.",
        "max_attempts": 3,
    },
    "hsa.run_persisted": {
        "key": "protein-stability-hsa",
        "version": "1.0.0",
        "description": "Heuristic stability analysis for a persisted HSA job.",
        "max_attempts": 2,
    },
    "rna_structure.predict_secondary": {
        "key": "rna-secondary-structure",
        "version": "1.0.0",
        "description": "RNA-FM secondary-structure prediction for a persisted RNA job.",
        "max_attempts": 2,
    },
    "research.resolve_structure": {
        "key": "research-target-structure",
        "version": "1.0.0",
        "description": "Resolve and validate a structure for a persisted research target.",
        "max_attempts": 2,
    },
}


def builtin_task_definition(task_name: str) -> WorkflowDefinition:
    """Return an immutable, code-reviewed one-step workflow definition."""
    config = BUILTIN_TASK_WORKFLOWS.get(task_name)
    contract = TASK_CONTRACTS.get(task_name)
    if config is None or contract is None:
        raise ValueError(f"No built-in workflow is registered for task '{task_name}'.")
    return WorkflowDefinition(
        key=config["key"],
        version=config["version"],
        description=config["description"],
        requires_quality_review=False,
        steps=(
            WorkflowStepDefinition(
                key="execute",
                task_name=task_name,
                queue_name=contract.queue_name,
                max_attempts=config["max_attempts"],
            ),
        ),
    )


def create_builtin_task_run(
    db: Session,
    *,
    task_name: str,
    args: list[Any],
    kwargs: dict[str, Any] | None = None,
    owner_user_id: int | None,
    scope_type: str,
    scope_id: int | str,
    context: dict[str, Any] | None = None,
    input_manifest: dict[str, Any] | None = None,
):
    """Create a domain-scoped run using only a built-in, allow-listed engine."""
    kwargs = dict(kwargs or {})
    payload = json.dumps(
        {"task_name": task_name, "args": args, "kwargs": kwargs},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )
    definition = builtin_task_definition(task_name)
    return create_workflow_run(
        db,
        definition,
        owner_user_id=owner_user_id,
        scope_type=scope_type,
        scope_id=scope_id,
        context={
            "submitted_by": "builtin_domain_adapter",
            "task_name": task_name,
            **(context or {}),
        },
        input_manifest={
            "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            **(input_manifest or {}),
        },
        step_inputs={"execute": {"args": args, "kwargs": kwargs}},
    )
