#!/usr/bin/env python3
"""Auditable evaluator for N3XORA external docking benchmark.

Two tracks are supported:
- virtual screening (CSV with active/decoy labels and three method scores)
- pose selection (CSV with native-pose RMSD for three method selections)

The evaluator deliberately does not download data or run docking engines.
All methods must be evaluated on the exact same target/ligand candidates.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np

METHOD_COLUMNS = {
    "vina_top1": "vina_top1_score",
    "asp_no_cluster": "asp_no_cluster_score",
    "asp_clustered": "asp_clustered_score",
}

METRIC_NAMES = ("roc_auc", "pr_auc", "ef1", "ef5")


def _as_arrays(rows: Sequence[dict], method_col: str) -> Tuple[np.ndarray, np.ndarray]:
    y: List[int] = []
    s: List[float] = []
    for r in rows:
        if str(r.get("active", "")).strip() not in {"0", "1"}:
            raise ValueError("active must be 0 or 1")
        val = float(r[method_col])
        if not math.isfinite(val):
            raise ValueError(f"non-finite score in {method_col}")
        y.append(int(r["active"]))
        # Vina-like scores: lower/more negative is better.
        s.append(-val)
    return np.asarray(y, dtype=int), np.asarray(s, dtype=float)


def roc_auc(y: np.ndarray, score: np.ndarray) -> float:
    pos = np.flatnonzero(y == 1)
    neg = np.flatnonzero(y == 0)
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    total = 0.0
    for i in pos:
        total += float(np.sum(score[i] > score[neg]))
        total += 0.5 * float(np.sum(score[i] == score[neg]))
    return total / (len(pos) * len(neg))


def average_precision(y: np.ndarray, score: np.ndarray) -> float:
    """Sklearn-compatible average precision for binary labels, no dependency on sklearn."""
    n_pos = int(np.sum(y == 1))
    if n_pos == 0:
        return float("nan")
    order = np.argsort(-score, kind="mergesort")
    yy = y[order]
    tp = np.cumsum(yy)
    fp = np.cumsum(1 - yy)
    recall = tp / n_pos
    precision = tp / np.maximum(tp + fp, 1)
    # Average precision integrates precision over recall jumps only.
    prev_recall = 0.0
    area = 0.0
    for r, p, label in zip(recall, precision, yy):
        if label == 1:
            area += float((r - prev_recall) * p)
            prev_recall = float(r)
    return area


def pr_auc(y: np.ndarray, score: np.ndarray) -> float:
    # The benchmark calls this PR-AUC; use the non-interpolated AP definition
    # to avoid optimistic trapezoidal interpolation on a discrete ranking.
    return average_precision(y, score)


def enrichment_factor(y: np.ndarray, score: np.ndarray, frac: float) -> float:
    if not 0 < frac <= 1:
        raise ValueError("frac must be in (0,1]")
    n = len(y)
    k = max(1, int(math.ceil(n * frac)))
    order = np.argsort(-score, kind="mergesort")
    top_hits = int(np.sum(y[order[:k]]))
    total_hits = int(np.sum(y))
    if total_hits == 0:
        return float("nan")
    return (top_hits / k) / (total_hits / n)


def compute_metrics(rows: Sequence[dict], method: str) -> Dict[str, float]:
    y, s = _as_arrays(rows, METHOD_COLUMNS[method])
    return {
        "roc_auc": float(roc_auc(y, s)),
        "pr_auc": float(pr_auc(y, s)),
        "ef1": float(enrichment_factor(y, s, 0.01)),
        "ef5": float(enrichment_factor(y, s, 0.05)),
    }


def _macro_target_metric(rows: Sequence[dict], method: str, metric: str) -> float:
    by_target: Dict[str, List[dict]] = {}
    for r in rows:
        by_target.setdefault(str(r["target"]), []).append(r)
    vals: List[float] = []
    for target, target_rows in sorted(by_target.items()):
        value = compute_metrics(target_rows, method)[metric]
        if math.isfinite(value):
            vals.append(value)
    return float(np.mean(vals)) if vals else float("nan")


def bootstrap_delta(rows: Sequence[dict], method_a: str, method_b: str,
                    metric_name: str, n_boot: int = 5000, seed: int = 20260913) -> dict:
    """Paired bootstrap at independent-target level using macro-target metrics."""
    targets = sorted({str(r["target"]) for r in rows})
    if len(targets) < 5:
        raise ValueError("target-level inference requires at least 5 independent targets")
    rng = np.random.default_rng(seed)
    by_target = {t: [r for r in rows if str(r["target"]) == t] for t in targets}

    a_vals = np.asarray([
        compute_metrics(by_target[t], method_a)[metric_name] for t in targets
    ], dtype=float)
    b_vals = np.asarray([
        compute_metrics(by_target[t], method_b)[metric_name] for t in targets
    ], dtype=float)
    delta_vals = a_vals - b_vals
    observed = float(np.nanmean(delta_vals))

    samples = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        idx = rng.integers(0, len(targets), size=len(targets))
        samples[i] = float(np.nanmean(delta_vals[idx]))
    lo, hi = np.percentile(samples, [2.5, 97.5])
    p_two_sided = float(2 * min(np.mean(samples <= 0), np.mean(samples >= 0)))
    p_two_sided = min(1.0, p_two_sided)
    return {
        "method_a": method_a,
        "method_b": method_b,
        "metric": metric_name,
        "observed_macro_delta": observed,
        "bootstrap_ci95": [float(lo), float(hi)],
        "bootstrap_p_two_sided": p_two_sided,
        "n_targets": len(targets),
        "bootstrap_seed": int(seed),
        "bootstrap_resamples": int(n_boot),
    }


def validate_vs(rows: Sequence[dict]) -> None:
    required = {"target", "ligand_id", "active", *METHOD_COLUMNS.values()}
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")
    seen = set()
    for r in rows:
        key = (str(r["target"]), str(r["ligand_id"]))
        if key in seen:
            raise ValueError(f"duplicate target/ligand row: {key}")
        seen.add(key)
    targets = {str(r["target"]) for r in rows}
    if len(targets) < 5:
        raise ValueError("virtual-screening benchmark requires at least 5 independent targets")
    # Every target must contain both classes for within-target metrics.
    for t in sorted(targets):
        ys = {str(r["active"]) for r in rows if str(r["target"]) == t}
        if ys != {"0", "1"}:
            raise ValueError(f"target {t} must contain both active and decoy labels")


def holm_adjust(pairs: List[dict]) -> None:
    """Holm-Bonferroni adjustment in place across the tested comparisons."""
    order = sorted(range(len(pairs)), key=lambda i: pairs[i]["bootstrap_p_two_sided"])
    m = len(order)
    adjusted = [1.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        raw = float(pairs[idx]["bootstrap_p_two_sided"])
        val = min(1.0, raw * (m - rank))
        running = max(running, val)
        adjusted[idx] = running
    for i, value in enumerate(adjusted):
        pairs[i]["holm_p_adjusted"] = float(value)


def evaluate_vs(rows: Sequence[dict], bootstrap: int, seed: int) -> dict:
    validate_vs(rows)
    targets = sorted({str(r["target"]) for r in rows})
    out: Dict[str, object] = {
        "track": "virtual_screening",
        "dataset": {"rows": len(rows), "targets": targets},
        "target_macro_metrics": {},
        "pooled_metrics": {},
        "paired_target_bootstrap": [],
    }
    for method in METHOD_COLUMNS:
        out["target_macro_metrics"][method] = {
            metric: _macro_target_metric(rows, method, metric) for metric in METRIC_NAMES
        }
        out["pooled_metrics"][method] = compute_metrics(rows, method)

    pairs = [
        ("asp_clustered", "vina_top1"),
        ("asp_no_cluster", "vina_top1"),
        ("asp_clustered", "asp_no_cluster"),
    ]
    tests: List[dict] = []
    for a, b in pairs:
        for metric in METRIC_NAMES:
            tests.append(bootstrap_delta(rows, a, b, metric, bootstrap, seed))
    holm_adjust(tests)
    out["paired_target_bootstrap"] = tests
    return out


def evaluate_pose(rows: Sequence[dict], bootstrap: int, seed: int) -> dict:
    required = {"target", "complex_id", "vina_top1_rmsd", "asp_no_cluster_rmsd", "asp_clustered_rmsd"}
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")
    seen = set()
    for r in rows:
        key = (str(r["target"]), str(r["complex_id"]))
        if key in seen:
            raise ValueError(f"duplicate target/complex row: {key}")
        seen.add(key)
        for c in required - {"target", "complex_id"}:
            v = float(r[c])
            if not math.isfinite(v) or v < 0:
                raise ValueError(f"invalid RMSD in {c}: {v}")
    targets = sorted({str(r["target"]) for r in rows})
    if len(targets) < 5:
        raise ValueError("pose benchmark requires at least 5 independent targets")

    method_cols = {
        "vina_top1": "vina_top1_rmsd",
        "asp_no_cluster": "asp_no_cluster_rmsd",
        "asp_clustered": "asp_clustered_rmsd",
    }
    target_groups = {t: [r for r in rows if str(r["target"]) == t] for t in targets}

    def target_summary(rs: Sequence[dict], col: str) -> dict:
        x = np.asarray([float(r[col]) for r in rs], float)
        return {
            "n": int(len(x)),
            "median_rmsd": float(np.median(x)),
            "success_rate_le_2A": float(np.mean(x <= 2.0)),
        }

    target_metrics = {
        m: {t: target_summary(target_groups[t], col) for t in targets}
        for m, col in method_cols.items()
    }

    # Paired bootstrap at target level, using target-level success and median.
    comparisons: List[dict] = []
    rng = np.random.default_rng(seed)
    for a, b in [("asp_clustered", "vina_top1"), ("asp_no_cluster", "vina_top1")]:
        for metric in ("success_rate_le_2A", "median_rmsd"):
            deltas = np.asarray([
                (target_metrics[a][t][metric] - target_metrics[b][t][metric])
                for t in targets
            ], float)
            samples = np.empty(bootstrap, float)
            for i in range(bootstrap):
                idx = rng.integers(0, len(targets), size=len(targets))
                samples[i] = float(np.mean(deltas[idx]))
            lo, hi = np.percentile(samples, [2.5, 97.5])
            observed = float(np.mean(deltas))
            p = float(2 * min(np.mean(samples <= 0), np.mean(samples >= 0)))
            comparisons.append({
                "method_a": a,
                "method_b": b,
                "metric": metric,
                "observed_macro_delta_a_minus_b": observed,
                "bootstrap_ci95": [float(lo), float(hi)],
                "bootstrap_p_two_sided": min(1.0, p),
                "n_targets": len(targets),
                "bootstrap_seed": seed,
                "bootstrap_resamples": bootstrap,
            })
    holm_adjust(comparisons)
    return {
        "track": "pose_selection",
        "dataset": {"rows": len(rows), "targets": targets},
        "target_metrics": target_metrics,
        "paired_target_bootstrap": comparisons,
        "primary_endpoint": "heavy_atom_RMSD <= 2.0 A",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", type=Path)
    ap.add_argument("--track", choices=["vs", "pose"], default="vs")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--bootstrap", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=20260913)
    args = ap.parse_args()

    with args.csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit("empty CSV")

    out = evaluate_vs(rows, args.bootstrap, args.seed) if args.track == "vs" else evaluate_pose(rows, args.bootstrap, args.seed)
    text = json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
