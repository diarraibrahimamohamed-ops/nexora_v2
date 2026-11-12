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
import hashlib
import platform
import subprocess
import sys
import os
import tempfile
import time
import shutil
import math
import numpy as np
from typing import Dict, List, Tuple, Optional

from app.core.docking.asp import aggregate_pose_scores
from app.core.protein.colabfold_runner import (
    build_colabfold_structure,
    pdb_to_atom_dicts,
    use_external_pdb_structure,
)
from app.core.protein.structure_resolver import (
    resolve_experimental_pdb,
    resolve_computed_alphafold_model,
)
from app.core.protein.esmfold_runner import build_esmfold_structure
from app.core.protein.io_utils import resolve_writable_dir
from app.core.protein.sequence_validation import (
    ProteinSequenceValidationError,
    normalize_protein_sequence,
)
from app.core.protein.structure_validation import validate_structure
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

            # Keep the exact RDKit molecule atom order/topology so the same
            # indexed graph can be used to validate Open Babel PDBQT order.
            rdkit_mol_block = Chem.MolToMolBlock(mol)

            # Nombre réel de liaisons rotables (Suggestion 1)
            num_rotatable_bonds = rdMolDescriptors.CalcNumRotatableBonds(mol)

            return {
                'name':                Chem.MolToSmiles(mol),
                'atoms':               atoms,
                'method':              'rdkit_mmff',
                'num_rotatable_bonds': num_rotatable_bonds,
                'mol_block':           rdkit_mol_block,
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


def _pdbqt_ligand_via_obabel(
    smiles: str,
    out_dir: str,
    timeout_seconds: int = 120,
    ligand_mol_block: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """PDBQT ligand natif Open Babel : types AutoDock corrects, protonation à
    pH 7.4 et arbre de torsion réel (BRANCH/ENDBRANCH + TORSDOF honnête)."""
    import shutil
    binary = shutil.which('obabel')
    if binary is None:
        return None, "obabel non installé"
    out = os.path.join(out_dir, 'ligand.pdbqt')
    if ligand_mol_block:
        # Convert the exact RDKit 3D molecule supplied by the upstream ligand
        # builder. Open Babel preserves the SDF atom order when writing PDBQT,
        # giving the RMSD symmetry mapper a verifiable source atom ordering.
        source_sdf = os.path.join(out_dir, 'ligand_source.sdf')
        with open(source_sdf, 'w', encoding='utf-8') as handle:
            handle.write(ligand_mol_block.rstrip("\r\n"))
            handle.write("\n" + "$$" + "\n")
        cmd = [binary, source_sdf, '-O', out, '--partialcharge', 'gasteiger', '-p', '7.4']
    else:
        # Legacy Open Babel-only preparation cannot provide a shared RDKit atom
        # index map. It remains usable, but symmetry-aware RMSD stays disabled.
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


def _should_retry_permissive_rcsb(metadata: Optional[Dict]) -> bool:
    """Retry discovery for no-hit/poor-match cases, not for upstream outages."""
    failure_category = (metadata or {}).get("failure_category")
    return failure_category not in {
        "provider_unavailable",
        "provider_timeout",
        "request_rejected",
        "invalid_provider_response",
    }


def _resolve_vina_executable(configured_path: str) -> Optional[str]:
    """Resolve the configured Vina binary without assuming a fixed install path."""
    configured = (configured_path or "vina").strip()
    if configured and os.path.isfile(configured) and os.access(configured, os.X_OK):
        return os.path.abspath(configured)
    return shutil.which(configured) if configured else shutil.which("vina")


_SUPPORTED_VINA_AD_TYPES = frozenset({
    "C", "A", "N", "NA", "NS", "OA", "OS", "S", "SA",
    "H", "HD", "F", "CL", "BR", "I", "P", "SI",
    "MG", "CA", "MN", "FE", "ZN", "CU", "NI", "CO", "SE",
})


def validate_pdbqt_content(content: str, molecule_type: str) -> Tuple[Optional[Dict], Optional[str]]:
    """Validate the structural contract of a receptor/ligand PDBQT document.

    This checks syntax and internal consistency only; it does not prove that
    protonation, atom typing or partial charges are chemically correct.
    """
    molecule_type = (molecule_type or "").strip().lower()
    if molecule_type not in {"ligand", "receptor"}:
        return None, "molecule_type must be 'ligand' or 'receptor'"
    if not isinstance(content, str) or not content.strip():
        return None, "PDBQT document is empty"

    atom_count = 0
    atom_types = set()
    root_count = 0
    endroot_count = 0
    torsdof_values = []
    branch_stack = []
    branch_count = 0
    unmatched_branch = None

    for line_number, line in enumerate(content.splitlines(), start=1):
        record = line.strip()
        if not record:
            continue
        upper = record.upper()

        if upper == "ROOT":
            root_count += 1
            continue
        if upper == "ENDROOT":
            endroot_count += 1
            continue
        if upper.startswith("TORSDOF"):
            parts = record.split()
            if len(parts) != 2:
                return None, f"TORSDOF invalide à la ligne {line_number}"
            try:
                value = int(parts[1])
            except ValueError:
                return None, f"TORSDOF non entier à la ligne {line_number}"
            if value < 0:
                return None, f"TORSDOF négatif à la ligne {line_number}"
            torsdof_values.append(value)
            continue
        if upper.startswith("BRANCH"):
            parts = record.split()
            if len(parts) != 3:
                return None, f"BRANCH invalide à la ligne {line_number}"
            branch_stack.append((parts[1], parts[2]))
            branch_count += 1
            continue
        if upper.startswith("ENDBRANCH"):
            parts = record.split()
            if len(parts) != 3 or not branch_stack:
                return None, f"ENDBRANCH sans BRANCH correspondant à la ligne {line_number}"
            pair = (parts[1], parts[2])
            if branch_stack[-1] != pair:
                return None, f"ENDBRANCH ne correspond pas au dernier BRANCH à la ligne {line_number}"
            branch_stack.pop()
            continue

        fields = line.split()
        if fields and fields[0] in {"ATOM", "HETATM"}:
            if len(line) < 54 or len(fields) < 5:
                return None, f"Enregistrement atomique PDBQT tronqué à la ligne {line_number}"
            try:
                xyz = [float(line[30:38]), float(line[38:46]), float(line[46:54])]
                partial_charge = float(fields[-2])
            except (ValueError, IndexError):
                return None, f"Coordonnées ou charge PDBQT invalides à la ligne {line_number}"
            if not np.isfinite(xyz + [partial_charge]).all():
                return None, f"Coordonnées ou charge non finies à la ligne {line_number}"
            atom_type = fields[-1].upper()
            if atom_type not in _SUPPORTED_VINA_AD_TYPES:
                return None, f"Type AutoDock/Vina non pris en charge {atom_type!r} à la ligne {line_number}"
            atom_types.add(atom_type)
            atom_count += 1

    if atom_count == 0:
        return None, "PDBQT sans atomes"
    if branch_stack:
        return None, f"PDBQT ligand avec {len(branch_stack)} branche(s) non fermée(s)"

    if molecule_type == "ligand":
        if root_count != 1 or endroot_count != 1:
            return None, "PDBQT ligand doit contenir exactement un ROOT et un ENDROOT"
        if not torsdof_values or len(torsdof_values) != 1:
            return None, "PDBQT ligand doit contenir exactement un TORSDOF"
        torsdof = torsdof_values[0]
        if torsdof != branch_count:
            return None, (
                f"TORSDOF incohérent: déclaré {torsdof}, "
                f"{branch_count} branche(s) BRANCH détectée(s)"
            )
    else:
        if root_count or endroot_count or branch_count or torsdof_values:
            return None, "PDBQT récepteur rigide ne doit pas contenir ROOT/BRANCH/TORSDOF"
        torsdof = None

    return {
        "valid": True,
        "molecule_type": molecule_type,
        "atom_count": atom_count,
        "atom_types": sorted(atom_types),
        "branch_count": branch_count,
        "torsdof": torsdof,
        "charge_and_coordinates_finite": True,
        "validation_scope": "structural PDBQT syntax; not chemical correctness",
    }, None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Optional[str]) -> Optional[str]:
    if not path or not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _tool_fingerprint(name: str, executable: Optional[str], version_args=None) -> Dict:
    info = {
        "name": name,
        "path": executable,
        "sha256": _sha256_file(executable),
        "version": None,
        "version_probe": "not_available",
    }
    if executable and version_args:
        try:
            result = subprocess.run(
                [executable, *version_args],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            output = (result.stdout or result.stderr or "").strip()
            info["version"] = output[:300] or None
            info["version_probe"] = "success" if result.returncode == 0 else f"exit_{result.returncode}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            info["version_probe"] = type(exc).__name__
    elif executable:
        info["version_probe"] = "skipped_unreliable_cli_banner"
    return info


def build_calculation_manifest(
    *,
    protein_sequence: str,
    ligand_smiles: str,
    ligand_data: Dict,
    ligand_mol_block: Optional[str],
    structure_path: Optional[str],
    structure_metadata: Optional[Dict],
    vina_executable: str,
    receptor_pdbqt: str,
    ligand_pdbqt: str,
    pdbqt_metadata: Dict,
    parameters: Dict,
    pockets: List[Dict],
) -> Dict:
    """Build reproducibility metadata without asserting scientific validity."""
    obabel_path = shutil.which("obabel")
    fpocket_path = shutil.which("fpocket")
    try:
        from rdkit import rdBase
        rdkit_version = rdBase.rdkitVersion
    except Exception:
        rdkit_version = None
    try:
        from importlib.metadata import version
        biopython_version = version("biopython")
    except Exception:
        biopython_version = None

    canonical_smiles = ligand_data.get("name")
    return {
        "schema": "nexora.docking-manifest.v1",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "inputs": {
            "protein_sequence_sha256": _sha256_text(protein_sequence),
            "protein_sequence_length_aa": len(protein_sequence),
            "ligand_smiles_sha256": _sha256_text(ligand_smiles),
            "ligand_canonical_smiles": canonical_smiles,
            "ligand_source_mol_block_sha256": (
                _sha256_text(ligand_mol_block) if ligand_mol_block else None
            ),
            "receptor_structure_sha256": _sha256_file(structure_path),
            "receptor_structure_basename": os.path.basename(structure_path) if structure_path else None,
            "ligand_atom_count": len(ligand_data.get("atoms", [])),
        },
        "toolchain": {
            "autodock_vina": _tool_fingerprint("AutoDock Vina", vina_executable, ["--version"]),
            "fpocket": _tool_fingerprint("fpocket", fpocket_path),
            "openbabel_cli": _tool_fingerprint("Open Babel CLI", obabel_path, ["-V"]),
            "rdkit_version": rdkit_version,
            "biopython_version": biopython_version,
        },
        "structure_provenance": structure_metadata or {},
        "pdbqt": {
            **pdbqt_metadata,
            "receptor_sha256": _sha256_text(receptor_pdbqt),
            "ligand_sha256": _sha256_text(ligand_pdbqt),
            "receptor_bytes": len(receptor_pdbqt.encode("utf-8")),
            "ligand_bytes": len(ligand_pdbqt.encode("utf-8")),
        },
        "parameters": parameters,
        "pocket_candidates": [
            {
                "candidate_index": pocket.get("candidate_index", index),
                "source_pocket_id": pocket.get("source_pocket_id", pocket.get("pocket_id")),
                "detection_source": pocket.get("detection_source"),
                "fpocket_score": pocket.get("fpocket_score"),
                "fpocket_druggability_score": pocket.get("fpocket_druggability_score"),
                "heuristic_score": pocket.get("heuristic_score"),
            }
            for index, pocket in enumerate(pockets)
        ],
        "scientific_scope": (
            "Reproducibility record for an empirical Vina score and ASP aggregation; "
            "this manifest does not validate binding affinity or experimental activity."
        ),
    }


def _build_vina_environment() -> Dict[str, str]:
    """Preserve the host/worker environment while adding standard system paths."""
    env = os.environ.copy()
    env.pop("LD_PRELOAD", None)
    existing_path = env.get("PATH", "")
    path_entries = ["/usr/bin", "/bin", "/usr/local/bin", *existing_path.split(os.pathsep)]
    env["PATH"] = os.pathsep.join(dict.fromkeys(entry for entry in path_entries if entry))
    system_libs = ["/usr/lib/x86_64-linux-gnu", "/lib/x86_64-linux-gnu"]
    existing_libs = env.get("LD_LIBRARY_PATH", "").split(os.pathsep)
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        dict.fromkeys(entry for entry in [*system_libs, *existing_libs] if entry)
    )
    return env


_PDBQT_TYPE_TO_ELEMENT = {
    "A": "C", "C": "C",
    "N": "N", "NA": "N", "NS": "N",
    "O": "O", "OA": "O", "OS": "O",
    "S": "S", "SA": "S",
    "F": "F", "CL": "CL", "BR": "BR", "I": "I",
    "P": "P", "SI": "SI", "MG": "MG", "CA": "CA",
    "MN": "MN", "FE": "FE", "ZN": "ZN", "CU": "CU",
    "NI": "NI", "CO": "CO", "SE": "SE",
    "H": "H", "HD": "H",
}


def _pdbqt_atom_order_metadata(content: str) -> Tuple[Optional[Dict], Optional[str]]:
    """Restore the source molecular order from unique PDBQT atom serial IDs.

    Open Babel may write PDBQT atoms in an order that differs from the source
    molecule's atom order. The serial column carries the stable atom identity;
    sorting source row indices by this value restores the preparation order.
    """
    records = []
    seen_serials = set()
    for line_number, line in enumerate((content or "").splitlines(), start=1):
        if not line.startswith(("ATOM  ", "HETATM")):
            continue
        try:
            serial = int(line[6:11].strip())
        except (ValueError, IndexError):
            return None, f"Numéro atomique PDBQT invalide à la ligne {line_number}"
        if serial <= 0 or serial in seen_serials:
            return None, f"Numéro atomique PDBQT non positif ou dupliqué à la ligne {line_number}"
        seen_serials.add(serial)
        fields = line.split()
        atom_type = (fields[-1] if fields else "").upper()
        element = _PDBQT_TYPE_TO_ELEMENT.get(atom_type)
        if not element:
            return None, f"Type atomique PDBQT inconnu à la ligne {line_number}"
        atom_name = line[12:16].strip().upper()
        records.append({
            "serial": serial,
            "element": element,
            "atom_name": atom_name,
            "is_heavy": element != "H",
        })

    if not records:
        return None, "PDBQT ligand sans atomes"

    order_indices = sorted(range(len(records)), key=lambda index: records[index]["serial"])
    ordered_records = [records[index] for index in order_indices]
    return {
        "source_row_order_indices": order_indices,
        "source_serials_in_order": [record["serial"] for record in ordered_records],
        "all_atom_names_in_order": [record["atom_name"] for record in ordered_records],
        "heavy_atom_elements_in_order": [
            record["element"] for record in ordered_records if record["is_heavy"]
        ],
        "heavy_atom_names_in_order": [
            record["atom_name"] for record in ordered_records if record["is_heavy"]
        ],
        "atom_count": len(records),
        "heavy_atom_count": sum(record["is_heavy"] for record in records),
        "ordering_basis": "ascending PDBQT atom serial field",
    }, None


def _restore_pose_atom_order(
    pose: Dict,
    source_serials_in_order: List[int],
) -> Tuple[bool, Optional[str]]:
    """Restore Vina atoms by their PDBQT serial IDs, not by incidental output row order."""
    atoms = pose.get("atoms") or []
    if len(atoms) != len(source_serials_in_order):
        return False, (
            f"Pose {pose.get('pose_id')} atom count {len(atoms)} "
            f"differs from source PDBQT atom count {len(source_serials_in_order)}"
        )
    atoms_by_serial = {}
    try:
        for atom in atoms:
            serial = int(atom["serial"])
            if serial in atoms_by_serial:
                return False, f"Pose {pose.get('pose_id')} contains duplicate atom serial {serial}"
            atoms_by_serial[serial] = atom
        expected = [int(serial) for serial in source_serials_in_order]
        if len(set(expected)) != len(expected):
            return False, "Source PDBQT contains duplicate atom serial IDs"
        if set(atoms_by_serial) != set(expected):
            return False, (
                f"Pose {pose.get('pose_id')} atom serials do not match source PDBQT serials"
            )
        pose["atoms"] = [atoms_by_serial[serial] for serial in expected]
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return False, f"Pose {pose.get('pose_id')} has invalid atom serials: {type(exc).__name__}"
    pose["atom_order_restored"] = True
    pose["atom_order_basis"] = "PDBQT atom serial IDs"
    return True, None


def _build_ligand_symmetry_atom_maps(
    ligand_mol_block: Optional[str],
    pdbqt_heavy_atom_elements: List[str],
    *,
    max_matches: int = 256,
) -> Tuple[Optional[List[List[int]]], Dict]:
    """Build RDKit automorphisms from the exact MolBlock used for PDBQT conversion.

    The same RDKit MolBlock is written as SDF and passed to Open Babel for
    PDBQT generation. Its atom order is therefore the graph's reference order;
    the source PDBQT atom serials restore that order in Vina output before RMSD.
    """
    metadata = {
        "applied": False,
        "mapping_count": 0,
        "max_matches": max_matches,
        "mapping_truncated": False,
        "atom_order_check": "not_performed",
        "source": "RDKit MolBlock -> Open Babel SDF/PDBQT -> RDKit graph automorphisms",
    }
    if not ligand_mol_block:
        metadata["reason"] = "No order-preserving RDKit MolBlock is available"
        return None, metadata

    try:
        from rdkit import Chem
    except ImportError:
        metadata["reason"] = "RDKit unavailable"
        return None, metadata

    molecule = Chem.MolFromMolBlock(
        ligand_mol_block,
        removeHs=False,
        sanitize=True,
        strictParsing=True,
    )
    if molecule is None:
        metadata["reason"] = "RDKit could not parse the source MolBlock"
        return None, metadata
    molecule = Chem.RemoveHs(molecule)
    rdkit_elements = [atom.GetSymbol().upper() for atom in molecule.GetAtoms()]
    pdbqt_elements = [str(element).upper() for element in pdbqt_heavy_atom_elements]
    if len(rdkit_elements) != len(pdbqt_elements):
        metadata["reason"] = "heavy-atom count differs between source MolBlock and PDBQT"
        metadata["rdkit_heavy_atom_count"] = len(rdkit_elements)
        metadata["pdbqt_heavy_atom_count"] = len(pdbqt_elements)
        metadata["atom_order_check"] = "failed_count"
        return None, metadata
    if rdkit_elements != pdbqt_elements:
        metadata["reason"] = "heavy-atom element order differs between source MolBlock and PDBQT serial order"
        metadata["atom_order_check"] = "failed_element_sequence"
        return None, metadata

    try:
        matches = molecule.GetSubstructMatches(
            molecule,
            uniquify=False,
            useChirality=True,
            maxMatches=max_matches,
        )
    except Exception as exc:
        metadata["reason"] = f"RDKit automorphism search failed: {type(exc).__name__}"
        metadata["atom_order_check"] = "passed_element_sequence"
        return None, metadata

    identity = tuple(range(len(rdkit_elements)))
    unique_matches = []
    seen = set()
    for match in matches:
        candidate = tuple(int(index) for index in match)
        if len(candidate) == len(identity) and set(candidate) == set(identity) and candidate not in seen:
            seen.add(candidate)
            unique_matches.append(list(candidate))
    if identity not in seen:
        unique_matches.insert(0, list(identity))
    metadata.update({
        "applied": len(unique_matches) > 1,
        "mapping_count": len(unique_matches),
        "mapping_truncated": len(matches) >= max_matches,
        "atom_order_check": "passed_element_sequence",
        "atom_order_reference": "same RDKit MolBlock written as SDF and converted to PDBQT by Open Babel",
        "rdkit_heavy_atom_count": len(rdkit_elements),
        "pdbqt_heavy_atom_count": len(pdbqt_elements),
        "reason": None if len(unique_matches) > 1 else "no non-identity heavy-atom automorphism",
    })
    return (unique_matches if len(unique_matches) > 1 else None), metadata


def _prepare_pdbqt_inputs(
    protein_atoms: List[Dict],
    ligand_atoms: List[Dict],
    smiles: str,
    torsdof: int,
    ligand_mol_block: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[Dict], Optional[str]]:
    """Create PDBQT contents and record each conversion backend/fallback reason."""
    with tempfile.TemporaryDirectory(prefix="nexora_pdbqt_") as work_dir:
        ligand_pdbqt, ligand_error = _pdbqt_ligand_via_obabel(
            smiles, work_dir, ligand_mol_block=ligand_mol_block
        )
        receptor_pdbqt, receptor_error = _pdbqt_receptor_via_obabel(protein_atoms, work_dir)

        if ligand_error or receptor_error:
            receptor_pdbqt, error = create_pdbqt_from_atoms(protein_atoms, "receptor")
            if error:
                return None, None, None, f"PDBQT récepteur échoué: {error}"
            ligand_pdbqt, error = create_pdbqt_from_atoms(
                ligand_atoms, "ligand", torsdof=torsdof
            )
            if error:
                return None, None, None, f"PDBQT ligand échoué: {error}"
            provider_info = {
                "provider": "internal_fallback",
                "ligand_provider": "internal_fallback",
                "receptor_provider": "internal_fallback",
                "ligand_flexible": False,
                "ligand_torsdof_requested": int(torsdof),
                "ligand_torsdof_effective": 0,
                "ligand_atom_order_source": (
                    "rdkit_mol_block" if ligand_mol_block
                    else "unverified_ligand_data_order"
                ),
                "fallback_reason": {
                    "ligand": ligand_error,
                    "receptor": receptor_error,
                },
                "scientific_warning": (
                    "Le générateur interne écrit un ligand rigide et des types AutoDock estimés; "
                    "ce résultat n'est pas équivalent à une préparation Open Babel flexible."
                ),
            }
            return receptor_pdbqt, ligand_pdbqt, provider_info, None

        provider_info = {
            "provider": "openbabel",
            "ligand_provider": "openbabel",
            "receptor_provider": "openbabel",
            "ligand_flexible": True,
            "ligand_torsdof_requested": int(torsdof),
            "ligand_torsdof_effective": int(torsdof),
            "ligand_atom_order_source": (
                "rdkit_mol_block_sdf" if ligand_mol_block
                else "obabel_smiles_order_unverified"
            ),
            "fallback_reason": None,
            "scientific_warning": None,
        }
        return receptor_pdbqt, ligand_pdbqt, provider_info, None


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
    
    try:
        protein_sequence = normalize_protein_sequence(protein_sequence, min_length=3, max_length=1000)
    except ProteinSequenceValidationError as exc:
        return {
            'success': False,
            'error_message': str(exc),
            'stage': 'protein_validation',
            'recommendation': "Fournir une séquence composée uniquement des 20 acides aminés standards; les résidus ambigus doivent être résolus à la source.",
        }

    vina_executable = _resolve_vina_executable(settings.VINA_PATH)
    if not vina_executable:
        return {
            'success': False,
            'error_message': f"Exécutable AutoDock Vina introuvable ou non exécutable: {settings.VINA_PATH}",
            'stage': 'vina_executable',
            'recommendation': "Installer AutoDock Vina dans l'image worker ou configurer VINA_PATH vers son exécutable.",
        }

    metadata['vina_executable'] = vina_executable
    metadata['vina_cpu'] = settings.VINA_CPU
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
        fallback_errors: List[str] = []

        # 1) Prefer experimental RCSB PDB coordinates.
        model_path, error, model_metadata = resolve_experimental_pdb(
            protein_sequence=protein_sequence,
            output_dir=output_dir,
            identity_cutoff=structure_identity_cutoff,
            evalue_cutoff=structure_evalue_cutoff,
            coverage_cutoff=settings.STRUCTURE_COVERAGE_CUTOFF,
        )
        if error:
            fallback_errors.append(f"RCSB expérimental (standard): {error}")
            if not _should_retry_permissive_rcsb(model_metadata):
                # Do not repeat an equivalent query against a transiently
                # unavailable service; move to the computed-model providers.
                failure_category = (model_metadata or {}).get("failure_category")
                fallback_errors.append(
                    f"RCSB permissif ignoré (catégorie {failure_category})"
                )
            else:
                if progress_callback:
                    progress_callback("modeling", 24, "RCSB expérimental sans match validé, élargissement contrôlé...")
                model_path, error, model_metadata = resolve_experimental_pdb(
                    protein_sequence=protein_sequence,
                    output_dir=output_dir,
                    identity_cutoff=0.5,
                    evalue_cutoff=10.0,
                    coverage_cutoff=settings.STRUCTURE_COVERAGE_CUTOFF,
                    acceptance_identity_cutoff=structure_identity_cutoff,
                )
                if not error:
                    model_metadata = model_metadata or {}
                    model_metadata["retry_used"] = "RCSB experimental permissive search"
                else:
                    fallback_errors.append(f"RCSB expérimental (permissif): {error}")

        # 2) If no experimental hit exists, consult the computed-model index.
        # This path downloads a versioned AlphaFold DB CIF and never labels it
        # as an experimental PDB structure.
        if error:
            if progress_callback:
                progress_callback("modeling", 28, "Recherche de modèles calculés RCSB / AlphaFold DB...")
            model_path, error, model_metadata = resolve_computed_alphafold_model(
                protein_sequence=protein_sequence,
                output_dir=output_dir,
                identity_cutoff=structure_identity_cutoff,
                coverage_cutoff=settings.STRUCTURE_COVERAGE_CUTOFF,
                evalue_cutoff=structure_evalue_cutoff,
            )
            if not error:
                model_metadata = model_metadata or {}
                model_metadata["fallback_used"] = "RCSB experimental → AlphaFold DB computed model"
                structure_provider = "alphafold_db"
            else:
                fallback_errors.append(f"RCSB/AlphaFold DB calculé: {error}")

        # 3) Last remote prediction fallback: ESM Atlas, then Tamarind.
        if error:
            if progress_callback:
                progress_callback("modeling", 34, "Aucun modèle indexé trouvé, tentative ESMFold/Tamarind...")
            model_path, error, model_metadata = build_esmfold_structure(
                protein_sequence=protein_sequence,
                output_dir=output_dir,
                nvidia_api_key=settings.NVIDIA_API_KEY,
            )
            if not error:
                model_metadata = model_metadata or {}
                model_metadata["fallback_used"] = "RCSB experimental → AlphaFold DB → ESMFold/Tamarind"
                structure_provider = "esmfold"
            else:
                fallback_errors.append(f"ESMFold/Tamarind: {error}")

                # ColabFold is not installed in the standard supported image.
                # If a separately isolated worker explicitly provides the
                # binary, allow it as the last optional fallback.
                colabfold_available = shutil.which(colabfold_binary) is not None
                if colabfold_available:
                    model_path, error, model_metadata = build_colabfold_structure(
                        protein_sequence=protein_sequence,
                        binary=colabfold_binary,
                        output_dir=output_dir,
                        num_models=colabfold_num_models,
                        num_recycle=colabfold_num_recycle,
                    )
                    if not error:
                        model_metadata = model_metadata or {}
                        model_metadata["fallback_used"] = (
                            "RCSB experimental → AlphaFold DB → ESMFold/Tamarind → isolated ColabFold"
                        )
                        structure_provider = "colabfold"
                    else:
                        fallback_errors.append(f"ColabFold isolé: {error}")
                else:
                    fallback_errors.append(
                        "ColabFold: absent (non fourni par l'image standard; worker isolé requis)"
                    )

                if error:
                    diag = " | ".join(fallback_errors)
                    print(f"[STRUCTURE] Tous les providers ont échoué: {diag}", file=sys.stderr)
                    error = (
                        "Impossible d'obtenir une structure validable après les fournisseurs configurés.\n"
                        f"Détail : {diag}\n"
                        "Solutions : vérifier la cible et ses seuils de correspondance, configurer "
                        "TAMARIND_API_KEY, fournir un PDB/mmCIF contrôlé ou déployer ColabFold "
                        "dans un worker séparé après revue de sécurité."
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

    model_metadata = model_metadata or {}
    metadata.update(model_metadata)
    explicit_source_type = model_metadata.get("source_type")
    if explicit_source_type:
        source_type = explicit_source_type
    elif structure_provider == "auto_rcsb":
        source_type = "experimental_pdb"
    elif structure_provider == "alphafold_db":
        source_type = "predicted_alphafold_db"
    elif structure_provider == "colabfold":
        source_type = "predicted_colabfold"
    elif structure_provider == "esmfold":
        provider_label = " ".join(str(model_metadata.get(key, "")) for key in ("provider", "method")).lower()
        source_type = "predicted_tamarind_esmfold" if "tamarind" in provider_label else "predicted_esmfold"
    else:
        source_type = "external_pdb"

    # Reject structure/target mismatches before any docking preparation. A
    # high local confidence score cannot substitute for sequence correspondence.
    try:
        structure_report = validate_structure(
            model_path,
            protein_sequence,
            source_type,
            identity_cutoff=structure_identity_cutoff,
            coverage_cutoff=settings.STRUCTURE_COVERAGE_CUTOFF,
        )
    except Exception as exc:
        return {
            'success': False,
            'error_message': f"Validation structurale impossible: {type(exc).__name__}: {exc}",
            'stage': 'structure_validation',
            'metadata': metadata,
        }

    metadata['structure_source_type'] = source_type
    metadata['structure_validation'] = structure_report
    if not structure_report.get("eligible_for_docking_review"):
        match = structure_report.get("sequence_match", {}).get("best_chain", {})
        return {
            'success': False,
            'error_message': (
                "Structure rejetée avant docking : correspondance insuffisante "
                f"(identité={match.get('identity')}, couverture={match.get('coverage')}; "
                f"seuils={structure_identity_cutoff}/{settings.STRUCTURE_COVERAGE_CUTOFF})."
            ),
            'stage': 'structure_validation',
            'metadata': metadata,
        }

    selected_chain = structure_report.get("sequence_match", {}).get("best_chain", {}).get("chain_id")
    if selected_chain is None:
        return {
            'success': False,
            'error_message': "La validation n'a pas identifié de chaîne protéique cible exploitable.",
            'stage': 'structure_validation',
            'metadata': metadata,
        }

    protein_atoms, error = pdb_to_atom_dicts(model_path, chain_id=selected_chain)
    if error:
        return {'success': False, 'error_message': error,
                'stage': 'protein_structure', 'metadata': metadata}
    metadata['selected_receptor_chain'] = selected_chain
    metadata['protein_atoms'] = len(protein_atoms)
    print(f"   ✓ Structure validée: chaîne {selected_chain!r}, {len(protein_atoms)} atomes ({structure_provider})", file=sys.stderr)

    # ── 3. Détection N poches candidates (SUPERPOSITION) ─────────────────────
    print("Étape 3/7: Détection poches candidates (superposition)...", file=sys.stderr)
    if progress_callback:
        progress_callback("pocket_detection", 30, "Détection poches candidates (fpocket)...")
    
    if not isinstance(max_pockets, int) or not 1 <= max_pockets <= 10:
        return {
            'success': False,
            'error_message': "max_pockets doit être un entier compris entre 1 et 10.",
            'stage': 'pocket_detection',
            'metadata': metadata,
        }

    binding_pockets, error = detect_binding_pockets_enriched_fixed(
        protein_atoms,
        allow_heuristic_fallback=settings.DOCKING_ALLOW_HEURISTIC_POCKET_FALLBACK,
    )
    if error:
        return {'success': False, 'error_message': f"Détection poches échouée: {error}",
                'stage': 'pocket_detection', 'metadata': metadata,
                'recommendation': "Installer fpocket 4.2.3; le fallback heuristique doit être activé explicitement pour une exploration non validée."}

    # Preserve detector provenance and stable source pocket IDs separately
    # from the zero-based candidate index used internally by ASP aggregation.
    for candidate_index, pocket in enumerate(binding_pockets):
        pocket['candidate_index'] = candidate_index
        pocket['source_pocket_id'] = pocket.get('pocket_id')
    candidate_pockets = binding_pockets[:max_pockets]
    if not candidate_pockets:
        return {'success': False, 'error_message': 'Aucune poche candidate exploitable',
                'stage': 'pocket_detection', 'metadata': metadata}
    heuristic_pockets = [
        pocket for pocket in candidate_pockets
        if pocket.get('detection_source') == 'heuristic_fallback'
    ]
    metadata['binding_pockets_found'] = len(binding_pockets)
    metadata['candidate_pockets'] = len(candidate_pockets)
    metadata['pocket_detection_method'] = (
        'fpocket' if not heuristic_pockets else 'heuristic_fallback'
    )
    metadata['pocket_detection_sources'] = sorted({
        str(pocket.get('detection_source') or 'unknown')
        for pocket in candidate_pockets
    })
    metadata['heuristic_fallback_used'] = bool(heuristic_pockets)
    metadata['heuristic_pocket_warning'] = (
        "Résultats exploratoires : les scores heuristiques ne sont ni calibrés "
        "ni équivalents aux scores fpocket."
        if heuristic_pockets else None
    )
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
    # VINA_EXHAUSTIVENESS remains the configured base (default 8); a
    # sequence-length multiplier preserves the established 32/48/64 defaults.
    size_multiplier = 8 if len(protein_sequence) > 300 else (6 if len(protein_sequence) > 100 else 4)
    base_exhaustiveness = min(64, max(1, settings.VINA_EXHAUSTIVENESS) * size_multiplier)
    exhaustiveness = min(128, base_exhaustiveness + torsdof * 2)
    metadata['vina_exhaustiveness_setting'] = settings.VINA_EXHAUSTIVENESS
    metadata['exhaustiveness_size_multiplier'] = size_multiplier
    metadata['exhaustiveness'] = exhaustiveness
    metadata['random_seed'] = 42
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
    
    # The helper stores only PDBQT text after conversion; its TemporaryDirectory
    # is removed before Vina starts, including all error/fallback branches.
    receptor_pdbqt, ligand_pdbqt, pdbqt_info, pdbqt_error = _prepare_pdbqt_inputs(
        protein_atoms, ligand_data['atoms'], smiles, torsdof,
        ligand_mol_block=ligand_data.get('mol_block'),
    )
    if pdbqt_error:
        return {'success': False, 'error_message': pdbqt_error,
                'stage': 'pdbqt_generation', 'metadata': metadata}

    receptor_validation, validation_error = validate_pdbqt_content(
        receptor_pdbqt, "receptor"
    )
    if validation_error:
        return {
            'success': False,
            'error_message': f"Validation PDBQT du récepteur échouée: {validation_error}",
            'stage': 'pdbqt_validation',
            'metadata': metadata,
        }
    ligand_validation, validation_error = validate_pdbqt_content(
        ligand_pdbqt, "ligand"
    )
    if validation_error:
        return {
            'success': False,
            'error_message': f"Validation PDBQT du ligand échouée: {validation_error}",
            'stage': 'pdbqt_validation',
            'metadata': metadata,
        }

    metadata['pdbqt_provider'] = pdbqt_info['provider']
    metadata['pdbqt_preparation'] = {
        **pdbqt_info,
        'receptor_validation': receptor_validation,
        'ligand_validation': ligand_validation,
    }

    ligand_atom_order, atom_order_error = _pdbqt_atom_order_metadata(ligand_pdbqt)
    if atom_order_error:
        symmetry_atom_maps = None
        symmetry_metadata = {
            "applied": False,
            "mapping_count": 0,
            "atom_order_restoration": "unavailable",
            "reason": atom_order_error,
        }
    else:
        symmetry_atom_maps, symmetry_metadata = _build_ligand_symmetry_atom_maps(
            ligand_data.get("mol_block"),
            ligand_atom_order["heavy_atom_elements_in_order"],
        )
        symmetry_metadata["atom_order_restoration"] = "PDBQT serial-number order"
        symmetry_metadata["source_atom_count"] = ligand_atom_order["atom_count"]
        symmetry_metadata["heavy_atom_names"] = ligand_atom_order["heavy_atom_names_in_order"]
    metadata["ligand_atom_order"] = (
        {
            "source_serials_in_order": ligand_atom_order["source_serials_in_order"],
            "source_row_order_indices": ligand_atom_order["source_row_order_indices"],
            "heavy_atom_elements_in_order": ligand_atom_order["heavy_atom_elements_in_order"],
            "atom_count": ligand_atom_order["atom_count"],
        }
        if ligand_atom_order else
        {"available": False, "error": atom_order_error}
    )
    metadata["symmetry_mapping"] = symmetry_metadata

    if pdbqt_info['provider'] == 'internal_fallback':
        print("[PDBQT] Open Babel indisponible ou en échec — repli interne rigide, explicitement signalé.",
              file=sys.stderr)
    else:
        print("   ✓ PDBQT Open Babel validés (atom types, charges, ROOT/torsions)",
              file=sys.stderr)
    print("   ✓ Fichiers PDBQT créés et validés", file=sys.stderr)

    metadata['calculation_manifest'] = build_calculation_manifest(
        protein_sequence=protein_sequence,
        ligand_smiles=smiles,
        ligand_data=ligand_data,
        ligand_mol_block=ligand_data.get("mol_block"),
        structure_path=model_path,
        structure_metadata={
            "source_type": source_type,
            "validation": structure_report,
            **model_metadata,
        },
        vina_executable=vina_executable,
        receptor_pdbqt=receptor_pdbqt,
        ligand_pdbqt=ligand_pdbqt,
        pdbqt_metadata=metadata['pdbqt_preparation'],
        parameters={
            "vina_cpu": settings.VINA_CPU,
            "vina_exhaustiveness_setting": settings.VINA_EXHAUSTIVENESS,
            "vina_exhaustiveness_effective": exhaustiveness,
            "exhaustiveness_size_multiplier": size_multiplier,
            "torsdof_requested": torsdof,
            "num_modes": 20,
            "energy_range_kcal_mol": 3,
            "random_seed": 42,
            "per_pocket_timeout_seconds": 300,
            "min_box_for_ligand_A": round(min_box_for_ligand, 3),
            "asp_temperature_K": 298.15,
            "asp_rmsd_threshold_A": 2.0,
            "heuristic_pocket_fallback_enabled": settings.DOCKING_ALLOW_HEURISTIC_POCKET_FALLBACK,
        },
        pockets=candidate_pockets,
    )
    metadata["calculation_manifest"]["ligand_atom_order"] = metadata["ligand_atom_order"]
    metadata["calculation_manifest"]["symmetry_mapping"] = symmetry_metadata
    metadata["calculation_manifest"]["parameters"]["symmetry_automorphism_count"] = (
        symmetry_metadata.get("mapping_count", 0)
    )
    metadata["calculation_manifest"]["parameters"]["symmetry_mapping_applied"] = bool(
        symmetry_metadata.get("applied")
    )

    # ── 6. Docking Vina sur chaque poche (SUPERPOSITION) ────────────────────
    print(f"Étape 6/7: Docking Vina sur {len(candidate_pockets)} poches...", file=sys.stderr)
    if progress_callback:
        progress_callback("docking", 60, f"Docking Vina sur {len(candidate_pockets)} poches...")

    best_score = None
    best_pose  = None
    all_poses  = []
    best_pocket = candidate_pockets[0]
    pocket_run_records = []

    # Preserve native/venv library paths from the Ubuntu worker; only add
    # standard system locations and clear LD_PRELOAD to avoid injected loaders.
    env = _build_vina_environment()

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
seed = 42
cpu = {settings.VINA_CPU}
"""
            with open(config_file, 'w') as f:
                f.write(config_content)

            pocket_run = {
                "candidate_index": pocket_idx,
                "source_pocket_id": pocket.get("source_pocket_id", pocket.get("pocket_id")),
                "detection_source": pocket.get("detection_source"),
                "center_A": [
                    round(float(pocket['center_x']), 4),
                    round(float(pocket['center_y']), 4),
                    round(float(pocket['center_z']), 4),
                ],
                "box_size_A": [
                    round(float(actual_size_x), 4),
                    round(float(actual_size_y), 4),
                    round(float(actual_size_z), 4),
                ],
                "exhaustiveness": exhaustiveness,
                "num_modes": 20,
                "energy_range_kcal_mol": 3,
                "random_seed": 42,
                "cpu": settings.VINA_CPU,
                "config_sha256": _sha256_file(config_file),
                "status": "started",
            }
            pocket_run_records.append(pocket_run)

            print(f"    Poche {pocket_idx + 1}/{len(candidate_pockets)}: "
                  f"({pocket['center_x']:.1f}, {pocket['center_y']:.1f}, "
                  f"{pocket['center_z']:.1f}) | box={actual_size_x:.1f} Å "
                  f"| source={pocket.get('detection_source', pocket.get('method', 'unknown'))}", file=sys.stderr)

            try:
                result = subprocess.run(
                    [vina_executable, '--config', config_file],
                    capture_output=True, text=True, timeout=300, env=env
                )
            except subprocess.TimeoutExpired:
                pocket_run["status"] = "timeout"
                print(f"    Timeout poche {pocket_idx + 1} — ignorée", file=sys.stderr)
                continue

            pocket_run["returncode"] = result.returncode
            pocket_run["stderr_tail"] = (result.stderr or "")[-1200:]
            pocket_run["stdout_tail"] = (result.stdout or "")[-1200:]
            if result.returncode != 0 or not os.path.exists(output_file):
                pocket_run["status"] = "vina_error"
                print(f"    Vina échoué poche {pocket_idx + 1} — ignorée", file=sys.stderr)
                continue

            pocket_run["output_sha256"] = _sha256_file(output_file)
            poses = parse_vina_output_strict(output_file)
            if not poses:
                pocket_run["status"] = "no_valid_poses"
                print(f"    Aucune pose poche {pocket_idx + 1}", file=sys.stderr)
                continue

            if ligand_atom_order is not None:
                ordered_poses = []
                rejected_atom_order = []
                for pose in poses:
                    restored, restore_error = _restore_pose_atom_order(
                        pose, ligand_atom_order["source_serials_in_order"]
                    )
                    if not restored:
                        rejected_atom_order.append({
                            "pose_id": pose.get("pose_id"),
                            "reason": restore_error,
                        })
                        continue
                    pose_heavy_elements = [
                        str(atom.get("element", "")).upper()
                        for atom in pose.get("atoms", [])
                        if str(atom.get("element", "")).upper() != "H"
                    ]
                    if pose_heavy_elements != ligand_atom_order["heavy_atom_elements_in_order"]:
                        rejected_atom_order.append({
                            "pose_id": pose.get("pose_id"),
                            "reason": "heavy-atom element order differs from source PDBQT",
                        })
                        continue
                    ordered_poses.append(pose)
                poses = ordered_poses
                pocket_run["atom_order_restored"] = len(poses)
                pocket_run["poses_rejected_atom_order"] = rejected_atom_order
            else:
                pocket_run["atom_order_restored"] = 0
                pocket_run["atom_order_warning"] = atom_order_error

            if not poses:
                pocket_run["status"] = "atom_order_validation_failed"
                print(f"    Ordre atomique non réconciliable poche {pocket_idx + 1}", file=sys.stderr)
                continue

            pocket_run["status"] = "completed"
            pocket_run["pose_count"] = len(poses)
            pocket_run["best_vina_score"] = min(float(pose["score"]) for pose in poses)

            # Annoter chaque pose avec l'identifiant de la poche
            for pose in poses:
                # pocket_id remains the candidate index for backward
                # compatibility with ASP; source_pocket_id retains fpocket's
                # original (one-based) pocket number in provenance.
                pose['pocket_id'] = pocket_idx
                pose['pocket_source_id'] = pocket.get('source_pocket_id')
                pose['pocket_detection_source'] = pocket.get('detection_source')
                pose['pocket_confidence'] = pocket.get('confidence')

            all_poses.extend(poses)

            # Mise à jour du meilleur score global
            local_best = min(poses, key=lambda p: p['score'])
            if best_score is None or local_best['score'] < best_score:
                best_score  = local_best['score']
                best_pose   = local_best
                best_pocket = pocket
                print(f"   ✓ Nouveau meilleur: {best_score:.2f} kcal/mol "
                      f"(poche {pocket_idx + 1})", file=sys.stderr)

    metadata['calculation_manifest']['pocket_runs'] = pocket_run_records

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
            effective_score, aggregation_metadata = aggregate_pose_scores(
                all_poses,
                atom_symmetry_maps=symmetry_atom_maps,
                symmetry_metadata=symmetry_metadata,
            )
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

    metadata['canonical_best_pocket_id'] = best_asp_pocket_id  # legacy candidate index
    metadata['canonical_best_pocket_index'] = best_asp_pocket_id
    metadata['canonical_best_pocket_source_id'] = (
        best_pocket.get('source_pocket_id', best_pocket.get('pocket_id'))
        if best_asp_pocket_id is not None else None
    )

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
        'calculation_manifest': metadata.get('calculation_manifest'),
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
                        'serial':     int(line[6:11].strip()),
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
