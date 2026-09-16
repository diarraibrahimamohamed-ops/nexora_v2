from pathlib import Path
from app.core.protein.structure_comparison import compare_structures

AA3={"A":"ALA","C":"CYS","D":"ASP","E":"GLU","F":"PHE","G":"GLY"}

def pdb_for(seq, shift=(0,0,0), mutate=None):
    lines=[]; serial=1
    for i, aa in enumerate(seq,1):
        aa3=AA3[aa]
        dx,dy,dz=shift
        # non-collinear first 4 residues so Kabsch is geometrically well defined
        pts=[(0,0,0),(1.5,0,0),(1.5,1.5,0),(0,1.5,1.2)]
        x,y,z=pts[(i-1)%4]
        x+=i*2+dx; y+=dy; z+=dz
        lines.append(f"ATOM  {serial:5d}  CA  {aa3:>3s} A{i:4d}    {x:8.3f}{y:8.3f}{z:8.3f}  1.00 80.00           C  ")
        serial+=1
    lines.append('END\n')
    return '\n'.join(lines)

def test_kabsch_removes_rigid_translation(tmp_path: Path):
    seq='ACDEFG'
    wt=tmp_path/'wt.pdb'; var=tmp_path/'var.pdb'
    wt.write_text(pdb_for(seq), encoding='utf-8')
    var.write_text(pdb_for(seq, shift=(20,-5,8)), encoding='utf-8')
    r=compare_structures(str(wt), str(var))
    assert r['status']=='computed'
    assert r['alignment']['identity']==1.0
    assert r['superposition']['pre_alignment_rmsd_A'] > 10
    assert r['superposition']['post_alignment_rmsd_A'] < 1e-6

def test_variant_position_is_reported(tmp_path: Path):
    wt=tmp_path/'wt.pdb'; var=tmp_path/'var.pdb'
    wt.write_text(pdb_for('ACDEFG'), encoding='utf-8')
    var.write_text(pdb_for('ACDEFG', shift=(3,2,1)), encoding='utf-8')
    r=compare_structures(str(wt), str(var), variant_protein_position=3)
    vs=r['variant_site']
    assert vs['wt_residue_id']==3
    assert vs['variant_residue_id']==3
    assert vs['wt_aa']=='D' and vs['variant_aa']=='D'
