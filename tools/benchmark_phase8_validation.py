#!/usr/bin/env python3
"""Phase 8: leakage-aware, provenance-first validation gate for N3XORA benchmarks.

This tool does not run docking engines and does not create performance metrics.
It audits a completed benchmark package before metrics are interpreted.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

REQUIRED_VS = {"target", "ligand_id", "active", "vina_top1_score", "asp_no_cluster_score", "asp_clustered_score"}
REQUIRED_POSE = {"target", "complex_id", "vina_top1_rmsd", "asp_no_cluster_rmsd", "asp_clustered_rmsd"}
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def read_csv(path: Path) -> List[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"empty CSV: {path}")
    return rows


def duplicate_keys(rows: List[dict], cols: Tuple[str, ...]) -> List[Tuple[str, ...]]:
    seen: Set[Tuple[str, ...]] = set(); dup=[]
    for r in rows:
        k=tuple(str(r[c]).strip() for c in cols)
        if k in seen: dup.append(k)
        seen.add(k)
    return dup


def audit_vs(path: Path) -> dict:
    rows=read_csv(path)
    missing=sorted(REQUIRED_VS-set(rows[0]))
    if missing: raise ValueError(f"missing columns: {missing}")
    targets=sorted({str(r['target']) for r in rows})
    if len(targets) < 5: raise ValueError('virtual-screening validation requires at least 5 independent targets')
    dups=duplicate_keys(rows,("target","ligand_id"))
    if dups: raise ValueError(f"duplicate target/ligand rows: {dups[:5]}")
    target_labels={t:{str(r['active']).strip() for r in rows if str(r['target'])==t} for t in targets}
    bad_labels={t:sorted(v) for t,v in target_labels.items() if v != {'0','1'}}
    cross_target_ligands: Dict[str, Set[str]]={}
    for r in rows: cross_target_ligands.setdefault(str(r['ligand_id']),set()).add(str(r['target']))
    shared=[lig for lig,ts in cross_target_ligands.items() if len(ts)>1]
    # Shared chemical IDs across targets are not automatically invalid, but must be visible.
    score_cols=["vina_top1_score","asp_no_cluster_score","asp_clustered_score"]
    nonfinite=[]
    for i,r in enumerate(rows,1):
        try:
            vals=[float(r[c]) for c in score_cols]
        except Exception as e: raise ValueError(f"non-numeric score at row {i}: {e}")
        if any(v != v or abs(v)==float('inf') for v in vals): nonfinite.append(i)
    if nonfinite: raise ValueError(f"non-finite scores at rows: {nonfinite[:10]}")
    return {"track":"virtual_screening","csv":str(path),"rows":len(rows),"targets":targets,
            "duplicate_target_ligand":0,"targets_missing_both_classes":bad_labels,
            "ligands_shared_across_targets":len(shared),"shared_ligand_examples":shared[:20],
            "status":"PASS" if not bad_labels else "BLOCKED"}


def audit_pose(path: Path) -> dict:
    rows=read_csv(path); missing=sorted(REQUIRED_POSE-set(rows[0]))
    if missing: raise ValueError(f"missing columns: {missing}")
    dups=duplicate_keys(rows,("target","complex_id"))
    if dups: raise ValueError(f"duplicate target/complex rows: {dups[:5]}")
    bad=[]
    for i,r in enumerate(rows,1):
        for c in REQUIRED_POSE-{"target","complex_id"}:
            try: v=float(r[c])
            except Exception as e: raise ValueError(f"non-numeric {c} row {i}: {e}")
            if v<0 or v!=v or abs(v)==float('inf'): bad.append((i,c,v))
    if bad: raise ValueError(f"invalid RMSD entries: {bad[:10]}")
    targets=sorted({str(r['target']) for r in rows})
    if len(targets) < 5: raise ValueError('pose-selection validation requires at least 5 independent targets')
    return {"track":"pose_selection","csv":str(path),"rows":len(rows),"targets":targets,"status":"PASS"}


def audit_manifest(path: Path) -> dict:
    data=json.loads(path.read_text(encoding='utf-8'))
    required=["benchmark_dataset","receptor_package","ligand_package","software","protocol","results"]
    missing=[k for k in required if k not in data]
    if missing: raise ValueError(f"manifest missing sections: {missing}")
    problems=[]
    # Require immutable hashes for packages/artifacts if paths are provided.
    for section_name, section in (("benchmark_dataset",data["benchmark_dataset"]),("receptor_package",data["receptor_package"]),("ligand_package",data["ligand_package"])):
        p=section.get("path"); h=section.get("sha256")
        if p and not h: problems.append(f"{section_name}: sha256 missing")
        if h and not HEX64.match(str(h)): problems.append(f"{section_name}: invalid sha256")
    software=data["software"]
    for k in ("vina_version","fpocket_version","benchmark_commit"):
        if not software.get(k): problems.append(f"software.{k} missing")
    protocol=data["protocol"]
    for k in ("vina_parameters","pocket_parameters","asp_parameters","structure_provenance_policy","ligand_preparation_policy"):
        if k not in protocol: problems.append(f"protocol.{k} missing")
    results=data["results"]
    if results.get("test_set_tuned") is True: problems.append("test_set_tuned=true blocks interpretation")
    return {"manifest":str(path),"problems":problems,"status":"PASS" if not problems else "BLOCKED"}


def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--vs',type=Path); ap.add_argument('--pose',type=Path); ap.add_argument('--manifest',type=Path, required=True); ap.add_argument('--out',type=Path)
    args=ap.parse_args(); out={"phase":"8","audit":[]}
    try: out['audit'].append(audit_manifest(args.manifest))
    except Exception as e: print(f"MANIFEST BLOCKED: {e}", file=sys.stderr); return 2
    try:
        if args.vs: out['audit'].append(audit_vs(args.vs))
        if args.pose: out['audit'].append(audit_pose(args.pose))
    except Exception as e: print(f"DATA BLOCKED: {e}", file=sys.stderr); return 2
    out['interpretation_gate'] = 'OPEN' if all(a.get('status')=='PASS' for a in out['audit']) else 'BLOCKED'
    text=json.dumps(out,indent=2,ensure_ascii=False)+'\n'; print(text)
    if args.out: args.out.write_text(text,encoding='utf-8')
    return 0 if out['interpretation_gate']=='OPEN' else 2
if __name__=='__main__': raise SystemExit(main())
