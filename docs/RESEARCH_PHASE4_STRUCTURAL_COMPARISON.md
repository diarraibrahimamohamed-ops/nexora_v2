# N3XORA — Research Phase 4

## Objective
Compare validated WT and variant protein structures descriptively before any comparative docking.

## Pipeline
1. Verify both target structures are already accepted for docking review.
2. Recover residue correspondence by global sequence alignment.
3. Superpose sequence-corresponding C-alpha atoms with Kabsch.
4. Report pre/post-superposition RMSD, mean/median/max C-alpha displacement, sequence identity and coverage.
5. If a protein variant position is known, report its corresponding C-alpha displacement and an optional local RMSD within a configurable radius.
6. Detect fpocket candidates independently on WT and variant structures and compare nearest pocket centers after structural superposition.
7. Persist the complete descriptive report with provenance.

## Critical methodological separation
Kabsch is appropriate here because the goal is to compare two molecular structures while removing arbitrary differences in global coordinate frame. It is **not** used for docking-pose clustering in N3XORA. Docking poses remain in the receptor frame and are clustered with fixed-frame heavy-atom RMSD so that distinct placements are not artificially aligned away.

## Scientific interpretation
This phase does not infer pathogenicity, resistance, binding affinity, drug efficacy, or causality. A structural difference is a geometric observation that requires biological interpretation and, where relevant, experimental validation.

## Pockets
Pocket comparison is descriptive: nearest-center matching is used after superposition. The report preserves the detection source (`fpocket` or explicit heuristic fallback) and does not convert pocket scores into probabilities.

## Gate for Phase 5
A WT/variant pair should reach comparative docking only when both structures are present, sequence-compatible, provenance-tagged, and the Phase 4 comparison report has been recorded.
