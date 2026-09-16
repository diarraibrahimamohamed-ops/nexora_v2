#!/usr/bin/env python3
"""N3XORA Phase 11 — freeze a real benchmark manifest from Phase-10 inputs.

Phase 11 does not choose methods or tune parameters. It only turns the Phase-10
input template plus a receptor-only fpocket selection file into a locked manifest
whose pocket boxes are explicit and hashed. It refuses placeholder/zero boxes.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from typing import Any

REQUIRED_TARGET_KEYS = ("target", "receptor_pdbqt", "ligands_csv", "pocket")
POCKET_KEYS = ("center_x","center_y","center_z","size_x","size_y","size_z")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''):
            h.update(b)
    return h.hexdigest()


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--template', type=Path, required=True)
    ap.add_argument('--pockets', type=Path, required=True)
    ap.add_argument('--dataset-root', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args=ap.parse_args()

    manifest=json.loads(args.template.read_text(encoding='utf-8'))
    pockets=json.loads(args.pockets.read_text(encoding='utf-8'))
    pocket_by_target={str(x['target']): x['selected_pocket'] for x in pockets.get('targets', [])}
    targets=manifest.get('targets')
    if not isinstance(targets,list) or not targets:
        raise SystemExit('template.targets must be non-empty')

    locked_targets=[]
    for t in targets:
        for k in REQUIRED_TARGET_KEYS:
            if k not in t:
                raise SystemExit(f"target missing key: {k}")
        tid=str(t['target'])
        if tid not in pocket_by_target:
            raise SystemExit(f'{tid}: no receptor-only fpocket selection')
        pk=pocket_by_target[tid]
        if not all(k in pk for k in POCKET_KEYS):
            raise SystemExit(f'{tid}: selected pocket missing coordinates/sizes')
        # Refuse obvious placeholders from the Phase-10 template.
        vals=[float(pk[k]) for k in POCKET_KEYS]
        if vals[3] <= 0 or vals[4] <= 0 or vals[5] <= 0:
            raise SystemExit(f'{tid}: invalid pocket dimensions')
        receptor=(args.dataset_root / t['receptor_pdbqt']).resolve()
        ligands=(args.dataset_root / t['ligands_csv']).resolve()
        if not receptor.is_file() or not ligands.is_file():
            raise SystemExit(f'{tid}: dataset inputs missing')
        nt=dict(t)
        nt['pocket']={k: float(pk[k]) for k in POCKET_KEYS}
        nt['pocket_metadata']={
            'pocket_id': pk.get('pocket_id'),
            'fpocket_score': pk.get('fpocket_score'),
            'selection_source': pk.get('selection_source'),
            'alpha_sphere_count': pk.get('alpha_sphere_count'),
        }
        nt['receptor_sha256']=sha256(receptor)
        nt['ligands_csv_sha256']=sha256(ligands)
        locked_targets.append(nt)

    out={
        'phase':'11',
        'status':'LOCKED',
        'parent_phase':'10',
        'dataset_name':manifest.get('dataset_name'),
        'dataset_root':str(args.dataset_root.resolve()),
        'engine':manifest.get('engine',{}),
        'protocol':manifest.get('protocol',{}),
        'targets':locked_targets,
        'freeze':{
            'template_sha256':sha256(args.template.resolve()),
            'pocket_manifest_sha256':sha256(args.pockets.resolve()),
            'native_ligand_used_for_pocket_selection':False,
            'labels_used_for_pocket_selection':False,
        },
        'interpretation_policy':{
            'primary_docking_method':'vina_top1',
            'asp_status':'experimental_comparison',
            'no_clinical_or_experimental_affinity_conclusion':True,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':'LOCKED','targets':len(locked_targets),'out':str(args.out)},indent=2))

if __name__=='__main__':
    raise SystemExit(main())
