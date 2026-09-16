#!/usr/bin/env python3
"""N3XORA Phase 11 — execute a locked Phase-10 campaign in an isolated Docker image.

No parameter tuning is performed here. The script first checks that Docker is
available and then mounts the dataset/project read-only except for the run output.
It returns a structured pre-execution status when Docker is unavailable.
"""
from __future__ import annotations
import argparse, json, shutil, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--manifest',type=Path,required=True)
    ap.add_argument('--dataset-root',type=Path,required=True)
    ap.add_argument('--run-root',type=Path,required=True)
    ap.add_argument('--image',default='n3xora-phase11-benchmark:1.0')
    ap.add_argument('--dockerfile',type=Path,default=ROOT/'benchmarks/external/phase10/Dockerfile')
    ap.add_argument('--build',action='store_true')
    args=ap.parse_args()
    docker=shutil.which('docker')
    if not docker:
        print(json.dumps({'status':'BLOCKED_ENVIRONMENT','reason':'docker executable not found','next_action':'run this script on the benchmark host'},indent=2))
        return 3
    if not args.manifest.is_file() or not args.dataset_root.is_dir():
        raise SystemExit('manifest/dataset-root missing')
    if args.build:
        subprocess.run([docker,'build','-t',args.image,'-f',str(args.dockerfile),str(args.dockerfile.parent)],check=True)
    args.run_root.mkdir(parents=True,exist_ok=True)
    project=ROOT.resolve()
    cmd=[docker,'run','--rm',
         '--network','none',
         '-v',f'{project}:/workspace/project:ro',
         '-v',f'{args.dataset_root.resolve()}:{args.dataset_root.resolve()}:ro',
         '-v',f'{args.run_root.resolve()}:/workspace/runs:rw',
         args.image,
         '/workspace/project/tools/phase10_execute_campaign.py',
         '/workspace/project/'+str(args.manifest.resolve().relative_to(project)),
         '--vina','vina',
         '--out-root','/workspace/runs']
    proc=subprocess.run(cmd,check=False)
    print(json.dumps({'status':'EXECUTED','returncode':proc.returncode,'run_root':str(args.run_root.resolve())},indent=2))
    return proc.returncode

if __name__=='__main__':
    raise SystemExit(main())
