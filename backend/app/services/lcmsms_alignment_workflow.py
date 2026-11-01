"""Versioned workflow contracts for batch feature alignment and QC correction."""
from app.services.scientific_workflow_policy import (
    WorkflowDefinition,
    WorkflowStepDefinition,
)

LCMS_FEATURE_ALIGNMENT_WORKFLOW = WorkflowDefinition(
    key="nexora.lcmsms.feature-alignment",
    version="1.0.0",
    description=(
        "Polarity-specific mutual-unique m/z anchor alignment, monotone piecewise RT mapping, "
        "and source-linked consensus feature grouping."
    ),
    requires_quality_review=True,
    steps=(
        WorkflowStepDefinition(
            key="align_features",
            task_name="lcmsms.align_batch_features",
            queue_name="lcmsms",
            max_attempts=1,
        ),
    ),
)

LCMS_FEATURE_QUANTIFICATION_WORKFLOW = WorkflowDefinition(
    key="nexora.lcmsms.qc-rlsc-quantification",
    version="1.0.0",
    description=(
        "Feature-wise robust LOESS correction using pooled-QC injections, "
        "without zero imputation or extrapolation."
    ),
    requires_quality_review=True,
    steps=(
        WorkflowStepDefinition(
            key="quantify_features",
            task_name="lcmsms.quantify_aligned_features",
            queue_name="lcmsms",
            max_attempts=1,
        ),
    ),
)
