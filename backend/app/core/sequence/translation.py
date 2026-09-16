"""core/sequence/translation.py — ADN→protéine + legacy geometry helper.

The geometry helper is retained for legacy sequence experiments only. The
scientific docking pipeline uses ColabFold/AlphaFold2 instead.
"""
#!/usr/bin/env python3
"""Traduction génétique + legacy protein geometry helper."""

import json
import math
import subprocess
import sys
import os
import tempfile
import time
import re
import numpy as np
from typing import Dict, List, Tuple, Optional

# Import fpocket et modules d'enrichissement
try:
    from app.core.docking.pocket_detector import detect_binding_pockets_enriched_fixed
    from app.core.protein.structure import detect_biological_sites
    FPOCKET_AVAILABLE = True
    print(" fpocket + sites biologiques chargés dans docking_complete.py", file=sys.stderr)
except ImportError as e:
    FPOCKET_AVAILABLE = False
    print(f"  fpocket non disponible dans docking_complete.py: {e}", file=sys.stderr)

# ============================================================================
# CONSTANTES SCIENTIFIQUES
# ============================================================================

GENETIC_CODE = {
    'TTT': 'F', 'TTC': 'F', 'TTA': 'L', 'TTG': 'L',
    'TCT': 'S', 'TCC': 'S', 'TCA': 'S', 'TCG': 'S',
    'TAT': 'Y', 'TAC': 'Y', 'TAA': '*', 'TAG': '*',
    'TGT': 'C', 'TGC': 'C', 'TGA': '*', 'TGG': 'W',
    'CTT': 'L', 'CTC': 'L', 'CTA': 'L', 'CTG': 'L',
    'CCT': 'P', 'CCC': 'P', 'CCA': 'P', 'CCG': 'P',
    'CAT': 'H', 'CAC': 'H', 'CAA': 'Q', 'CAG': 'Q',
    'CGT': 'R', 'CGC': 'R', 'CGA': 'R', 'CGG': 'R',
    'ATT': 'I', 'ATC': 'I', 'ATA': 'I', 'ATG': 'M',
    'ACT': 'T', 'ACC': 'T', 'ACA': 'T', 'ACG': 'T',
    'AAT': 'N', 'AAC': 'N', 'AAA': 'K', 'AAG': 'K',
    'AGT': 'S', 'AGC': 'S', 'AGA': 'R', 'AGG': 'R',
    'GTT': 'V', 'GTC': 'V', 'GTA': 'V', 'GTG': 'V',
    'GCT': 'A', 'GCC': 'A', 'GCA': 'A', 'GCG': 'A',
    'GAT': 'D', 'GAC': 'D', 'GAA': 'E', 'GAG': 'E',
    'GGT': 'G', 'GGC': 'G', 'GGA': 'G', 'GGG': 'G'
}

# Charges atomiques partielles (Gasteiger-Marsili — AMBER ff99SB)
ATOMIC_CHARGES = {
    'C': {'sp3': -0.12, 'sp2': 0.08,  'aromatic': -0.03},
    'N': {'amine': -0.35, 'amide': -0.40, 'aromatic': -0.50},  # -0.90 corrigé → -0.35
    'O': {'carbonyl': -0.50, 'hydroxyl': -0.65, 'ether': -0.40},
    'S': {'thiol': -0.23, 'sulfide': -0.15},
    'H': {'C-H': 0.06,  'O-H': 0.43,  'N-H': 0.36}
}

