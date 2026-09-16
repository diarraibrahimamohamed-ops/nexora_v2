"""Controlled ASP benchmark.

Important: synthetic only. It tests method behavior under known labels; it does
NOT substitute for an external docking benchmark with experimental structures.
"""
from __future__ import annotations
import csv, json, random, sys
from pathlib import Path
from typing import Dict, List, Tuple
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.core.docking.asp.aggregation import aggregate_pose_scores
from app.core.docking.asp.scoring import boltzmann_logmean_score


def make_pose(score: float, pocket: int, rmsd: float, idx: int, native_like: bool) -> Dict:
    # Native and decoy geometries differ in internal shape, not by rigid translation.
    bend = 0.10 if native_like else 0.75
    perturb = min(rmsd, 8.0) * 0.015
    x = 0.02 * idx
    return {
        'pose_id': f'p{pocket}_{idx}', 'score': float(score), 'pocket_id': pocket,
        'rmsd_to_native': float(rmsd),
        'atoms': [
            {'element':'C','x':0.0,'y':0.0,'z':0.0},
            {'element':'C','x':1.0,'y':0.0,'z':0.0},
            {'element':'N','x':0.50,'y':1.0 + bend + perturb,'z':0.0},
            {'element':'O','x':0.50,'y':0.5,'z':1.0 + bend + perturb},
        ]
    }


def scenario(rng: random.Random, case_id: int) -> Tuple[List[Dict], int]:
    native_pocket = rng.choice([0,1])
    poses=[]; idx=0
    for pocket in [0,1]:
        n=rng.randint(10,18)
        for _ in range(n):
            native_like=(pocket==native_pocket and rng.random()<0.32)
            if native_like:
                rmsd=max(0.35,rng.gauss(1.35,0.45)); base=-7.0
            else:
                rmsd=max(2.6,rng.gauss(5.0,1.3)); base=-7.15 if pocket!=native_pocket else -6.75
            score=base+rng.gauss(0.0,0.65)
            poses.append(make_pose(score,pocket,rmsd,idx,native_like)); idx+=1
    return poses,native_pocket


def top1(poses): return min(poses,key=lambda p:p['score'])

def asp_no_cluster(poses,T=298.15):
    best=None
    for pocket in sorted(set(p['pocket_id'] for p in poses)):
        ps=[p for p in poses if p['pocket_id']==pocket]
        s=boltzmann_logmean_score([p['score'] for p in ps],T)
        cand=min(ps,key=lambda p:p['score'])
        d=dict(cand); d['aggregate_score']=s
        if best is None or s<best['aggregate_score']: best=d
    return best

def asp_clustered(poses,T=298.15,thr=2.0):
    copied=[]
    for p in poses: copied.append(dict(p, atoms=[dict(a) for a in p['atoms']]))
    score,meta=aggregate_pose_scores(copied,T,thr)
    pocket=meta['best_pocket_id']
    # The current ASP contract selects the representative with the greatest
    # within-pocket Boltzmann weight; because weight is monotonic in score,
    # this is the lowest-score cluster representative.
    ps=[p for p in copied if p['pocket_id']==pocket and p.get('pose_cluster_id') is not None]
    chosen=min(ps,key=lambda p:p['score'])
    d=dict(chosen); d['aggregate_score']=score; d['best_pocket']=pocket; d['meta']=meta
    return d

def summarize(rows):
    out={}
    for m in sorted({r['method'] for r in rows}):
        rr=[r for r in rows if r['method']==m]
        rms=np.array([r['selected_rmsd'] for r in rr])
        out[m]={
            'n':len(rr),
            'native_like_hit_rate_RMSD<=2A':float(np.mean(rms<=2.0)),
            'native_pocket_selection_accuracy':float(np.mean([r['selected_pocket']==r['native_pocket'] for r in rr])),
            'median_selected_rmsd_A':float(np.median(rms)),
            'mean_selected_rmsd_A':float(np.mean(rms)),
        }
    return out

def main():
    root=Path(__file__).resolve().parents[1]; out=root/'benchmarks'/'synthetic'; out.mkdir(parents=True,exist_ok=True)
    rng=random.Random(20260913); rows=[]
    for case in range(2000):
        poses,native=scenario(rng,case)
        for method,sel in [('vina_top1',top1(poses)),('asp_logmean_no_clustering',asp_no_cluster(poses)),('asp_clustered_2A',asp_clustered(poses))]:
            rows.append({'case_id':case,'method':method,'native_pocket':native,'selected_pocket':sel['pocket_id'],'selected_score':sel['score'],'selected_rmsd':sel['rmsd_to_native']})
    summary=summarize(rows)
    payload={'n_cases':2000,'seed':20260913,'summary':summary,'rows':rows}
    (out/'results.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    with (out/'results.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()
