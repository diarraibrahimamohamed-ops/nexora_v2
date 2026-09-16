#!/usr/bin/env python3
"""Phase 9: reproducible external benchmark campaign and inference.

This tool consumes completed benchmark score tables. It NEVER runs docking or
creates biological labels. It combines Phase-8 validation with:
- target-macro metrics from benchmark_external.py;
- paired target-level bootstrap CIs;
- paired sign-flip permutation p-values under the sharp null;
- Holm correction across the planned method/metric comparisons;
- optional RDKit Bemis-Murcko scaffold-overlap diagnostics when SMILES exist.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, math, subprocess, sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TOOL8 = ROOT / "tools" / "benchmark_phase8_validation.py"
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
from benchmark_external import METHOD_COLUMNS, compute_metrics

METRICS = ("roc_auc", "pr_auc", "ef1", "ef5")
PAIRS = (("asp_clustered", "vina_top1"), ("asp_no_cluster", "vina_top1"), ("asp_clustered", "asp_no_cluster"))


def read_rows(path: Path) -> List[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"empty CSV: {path}")
    return rows


def by_target(rows: Sequence[dict]) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for r in rows:
        out.setdefault(str(r["target"]), []).append(r)
    return out


def target_values(rows: Sequence[dict], method: str, metric: str) -> Tuple[List[str], np.ndarray]:
    groups = by_target(rows)
    targets = sorted(groups)
    vals = np.asarray([compute_metrics(groups[t], method)[metric] for t in targets], dtype=float)
    if not np.all(np.isfinite(vals)):
        raise ValueError(f"non-finite target metric for {method}/{metric}")
    return targets, vals


def bootstrap_ci(delta: np.ndarray, rng: np.random.Generator, n_boot: int) -> Tuple[float, float]:
    samples = np.empty(n_boot, dtype=float)
    n = len(delta)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        samples[i] = float(np.mean(delta[idx]))
    return tuple(float(x) for x in np.percentile(samples, [2.5, 97.5]))


def sign_flip_pvalue(delta: np.ndarray, rng: np.random.Generator, n_perm: int) -> float:
    """Two-sided Monte-Carlo sign-flip test at independent-target level.

    Under the sharp paired null, target-wise differences are exchangeable under
    sign reversal. This avoids treating individual ligands as independent.
    """
    observed = abs(float(np.mean(delta)))
    n = len(delta)
    if n == 0:
        return float("nan")
    ge = 0
    batch = 2048
    done = 0
    while done < n_perm:
        b = min(batch, n_perm - done)
        signs = rng.choice(np.array([-1.0, 1.0]), size=(b, n))
        stats = np.abs(np.mean(signs * delta[None, :], axis=1))
        ge += int(np.sum(stats >= observed))
        done += b
    return float((ge + 1) / (n_perm + 1))


def holm(pvals: List[float]) -> List[float]:
    order = np.argsort(np.asarray(pvals, dtype=float))
    out = np.empty(len(pvals), dtype=float)
    running = 0.0
    m = len(pvals)
    for rank, idx in enumerate(order):
        val = min(1.0, float(pvals[int(idx)]) * (m - rank))
        running = max(running, val)
        out[int(idx)] = running
    return [float(x) for x in out]


def scaffold_report(rows: Sequence[dict]) -> dict:
    """Optional diagnostics: same ligand scaffold reused across targets.

    This is only a visibility diagnostic; shared chemistry is not automatically
    invalid because the benchmark is target-conditioned.
    """
    if "smiles" not in rows[0]:
        return {"available": False, "reason": "CSV has no smiles column"}
    try:
        from rdkit import Chem
        from rdkit.Chem.Scaffolds import MurckoScaffold
    except Exception as exc:
        return {"available": False, "reason": f"RDKit unavailable: {exc}"}
    mapping: Dict[str, set] = {}
    invalid = 0
    for r in rows:
        mol = Chem.MolFromSmiles(str(r.get("smiles", "")))
        if mol is None:
            invalid += 1
            continue
        scaf = MurckoScaffold.MurckoScaffoldSmiles(mol=mol, includeChirality=False)
        if not scaf:
            scaf = "[acyclic/no Murcko scaffold]"
        mapping.setdefault(scaf, set()).add(str(r["target"]))
    shared = [s for s, targets in mapping.items() if len(targets) > 1]
    return {
        "available": True,
        "rows_with_invalid_smiles": invalid,
        "unique_scaffolds": len(mapping),
        "scaffolds_shared_across_targets": len(shared),
        "shared_scaffold_examples": [
            {"scaffold": s, "targets": sorted(mapping[s])} for s in shared[:20]
        ],
    }


def evaluate(rows: Sequence[dict], bootstrap: int, permutations: int, seed: int) -> dict:
    groups = by_target(rows)
    targets = sorted(groups)
    rng = np.random.default_rng(seed)
    tests = []
    for a, b in PAIRS:
        for metric in METRICS:
            _, av = target_values(rows, a, metric)
            _, bv = target_values(rows, b, metric)
            delta = av - bv
            lo, hi = bootstrap_ci(delta, rng, bootstrap)
            p = sign_flip_pvalue(delta, rng, permutations)
            tests.append({
                "method_a": a, "method_b": b, "metric": metric,
                "observed_macro_delta": float(np.mean(delta)),
                "bootstrap_ci95": [lo, hi],
                "sign_flip_p_two_sided": p,
                "n_targets": len(targets),
                "bootstrap_resamples": bootstrap,
                "permutations": permutations,
                "seed": seed,
            })
    adj = holm([x["sign_flip_p_two_sided"] for x in tests])
    for row, p in zip(tests, adj):
        row["holm_p_adjusted"] = p
    return {
        "phase": "9",
        "track": "virtual_screening",
        "targets": targets,
        "n_rows": len(rows),
        "target_macro_metrics": {
            m: {metric: float(np.mean(target_values(rows, m, metric)[1])) for metric in METRICS}
            for m in METHOD_COLUMNS
        },
        "pooled_metrics": {
            m: compute_metrics(rows, m) for m in METHOD_COLUMNS
        },
        "paired_target_inference": tests,
        "scaffold_diagnostics": scaffold_report(rows),
        "interpretation": {
            "primary_method": "vina_top1",
            "asp_status": "experimental_comparison",
            "confidence_level": 0.95,
            "note": "No causal, clinical, or experimental affinity conclusion is produced by this tool.",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", type=Path)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--bootstrap", type=int, default=5000)
    ap.add_argument("--permutations", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=20260915)
    args = ap.parse_args()
    if args.bootstrap < 1000 or args.permutations < 1000:
        raise SystemExit("bootstrap/permutations too small for Phase 9")

    # Reuse the Phase-8 gate before inference.
    gate = subprocess.run(
        [sys.executable, str(TOOL8), "--vs", str(args.csv), "--manifest", str(args.manifest)],
        capture_output=True, text=True,
    )
    if gate.returncode != 0:
        print(gate.stdout, end="")
        print(gate.stderr, file=sys.stderr, end="")
        return gate.returncode

    rows = read_rows(args.csv)
    report = evaluate(rows, args.bootstrap, args.permutations, args.seed)
    report["phase8_gate"] = json.loads(gate.stdout)
    report["input_sha256"] = hashlib.sha256(args.csv.read_bytes()).hexdigest()
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
