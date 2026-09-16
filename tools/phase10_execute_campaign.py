#!/usr/bin/env python3
"""N3XORA Phase 10 — real Vina/ASP screening executor.

Input is a frozen JSON manifest. The executor:
- performs a strict preflight;
- refuses Vina version drift;
- docks each ligand against the *same frozen receptor and pocket box*;
- preserves raw Vina outputs;
- derives Vina TOP1, ASP no-cluster and ASP clustered scores from the exact same
  poses;
- emits the Phase-9-compatible CSV plus an immutable run manifest.

It does not invent labels, rescore ligands with a second engine, or use native
ligand coordinates to choose the pocket.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.docking.asp.scoring import boltzmann_logmean_score
from app.core.docking.asp.aggregation import aggregate_pose_scores

import math as _math

def parse_vina_output_strict(output_file: str) -> list[dict[str, Any]]:
    """Local dependency-light parser for Vina PDBQT output.

    It mirrors N3XORA's strict parser contract without importing the full app
    settings, so the benchmark tool can run independently of PostgreSQL/Redis.
    """
    poses: list[dict[str, Any]] = []
    current_atoms: list[dict[str, Any]] = []
    current_score: float | None = None
    pose_id = 0
    with open(output_file, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "REMARK VINA RESULT" in line:
                if current_atoms and current_score is not None and _math.isfinite(current_score):
                    poses.append({"pose_id": pose_id, "score": current_score, "atoms": current_atoms, "num_atoms": len(current_atoms)})
                    pose_id += 1
                current_atoms = []
                parts = line.split()
                try:
                    current_score = float(parts[3])
                except (IndexError, ValueError):
                    current_score = None
            elif line.startswith(("ATOM", "HETATM")) and len(line) >= 54:
                try:
                    atom_type = line[76:79].strip() if len(line) >= 79 else ""
                    atom_name = line[12:16].strip() if len(line) >= 16 else ""
                    if atom_type.upper().startswith("A"):
                        element = "C"
                    elif atom_type.upper().startswith(("CL", "BR")):
                        element = atom_type[:2].upper()
                    else:
                        element = (atom_type[:1] or atom_name[:1] or "C").upper()
                    current_atoms.append({
                        "x": float(line[30:38]), "y": float(line[38:46]), "z": float(line[46:54]),
                        "atom": atom_type or atom_name[:1], "atom_name": atom_name, "element": element,
                        "residue": line[17:20].strip() if len(line) > 20 else "LIG"
                    })
                except (ValueError, IndexError):
                    continue
    if current_atoms and current_score is not None and _math.isfinite(current_score):
        poses.append({"pose_id": pose_id, "score": current_score, "atoms": current_atoms, "num_atoms": len(current_atoms)})
    poses.sort(key=lambda x: x["score"])
    return poses

EXPECTED_VINA = "1.2.5"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"JSON object required: {path}")
    return obj


def require_path(base: Path, value: str, label: str) -> Path:
    p = Path(value)
    if not p.is_absolute():
        p = base / p
    p = p.resolve()
    if not p.is_file():
        raise FileNotFoundError(f"{label} not found: {p}")
    return p


def parse_version(vina: str) -> str:
    proc = subprocess.run([vina, "--version"], capture_output=True, text=True, check=False)
    text = (proc.stdout + proc.stderr).strip()
    m = re.search(r"(?:v|version\s*)?(\d+\.\d+\.\d+)", text, flags=re.I)
    if not m:
        raise RuntimeError(f"Unable to parse Vina version from: {text!r}")
    return m.group(1)


def prepare_run_dir(root: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    candidate = root / stamp
    suffix = 0
    while candidate.exists():
        suffix += 1
        candidate = root / f"{stamp}_{suffix:02d}"
    candidate.mkdir(parents=True)
    return candidate


def dock_one(vina: str, receptor: Path, ligand: Path, box: dict[str, float], out_file: Path, log_file: Path, params: dict[str, Any]) -> None:
    cmd = [
        vina,
        "--receptor", str(receptor),
        "--ligand", str(ligand),
        "--center_x", str(box["center_x"]),
        "--center_y", str(box["center_y"]),
        "--center_z", str(box["center_z"]),
        "--size_x", str(box["size_x"]),
        "--size_y", str(box["size_y"]),
        "--size_z", str(box["size_z"]),
        "--exhaustiveness", str(params["exhaustiveness"]),
        "--num_modes", str(params["num_modes"]),
        "--energy_range", str(params["energy_range"]),
        "--seed", str(params["seed"]),
        "--out", str(out_file),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    log_file.write_text(proc.stdout or "", encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"Vina failed for {ligand.name}; see {log_file}")


def no_cluster_score(poses: list[dict[str, Any]], temperature: float) -> float:
    scores = [float(p["score"]) for p in poses]
    if not scores:
        raise ValueError("No poses returned")
    return float(boltzmann_logmean_score(scores, temperature))


def score_result(output_file: Path, temperature: float, rmsd_threshold: float) -> dict[str, Any]:
    poses = parse_vina_output_strict(str(output_file))
    if not poses:
        raise ValueError(f"No valid Vina poses parsed: {output_file}")
    for p in poses:
        p["pocket_id"] = 1
    top1 = float(min(p["score"] for p in poses))
    asp_no = no_cluster_score(poses, temperature)
    asp_clustered, asp_meta = aggregate_pose_scores(
        [dict(p) for p in poses],
        temperature=temperature,
        rmsd_threshold=rmsd_threshold,
    )
    return {
        "vina_top1_score": top1,
        "asp_no_cluster_score": float(asp_no),
        "asp_clustered_score": float(asp_clustered),
        "pose_count": len(poses),
        "asp_metadata": asp_meta,
        "selected_pose_id": asp_meta.get("selected_pose_id"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--vina", default="vina")
    ap.add_argument("--out-root", type=Path, default=ROOT / "benchmarks" / "external" / "phase10" / "runs")
    ap.add_argument("--preflight", action="store_true", help="Run and display strict environment preflight")
    args = ap.parse_args()

    manifest_path = args.manifest.resolve()
    manifest = load_json(manifest_path)
    protocol = manifest.get("protocol", {})
    engine = manifest.get("engine", {})
    params = protocol.get("vina_parameters", {})
    expected_version = engine.get("vina_version", EXPECTED_VINA)
    if expected_version != EXPECTED_VINA:
        raise SystemExit(f"Phase 10 requires Vina {EXPECTED_VINA}; manifest declared {expected_version}")

    vina_path = shutil.which(args.vina)
    if not vina_path:
        raise SystemExit(f"Vina executable not found: {args.vina}")
    actual_version = parse_version(vina_path)
    if actual_version != expected_version:
        raise SystemExit(f"Vina version mismatch: expected {expected_version}, got {actual_version}")

    dataset_root = (manifest_path.parent / manifest["dataset_root"]).resolve()
    targets = manifest.get("targets")
    if not isinstance(targets, list) or not targets:
        raise SystemExit("manifest.targets must be a non-empty list")

    run_dir = prepare_run_dir(args.out_root.resolve())
    (run_dir / "raw").mkdir()
    (run_dir / "logs").mkdir()

    frozen = dict(manifest)
    frozen["execution"] = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "vina_path": str(Path(vina_path).resolve()),
        "vina_version_actual": actual_version,
        "manifest_sha256": sha256_file(manifest_path),
        "run_directory": str(run_dir),
        "status": "running",
    }
    (run_dir / "MANIFEST_LOCKED.json").write_text(json.dumps(frozen, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    rows: list[dict[str, Any]] = []
    try:
        for target in targets:
            target_id = str(target["target"])
            receptor = require_path(dataset_root, target["receptor_pdbqt"], f"receptor {target_id}")
            ligands_csv = require_path(dataset_root, target["ligands_csv"], f"ligands {target_id}")
            pocket = target.get("pocket")
            if not isinstance(pocket, dict):
                raise ValueError(f"target {target_id}: frozen pocket box required")
            for key in ("center_x", "center_y", "center_z", "size_x", "size_y", "size_z"):
                if key not in pocket:
                    raise ValueError(f"target {target_id}: missing pocket key {key}")

            with ligands_csv.open(newline="", encoding="utf-8") as fh:
                ligand_rows = list(csv.DictReader(fh))
            required = {"ligand_id", "active", "pdbqt"}
            if not ligand_rows or not required.issubset(ligand_rows[0].keys()):
                raise ValueError(f"{ligands_csv}: required columns {sorted(required)}")

            for item in ligand_rows:
                ligand_id = str(item["ligand_id"])
                active = str(item["active"])
                if active not in {"0", "1"}:
                    raise ValueError(f"{target_id}/{ligand_id}: active must be 0 or 1")
                ligand = require_path(dataset_root, item["pdbqt"], f"ligand {target_id}/{ligand_id}")
                token = re.sub(r"[^A-Za-z0-9_.-]", "_", ligand_id)
                out_file = run_dir / "raw" / f"{target_id}__{token}.pdbqt"
                log_file = run_dir / "logs" / f"{target_id}__{token}.log"
                dock_one(args.vina, receptor, ligand, {k: float(pocket[k]) for k in pocket}, out_file, log_file, params)
                scored = score_result(out_file, float(protocol.get("asp_parameters", {}).get("temperature_K", 298.15)), float(protocol.get("asp_parameters", {}).get("clustering_threshold_A", 2.0)))
                rows.append({
                    "target": target_id,
                    "ligand_id": ligand_id,
                    "active": active,
                    "vina_top1_score": f"{scored['vina_top1_score']:.6f}",
                    "asp_no_cluster_score": f"{scored['asp_no_cluster_score']:.6f}",
                    "asp_clustered_score": f"{scored['asp_clustered_score']:.6f}",
                    "raw_output_sha256": sha256_file(out_file),
                    "receptor_sha256": sha256_file(receptor),
                    "vina_version": actual_version,
                })

        result_csv = run_dir / "vs_results.csv"
        with result_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=[
                "target", "ligand_id", "active",
                "vina_top1_score", "asp_no_cluster_score", "asp_clustered_score",
                "raw_output_sha256", "receptor_sha256", "vina_version",
            ])
            writer.writeheader()
            writer.writerows(rows)

        frozen["execution"]["status"] = "completed"
        frozen["execution"]["rows"] = len(rows)
        frozen["execution"]["results_sha256"] = sha256_file(result_csv)
        (run_dir / "MANIFEST_LOCKED.json").write_text(json.dumps(frozen, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"status": "completed", "run_dir": str(run_dir), "results": str(result_csv), "rows": len(rows)}, indent=2))
        return 0
    except Exception:
        frozen["execution"]["status"] = "failed"
        (run_dir / "MANIFEST_LOCKED.json").write_text(json.dumps(frozen, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
