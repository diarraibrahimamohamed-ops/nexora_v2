import json
from pathlib import Path
from app.core.docking.comparative import compare_completed_results

def result(path, score, asp):
    path.write_text(json.dumps({"success": True, "vina_best_score": score, "boltzmann_effective_score": asp, "boltzmann_mean_score": asp+0.1, "num_poses": 3, "metadata": {"num_pockets_tested": 2}, "docking_engine": "AutoDock Vina", "aggregation_method": "ASP — analyse complémentaire", "best_pocket": {"id": 1}}), encoding="utf-8")

def test_paired_score_contrast_is_not_delta_g(tmp_path: Path):
    wt=tmp_path/'wt.json'; var=tmp_path/'var.json'
    result(wt, -8.0, -7.7); result(var, -7.0, -6.8)
    r=compare_completed_results(str(wt), str(var), ligand_smiles='CCO', protocol={'protocol_version':'research-vina-paired-v1','max_pockets':3})
    assert r['status']=='computed'
    assert r['score_contrast']['delta_vina_variant_minus_wt']==1.0
    assert 'experimental' in r['score_contrast']['interpretation']
    assert r['ligand']['same_ligand'] is True
