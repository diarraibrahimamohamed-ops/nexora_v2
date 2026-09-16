"""core/docking/vina_runner.py — AutoDock Vina + ASP (post-traitement).

Moteur de docking : AutoDock Vina (Trott & Olson, 2010).
ASP = Agrégation Statistique des Poses — post-traitement Boltzmann des
     scores Vina. La quantité calculée est un score effectif S_eff; elle
     n'est PAS assimilée à une énergie libre ΔG expérimentale.
Structure 3D : PDB externe par défaut; prédiction AlphaFold2 via ColabFold en option.
"""
#!/usr/bin/env python3
"""
Docking moléculaire Nexora
==========================
1. AutoDock Vina — blind docking multi-poches (moteur réel)
2. ASP — Agrégation Statistique des Poses (post-traitement Boltzmann)

Conformité moteur :
  - AutoDock Vina Best Practices (Trott & Olson, J. Comput. Chem. 2010)
  - TORSDOF dynamique (RDKit / OpenBabel), charges Gasteiger
Voir algo.md pour références d'agrégation d'ensemble.
"""

import json
import subprocess
import sys
import os
import tempfile
import time
import re
import math
import numpy as np
from typing import Dict, List, Tuple, Optional

from app.core.docking.asp import aggregate_pose_scores
from app.core.protein.colabfold_runner import (
    build_colabfold_structure,
    pdb_to_atom_dicts,
    use_external_pdb_structure,
)
from app.core.protein.structure_resolver import resolve_experimental_pdb
from app.core.protein.esmfold_runner import build_esmfold_structure
from app.core.protein.io_utils import resolve_writable_dir
from app.config import settings

# Import fpocket et modules d'enrichissement
try:
    from app.core.docking.pocket_detector import detect_binding_pockets_enriched_fixed
    FPOCKET_AVAILABLE = True
    print(" fpocket + sites biologiques chargés", file=sys.stderr)
except ImportError as e:
    FPOCKET_AVAILABLE = False
    print(f"  fpocket non disponible: {e}", file=sys.stderr)

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

# ============================================================================
# TRADUCTION ADN → PROTÉINE
# ============================================================================


# Importer depuis le module séquences
from app.core.sequence.translation import (
    translate_dna_to_protein,
    AA_PROPERTIES, GENETIC_CODE
)
def smiles_to_3d_structure(smiles: str) -> Tuple[Optional[Dict], Optional[str]]:
    """
    SMILES → 3D via RDKit (MMFF + Gasteiger complet + TORSDOF réel)
    Fallback: Open Babel (MMFF94 + charges + NumRotors)
    """
    try:
        # ── Tentative 1: RDKit ──────────────────────────────────────────────
        try:
            from rdkit import Chem
            from rdkit.Chem import AllChem, rdMolDescriptors, rdPartialCharges

            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                return None, f"SMILES invalide: {smiles}"

            mol = Chem.AddHs(mol)
            params = AllChem.ETKDGv3()
            params.randomSeed = 42
            if AllChem.EmbedMolecule(mol, params) != 0:
                return None, (
                    "RDKit: échec de génération des coordonnées 3D "
                    "(EmbedMolecule). Le SMILES peut être invalide ou trop petit."
                )
            if AllChem.MMFFOptimizeMolecule(mol) != 0:
                print("[Ligand] MMFF: optimisation non convergée "
                      "(géométrie utilisée telle quelle)", file=sys.stderr)

            # Charges Gasteiger complètes (Suggestion 4)
            rdPartialCharges.ComputeGasteigerCharges(mol)

            conf  = mol.GetConformer()
            atoms = []
            for i, atom in enumerate(mol.GetAtoms()):
                pos = conf.GetAtomPosition(i)
                gasteiger_charge = float(atom.GetDoubleProp('_GasteigerCharge'))
                if np.isnan(gasteiger_charge):
                    gasteiger_charge = 0.0
                atoms.append({
                    'atom_id':     i + 1,
                    'atom_name':   atom.GetSymbol(),
                    'x':           round(pos.x, 3),
                    'y':           round(pos.y, 3),
                    'z':           round(pos.z, 3),
                    'element':     atom.GetSymbol(),
                    'charge':      round(gasteiger_charge, 4),
                    'residue_name': 'LIG',
                    'residue_id':  1
                })

            # Nombre réel de liaisons rotables (Suggestion 1)
            num_rotatable_bonds = rdMolDescriptors.CalcNumRotatableBonds(mol)

            return {
                'name':                Chem.MolToSmiles(mol),
                'atoms':               atoms,
                'method':              'rdkit_mmff',
                'num_rotatable_bonds': num_rotatable_bonds
            }, None

        except ImportError:
            pass

        # ── Tentative 2: Open Babel ──────────────────────────────────────────
        try:
            from openbabel import pybel

            mol = pybel.readstring("smi", smiles)
            mol.make3D(forcefield='mmff94', steps=500)

            # NumRotors Open Babel (Suggestion 1)
            num_rotatable_bonds = mol.OBMol.NumRotors()

            atoms = []
            for i, atom in enumerate(mol.atoms):
                atoms.append({
                    'atom_id':     i + 1,
                    'atom_name':   atom.type,
                    'x':           round(atom.coords[0], 3),
                    'y':           round(atom.coords[1], 3),
                    'z':           round(atom.coords[2], 3),
                    'element':     atom.type[0],
                    'charge':      atom.partialcharge,
                    'residue_name': 'LIG',
                    'residue_id':  1
                })

            return {
                'name':                smiles,
                'atoms':               atoms,
                'method':              'openbabel_mmff94',
                'num_rotatable_bonds': num_rotatable_bonds
            }, None

        except ImportError:
            pass

        return None, (
            "Conversion SMILES impossible: RDKit et Open Babel non disponibles.\n"
            "Installation: pip install rdkit-pypi  OU  pip install openbabel"
        )

    except Exception as e:
        return None, f"Erreur conversion SMILES: {str(e)}"


