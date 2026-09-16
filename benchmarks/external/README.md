# N3XORA v2 — External docking benchmark protocol

This directory is a **data-free benchmark harness/protocol**. No external metric is reported until the same ligand set has been docked under the same receptor, search space, ligand preparation, and Vina settings for every method.

## Track A — pose-selection benchmark
Use experimentally determined protein–ligand complexes with a known bound pose. For every complex:

1. prepare the receptor once;
2. identify the same candidate pockets for all methods;
3. generate the same Vina poses once;
4. compute the selected pose using:
   - Vina TOP1;
   - ASP without clustering;
   - ASP + RMSD clustering;
5. compare the selected pose to the crystallographic ligand with heavy-atom RMSD.

Primary endpoint: success rate at RMSD ≤ 2.0 Å. Report median RMSD and bootstrap 95% CI. Do not tune the 2 Å threshold on the test set.

## Track B — virtual screening benchmark
DUD-E is appropriate for enrichment testing. Its official site describes 102 targets, 22,886 actives and 50 physicochemical-matched decoys per active on average. The official diverse subset contains 8 targets: AKT1/3cqw, AMPC/1l2s, CP3A4/3nxu, CXCR4/3odu, GCR/3bqd, HIVPR/1xl2, HIVRT/3lan and KIF11/3cjo. Cite the DUD-E paper when publishing results.

All methods must see the **same compounds** and the **same receptor/pocket set**. The CSV evaluator expects:

```csv
target,ligand_id,active,vina_top1_score,asp_no_cluster_score,asp_clustered_score
AKT1,lig001,1,-7.1,-7.0,-7.0
AKT1,lig002,0,-6.4,-6.5,-6.5
...
```

Scores are assumed to be Vina-like docking scores where lower/more negative is better. The evaluator converts them to a higher-is-better ranking internally.

Reported metrics:
- ROC-AUC
- PR-AUC
- EF1%
- EF5%
- paired target-level bootstrap 95% CIs for method differences

## Leakage and reproducibility controls
- never tune parameters on the final test targets;
- keep target identities fixed and report them;
- record receptor PDB ID and preparation hash;
- record ligand-preparation version/hash;
- record Vina version and all search parameters;
- record fpocket version and pocket-selection parameters;
- record ASP temperature, clustering threshold and aggregation formula;
- preserve the exact raw pose files and result JSON;
- separate experimental structures from predicted/fallback structures.

## Important scientific constraint
ASP is an aggregation of empirical docking scores. It must not be reported as an experimentally calibrated binding free energy or Kd without independent calibration/validation.

## Phase 7 hardening
The evaluator now uses target-macro metrics and paired target-level bootstrap inference. PR-AUC is implemented as Average Precision. Holm-Bonferroni adjustment is applied across the planned exploratory pair/metric comparisons. The pose track is executable with `--track pose`.
