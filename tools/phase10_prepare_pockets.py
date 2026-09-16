#!/usr/bin/env python3
"""Phase 10 — receptor-only fpocket preparation for benchmark inputs.

For each target directory containing receptor.pdb, run fpocket and produce a
frozen pocket box manifest. The selector uses ONLY receptor/fpocket information:
the native/active ligand is never inspected.
"""
from __future__ import annotations
import argparse, json, math, re, shutil, subprocess
from pathlib import Path
from typing import Any


def parse_scores(info: Path) -> list[float]:
    scores=[]
    if not info.exists(): return scores
    for line in info.read_text(errors='replace').splitlines():
        m=re.search(r"Score\s*:\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", line)
        if m:
            scores.append(float(m.group(1)))
    return scores


def parse_vert(path: Path, pid: int, score: float | None) -> dict[str, Any] | None:
    pts=[]
    for line in path.read_text(errors='replace').splitlines():
        if not line.strip() or line.startswith('#'): continue
        parts=line.split()
        try:
            x,y,z=float(parts[6]),float(parts[7]),float(parts[8])
        except (IndexError,ValueError):
            continue
        pts.append((x,y,z))
    if not pts: return None
    xs,ys,zs=zip(*pts)
    minx,maxx,miny,maxy,minz,maxz=min(xs),max(xs),min(ys),max(ys),min(zs),max(zs)
    # Same margin convention used by N3XORA's pocket detector.
    margin=4.0
    return {
        'pocket_id': pid,
        'fpocket_score': score,
        'center_x': sum(xs)/len(xs),
        'center_y': sum(ys)/len(ys),
        'center_z': sum(zs)/len(zs),
        'size_x': max(1.0, maxx-minx+2*margin),
        'size_y': max(1.0, maxy-miny+2*margin),
        'size_z': max(1.0, maxz-minz+2*margin),
        'alpha_sphere_count': len(pts),
        'selection_source': 'fpocket_score_only_receptor_only'
    }


def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('root',type=Path); ap.add_argument('--fpocket',default='fpocket'); ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    exe=shutil.which(args.fpocket)
    if not exe: raise SystemExit('fpocket executable not found')
    root=args.root.resolve(); records=[]
    for receptor in sorted(root.glob('*/receptor.pdb')):
        target=receptor.parent.name
        proc=subprocess.run([exe,'-f',str(receptor)],cwd=receptor.parent,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,check=False)
        (receptor.parent/'fpocket.log').write_text(proc.stdout or '',encoding='utf-8')
        if proc.returncode!=0: raise SystemExit(f'fpocket failed for {target}')
        prefix=receptor.stem
        pockets_dir=receptor.parent/f'{prefix}_out'/'pockets'
        info_file=receptor.parent/f'{prefix}_out'/f'{prefix}_info.txt'
        scores=parse_scores(info_file)
        pockets=[]
        for vf in sorted(pockets_dir.glob('pocket*_vert.pqr')):
            m=re.search(r'pocket(\d+)_vert',vf.name)
            if not m: continue
            pid=int(m.group(1))
            score=scores[pid-1] if 0<pid<=len(scores) else None
            item=parse_vert(vf,pid,score)
            if item: pockets.append(item)
        if not pockets: raise SystemExit(f'No fpocket alpha spheres parsed for {target}')
        scored=[p for p in pockets if isinstance(p.get('fpocket_score'),(int,float)) and math.isfinite(float(p['fpocket_score']))]
        if not scored: raise SystemExit(f'No finite fpocket scores for {target}; refuse implicit selection')
        selected=max(scored,key=lambda p:(float(p['fpocket_score']),-int(p['pocket_id'])))
        records.append({'target':target,'receptor_pdb':str(receptor.relative_to(root)),'selected_pocket':selected,'all_pockets':pockets})
    args.out.write_text(json.dumps({'phase':'10','pocket_policy':'receptor-only; max fpocket score; no native-ligand inspection','targets':records},indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':'PASS','targets':len(records),'output':str(args.out)},indent=2))
    return 0
if __name__=='__main__': raise SystemExit(main())