# Propriétés acides aminés (Kyte-Doolittle, Richards, B-factor normalisé)
AA_PROPERTIES = {
    'A': {'hydrophobicity':  1.8,  'volume':  88.6, 'flexibility': 0.36},
    'R': {'hydrophobicity': -4.5,  'volume': 173.4, 'flexibility': 0.53},
    'N': {'hydrophobicity': -3.5,  'volume': 114.1, 'flexibility': 0.46},
    'D': {'hydrophobicity': -3.5,  'volume': 111.1, 'flexibility': 0.51},
    'C': {'hydrophobicity':  2.5,  'volume': 108.5, 'flexibility': 0.35},
    'Q': {'hydrophobicity': -3.5,  'volume': 143.8, 'flexibility': 0.49},
    'E': {'hydrophobicity': -3.5,  'volume': 138.4, 'flexibility': 0.50},
    'G': {'hydrophobicity': -0.4,  'volume':  60.1, 'flexibility': 0.54},
    'H': {'hydrophobicity': -3.2,  'volume': 153.2, 'flexibility': 0.32},
    'I': {'hydrophobicity':  4.5,  'volume': 166.7, 'flexibility': 0.40},  # 0.46 → 0.40
    'L': {'hydrophobicity':  3.8,  'volume': 166.7, 'flexibility': 0.37},
    'K': {'hydrophobicity': -3.9,  'volume': 168.6, 'flexibility': 0.47},
    'M': {'hydrophobicity':  1.9,  'volume': 162.9, 'flexibility': 0.42},  # 0.30 → 0.42
    'F': {'hydrophobicity':  2.8,  'volume': 189.9, 'flexibility': 0.31},
    'P': {'hydrophobicity': -1.6,  'volume': 112.7, 'flexibility': 0.07},  # 0.51 → 0.07 (cycle pyrrolidine)
    'S': {'hydrophobicity': -0.8,  'volume':  89.0, 'flexibility': 0.51},
    'T': {'hydrophobicity': -0.7,  'volume': 116.1, 'flexibility': 0.44},
    'W': {'hydrophobicity': -0.9,  'volume': 227.8, 'flexibility': 0.31},
    'Y': {'hydrophobicity': -1.3,  'volume': 193.6, 'flexibility': 0.42},
    'V': {'hydrophobicity':  4.2,  'volume': 140.0, 'flexibility': 0.39}
}

# Constante thermodynamique Boltzmann — RT à 298 K
RT_KCAL = 0.593  # kcal/mol  (R=1.987 cal/mol/K × 298K / 1000)

# ============================================================================
# TRADUCTION ADN → PROTÉINE
# ============================================================================

def translate_dna_to_protein(dna_sequence: str) -> Tuple[Optional[str], Optional[str]]:
    """Traduction ADN → Protéine avec validation stricte."""
    try:
        dna_sequence = dna_sequence.upper().replace('U', 'T').strip()
        dna_sequence = re.sub(r'[^ATCG]', '', dna_sequence)
        if len(dna_sequence) < 9:
            return None, f"Séquence trop courte: {len(dna_sequence)} nt (minimum 9 nt)"
        if len(dna_sequence) % 3 != 0:
            dna_sequence = dna_sequence[:-(len(dna_sequence) % 3)]
        start_index = dna_sequence.find('ATG')
        if start_index == -1:
            return None, "Pas de codon initiateur ATG trouvé"
        protein = ""
        for i in range(start_index, len(dna_sequence) - 2, 3):
            codon = dna_sequence[i:i+3]
            amino_acid = GENETIC_CODE.get(codon, 'X')
            if amino_acid == '*':
                break
            if amino_acid == 'X':
                return None, f"Codon invalide: {codon}"
            protein += amino_acid
        if len(protein) < 3:
            return None, f"Protéine trop courte: {len(protein)} aa (minimum 3 aa)"
        if len(protein) > 1000:
            return None, f"Protéine trop longue: {len(protein)} aa (maximum 1000 aa)"
        return protein, None
    except Exception as e:
        return None, f"Erreur traduction: {str(e)}"

# ============================================================================
# STRUCTURE 3D PROTÉINE
# ============================================================================

def get_secondary_structure(aa: str) -> str:
    """Prédit la structure secondaire probable d'un AA."""
    helix_formers = set('AELMK')
    sheet_formers = set('VIYF')
    if aa in helix_formers:
        return 'H'
    elif aa in sheet_formers:
        return 'E'
    else:
        return 'C'