def estimate_atomic_charge(atom) -> float:
    """
    Gasteiger simplifié — utilisé uniquement si RDKit/OpenBabel absents.
    Préférer ComputeGasteigerCharges() de RDKit pour la précision.
    """
    symbol = atom.GetSymbol()
    base_charges = {
        'C': -0.10, 'N': -0.40, 'O': -0.50,
        'F': -0.25, 'S': -0.20, 'Cl': -0.10, 'H': 0.10
    }
    charge = base_charges.get(symbol, 0.0)
    if symbol == 'C':
        if atom.GetIsAromatic():        charge = -0.03
        elif atom.GetTotalDegree() == 3: charge =  0.05
    elif symbol == 'O':
        if   len(atom.GetBonds()) == 1:  charge = -0.50
        elif len(atom.GetBonds()) == 2:  charge = -0.40
    elif symbol == 'N':
        if   atom.GetIsAromatic():       charge = -0.50
        elif len(atom.GetBonds()) == 3:  charge = -0.60
    return round(charge, 2)

# ============================================================================
# CRÉATION FICHIERS PDBQT (FORMAT STRICT)
# ============================================================================

# Carte élément chimique → type AutoDock (AutoDock 4 / Vina, Trott & Olson 2010).
# Sans ces types (colonnes 78-79 du PDBQT), Vina rejette le fichier
# ("unknown atom type") ou dégrade les calculs d'affinité.
AD_TYPE_MAP = {
    'C': 'C', 'A': 'A', 'N': 'N', 'NA': 'NA', 'O': 'OA', 'OA': 'OA',
    'S': 'SA', 'SA': 'SA', 'H': 'H', 'HD': 'HD', 'F': 'F', 'CL': 'Cl',
    'BR': 'Br', 'I': 'I', 'P': 'P', 'MG': 'MG', 'ZN': 'ZN', 'CA': 'CA',
    'FE': 'FE', 'MN': 'MN', 'CU': 'CU', 'NI': 'NI',
}

def _ad_type(element: str, atom_name: str = '', aromatic: bool = False) -> str:
    """Élément → type AutoDock. C aromatique → A ; H potentiellement polaire
    (précédé de N/O/S dans le nom) → HD ; sinon H."""
    e = (element or 'C').upper().strip()
    if e == 'C' and aromatic:
        return 'A'
    if e == 'H':
        name = atom_name.upper()
        if len(name) > 1 and name[1] in 'NOS':
            return 'HD'
        return 'H'
    return AD_TYPE_MAP.get(e, e[:2] or 'C')

def create_pdbqt_from_atoms(atoms: List[Dict], molecule_type: str,
                              torsdof: int = 0) -> Tuple[Optional[str], Optional[str]]:
    """
    Format PDBQT AutoDock strict — colonnes fixes wwPDB.
    TORSDOF = nombre réel de liaisons rotables (passé en paramètre).
    """
    try:
        if not atoms:
            return None, "Liste d'atomes vide"

        lines = []
        if molecule_type == "receptor":
            lines.append("REMARK  Receptor — AutoDock Vina + ASP (Boltzmann post-traitement)")
        else:
            lines.append("REMARK  Ligand   — AutoDock Vina + ASP (Boltzmann post-traitement)")
            lines.append("ROOT")

        for atom in atoms:
            record_type  = "ATOM  " if molecule_type == "receptor" else "HETATM"
            atom_id      = atom['atom_id']
            atom_name    = atom['atom_name'].ljust(4)[:4]
            residue_name = atom.get('residue_name', 'UNK').rjust(3)[:3]
            chain        = 'A'
            residue_id   = atom.get('residue_id', 1)
            x            = atom['x']
            y            = atom['y']
            z            = atom['z']
            occupancy    = 1.00
            temp_factor  = atom.get('charge', 0.00)
            element      = _ad_type(
                atom['element'], atom.get('atom_name', ''), atom.get('aromatic', False)
            ).rjust(2)[:2]

            line = (
                f"{record_type}"
                f"{atom_id:>5} "
                f"{atom_name}"
                f" {residue_name} "
                f"{chain}"
                f"{residue_id:>4}    "
                f"{x:>8.3f}"
                f"{y:>8.3f}"
                f"{z:>8.3f}"
                f"{occupancy:>6.2f}"
                f"{temp_factor:>6.2f}"
                f"          "
                f"{element}"
            )
            lines.append(line)

        if molecule_type != "receptor":
            lines.append("ENDROOT")
            # Un ROOT plat (sans BRANCH/ENDBRANCH) ne peut PAS exprimer de
            # liaisons rotables : déclarer TORSDOF > 0 rendrait le fichier
            # incohérent pour Vina. On force 0 (ligand rigide) dans ce repli,
            # le vrai arbre de torsion étant généré par Open Babel quand il
            # est disponible (voir _pdbqt_ligand_via_obabel).
            if torsdof != 0:
                print("[PDBQT] Générateur interne : ligand traité en RIGIDE "
                      f"(TORSDOF {torsdof} ignoré, pas d'arbre de torsion). "
                      "Installez Open Babel pour un ligand flexible.",
                      file=sys.stderr)
            lines.append("TORSDOF 0")

        return '\n'.join(lines) + '\n', None

    except Exception as e:
        return None, f"Erreur création PDBQT: {str(e)}"

