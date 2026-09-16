import csv, json, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
TOOL=ROOT/'tools'/'benchmark_phase9_campaign.py'

def manifest():
    return {
      'benchmark_dataset': {'name':'test','path':'pkg.zip','sha256':'a'*64},
      'receptor_package': {'path':'r.zip','sha256':'b'*64},
      'ligand_package': {'path':'l.zip','sha256':'c'*64},
      'software': {'vina_version':'1.2.5','fpocket_version':'4.0','rdkit_version':'2025.03','benchmark_commit':'deadbeef'},
      'protocol': {'vina_parameters':{},'pocket_parameters':{},'asp_parameters':{},'structure_provenance_policy':'x','ligand_preparation_policy':'x'},
      'results': {'test_set_tuned':False}
    }

def test_phase9_statistical_engine_runs(tmp_path):
    rows=[]
    for i in range(1,9):
        t=f'T{i}'
        rows += [
          {'target':t,'ligand_id':f'L{i}A','active':'1','vina_top1_score':str(-8.0-i*0.1),'asp_no_cluster_score':str(-7.0-i*0.1),'asp_clustered_score':str(-7.5-i*0.1),'smiles':'CCO'},
          {'target':t,'ligand_id':f'L{i}D','active':'0','vina_top1_score':'-5.0','asp_no_cluster_score':'-5.0','asp_clustered_score':'-5.0','smiles':'CCN'},
        ]
    csvp=tmp_path/'vs.csv'
    with csvp.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    mp=tmp_path/'m.json'; mp.write_text(json.dumps(manifest()),encoding='utf-8')
    out=tmp_path/'out.json'
    r=subprocess.run([sys.executable,str(TOOL),str(csvp),'--manifest',str(mp),'--out',str(out),'--bootstrap','1000','--permutations','1000'],capture_output=True,text=True)
    assert r.returncode==0, r.stderr+r.stdout
    data=json.loads(out.read_text())
    assert data['phase']=='9'
    assert len(data['paired_target_inference'])==12
    assert data['scaffold_diagnostics']['available'] is True
