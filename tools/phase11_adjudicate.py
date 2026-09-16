#!/usr/bin/env python3
"""Phase 11 — adjudicate a completed campaign without changing the test set.

Runs Phase-8 gate then Phase-9 inference and writes an immutable evidence index.
No rows are filtered or retuned after seeing scores.
"""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def sha256(p:Path)->str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--csv',type=Path,required=True); ap.add_argument('--manifest',type=Path,required=True); ap.add_argument('--out',type=Path,required=True); args=ap.parse_args()
    gate=subprocess.run([sys.executable,str(ROOT/'tools/benchmark_phase8_validation.py'),'--vs',str(args.csv),'--manifest',str(args.manifest)],capture_output=True,text=True,check=False)
    if gate.returncode!=0:
        args.out.write_text(json.dumps({'phase':'11','status':'BLOCKED','phase8_stdout':gate.stdout,'phase8_stderr':gate.stderr},indent=2)+'\n',encoding='utf-8')
        print(gate.stdout,end=''); print(gate.stderr,file=sys.stderr,end=''); return gate.returncode
    report_path=args.out.with_suffix('.phase9.json')
    inf=subprocess.run([sys.executable,str(ROOT/'tools/benchmark_phase9_campaign.py'),str(args.csv),'--manifest',str(args.manifest),'--out',str(report_path)],capture_output=True,text=True,check=False)
    payload={'phase':'11','status':'COMPLETED' if inf.returncode==0 else 'INFERENCE_FAILED','csv_sha256':sha256(args.csv),'manifest_sha256':sha256(args.manifest),'phase8_gate':json.loads(gate.stdout),'phase9_report':str(report_path),'phase9_returncode':inf.returncode}
    args.out.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps(payload,indent=2,ensure_ascii=False))
    return inf.returncode
if __name__=='__main__': raise SystemExit(main())
