# N3XORA v2 — Phase 10 — real external campaign

This phase is the execution layer following the Phase-8 validation gate and Phase-9 statistical plan.

## What is new

- strict environment preflight;
- receptor-only fpocket preparation;
- deterministic pocket freezing without using active/native ligands;
- Vina 1.2.5 execution;
- preservation and SHA-256 hashing of raw PDBQT outputs;
- calculation of Vina TOP1, ASP-no-cluster and ASP-clustered from the exact same Vina poses;
- locked manifest for each run;
- Phase-9-compatible `vs_results.csv`.

## Why Vina 1.2.5 is frozen

The project benchmark uses Vina 1.2.5 as its reference engine. Vina's current upstream release history records 1.2.5 as a pose-sorting bugfix release. Do not silently substitute another version during the benchmark.

## Pocket rule

The pocket is selected from receptor-only fpocket output using the highest fpocket score (ties resolved by lowest pocket id). This is a **predeclared protocol choice**, not a claim that the selected pocket is biologically correct. Native/active ligand coordinates are not inspected by the preparation tool.

## Real execution

Build the isolated runner:

```bash
docker compose -f docker-compose.phase10.yml build n3xora-benchmark
```

Prepare pocket candidates from receptor PDB files:

```bash
python tools/phase10_prepare_pockets.py benchmarks/external/phase10/data --out benchmarks/external/phase10/pockets.json
```

Populate the final input manifest from those frozen pocket boxes and then run:

```bash
python tools/phase10_preflight.py --json
docker compose -f docker-compose.phase10.yml run --rm n3xora-benchmark /workspace/tools/phase10_execute_campaign.py /workspace/phase10/PHASE10_INPUT_MANIFEST.json --out-root /workspace/runs
```

Run Phase-8/9 analysis on the generated CSV only after the run is complete and the locked manifest is archived.
