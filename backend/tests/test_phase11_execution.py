from pathlib import Path
import importlib.util

ROOT=Path(__file__).resolve().parents[2]

def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


def test_phase11_locked_manifest_rejects_placeholder_boxes(tmp_path):
    tool=load('p11lock', ROOT/'tools/phase11_prepare_locked_manifest.py')
    template={'targets':[{'target':'T','receptor_pdbqt':'T/receptor.pdbqt','ligands_csv':'T/ligands.csv','pocket':{}}]}
    pockets={'targets':[{'target':'T','selected_pocket':{'center_x':0,'center_y':0,'center_z':0,'size_x':0,'size_y':0,'size_z':0}}]}
    t=tmp_path/'t.json'; p=tmp_path/'p.json'; d=tmp_path/'data'/'T'; d.mkdir(parents=True)
    (d/'receptor.pdbqt').write_text('x'); (d/'ligands.csv').write_text('ligand_id,active,pdbqt\nL,1,L.pdbqt\n'); (d/'L.pdbqt').write_text('x')
    t.write_text(__import__('json').dumps(template)); p.write_text(__import__('json').dumps(pockets))
    import subprocess,sys
    r=subprocess.run([sys.executable,str(ROOT/'tools/phase11_prepare_locked_manifest.py'),'--template',str(t),'--pockets',str(p),'--dataset-root',str(tmp_path/'data'),'--out',str(tmp_path/'o.json')],capture_output=True,text=True)
    assert r.returncode != 0