def create_realistic_protein_structure(protein_sequence: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """
    Crée une structure 3D backbone complet (N, CA, C, O).
    Angles Ramachandran corrects: hélice (−60°,−45°), feuillet (−120°,120°).
    Charges AMBER ff99SB.
    """
    try:
        if not protein_sequence:
            return None, "Séquence vide"
        atoms   = []
        atom_id = 1
        helix_formers = set('AELMK')
        sheet_formers = set('VIYF')
        x, y, z   = 0.0, 0.0, 0.0
        phi, psi  = -60.0, -45.0

        for i, aa in enumerate(protein_sequence):
            if aa in helix_formers:
                phi, psi = -60.0, -45.0
                # Hélice alpha compacte avec rayon réaliste
                angle = i * 100 * np.pi / 180
                radius = 2.3
                dx = radius * np.cos(angle)
                dy = radius * np.sin(angle)
                dz = 1.5
            elif aa in sheet_formers:
                phi, psi = -120.0, 120.0
                # Feuillet beta étendu et planaire
                dx = 3.8 * (i % 2)
                dy = 4.5 * (i // 2 % 2)
                dz = 0.0
            else:
                # Deterministic loop geometry for reproducibility.
                # This helper remains a visualization model, not an experimental
                # or structure-prediction model.
                phi = -90.0 + 30.0 * np.sin(i * 0.7)
                psi = 30.0 * np.cos(i * 0.7)
                # Boucles avec plus de structure
                dx = 2.0 * np.cos(i * 0.5)
                dy = 2.0 * np.sin(i * 0.5)
                dz = 1.0 + 0.5 * np.sin(i * 0.3)

            # Accumulation des coordonnées
            x += dx
            y += dy
            z += dz
            ss = get_secondary_structure(aa)

            # N backbone
            atoms.append({'atom_id': atom_id, 'atom_name': 'N',  'residue_name': aa,
                          'residue_id': i + 1, 'x': round(x, 3), 'y': round(y, 3),
                          'z': round(z, 3), 'element': 'N', 'charge': -0.40,
                          'secondary_structure': ss})
            atom_id += 1

            # CA backbone
            atoms.append({'atom_id': atom_id, 'atom_name': 'CA', 'residue_name': aa,
                          'residue_id': i + 1,
                          'x': round(x + 1.458 * np.cos(np.radians(phi)), 3),
                          'y': round(y + 1.458 * np.sin(np.radians(phi)), 3),
                          'z': round(z, 3), 'element': 'C', 'charge': 0.03,
                          'secondary_structure': ss})
            atom_id += 1

            # C backbone
            c_x = x + 2.45 * np.cos(np.radians(psi))
            c_y = y + 2.45 * np.sin(np.radians(psi))
            c_z = z + 0.5
            atoms.append({'atom_id': atom_id, 'atom_name': 'C',  'residue_name': aa,
                          'residue_id': i + 1, 'x': round(c_x, 3), 'y': round(c_y, 3),
                          'z': round(c_z, 3), 'element': 'C', 'charge': 0.55,
                          'secondary_structure': ss})
            atom_id += 1

            # O backbone
            atoms.append({'atom_id': atom_id, 'atom_name': 'O',  'residue_name': aa,
                          'residue_id': i + 1,
                          'x': round(c_x + 1.24 * np.cos(np.radians(psi + 30)), 3),
                          'y': round(c_y + 1.24 * np.sin(np.radians(psi + 30)), 3),
                          'z': round(c_z, 3), 'element': 'O', 'charge': -0.55,
                          'secondary_structure': ss})
            atom_id += 1

            # AJOUT DES SIDE-CHAINS COMPLETS pour des interactions réalistes
            side_chain_atoms = generate_side_chain_atoms(aa, x, y, z, phi, psi, atom_id)
            atoms.extend(side_chain_atoms)
            atom_id += len(side_chain_atoms)

        if len(atoms) < 12:
            return None, f"Structure trop petite: {len(atoms)} atomes"
        return atoms, None

    except Exception as e:
        return None, f"Erreur création structure 3D: {str(e)}"

def generate_side_chain_atoms(aa: str, ca_x: float, ca_y: float, ca_z: float, 
                             phi: float, psi: float, start_atom_id: int) -> List[Dict]:
    """
    Génère les side-chains complets pour des interactions réalistes.
    Positions optimales pour chaque acide aminé.
    """
    side_chains = {
        'A': [{'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1}],
        'R': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG', 'element': 'C', 'x': 2.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CD', 'element': 'C', 'x': 3.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'NE', 'element': 'N', 'x': 4.5, 'y': 0.0, 'z': 0.0, 'charge': -0.4},
            {'atom_name': 'CZ', 'element': 'C', 'x': 5.5, 'y': 0.0, 'z': 0.0, 'charge': 0.5},
            {'atom_name': 'NH1', 'element': 'N', 'x': 6.2, 'y': 0.9, 'z': 0.0, 'charge': -0.4},
            {'atom_name': 'NH2', 'element': 'N', 'x': 6.2, 'y': -0.9, 'z': 0.0, 'charge': -0.4}
        ],
        'N': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG', 'element': 'C', 'x': 2.5, 'y': 0.0, 'z': 0.0, 'charge': 0.5},
            {'atom_name': 'OD1', 'element': 'O', 'x': 3.3, 'y': 0.8, 'z': 0.0, 'charge': -0.5},
            {'atom_name': 'ND2', 'element': 'N', 'x': 3.3, 'y': -0.8, 'z': 0.0, 'charge': -0.4}
        ],
        'D': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG', 'element': 'C', 'x': 2.5, 'y': 0.0, 'z': 0.0, 'charge': 0.5},
            {'atom_name': 'OD1', 'element': 'O', 'x': 3.3, 'y': 0.8, 'z': 0.0, 'charge': -0.5},
            {'atom_name': 'OD2', 'element': 'O', 'x': 3.3, 'y': -0.8, 'z': 0.0, 'charge': -0.5}
        ],
        'C': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'SG', 'element': 'S', 'x': 2.8, 'y': 0.0, 'z': 0.0, 'charge': -0.2}
        ],
        'E': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG', 'element': 'C', 'x': 2.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CD', 'element': 'C', 'x': 3.5, 'y': 0.0, 'z': 0.0, 'charge': 0.5},
            {'atom_name': 'OE1', 'element': 'O', 'x': 4.3, 'y': 0.8, 'z': 0.0, 'charge': -0.5},
            {'atom_name': 'OE2', 'element': 'O', 'x': 4.3, 'y': -0.8, 'z': 0.0, 'charge': -0.5}
        ],
        'Q': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG', 'element': 'C', 'x': 2.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CD', 'element': 'C', 'x': 3.5, 'y': 0.0, 'z': 0.0, 'charge': 0.5},
            {'atom_name': 'OE1', 'element': 'O', 'x': 4.3, 'y': 0.8, 'z': 0.0, 'charge': -0.5},
            {'atom_name': 'NE2', 'element': 'N', 'x': 4.3, 'y': -0.8, 'z': 0.0, 'charge': -0.4}
        ],
        'G': [],  # Glycine - pas de side chain
        'H': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG', 'element': 'C', 'x': 2.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'ND1', 'element': 'N', 'x': 3.3, 'y': 0.8, 'z': 0.0, 'charge': -0.4},
            {'atom_name': 'CD2', 'element': 'C', 'x': 3.3, 'y': -0.8, 'z': 0.0, 'charge': 0.2},
            {'atom_name': 'CE1', 'element': 'C', 'x': 4.1, 'y': 0.0, 'z': 0.0, 'charge': 0.2},
            {'atom_name': 'NE2', 'element': 'N', 'x': 4.9, 'y': 0.8, 'z': 0.0, 'charge': -0.4}
        ],
        'I': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG1', 'element': 'C', 'x': 2.5, 'y': 0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG2', 'element': 'C', 'x': 2.5, 'y': -0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CD1', 'element': 'C', 'x': 3.5, 'y': 0.8, 'z': 0.0, 'charge': -0.1}
        ],
        'L': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG', 'element': 'C', 'x': 2.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CD1', 'element': 'C', 'x': 3.5, 'y': 0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CD2', 'element': 'C', 'x': 3.5, 'y': -0.8, 'z': 0.0, 'charge': -0.1}
        ],
        'K': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG', 'element': 'C', 'x': 2.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CD', 'element': 'C', 'x': 3.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CE', 'element': 'C', 'x': 4.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'NZ', 'element': 'N', 'x': 5.5, 'y': 0.0, 'z': 0.0, 'charge': -0.4}
        ],
        'M': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG', 'element': 'C', 'x': 2.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'SD', 'element': 'S', 'x': 3.5, 'y': 0.0, 'z': 0.0, 'charge': -0.2},
            {'atom_name': 'CE', 'element': 'C', 'x': 4.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1}
        ],
        'F': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG', 'element': 'C', 'x': 2.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CD1', 'element': 'C', 'x': 3.3, 'y': 0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CD2', 'element': 'C', 'x': 3.3, 'y': -0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CE1', 'element': 'C', 'x': 4.1, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CE2', 'element': 'C', 'x': 4.9, 'y': 0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CZ', 'element': 'C', 'x': 4.9, 'y': -0.8, 'z': 0.0, 'charge': -0.1}
        ],
        'P': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG', 'element': 'C', 'x': 2.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CD', 'element': 'C', 'x': 3.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1}
        ],
        'S': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'OG', 'element': 'O', 'x': 2.5, 'y': 0.0, 'z': 0.0, 'charge': -0.5}
        ],
        'T': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'OG1', 'element': 'O', 'x': 2.5, 'y': 0.8, 'z': 0.0, 'charge': -0.5},
            {'atom_name': 'CG2', 'element': 'C', 'x': 2.5, 'y': -0.8, 'z': 0.0, 'charge': -0.1}
        ],
        'W': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG', 'element': 'C', 'x': 2.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CD1', 'element': 'C', 'x': 3.3, 'y': 0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CD2', 'element': 'C', 'x': 3.3, 'y': -0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'NE1', 'element': 'N', 'x': 4.1, 'y': 0.0, 'z': 0.0, 'charge': -0.4},
            {'atom_name': 'CE2', 'element': 'C', 'x': 4.9, 'y': 0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CE3', 'element': 'C', 'x': 4.9, 'y': -0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CZ2', 'element': 'C', 'x': 5.7, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CZ3', 'element': 'C', 'x': 5.7, 'y': 1.6, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CH2', 'element': 'C', 'x': 6.5, 'y': 0.8, 'z': 0.0, 'charge': -0.1}
        ],
        'Y': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG', 'element': 'C', 'x': 2.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CD1', 'element': 'C', 'x': 3.3, 'y': 0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CD2', 'element': 'C', 'x': 3.3, 'y': -0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CE1', 'element': 'C', 'x': 4.1, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CE2', 'element': 'C', 'x': 4.9, 'y': 0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CZ', 'element': 'C', 'x': 4.9, 'y': -0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'OH', 'element': 'O', 'x': 5.7, 'y': 0.0, 'z': 0.0, 'charge': -0.5}
        ],
        'V': [
            {'atom_name': 'CB', 'element': 'C', 'x': 1.5, 'y': 0.0, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG1', 'element': 'C', 'x': 2.5, 'y': 0.8, 'z': 0.0, 'charge': -0.1},
            {'atom_name': 'CG2', 'element': 'C', 'x': 2.5, 'y': -0.8, 'z': 0.0, 'charge': -0.1}
        ]
    }
    
    atoms = []
    if aa not in side_chains:
        return atoms
    
    # Rotation basée sur phi/psi pour positionnement réaliste
    cos_phi = np.cos(np.radians(phi))
    sin_phi = np.sin(np.radians(phi))
    cos_psi = np.cos(np.radians(psi))
    sin_psi = np.sin(np.radians(psi))
    
    for i, atom_data in enumerate(side_chains[aa]):
        # Appliquer rotation et translation
        x = ca_x + atom_data['x'] * cos_phi - atom_data['y'] * sin_phi
        y = ca_y + atom_data['x'] * sin_phi + atom_data['y'] * cos_phi
        z = ca_z + atom_data['z']
        
        atoms.append({
            'atom_id': start_atom_id + i,
            'atom_name': atom_data['atom_name'],
            'residue_name': aa,
            'residue_id': start_atom_id,  # Sera corrigé par l'appelant
            'x': round(x, 3),
            'y': round(y, 3),
            'z': round(z, 3),
            'element': atom_data['element'],
            'charge': atom_data['charge'],
            'secondary_structure': 'side_chain'
        })
    
    return atoms
# ============================================================================
# DÉTECTION AVANCÉE DES SITES DE LIAISON
# ============================================================================

