"""core/protein/structure.py
Migré depuis biological_site_detector.py (Nexora v1)."""
#!/usr/bin/env python3
"""
Module d'enrichissement pour sites actifs biologiques
Intègre des bases de données de sites connus (UniProt, PDB, Catalytic Site Atlas)
CONSERVATION: Ne modifie pas les codes existants
"""

import os
import json
import requests
import tempfile
import sqlite3
from typing import List, Dict, Optional, Tuple
import re
import numpy as np

class BiologicalSiteDetector:
    """
    Détecteur de sites actifs biologiques basé sur des bases de données
    """
    
    def __init__(self):
        self.uniprot_api = "https://rest.uniprot.org/uniprotkb"
        self.pdb_api = "https://data.rcsb.org/rest/v1/core"
        self.csa_known_sites = self._load_csa_patterns()
        self.local_cache = {}
    
    def _load_csa_patterns(self) -> Dict:
        """Charge les motifs connus du Catalytic Site Atlas"""
        # Motifs consensus de sites catalytiques courants
        return {
            'serine_protease': {
                'patterns': [r'G[DE]S[AG]', r'HS[GA]', r'GDS[GS]'],
                'residues': ['H', 'D', 'S'],
                'description': 'Site catalytique des sérines protéases'
            },
            'cysteine_protease': {
                'patterns': [r'CG[SC]', r'QC[GS]', r'WCG'],
                'residues': ['C', 'H', 'N'],
                'description': 'Site catalytique des cystéines protéases'
            },
            'kinase': {
                'patterns': [r'VAIK', r'HRD', r'DFG'],
                'residues': ['K', 'D', 'F', 'G'],
                'description': 'Site catalytique des kinases'
            },
            'metalloprotease': {
                'patterns': [r'HELGH', r'HExxH', r'H[AE]LGH'],
                'residues': ['H', 'E', 'L', 'G'],
                'description': 'Site métallique des métalloprotéases'
            },
            'nad_binding': {
                'patterns': [r'GGXGG', r'TGXXXGIG', r'VIGLG'],
                'residues': ['G', 'T', 'V', 'I', 'L'],
                'description': 'Site de liaison NAD/NADH'
            },
            'atp_binding': {
                'patterns': [r'P-loop', r'GxxxxGKT', r'Walker A'],
                'residues': ['G', 'K', 'T', 'S'],
                'description': 'Site de liaison ATP (P-loop)'
            }
        }
    
    def detect_catalytic_sites(self, protein_atoms: List[Dict], protein_sequence: str = None) -> Tuple[Optional[List[Dict]], Optional[str]]:
        """
        Détecte les sites catalytiques basés sur les motifs séquentiels et structurels
        """
        catalytic_sites = []
        
        # Analyse séquentielle si disponible
        if protein_sequence:
            sequence_sites = self._analyze_sequence_patterns(protein_sequence)
            for site in sequence_sites:
                # Mapper les positions séquentielles sur les coordonnées 3D
                coords = self._map_sequence_to_3d(site['position'], protein_atoms)
                if coords:
                    catalytic_sites.append({
                        'center_x': coords[0],
                        'center_y': coords[1], 
                        'center_z': coords[2],
                        'site_type': site['type'],
                        'confidence': site['confidence'],
                        'method': 'sequence_pattern',
                        'description': site['description'],
                        'pattern': site['pattern'],
                        'size_x': 15.0,
                        'size_y': 15.0,
                        'size_z': 15.0,
                        'score': site['confidence']
                    })
        
        # Analyse structurelle des clusters de résidus fonctionnels
        structure_sites = self._analyze_functional_clusters(protein_atoms)
        catalytic_sites.extend(structure_sites)
        
        if not catalytic_sites:
            return None, "Aucun site catalytique détecté"
        
        # Éliminer les redondances
        unique_sites = self._remove_redundant_sites(catalytic_sites)
        
        return unique_sites, None
    
    def _analyze_sequence_patterns(self, sequence: str) -> List[Dict]:
        """Analyse les motifs séquentiels connus"""
        sites = []
        
        for site_type, info in self.csa_known_sites.items():
            for pattern in info['patterns']:
                matches = re.finditer(pattern, sequence, re.IGNORECASE)
                for match in matches:
                    confidence = 0.8 if len(match.group()) >= 4 else 0.6
                    sites.append({
                        'type': site_type,
                        'position': match.start(),
                        'pattern': match.group(),
                        'confidence': confidence,
                        'description': info['description']
                    })
        
        return sites
    
    def _map_sequence_to_3d(self, seq_position: int, atoms: List[Dict]) -> Optional[Tuple[float, float, float]]:
        """Mappe une position séquentielle sur des coordonnées 3D"""
        # Regrouper les atomes par résidu
        residues = {}
        for atom in atoms:
            res_id = atom.get('residue_id', 0)
            if res_id not in residues:
                residues[res_id] = []
            residues[res_id].append(atom)
        
        # Trouver le résidu correspondant
        target_residue_id = seq_position + 1  # Conversion 1-based
        
        if target_residue_id in residues:
            residue_atoms = residues[target_residue_id]
            # Calculer le centre du résidu
            x = np.mean([a['x'] for a in residue_atoms])
            y = np.mean([a['y'] for a in residue_atoms])
            z = np.mean([a['z'] for a in residue_atoms])
            return (x, y, z)
        
        return None
    
    def _analyze_functional_clusters(self, atoms: List[Dict]) -> List[Dict]:
        """Analyse les clusters de résidus fonctionnels"""
        functional_sites = []
        
        # Identifier les résidus fonctionnels
        functional_residues = []
        for atom in atoms:
            element = atom['element']
            residue_name = atom.get('residue_name', 'UNK')
            
            # Résidus fonctionnels courants
            if element in ['N', 'O', 'S'] and residue_name in ['HIS', 'ASP', 'GLU', 'SER', 'THR', 'CYS', 'LYS', 'ARG', 'TYR']:
                functional_residues.append(atom)
        
        # Chercher les clusters
        if len(functional_residues) >= 3:
            cluster_centers = self._find_functional_clusters(functional_residues)
            
            for i, center in enumerate(cluster_centers):
                functional_sites.append({
                    'center_x': center[0],
                    'center_y': center[1],
                    'center_z': center[2],
                    'site_type': 'functional_cluster',
                    'confidence': 0.7,
                    'method': 'structural_analysis',
                    'description': f'Cluster fonctionnel {i+1}',
                    'size_x': 12.0,
                    'size_y': 12.0,
                    'size_z': 12.0,
                    'score': 0.7
                })
        
        return functional_sites
    
    def _find_functional_clusters(self, residues: List[Dict], cluster_radius: float = 6.0) -> List[Tuple[float, float, float]]:
        """Trouve les clusters de résidus fonctionnels"""
        clusters = []
        used = set()
        
        for i, residue in enumerate(residues):
            if i in used:
                continue
            
            cluster = [residue]
            used.add(i)
            
            # Chercher les voisins
            for j, other in enumerate(residues):
                if j in used:
                    continue
                
                dist = np.sqrt((residue['x'] - other['x'])**2 +
                             (residue['y'] - other['y'])**2 +
                             (residue['z'] - other['z'])**2)
                
                if dist < cluster_radius:
                    cluster.append(other)
                    used.add(j)
            
            if len(cluster) >= 3:
                # Calculer le centre du cluster
                center_x = np.mean([r['x'] for r in cluster])
                center_y = np.mean([r['y'] for r in cluster])
                center_z = np.mean([r['z'] for r in cluster])
                clusters.append((center_x, center_y, center_z))
        
        return clusters
    
    def _remove_redundant_sites(self, sites: List[Dict], min_distance: float = 10.0) -> List[Dict]:
        """Élimine les sites redondants"""
        if not sites:
            return []
        
        unique_sites = [sites[0]]
        
        for site in sites[1:]:
            is_redundant = False
            for existing in unique_sites:
                dist = np.sqrt((site['center_x'] - existing['center_x'])**2 +
                             (site['center_y'] - existing['center_y'])**2 +
                             (site['center_z'] - existing['center_z'])**2)
                if dist < min_distance:
                    # Garder le site avec le meilleur score
                    if site['score'] > existing['score']:
                        unique_sites.remove(existing)
                        unique_sites.append(site)
                    is_redundant = True
                    break
            
            if not is_redundant:
                unique_sites.append(site)
        
        return unique_sites
    
    def detect_binding_sites_uniprot(self, protein_sequence: str, uniprot_id: str = None) -> Tuple[Optional[List[Dict]], Optional[str]]:
        """
        Détecte les sites de liaison depuis UniProt
        """
        try:
            if not uniprot_id:
                return None, "ID UniProt requis"
            
            # API UniProt pour les sites fonctionnels
            url = f"{self.uniprot_api}/{uniprot_id}.json"
            response = requests.get(url, timeout=30)
            
            if response.status_code != 200:
                return None, f"Erreur UniProt: {response.status_code}"
            
            data = response.json()
            binding_sites = []
            
            # Parser les sites fonctionnels
            if 'features' in data:
                for feature in data['features']:
                    if feature.get('type') in ['BINDING', 'ACT_SITE', 'SITE', 'METAL']:
                        site_info = {
                            'site_type': feature['type'],
                            'description': feature.get('description', ''),
                            'position': feature.get('location', {}).get('start', {}).get('value', 0),
                            'confidence': 0.9,  # Haute confiance pour UniProt
                            'method': 'uniprot_annotation',
                            'source': 'UniProt'
                        }
                        binding_sites.append(site_info)
            
            if not binding_sites:
                return None, "Aucun site fonctionnel trouvé dans UniProt"
            
            return binding_sites, None
            
        except Exception as e:
            return None, f"Erreur requête UniProt: {str(e)}"