# ============================================================================
# Générateurs PDBQT Open Babel (types AutoDock + protonation + torsions)
# ============================================================================

def _write_pdb_from_atoms_list(atoms: List[Dict], pdb_path: str) -> None:
    """Écrit un PDB minimal (ATOM) à partir des dicts d'atomes Nexora."""
    with open(pdb_path, 'w', encoding='utf-8') as fh:
        for i, atom in enumerate(atoms, start=1):
            name             = atom.get('atom_name', atom['element'])[:4]
            resn             = atom.get('residue_name', 'UNK')[:3]
            resi             = atom.get('residue_id', 1) % 9999
            x, y, z          = float(atom['x']), float(atom['y']), float(atom['z'])
            el               = atom['element'][:2]
            fh.write(
                f"ATOM  {i:5d} {name:<4s} {resn:>3s} A{resi:4d}    "
                f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {el:>2s}\n"
            )
        fh.write("END\n")


def _pdbqt_ligand_via_obabel(smiles: str, out_dir: str,
                             timeout_seconds: int = 120) -> Tuple[Optional[str], Optional[str]]:
    """PDBQT ligand natif Open Babel : types AutoDock corrects, protonation à
    pH 7.4 et arbre de torsion réel (BRANCH/ENDBRANCH + TORSDOF honnête)."""
    import shutil
    binary = shutil.which('obabel')
    if binary is None:
        return None, "obabel non installé"
    out = os.path.join(out_dir, 'ligand.pdbqt')
    cmd = [binary, '-:' + smiles, '-O', out, '--gen3d',
           '--partialcharge', 'gasteiger', '-p', '7.4']
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout_seconds)
    except Exception as exc:
        return None, f"obabel ligand: {type(exc).__name__}: {exc}"
    if res.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        return None, f"obabel ligand: retour {res.returncode} — {res.stderr[-300:]}"
    with open(out, encoding='utf-8', errors='replace') as fh:
        content = fh.read()
    if 'ROOT' not in content:
        return None, "obabel ligand: PDBQT sans section ROOT"
    return content, None


def _pdbqt_receptor_via_obabel(protein_atoms: List[Dict], out_dir: str,
                               timeout_seconds: int = 300) -> Tuple[Optional[str], Optional[str]]:
    """PDBQT récepteur natif Open Babel : types AutoDock, protonation pH 7.4,
    récepteur rigide (-xr)."""
    import shutil
    binary = shutil.which('obabel')
    if binary is None:
        return None, "obabel non installé"
    try:
        pdb_in = os.path.join(out_dir, 'receptor_raw.pdb')
        _write_pdb_from_atoms_list(protein_atoms, pdb_in)
        out = os.path.join(out_dir, 'receptor.pdbqt')
        cmd = [binary, pdb_in, '-O', out, '-xr', '-p', '7.4']
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout_seconds)
    except Exception as exc:
        return None, f"obabel récepteur: {type(exc).__name__}: {exc}"
    if res.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        return None, f"obabel récepteur: retour {res.returncode} — {res.stderr[-300:]}"
    with open(out, encoding='utf-8', errors='replace') as fh:
        content = fh.read()
    return content, None


# ============================================================================
# Agregation statistiqueque des Poses — PIPELINE PRINCIPAL
# ============================================================================

