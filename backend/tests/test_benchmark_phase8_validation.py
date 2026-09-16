import csv, json, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
TOOL=ROOT/'tools'/'benchmark_phase8_validation.py'

def run(tmp_path, vs, manifest):
    v=tmp_path/'vs.csv'; v.write_text(vs, encoding='utf-8')
    m=tmp_path/'manifest.json'; m.write_text(json.dumps(manifest), encoding='utf-8')
    return subprocess.run([sys.executable, str(TOOL), '--vs', str(v), '--manifest', str(m)], capture_output=True, text=True)

def valid_manifest():
    return {
      'benchmark_dataset': {'name':'test','path':'pkg.zip','sha256':'a'*64},
      'receptor_package': {'path':'r.zip','sha256':'b'*64},
      'ligand_package': {'path':'l.zip','sha256':'c'*64},
      'software': {'vina_version':'1.2.5','fpocket_version':'4.0','benchmark_commit':'deadbeef'},
      'protocol': {'vina_parameters':{},'pocket_parameters':{},'asp_parameters':{},'structure_provenance_policy':'x','ligand_preparation_policy':'x'},
      'results': {'test_set_tuned': False}
    }

def test_gate_opens_on_clean_case(tmp_path):
    vs='target,ligand_id,active,vina_top1_score,asp_no_cluster_score,asp_clustered_score\nT1,L1,1,-8,-7,-7\nT1,L2,0,-6,-6,-6\nT2,L3,1,-9,-8,-8\nT2,L4,0,-6,-6,-6\nT3,L5,1,-8,-7,-7\nT3,L6,0,-5,-5,-5\nT4,L7,1,-9,-8,-8\nT4,L8,0,-6,-6,-6\nT5,L9,1,-8,-7,-7\nT5,L10,0,-6,-6,-6\n'
    r=run(tmp_path,vs,valid_manifest())
    assert r.returncode==0, r.stderr+r.stdout
    assert '"OPEN"' in r.stdout

def test_gate_blocks_duplicate_pair(tmp_path):
    vs='target,ligand_id,active,vina_top1_score,asp_no_cluster_score,asp_clustered_score\nT1,L1,1,-8,-7,-7\nT1,L1,1,-8,-7,-7\nT1,L2,0,-6,-6,-6\n'
    r=run(tmp_path,vs,valid_manifest())
    assert r.returncode==2

def test_gate_blocks_missing_manifest_field(tmp_path):
    m=valid_manifest(); del m['software']['vina_version']
    vs='target,ligand_id,active,vina_top1_score,asp_no_cluster_score,asp_clustered_score\nT1,L1,1,-8,-7,-7\nT1,L2,0,-6,-6,-6\n'
    r=run(tmp_path,vs,m)
    assert r.returncode==2