# Instance globale
biological_site_detector = BiologicalSiteDetector()

def detect_biological_sites(protein_atoms: List[Dict], protein_sequence: str = None, uniprot_id: str = None) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """
    Fonction wrapper pour la détection de sites biologiques
    """
    all_sites = []
    
    # Sites catalytiques
    catalytic_sites, error = biological_site_detector.detect_catalytic_sites(protein_atoms, protein_sequence)
    if not error and catalytic_sites:
        all_sites.extend(catalytic_sites)
    
    # Sites UniProt (si ID disponible)
    if uniprot_id:
        uniprot_sites, error = biological_site_detector.detect_binding_sites_uniprot(protein_sequence, uniprot_id)
        if not error and uniprot_sites:
            # Convertir en format 3D
            for site in uniprot_sites:
                coords = biological_site_detector._map_sequence_to_3d(site['position'], protein_atoms)
                if coords:
                    all_sites.append({
                        'center_x': coords[0],
                        'center_y': coords[1],
                        'center_z': coords[2],
                        'site_type': site['site_type'],
                        'confidence': site['confidence'],
                        'method': site['method'],
                        'description': site['description'],
                        'size_x': 15.0,
                        'size_y': 15.0,
                        'size_z': 15.0,
                        'score': site['confidence']
                    })
    
    if not all_sites:
        return None, "Aucun site biologique détecté"
    
    # Éliminer les redondances et retourner
    unique_sites = biological_site_detector._remove_redundant_sites(all_sites)
    return unique_sites, None