def run_scientific_docking(protein_sequence: str, smiles: str,
                            max_pockets: int = 3,
                            progress_callback=None,
                            colabfold_binary: str = "colabfold_batch",
                            colabfold_output_dir: Optional[str] = None,
                            colabfold_num_models: int = 5,
                            colabfold_num_recycle: int = 3,
                            structure_provider: str = "auto_rcsb",
                            receptor_pdb_path: Optional[str] = None,
                            structure_identity_cutoff: float = 0.9,
                            structure_evalue_cutoff: float = 1.0) -> Dict:
    """
    Pipeline Vina + ASP (Agrégation Statistique des Poses, post-traitement).

    Étapes:
      1. Validation séquence protéique
    2. Prédiction de structure AlphaFold2 via ColabFold
      3. Détection N poches candidates (superposition)
      4. Conversion SMILES → 3D + Gasteiger + TORSDOF
      5. Génération PDBQT
      6. Docking Vina adaptatif sur chaque poche (boucle)
      7. Agrégation ASP par pondération de Boltzmann des scores Vina après clustering

    max_pockets : nombre de sites candidats explorés simultanément
    structure_provider : ``auto_rcsb`` (léger), ``external_pdb`` ou ``colabfold``
    progress_callback : fonction(stage, progress, message) pour les mises à jour
    """
    start_time = time.time()
    metadata   = {}

    # ── 1. Validation séquence ───────────────────────────────────────────────
    print("Étape 1/7: Validation séquence protéique...", file=sys.stderr)
    if progress_callback:
        progress_callback("validation", 10, "Validation séquence protéique...")
    
    protein_sequence = protein_sequence.upper().strip()
    protein_sequence = re.sub(r'[^ACDEFGHIKLMNPQRSTVWY]', '', protein_sequence)

    if not protein_sequence:
        return {'success': False,
                'error_message': "Séquence protéique vide ou invalide",
                'stage': 'protein_validation',
                'recommendation': "Fournir une séquence d'acides aminés valide (ex: MKPGF)"}
    if len(protein_sequence) < 3:
        return {'success': False,
                'error_message': f"Protéine trop courte: {len(protein_sequence)} aa (minimum 3 aa)",
                'stage': 'protein_validation'}
    if len(protein_sequence) > 1000:
        return {'success': False,
                'error_message': f"Protéine trop longue: {len(protein_sequence)} aa (maximum 1000 aa)",
                'stage': 'protein_validation'}

    metadata['protein_sequence'] = protein_sequence
    metadata['protein_length']   = len(protein_sequence)
    print(f"   ✓ Protéine: {len(protein_sequence)} aa", file=sys.stderr)

    # ── 2. Structure externe ou prédiction ColabFold ─────────────────────────
    print(f"Étape 2/7: Structure protéique ({structure_provider})...", file=sys.stderr)
    if progress_callback:
        progress_callback("modeling", 20, f"Structure protéique ({structure_provider})...")

    output_dir = resolve_writable_dir(colabfold_output_dir)
    
    # Retry logic pour RCSB avec paramètres moins stricts
    if structure_provider == "auto_rcsb":
        # Tracker les erreurs de chaque provider pour un message final diagnostique
        fallback_errors: List[str] = []

        # ── Tentative 1 : RCSB expérimental (paramètres standards) ───────────
        model_path, error, model_metadata = resolve_experimental_pdb(
            protein_sequence=protein_sequence,
            output_dir=output_dir,
            identity_cutoff=structure_identity_cutoff,
            evalue_cutoff=structure_evalue_cutoff,
        )

        if error:
            fallback_errors.append(f"RCSB (standard): {error}")
            print(f"RCSB échoué ({error}), retry paramètres permissifs…", file=sys.stderr)
            if progress_callback:
                progress_callback("modeling", 25, "RCSB retry paramètres permissifs…")

            # ── Tentative 2 : RCSB permissif (identity 50 %, e-value 50) ─────
            model_path, error, model_metadata = resolve_experimental_pdb(
                protein_sequence=protein_sequence,
                output_dir=output_dir,
                identity_cutoff=0.5,
                evalue_cutoff=10.0,
            )
            if not error:
                model_metadata = model_metadata or {}
                model_metadata["retry_used"] = "RCSB with permissive parameters"
            else:
                fallback_errors.append(f"RCSB (permissif): {error}")

                # ── Tentative 3 : ESMFold (NVIDIA NIM ou ESM Atlas gratuit) ──
                print(f"RCSB échoué après retry, tentative ESMFold…", file=sys.stderr)
                if progress_callback:
                    progress_callback("modeling", 30, "RCSB échoué, tentative fallback ESMFold...")
                # Essayer ESMFold d'abord (API légère, pas d'installation locale requise)
                model_path, error, model_metadata = build_esmfold_structure(
                    protein_sequence=protein_sequence,
                    output_dir=output_dir,
                    nvidia_api_key=settings.NVIDIA_API_KEY,
                )
                if not error:
                    model_metadata = model_metadata or {}
                    model_metadata["fallback_used"] = "RCSB → ESMFold"
                    structure_provider = "esmfold"
                else:
                    fallback_errors.append(f"ESMFold: {error}")

                    # ── Tentative 4 : ColabFold local ────────────────────────
                    print(f"ESMFold échoué ({error}), tentative ColabFold local…", file=sys.stderr)
                    try:
                        import shutil as _shutil
                        colabfold_available = _shutil.which(colabfold_binary) is not None
                    except Exception:
                        colabfold_available = False

                    if colabfold_available:
                        print(f"ColabFold trouvé, fallback ColabFold…", file=sys.stderr)
                        model_path, error, model_metadata = build_colabfold_structure(
                            protein_sequence=protein_sequence,
                            binary=colabfold_binary,
                            output_dir=output_dir,
                            num_models=colabfold_num_models,
                            num_recycle=colabfold_num_recycle,
                        )
                        if not error:
                            model_metadata = model_metadata or {}
                            model_metadata["fallback_used"] = "RCSB → ESMFold → ColabFold"
                            structure_provider = "colabfold"
                        else:
                            fallback_errors.append(f"ColabFold: {error}")
                    else:
                        fallback_errors.append("ColabFold: non installé (colabfold_batch introuvable)")

                    if error:
                        # Tous les providers ont échoué — message diagnostique
                        diag = " | ".join(fallback_errors)
                        print(f"[STRUCTURE] Tous les providers ont échoué: {diag}", file=sys.stderr)
                        error = (
                            f"Impossible d'obtenir la structure protéique après 4 tentatives.\n"
                            f"Détail : {diag}\n"
                            f"Solutions : (1) Définissez NVIDIA_API_KEY pour ESMFold NVIDIA, "
                            f"(2) Installez ColabFold (pip install colabfold), "
                            f"(3) Fournissez un PDB externe via structure_provider='external_pdb'."
                        )
    elif structure_provider == "external_pdb":
        model_path, error, model_metadata = use_external_pdb_structure(
            receptor_pdb_path or "",
            output_dir,
        )
    elif structure_provider == "colabfold":
        model_path, error, model_metadata = build_colabfold_structure(
            protein_sequence=protein_sequence,
            binary=colabfold_binary,
            output_dir=output_dir,
            num_models=colabfold_num_models,
            num_recycle=colabfold_num_recycle,
        )
    else:
        return {'success': False,
                'error_message': f"Structure provider inconnu: {structure_provider}",
                'stage': 'protein_structure', 'metadata': metadata}
    if error:
        return {'success': False, 'error_message': error,
                'stage': 'protein_structure', 'metadata': metadata}
    protein_atoms, error = pdb_to_atom_dicts(model_path)
    if error:
        return {'success': False, 'error_message': error,
                'stage': 'protein_structure', 'metadata': metadata}
    metadata.update(model_metadata)
    metadata['protein_atoms'] = len(protein_atoms)
    print(f"   ✓ Structure: {len(protein_atoms)} atomes ({structure_provider})", file=sys.stderr)

    # ── 3. Détection N poches candidates (SUPERPOSITION) ─────────────────────
    print("Étape 3/7: Détection poches candidates (superposition)...", file=sys.stderr)
    if progress_callback:
        progress_callback("pocket_detection", 30, "Détection poches candidates (fpocket)...")
    
    binding_pockets, error = detect_binding_pockets_enriched_fixed(protein_atoms)
    if error:
        return {'success': False, 'error_message': f"Détection poches échouée: {error}",
                'stage': 'pocket_detection', 'metadata': metadata,
                'recommendation': "Protéine trop petite ou structure trop uniforme"}

    # Garder les N meilleures poches pour la superposition
    candidate_pockets = binding_pockets[:max_pockets]
    if not candidate_pockets:
        return {'success': False, 'error_message': 'Aucune poche candidate exploitable',
                'stage': 'pocket_detection', 'metadata': metadata}
    metadata['binding_pockets_found'] = len(binding_pockets)
    metadata['candidate_pockets']     = len(candidate_pockets)
    print(f"   ✓ Poches: {len(binding_pockets)} détectées, {len(candidate_pockets)} candidates",
          file=sys.stderr)

    # ── 4. Conversion SMILES → 3D (Gasteiger + TORSDOF) ─────────────────────
    print("Étape 4/7: Conversion SMILES → 3D...", file=sys.stderr)
    if progress_callback:
        progress_callback("ligand_conversion", 40, "Conversion SMILES → 3D...")
    
    ligand_data, error = smiles_to_3d_structure(smiles)
    if error:
        return {'success': False, 'error_message': f"Conversion ligand échouée: {error}",
                'stage': 'ligand_conversion', 'metadata': metadata,
                'recommendation': "Installer RDKit (pip install rdkit-pypi) ou Open Babel"}

    metadata['ligand_name']   = ligand_data['name']
    metadata['ligand_atoms']  = len(ligand_data['atoms'])
    metadata['ligand_method'] = ligand_data['method']
    torsdof = ligand_data.get('num_rotatable_bonds', 0)
    metadata['torsdof'] = torsdof
    print(f"   ✓ Ligand: {len(ligand_data['atoms'])} atomes, TORSDOF={torsdof} ({ligand_data['method']})",
          file=sys.stderr)

    # ── Exhaustivité adaptative (taille protéine + flexibilité ligand) ───────
    #    base: 32 (< 100 aa) | 48 (100-300 aa) | 64 (> 300 aa)
    #    ajustement: +2 par liaison rotable, capé à 128
    base_exhaustiveness = 32
    if len(protein_sequence) > 300:
        base_exhaustiveness = 64
    elif len(protein_sequence) > 100:
        base_exhaustiveness = 48
    exhaustiveness = min(128, base_exhaustiveness + torsdof * 2)
    metadata['exhaustiveness'] = exhaustiveness
    print(f"   ✓ Exhaustivité adaptative: {exhaustiveness}", file=sys.stderr)

    # ── Box minimale adaptée au ligand ───────────────────────────────────────
    #    Rayon estimé: (N_HA)^(1/3) × 2.0 Å ; marge: 12.0 Å (6 Å × 2 côtés)
    heavy_atoms_ligand = len([a for a in ligand_data['atoms'] if a['element'] not in ['H']])
    ligand_radius      = (heavy_atoms_ligand ** (1 / 3)) * 2.0
    adaptive_margin    = 12.0
    min_box_for_ligand = max(18.0, ligand_radius * 2 + adaptive_margin)
    metadata['min_box_for_ligand'] = round(min_box_for_ligand, 2)

    # ── 5. Génération PDBQT (une fois, partagé entre les poches) ─────────────
    print("Étape 5/7: Génération fichiers PDBQT...", file=sys.stderr)
    if progress_callback:
        progress_callback("pdbqt_generation", 50, "Génération fichiers PDBQT...")
    
    # Préférence : PDBQT natifs Open Babel (types AutoDock propres, protonation
    # pH 7.4, arbre de torsion réel) — bonnes pratiques AutoDock Vina.
    pdbqt_dir = tempfile.mkdtemp(prefix='nexora_pdbqt_')
    ligand_pdbqt, lig_err   = _pdbqt_ligand_via_obabel(smiles, pdbqt_dir)
    receptor_pdbqt, rec_err = _pdbqt_receptor_via_obabel(protein_atoms, pdbqt_dir)

    if lig_err or rec_err:
        print(f"[PDBQT] Open Babel indisponible ou en échec "
              f"(ligand: {lig_err} | récepteur: {rec_err}) — "
              f"repli sur le générateur interne (rigide, types AutoDock estimés).",
              file=sys.stderr)
        metadata['pdbqt_provider'] = 'internal_fallback'
        receptor_pdbqt, error = create_pdbqt_from_atoms(protein_atoms, "receptor")
        if error:
            return {'success': False, 'error_message': f"PDBQT récepteur échoué: {error}",
                    'stage': 'pdbqt_receptor', 'metadata': metadata}
        ligand_pdbqt, error = create_pdbqt_from_atoms(ligand_data['atoms'], "ligand",
                                                       torsdof=torsdof)
        if error:
            return {'success': False, 'error_message': f"PDBQT ligand échoué: {error}",
                    'stage': 'pdbqt_ligand', 'metadata': metadata}
    else:
        metadata['pdbqt_provider'] = 'openbabel'
        print("   ✓ PDBQT natifs Open Babel (types AutoDock, pH 7.4, "
              "ligand flexible)", file=sys.stderr)
    print("   ✓ Fichiers PDBQT créés", file=sys.stderr)

    # ── 6. Docking Vina sur chaque poche (SUPERPOSITION) ────────────────────
    print(f"Étape 6/7: Docking Vina sur {len(candidate_pockets)} poches...", file=sys.stderr)
    if progress_callback:
        progress_callback("docking", 60, f"Docking Vina sur {len(candidate_pockets)} poches...")

    best_score = None
    best_pose  = None
    all_poses  = []
    best_pocket = candidate_pockets[0]

    env = os.environ.copy()
    env['LD_LIBRARY_PATH'] = '/usr/lib/x86_64-linux-gnu:/lib/x86_64-linux-gnu'
    if 'LD_PRELOAD' in env:
        del env['LD_PRELOAD']
    env['PATH'] = '/usr/bin:/bin:/usr/local/bin'

    with tempfile.TemporaryDirectory() as temp_dir:
        receptor_file = os.path.join(temp_dir, 'receptor.pdbqt')
        ligand_file   = os.path.join(temp_dir, 'ligand.pdbqt')
        with open(receptor_file, 'w') as f:
            f.write(receptor_pdbqt)
        with open(ligand_file, 'w') as f:
            f.write(ligand_pdbqt)

        for pocket_idx, pocket in enumerate(candidate_pockets):
            # Mise à jour de progression pour chaque poche
            pocket_progress = 60 + (pocket_idx / len(candidate_pockets)) * 30
            if progress_callback:
                progress_callback("docking_pocket", int(pocket_progress), 
                                f"Docking poche {pocket_idx + 1}/{len(candidate_pockets)}...")
            
            # Adapter la box au ligand. Avec fpocket, dériver la taille
            # depuis les coordonnées de la poche plutôt que d'imposer 20 Å.
            pocket_coords = pocket.get('coordinates')
            if pocket_coords:
                coords = np.asarray(pocket_coords, dtype=float)
                extents = coords.max(axis=0) - coords.min(axis=0)
                pocket['center_x'] = float(coords[:, 0].mean())
                pocket['center_y'] = float(coords[:, 1].mean())
                pocket['center_z'] = float(coords[:, 2].mean())
                margin = 4.0
                derived_sizes = extents + 2.0 * margin
                actual_size_x = max(float(derived_sizes[0]), min_box_for_ligand)
                actual_size_y = max(float(derived_sizes[1]), min_box_for_ligand)
                actual_size_z = max(float(derived_sizes[2]), min_box_for_ligand)
            else:
                actual_size_x = max(float(pocket['size_x']), min_box_for_ligand)
                actual_size_y = max(float(pocket['size_y']), min_box_for_ligand)
                actual_size_z = max(float(pocket['size_z']), min_box_for_ligand)

            config_file = os.path.join(temp_dir, f'config_p{pocket_idx}.txt')
            output_file = os.path.join(temp_dir, f'out_p{pocket_idx}.pdbqt')

            config_content = f"""receptor = {receptor_file}
ligand = {ligand_file}
out = {output_file}

center_x = {pocket['center_x']}
center_y = {pocket['center_y']}
center_z = {pocket['center_z']}

size_x = {actual_size_x}
size_y = {actual_size_y}
size_z = {actual_size_z}

exhaustiveness = {exhaustiveness}
num_modes = 20
energy_range = 3
"""
            with open(config_file, 'w') as f:
                f.write(config_content)

            print(f"    Poche {pocket_idx + 1}/{len(candidate_pockets)}: "
                  f"({pocket['center_x']:.1f}, {pocket['center_y']:.1f}, "
                  f"{pocket['center_z']:.1f}) | box={actual_size_x:.1f} Å "
                  f"| source={pocket.get('detection_source', pocket.get('method', 'unknown'))}", file=sys.stderr)

            try:
                result = subprocess.run(
                    ['/usr/local/bin/vina', '--config', config_file],
                    capture_output=True, text=True, timeout=300, env=env
                )
            except subprocess.TimeoutExpired:
                print(f"    Timeout poche {pocket_idx + 1} — ignorée", file=sys.stderr)
                continue

            if result.returncode != 0 or not os.path.exists(output_file):
                print(f"    Vina échoué poche {pocket_idx + 1} — ignorée", file=sys.stderr)
                continue

            poses = parse_vina_output_strict(output_file)
            if not poses:
                print(f"    Aucune pose poche {pocket_idx + 1}", file=sys.stderr)
                continue

            # Annoter chaque pose avec l'identifiant de la poche
            for pose in poses:
                pose['pocket_id'] = pocket_idx
                pose['pocket_confidence'] = pocket['confidence']

            all_poses.extend(poses)

            # Mise à jour du meilleur score global
            local_best = min(poses, key=lambda p: p['score'])
            if best_score is None or local_best['score'] < best_score:
                best_score  = local_best['score']
                best_pose   = local_best
                best_pocket = pocket
                print(f"   ✓ Nouveau meilleur: {best_score:.2f} kcal/mol "
                      f"(poche {pocket_idx + 1})", file=sys.stderr)

    if best_pose is None or best_score is None:
        return {'success': False,
                'error_message': "Aucune pose valide sur l'ensemble des poches candidates",
                'stage': 'vina_no_poses', 'metadata': metadata,
                'recommendation': "Agrandir max_pockets ou vérifier la séquence protéique"}

    # ── 7. Agrégation ASP des représentants de clusters ─────────────────────
    print("Étape 7/7: ASP — agrégation Boltzmann des poses Vina...", file=sys.stderr)
    if progress_callback:
        progress_callback("boltzmann", 95, "ASP — agrégation Boltzmann des poses Vina...")

    print(f"   ASP: {len(all_poses)} poses à agréger", file=sys.stderr)
    if not all_poses:
        print("   WARNING: Aucune pose pour ASP - utilisation du meilleur score brut", file=sys.stderr)
        effective_score = best_score
        aggregation_metadata = {
            "error": "No poses for ASP aggregation",
            "fallback": "Using raw best score",
            "num_raw_poses": 0,
            "num_pose_clusters": 0
        }
    else:
        try:
            effective_score, aggregation_metadata = aggregate_pose_scores(all_poses)
            print(f"   ASP terminé: score effectif={effective_score:.3f}", file=sys.stderr)
        except Exception as asp_error:
            print(f"   ERREUR ASP: {asp_error} - fallback vers meilleur score brut", file=sys.stderr)
            effective_score = best_score
            aggregation_metadata = {
                "error": str(asp_error),
                "fallback": "Using raw best score due to ASP error",
                "num_raw_poses": len(all_poses)
            }

    # Aucun score normalisé artificiel n'est utilisé dans le contrat scientifique.
    # L'ancien normalized_score reste volontairement absent du résultat canonique.

    total_time = time.time() - start_time
    metadata['num_pockets_tested'] = len(candidate_pockets)
    metadata['num_poses_total']    = len(all_poses)
    metadata.update(aggregation_metadata)

    # Le site canonique doit suivre le même critère que le score ASP global.
    # Le TOP1 Vina peut différer du meilleur agrégat ASP; dans ce cas,
    # conserver `best_pocket` basé uniquement sur Vina crée un contrat incohérent.
    best_asp_pocket_id = aggregation_metadata.get('best_pocket_id')
    if best_asp_pocket_id is not None:
        try:
            best_asp_index = int(best_asp_pocket_id)
            if 0 <= best_asp_index < len(candidate_pockets):
                best_pocket = candidate_pockets[best_asp_index]
        except (TypeError, ValueError):
            pass

    metadata['canonical_best_pocket_id'] = best_asp_pocket_id

    print(f"   ✓ Vina OK + ASP: {len(all_poses)} poses / {len(candidate_pockets)} poches",
          file=sys.stderr)
    print(f"   ✓ Meilleur score brut:  {best_score:.2f} kcal/mol", file=sys.stderr)
    print(f"   ✓ Score effectif ASP (post-Vina): {effective_score:.2f} kcal/mol", file=sys.stderr)
    print(f"   ✓ Temps total:          {total_time:.1f}s", file=sys.stderr)

    return {
        'success':          True,
        # Contrat scientifique canonique.
        'vina_best_score': round(best_score, 3),
        'boltzmann_effective_score': round(effective_score, 3),
        'boltzmann_mean_score': round(float(aggregation_metadata.get('boltzmann_mean_score', effective_score)), 3),
        'docking_engine': 'AutoDock Vina',
        'aggregation_method': 'ASP — pondération de Boltzmann après clustering RMSD',
        'aggregation_metadata': aggregation_metadata,
        # Alias legacy conservés temporairement pour les clients v1/v2 existants.
        'docking_score': best_score,
        'effective_score': round(effective_score, 3),
        'poses':            all_poses,
        'num_poses':        len(all_poses),
        'execution_time':   round(total_time, 2),
        'modeling_method':   None,
        'metadata':         metadata,
        'best_pocket':      best_pocket,
        'validation': {
            'score_finite':          math.isfinite(best_score),
            'typical_range_note':    'Les valeurs typiques ne constituent pas un critère de rejet.',
            'score_interpretation':  interpret_docking_score(best_score),
            'effective_score_interpretation': (
                'Score effectif d’agrégation ASP; ce résultat n’est pas une ΔG de liaison expérimentale.'
            ),
            'no_fallback':           not bool(metadata.get('fallback_used')) and not bool(aggregation_metadata.get('fallback')) and metadata.get('pdbqt_provider') == 'openbabel',
            'structure_fallback_used': bool(metadata.get('fallback_used')),
            'pdbqt_fallback_used': metadata.get('pdbqt_provider') == 'internal_fallback',
            'asp_fallback_used': bool(aggregation_metadata.get('fallback')),
            'authentic_vina_result': True,
            'method':                'AutoDock Vina + ASP clustered score aggregation'
        }
    }

