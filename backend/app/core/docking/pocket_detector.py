"""core/docking/pocket_detector.py
Migré depuis pocket_detection_enriched_fixed.py (Nexora v1)."""
#!/usr/bin/env python3
"""
Module d'enrichissement CORRIGÉ pour la détection de sites actifs
Version avec fpocket corrigé et fallbacks robustes
"""

import os
import subprocess
import sys
import tempfile
import json
import numpy as np
from typing import List, Dict, Optional, Tuple
import re

class PocketDetectorEnrichedFixed:
    """
    Classe d'enrichissement CORRIGÉE pour la détection de poches de liaison
    """
    
    def __init__(self):
        self.fpocket_available = self._check_fpocket()
        self.methods_available = []
        if self.fpocket_available:
            self.methods_available.append('fpocket')
    
    def _check_fpocket(self) -> bool:
        """Vérifie si fpocket est disponible"""
        try:
            result = subprocess.run(['fpocket', '--version'], 
                                  capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except:
            return False
    
    def detect_pockets_fpocket(self, protein_atoms: List[Dict]) -> Tuple[Optional[List[Dict]], Optional[str]]:
        """Détecte les poches avec fpocket et utilise ses alpha-sphères.

        fpocket écrit les poches dans ``<pdb>_out/pockets/``. Les fichiers
        ``pocket*_vert.pqr`` contiennent les centres/rayons des alpha-sphères;
        ils sont donc préférés pour construire la boîte Vina. Les métriques
        textuelles sont associées aux poches par leur ordre de sortie.
        """
        if not self.fpocket_available:
            return None, "fpocket n'est pas installé"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                pdb_file = os.path.join(temp_dir, 'protein.pdb')
                self._write_pdb_from_atoms(protein_atoms, pdb_file)
                result = subprocess.run(
                    ['fpocket', '-f', pdb_file], capture_output=True, text=True,
                    timeout=120, cwd=temp_dir
                )
                if result.returncode != 0:
                    return None, f"fpocket erreur: {result.stderr.strip()}"

                output_dir = os.path.join(temp_dir, 'protein_out')
                pockets_dir = os.path.join(output_dir, 'pockets')
                if not os.path.isdir(pockets_dir):
                    return None, "Répertoire pockets de fpocket non créé"

                # Pocket files are zero-based (pocket0_*), while the human-readable
                # info file is one-based ("Pocket 1 :"). Parse metrics by ID,
                # never by the incidental order of os.listdir()/lexicographic sort.
                info_candidates = [
                    os.path.join(output_dir, 'protein_pockets.info'),
                    os.path.join(output_dir, 'protein_info.txt'),
                ]
                metrics_by_id = {}
                for info_file in info_candidates:
                    if os.path.exists(info_file):
                        metrics_by_id = self._parse_fpocket_metrics_by_id(info_file)
                        if metrics_by_id:
                            break

                vert_files = sorted(
                    (
                        name for name in os.listdir(pockets_dir)
                        if re.fullmatch(r'pocket\d+_vert\.pqr', name)
                    ),
                    key=lambda name: int(re.search(r'pocket(\d+)_', name).group(1)),
                )
                atm_files = {
                    int(m.group(1)): os.path.join(pockets_dir, f)
                    for f in os.listdir(pockets_dir)
                    if (m := re.fullmatch(r'pocket(\d+)_atm\.pdb', f))
                }

                pockets = []
                for vert_name in vert_files:
                    pocket_id = int(re.search(r'pocket(\d+)_', vert_name).group(1))
                    vert_path = os.path.join(pockets_dir, vert_name)
                    pocket = self._parse_pocket_vert_file(vert_path, pocket_id)
                    if pocket is None and pocket_id in atm_files:
                        pocket = self._parse_pocket_pdb_file(atm_files[pocket_id], pocket_id)
                    if pocket is None:
                        continue

                    # fpocket's info sections are one-based: Pocket 1 maps to
                    # pocket0_vert.pqr. Gaps in the parsed files do not shift scores.
                    metrics = metrics_by_id.get(pocket_id, {})
                    fpocket_score = metrics.get('fpocket_score')
                    druggability_score = metrics.get('druggability_score')
                    if fpocket_score is not None:
                        pocket['score'] = fpocket_score  # compatibility alias
                    pocket['fpocket_score'] = fpocket_score
                    pocket['fpocket_druggability_score'] = druggability_score
                    pocket['fpocket_pocket_number'] = pocket_id + 1
                    pocket['score_semantics'] = 'fpocket binding-site score; not a probability'
                    pocket['druggability_score_semantics'] = (
                        'fpocket druggability estimate for small-molecule sites; not experimental evidence'
                    )
                    pocket['method'] = 'fpocket'
                    pocket['detection_source'] = 'fpocket'
                    pocket['quality_class'] = 'algorithmic_pocket_candidate'
                    pocket['combined_score'] = fpocket_score
                    pocket['confidence'] = None
                    pocket['confidence_semantics'] = 'not calibrated'
                    pockets.append(pocket)

                if not pockets:
                    return None, "Aucune poche exploitable détectée par fpocket"
                pockets.sort(
                    key=lambda p: (
                        p.get('combined_score') is not None,
                        p.get('combined_score') if p.get('combined_score') is not None else float('-inf'),
                    ),
                    reverse=True,
                )
                return pockets[:10], None
        except subprocess.TimeoutExpired:
            return None, "fpocket timeout (120s)"
        except Exception as e:
            return None, f"Erreur fpocket: {str(e)}"

    def _write_pdb_from_atoms(self, atoms: List[Dict], pdb_file: str):
        """Écrit les atomes au format PDB standard CORRIGÉ"""
        with open(pdb_file, 'w') as f:
            for i, atom in enumerate(atoms):
                # Format PDB standard avec numéros atomiques valides
                atom_num = (i % 99999) + 1  # Éviter les numéros > 99999
                res_num = ((int(atom.get('residue_id', 1)) - 1) % 9999) + 1
                
                # Nettoyer les noms
                atom_name = atom.get('atom_name', 'C')[:4]
                residue_name = atom.get('residue_name', 'UNK')[:3]
                element = atom['element'][:2]
                
                line = f"ATOM  {atom_num:5d} {atom_name:<4s} {residue_name:>3s} A{res_num:4d}    {atom['x']:8.3f}{atom['y']:8.3f}{atom['z']:8.3f}  1.00  0.00          {element:>2s}\n"
                f.write(line)
            
            # Ajouter END
            f.write("END\n")
    
    def _parse_fpocket_metrics_by_id(self, info_file: str) -> Dict[int, Dict]:
        """Parse score/druggability par ID de poche, en convertissant Pocket 1 → pocket0."""
        metrics: Dict[int, Dict] = {}
        number = r"([-+]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][-+]?\\d+)?)"
        try:
            with open(info_file, 'r', encoding='utf-8', errors='replace') as handle:
                content = handle.read()
            sections = re.finditer(
                r"(?ims)^\\s*Pocket\\s+(\\d+)\\s*:\\s*(.*?)(?=^\\s*Pocket\\s+\\d+\\s*:|\\Z)",
                content,
            )
            for section in sections:
                pocket_number = int(section.group(1))
                if pocket_number < 1:
                    continue
                block = section.group(2)
                score_match = re.search(rf"(?im)^\\s*Score\\s*:\\s*{number}", block)
                drug_match = re.search(
                    rf"(?im)^\\s*Druggability\\s+Score\\s*:\\s*{number}", block
                )
                entry = {}
                if score_match:
                    value = float(score_match.group(1))
                    if np.isfinite(value):
                        entry['fpocket_score'] = value
                if drug_match:
                    value = float(drug_match.group(1))
                    if np.isfinite(value):
                        entry['druggability_score'] = value
                if entry:
                    metrics[pocket_number - 1] = entry
        except Exception as e:
            print(f"  Erreur parsing métriques fpocket: {e}", file=sys.stderr)
        return metrics

    def _parse_fpocket_scores(self, info_file: str) -> List[float]:
        """Compatibilité: renvoie les scores dans l'ordre numérique des IDs zero-based."""
        metrics = self._parse_fpocket_metrics_by_id(info_file)
        return [
            metrics[pocket_id]['fpocket_score']
            for pocket_id in sorted(metrics)
            if 'fpocket_score' in metrics[pocket_id]
        ]

    def _parse_fpocket_info_file(self, info_file: str) -> List[Dict]:
        """Compatibilité: transforme les métriques en enregistrements zero-based."""
        return [
            {'pocket_id': pocket_id, **metrics}
            for pocket_id, metrics in sorted(self._parse_fpocket_metrics_by_id(info_file).items())
        ]

    def _parse_pocket_vert_file(self, pocket_file: str, pocket_id: int) -> Optional[Dict]:
        """Parse les alpha-sphères ``pocket*_vert.pqr`` produites par fpocket."""
        coords = []
        radii = []
        try:
            with open(pocket_file, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if not line.strip() or line.startswith(('#', 'REMARK')):
                        continue
                    parts = line.split()
                    # PQR columns: record, serial, atom, residue, chain,
                    # residue ID, x, y, z, charge, radius.
                    if len(parts) < 11:
                        continue
                    try:
                        x, y, z = float(parts[6]), float(parts[7]), float(parts[8])
                        charge, radius = float(parts[9]), float(parts[10])
                    except (ValueError, IndexError):
                        continue
                    if not np.isfinite([x, y, z, charge, radius]).all() or radius <= 0:
                        continue
                    coords.append([x, y, z])
                    radii.append(radius)
            if not coords:
                return None
            arr = np.asarray(coords, dtype=float)
            if not np.isfinite(arr).all():
                return None
            extents = arr.max(axis=0) - arr.min(axis=0)
            return {
                'pocket_id': pocket_id,
                'center_x': float(arr[:, 0].mean()),
                'center_y': float(arr[:, 1].mean()),
                'center_z': float(arr[:, 2].mean()),
                'coordinates': arr.tolist(),
                'alpha_sphere_count': len(coords),
                'alpha_sphere_radii_A': radii,
                'mean_alpha_sphere_radius_A': float(np.mean(radii)) if radii else None,
                'max_alpha_sphere_radius_A': float(max(radii)) if radii else None,
                'size_x': float(extents[0] + 8.0),
                'size_y': float(extents[1] + 8.0),
                'size_z': float(extents[2] + 8.0),
            }
        except Exception as e:
            print(f"  Erreur parsing alpha-sphères poche {pocket_id}: {e}", file=sys.stderr)
            return None

    def _parse_pocket_pdb_file(self, pocket_file: str, pocket_id: int) -> Optional[Dict]:
        """Parse un fichier de poche individuel (.pdb)"""
        try:
            atoms = []
            with open(pocket_file, 'r') as f:
                for line in f:
                    if line.startswith('ATOM') or line.startswith('HETATM'):
                        try:
                            x = float(line[30:38].strip())
                            y = float(line[38:46].strip())
                            z = float(line[46:54].strip())
                            atoms.append({'x': x, 'y': y, 'z': z})
                        except (ValueError, IndexError):
                            continue
            
            if atoms:
                # Calculer le centre de la poche
                center_x = np.mean([a['x'] for a in atoms])
                center_y = np.mean([a['y'] for a in atoms])
                center_z = np.mean([a['z'] for a in atoms])
                
                coords = np.asarray([[a['x'], a['y'], a['z']] for a in atoms], dtype=float)
                extents = coords.max(axis=0) - coords.min(axis=0)
                return {
                    'pocket_id': pocket_id,
                    'center_x': center_x,
                    'center_y': center_y,
                    'center_z': center_z,
                    'atoms_count': len(atoms),
                    'coordinates': coords.tolist(),
                    'size_x': float(extents[0] + 8.0),
                    'size_y': float(extents[1] + 8.0),
                    'size_z': float(extents[2] + 8.0)
                }
        
        except Exception as e:
            print(f"  Erreur parsing poche {pocket_id}: {e}", file=sys.stderr)
        
        return None
    
    def detect_pockets_alternative_methods(self, protein_atoms: List[Dict]) -> Tuple[Optional[List[Dict]], Optional[str]]:
        """
        Méthodes alternatives de détection de poches
        """
        pockets = []
        
        # Méthode 1: Détection par densité atomique
        density_pockets = self._detect_by_atomic_density(protein_atoms)
        pockets.extend(density_pockets)
        
        # Méthode 2: Détection par cavités géométriques
        cavity_pockets = self._detect_by_geometric_cavities(protein_atoms)
        pockets.extend(cavity_pockets)
        
        # Méthode 3: Détection par clusters de résidus hydrophobes
        hydrophobic_pockets = self._detect_hydrophobic_clusters(protein_atoms)
        pockets.extend(hydrophobic_pockets)
        
        if not pockets:
            return None, "Aucune poche détectée par les méthodes alternatives"
        
        # Dédupliquer et enrichir
        unique_pockets = self._remove_redundant_pockets(pockets)
        
        for pocket in unique_pockets:
            # These are exploratory geometric heuristics, not trained
            # probabilities and not equivalent to fpocket scores.
            pocket['method'] = 'alternative_enhanced'
            pocket['heuristic_score'] = float(pocket.get('score', 0.5))
            pocket['score_semantics'] = 'uncalibrated heuristic ranking score'
            pocket['confidence'] = None
            pocket['confidence_semantics'] = 'not calibrated; no probability is available'
            pocket['quality_class'] = 'exploratory_heuristic_candidate'

        return unique_pockets[:8], None
    
    def _detect_by_atomic_density(self, atoms: List[Dict]) -> List[Dict]:
        """Détection par analyse de densité atomique"""
        pockets = []
        
        if len(atoms) < 50:
            return pockets
        
        # Calculer le centre de masse
        center_x = np.mean([a['x'] for a in atoms])
        center_y = np.mean([a['y'] for a in atoms])
        center_z = np.mean([a['z'] for a in atoms])
        
        # Grille 3D pour l'analyse de densité
        grid_size = 3.0
        search_radius = 15.0
        
        for dx in np.arange(-search_radius, search_radius, grid_size):
            for dy in np.arange(-search_radius, search_radius, grid_size):
                for dz in np.arange(-search_radius, search_radius, grid_size):
                    test_x = center_x + dx
                    test_y = center_y + dy
                    test_z = center_z + dz
                    
                    # Compter les atomes voisins
                    nearby_count = 0
                    for atom in atoms:
                        dist = np.sqrt((atom['x'] - test_x)**2 + 
                                     (atom['y'] - test_y)**2 + 
                                     (atom['z'] - test_z)**2)
                        if dist < 6.0:
                            nearby_count += 1
                    
                    # Si densité appropriée pour une poche
                    if 8 <= nearby_count <= 20:
                        density_score = nearby_count / 20.0
                        
                        pockets.append({
                            'center_x': test_x,
                            'center_y': test_y,
                            'center_z': test_z,
                            'score': density_score,
                            'size_x': 18.0,
                            'size_y': 18.0,
                            'size_z': 18.0,
                            'detection_method': 'atomic_density',
                            'nearby_atoms': nearby_count
                        })
        
        return pockets[:5]
    
    def _detect_by_geometric_cavities(self, atoms: List[Dict]) -> List[Dict]:
        """Détection par analyse de cavités géométriques"""
        pockets = []
        
        # Simplification: chercher les régions avec peu d'atomes proches
        # mais entourées par des atomes plus lointains
        
        if len(atoms) < 30:
            return pockets
        
        # Points de test sur une grille
        min_x = min(a['x'] for a in atoms) - 5
        max_x = max(a['x'] for a in atoms) + 5
        min_y = min(a['y'] for a in atoms) - 5
        max_y = max(a['y'] for a in atoms) + 5
        min_z = min(a['z'] for a in atoms) - 5
        max_z = max(a['z'] for a in atoms) + 5
        
        grid_step = 4.0
        
        for x in np.arange(min_x, max_x, grid_step):
            for y in np.arange(min_y, max_y, grid_step):
                for z in np.arange(min_z, max_z, grid_step):
                    # Compter les atomes à différentes distances
                    close_atoms = sum(1 for a in atoms if np.sqrt((a['x']-x)**2 + (a['y']-y)**2 + (a['z']-z)**2) < 4.0)
                    medium_atoms = sum(1 for a in atoms if 4.0 <= np.sqrt((a['x']-x)**2 + (a['y']-y)**2 + (a['z']-z)**2) < 8.0)
                    
                    # Cavité potentielle: peu d'atomes proches, entourée par des atomes moyens
                    if close_atoms <= 2 and medium_atoms >= 6:
                        cavity_score = medium_atoms / (close_atoms + 1) / 10.0
                        
                        pockets.append({
                            'center_x': x,
                            'center_y': y,
                            'center_z': z,
                            'score': min(cavity_score, 1.0),
                            'size_x': 16.0,
                            'size_y': 16.0,
                            'size_z': 16.0,
                            'detection_method': 'geometric_cavity',
                            'close_atoms': close_atoms,
                            'medium_atoms': medium_atoms
                        })
        
        return pockets[:3]
    
    def _detect_hydrophobic_clusters(self, atoms: List[Dict]) -> List[Dict]:
        """Détection de clusters de résidus hydrophobes"""
        pockets = []
        
        # Identifier les atomes hydrophobes
        hydrophobic_elements = ['C']
        hydrophobic_residues = ['ALA', 'VAL', 'LEU', 'ILE', 'MET', 'PHE', 'TRP', 'PRO']
        
        hydrophobic_atoms = []
        for atom in atoms:
            element = atom['element']
            residue = atom.get('residue_name', '')
            
            if element in hydrophobic_elements or residue in hydrophobic_residues:
                hydrophobic_atoms.append(atom)
        
        if len(hydrophobic_atoms) < 10:
            return pockets
        
        # Chercher les clusters
        cluster_radius = 6.0
        used = set()
        
        for i, atom in enumerate(hydrophobic_atoms):
            if i in used:
                continue
            
            cluster = [atom]
            used.add(i)
            
            for j, other in enumerate(hydrophobic_atoms):
                if j in used:
                    continue
                
                dist = np.sqrt((atom['x'] - other['x'])**2 + 
                             (atom['y'] - other['y'])**2 + 
                             (atom['z'] - other['z'])**2)
                
                if dist < cluster_radius:
                    cluster.append(other)
                    used.add(j)
            
            if len(cluster) >= 5:
                # Calculer le centre du cluster
                center_x = np.mean([a['x'] for a in cluster])
                center_y = np.mean([a['y'] for a in cluster])
                center_z = np.mean([a['z'] for a in cluster])
                
                pockets.append({
                    'center_x': center_x,
                    'center_y': center_y,
                    'center_z': center_z,
                    'score': min(len(cluster) / 15.0, 1.0),
                    'size_x': 14.0,
                    'size_y': 14.0,
                    'size_z': 14.0,
                    'detection_method': 'hydrophobic_cluster',
                    'cluster_size': len(cluster)
                })
        
        return pockets[:3]
    
    def _remove_redundant_pockets(self, pockets: List[Dict], min_distance: float = 8.0) -> List[Dict]:
        """Élimine les poches redondantes"""
        if not pockets:
            return []
        
        unique_pockets = [pockets[0]]
        
        for pocket in pockets[1:]:
            is_redundant = False
            for existing in unique_pockets:
                dist = np.sqrt((pocket['center_x'] - existing['center_x'])**2 +
                             (pocket['center_y'] - existing['center_y'])**2 +
                             (pocket['center_z'] - existing['center_z'])**2)
                if dist < min_distance:
                    # Garder la poche avec le meilleur score
                    if pocket.get('score', 0) > existing.get('score', 0):
                        unique_pockets.remove(existing)
                        unique_pockets.append(pocket)
                    is_redundant = True
                    break
            
            if not is_redundant:
                unique_pockets.append(pocket)
        
        return unique_pockets

# Instance globale
pocket_detector_enriched_fixed = PocketDetectorEnrichedFixed()

def detect_binding_pockets_enriched_fixed(protein_atoms: List[Dict]) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """Détection primaire fpocket; les heuristiques restent un secours exploratoire signalé."""
    detector = pocket_detector_enriched_fixed
    if detector.fpocket_available:
        pockets, primary_error = detector.detect_pockets_fpocket(protein_atoms)
        if not primary_error and pockets:
            return pockets, None
    else:
        primary_error = "fpocket indisponible dans cet environnement"

    alt_pockets, fallback_error = detector.detect_pockets_alternative_methods(protein_atoms)
    if not fallback_error and alt_pockets:
        for pocket in alt_pockets:
            pocket['detection_source'] = 'heuristic_fallback'
            pocket['fallback_reason'] = primary_error
            pocket['quality_warning'] = (
                "Poche exploratoire issue d'heuristiques géométriques; "
                "score non calibré et non équivalent à une probabilité de liaison."
            )
        return alt_pockets, None
    return None, primary_error or fallback_error or "Aucune poche détectée"

# Alias pour compatibilité avec vina_runner.py
detect_binding_pockets = detect_binding_pockets_enriched_fixed
