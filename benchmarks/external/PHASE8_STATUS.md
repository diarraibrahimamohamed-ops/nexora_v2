# Phase 8 status — N3XORA v2

## What is validated in this phase

Phase 8 validates the **conditions of interpretation**, not the biological performance of ASP.

Implemented:
- provenance manifest gate;
- required software/protocol metadata;
- immutable SHA-256 fields for external packages when paths are supplied;
- duplicate target/ligand and target/complex checks;
- finite numeric score/RMSD checks;
- active/decoy class checks per target;
- minimum of five independent targets for interpretation;
- visibility of ligands shared across targets;
- explicit block when the final test set was tuned;
- separate pose-selection and virtual-screening tracks;
- no automatic conversion of docking scores to experimental affinity;
- no conclusion of resistance or drug efficacy from docking.

## What is intentionally NOT claimed

No real DUD-E/CASF performance number is included in this archive. The project still requires the external data packages, exact preparation artifacts and local execution of Vina/fpocket before an empirical result can be reported.

DUD-E is useful for virtual-screening enrichment but its decoys are property-matched computational decoys, not experimentally confirmed inactives. CASF-2016 is a distinct benchmark designed to separate scoring, ranking, docking and screening tasks.

## Release decision

`INTERPRETATION_GATE = BLOCKED` until a completed external benchmark package passes the manifest and data audits and produces archived raw outputs plus parsed results.