# ============================================================================
# PARSING VINA — STRICT, SANS FALLBACK
# ============================================================================

def parse_vina_output_strict(output_file: str) -> List[Dict]:
    """
    Parse le fichier output Vina sans filtrage énergétique arbitraire.
    """
    poses = []
    try:
        with open(output_file, 'r') as f:
            content = f.read()
        if not content.strip():
            return []

        lines             = content.split('\n')
        current_pose_atoms = []
        current_score     = None
        pose_id           = 0

        for line in lines:
            if 'REMARK VINA RESULT' in line:
                if current_pose_atoms and current_score is not None:
                    poses.append({
                        'pose_id':   pose_id,
                        'score':     current_score,
                        'atoms':     current_pose_atoms,
                        'num_atoms': len(current_pose_atoms)
                    })
                    pose_id += 1
                current_pose_atoms = []
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        current_score = float(parts[3])
                    except ValueError:
                        current_score = None

            elif line.startswith(('ATOM', 'HETATM')) and len(line) >= 54:
                try:
                    atom_type = line[76:79].strip() if len(line) >= 79 else ''
                    atom_name = line[12:16].strip() if len(line) >= 16 else ''
                    if atom_type:
                        if atom_type.upper().startswith('A'):
                            element = 'C'
                        elif atom_type.upper().startswith(('CL', 'BR')):
                            element = atom_type[:2].upper()
                        else:
                            element = atom_type[:1].upper()
                    else:
                        element = (line[76:78].strip() if len(line) > 76 else '') or (atom_name[:1] or 'C')
                    atom_data = {
                        'x':          float(line[30:38].strip()),
                        'y':          float(line[38:46].strip()),
                        'z':          float(line[46:54].strip()),
                        'atom':       atom_type or atom_name[:1],
                        'atom_name':  atom_name,
                        'element':    element,
                        'residue':    line[17:20].strip() if len(line) > 20 else 'LIG'
                    }
                    current_pose_atoms.append(atom_data)
                except (ValueError, IndexError):
                    continue

        if current_pose_atoms and current_score is not None:
            poses.append({
                'pose_id':   pose_id,
                'score':     current_score,
                'atoms':     current_pose_atoms,
                'num_atoms': len(current_pose_atoms)
            })

        # Ne pas imposer une plage énergétique arbitraire: Vina produit un
        # score empirique et une valeur extrême doit être inspectée, pas
        # silencieusement supprimée. On rejette uniquement les valeurs non finies.
        poses = [p for p in poses if p['num_atoms'] > 0 and math.isfinite(p['score'])]
        poses.sort(key=lambda x: x['score'])
        return poses

    except Exception as e:
        print(f"Erreur parsing Vina: {e}", file=sys.stderr)
        return []

# ============================================================================
# INTERPRÉTATION SCIENTIFIQUE DES SCORES
# ============================================================================

def interpret_docking_score(score: float) -> str:
    """Décrit un score Vina sans le convertir en Kd/ΔG expérimental."""
    if score > 0:
        return "Score Vina positif; l'interaction classée par la fonction de scoring est défavorable."
    if score > -4.0:
        return "Score Vina peu favorable; comparaison relative à d'autres poses/ligands uniquement."
    if score > -7.0:
        return "Score Vina intermédiaire; ne constitue pas une mesure expérimentale d'affinité."
    if score > -10.0:
        return "Score Vina favorable; à interpréter pour le classement relatif et à valider expérimentalement."
    if score > -12.0:
        return "Score Vina très favorable; vérifier le contexte structural et la reproductibilité."
    return "Score Vina extrême; vérifier la structure, le protocole de docking et la reproductibilité."

# ============================================================================
# MAIN
# ============================================================================
