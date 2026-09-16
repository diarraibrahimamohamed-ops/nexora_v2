import json
from pathlib import Path
from app.core.docking.pose_comparison import compare_docking_pose_sets


def _pdb(path: Path, shift=(0.0,0.0,0.0)):
    sx,sy,sz=shift
    atoms=[
        ('N',1,'ALA',0,0,0),('CA',1,'ALA',1,0,0),('C',1,'ALA',1.8,0,0),
        ('N',2,'GLY',2.8,0,0),('CA',2,'GLY',3.8,0,0),('C',2,'GLY',4.8,0,0),
        ('N',3,'SER',5.8,0,0),('CA',3,'SER',6.8,0,0),('C',3,'SER',7.8,0,0),
    ]
    with path.open('w') as f:
        serial=1
        for name,rid,res,x,y,z in atoms:
            f.write(f"ATOM  {serial:5d} {name:^4} {res:>3} A{rid:4d}    {x+sx:8.3f}{y+sy:8.3f}{z+sz:8.3f}  1.00 20.00           C\n")
            serial+=1
        f.write('END\n')


def _result(path: Path, offset=(0,0,0)):
    ox,oy,oz=offset
    poses=[]
    for pid,score in [(0,-8.0),(1,-7.5)]:
        poses.append({"pose_id":pid,"score":score,"num_atoms":3,"atoms":[
            {"x":1+ox,"y":0.0+oy,"z":0.0+oz,"atom_name":"C1","element":"C"},
            {"x":1.5+ox,"y":0.5+oy,"z":0.0+oz,"atom_name":"O1","element":"O"},
            {"x":2+ox,"y":0.0+oy,"z":0.0+oz,"atom_name":"N1","element":"N"},
        ]})
    path.write_text(json.dumps({"success":True,"poses":poses,"vina_best_score":score if False else -8.0,"best_pocket":{"id":0,"center_x":2,"center_y":0,"center_z":0}}))


def test_phase6_superposes_variant_frame_and_reports_geometry(tmp_path: Path):
    wt=tmp_path/'wt.pdb'; var=tmp_path/'var.pdb'
    _pdb(wt)
    _pdb(var, shift=(10,5,-2))
    wr=tmp_path/'wr.json'; vr=tmp_path/'vr.json'
    _result(wr, offset=(0,0,0))
    _result(vr, offset=(10,5,-2))
    out=compare_docking_pose_sets(str(wr),str(vr),str(wt),str(var),ligand_smiles='CCN',top_poses=2)
    assert out['status']=='computed'
    assert out['structural_frame']['variant_coordinates_transformed_into_wt_frame'] is True
    assert out['pose_analysis']['same_rank_pair']['ligand_pose_rmsd_fixed_frame_A'] < 1e-6
    assert out['pocket_alignment']['available'] is True
