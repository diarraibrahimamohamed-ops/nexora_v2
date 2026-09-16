"""ColabFold/AlphaFold2 structure prediction adapter.

The adapter calls the pinned ``colabfold_batch`` executable and refuses to
silently fall back to a synthetic protein geometry.

Ajout v2:
  pdb_to_atom_dicts() — BioPython en priorité, parseur natif PDB en fallback.
  Cela garantit que les PDB issus de RCSB ou d'ESMFold sont lisibles
  même si BioPython n'est pas installé.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Parseur PDB natif (fallback sans BioPython)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_pdb_native(pdb_path: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """Parseur PDB minimal conforme wwPDB — ne requiert aucune dépendance.

    Colonnes (1-based, format PDB fixe) :
      1-6   Record type (ATOM / HETATM)
      7-11  Atom serial number
      13-16 Atom name
      18-20 Residue name
      23-26 Residue sequence number
      31-38 X (Å)
      39-46 Y (Å)
      47-54 Z (Å)
      77-78 Element symbol (optionnel)
    """
    atoms: List[Dict] = []
    try:
        with open(pdb_path, encoding="utf-8", errors="replace") as fh:
            for serial, raw_line in enumerate(fh, start=1):
                line = raw_line.rstrip("\n")
                record = line[:6]
                if record not in ("ATOM  ", "HETATM"):
                    continue
                if len(line) < 54:
                    continue
                try:
                    atom_name    = line[12:16].strip()
                    residue_name = line[17:20].strip()
                    residue_id   = int(line[22:26].strip())
                    x            = float(line[30:38])
                    y            = float(line[38:46])
                    z            = float(line[46:54])
                    # Élément : col 77-78 d'abord, sinon premier char de atom_name
                    element_raw  = line[76:78].strip() if len(line) > 76 else ""
                    element      = (element_raw or atom_name[0]).upper()
                    atoms.append({
                        "atom_id":      serial,
                        "atom_name":    atom_name,
                        "residue_name": residue_name,
                        "residue_id":   residue_id,
                        "x":            round(x, 3),
                        "y":            round(y, 3),
                        "z":            round(z, 3),
                        "element":      element,
                        "charge":       0.0,
                    })
                except (ValueError, IndexError):
                    continue

        if not atoms:
            return None, "Le fichier PDB ne contient aucun enregistrement ATOM/HETATM valide"
        return atoms, None

    except Exception as exc:
        return None, f"Parseur PDB natif: {type(exc).__name__}: {exc}"


# ─────────────────────────────────────────────────────────────────────────────
# Utilitaires internes
# ─────────────────────────────────────────────────────────────────────────────

def _mean_plddt(pdb_path: str) -> Optional[float]:
    values = []
    with open(pdb_path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith(("ATOM  ", "HETATM")):
                try:
                    values.append(float(line[60:66].strip()))
                except (ValueError, IndexError):
                    continue
    return round(sum(values) / len(values), 3) if values else None


# ─────────────────────────────────────────────────────────────────────────────
# ColabFold / AlphaFold2
# ─────────────────────────────────────────────────────────────────────────────

def build_colabfold_structure(
    protein_sequence: str,
    output_dir: str,
    binary: str = "colabfold_batch",
    num_models: int = 5,
    num_recycle: int = 3,
    timeout_seconds: int = 3600,
) -> Tuple[Optional[str], Optional[str], Dict]:
    """Predict a monomer structure and select the highest-ranked PDB."""
    sequence = "".join(protein_sequence.split()).upper()
    if not sequence:
        return None, "Séquence protéique vide", {}
    if num_models < 1 or num_recycle < 1:
        return None, "num_models et num_recycle doivent être positifs", {}

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    fasta_path = output_path / "nexora_target.fasta"
    fasta_path.write_text(f">nexora_target\n{sequence}\n", encoding="utf-8")

    command = [
        binary,
        str(fasta_path),
        str(output_path),
        "--num-models",  str(num_models),
        "--num-recycle", str(num_recycle),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        return None, f"ColabFold absent: commande introuvable ({binary})", {}
    except subprocess.TimeoutExpired:
        return None, f"ColabFold timeout après {timeout_seconds}s", {}

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "échec sans message").strip()
        return None, f"ColabFold a échoué: {message[-2000:]}", {}

    candidates = sorted(output_path.glob("*rank_001*.pdb"))
    if not candidates:
        return None, "ColabFold n'a produit aucun modèle PDB rank_001", {}

    selected   = candidates[0].resolve()
    mean_plddt = _mean_plddt(str(selected))
    if mean_plddt is None:
        return None, "Impossible de lire les valeurs pLDDT du modèle ColabFold", {}
    if mean_plddt < 50.0:
        return None, (
            f"Confiance ColabFold trop faible: pLDDT moyen={mean_plddt} "
            f"(seuil: 50.0)"
        ), {
            "mean_plddt":    mean_plddt,
            "selected_model": str(selected),
        }

    return str(selected), None, {
        "method":               "AlphaFold2 via ColabFold",
        "colabfold_binary":     binary,
        "models_generated":     len(candidates),
        "selected_model":       str(selected),
        "mean_plddt":           mean_plddt,
        "confidence_threshold": 50.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Conversion PDB → dictionnaires d'atomes (utilisé par vina_runner)
# ─────────────────────────────────────────────────────────────────────────────

def pdb_to_atom_dicts(pdb_path: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """Convertit un PDB en liste de dicts d'atomes pour AutoDock Vina.

    Tente BioPython (Bio.PDB) en priorité pour la robustesse sur les PDB
    non-standards. Si BioPython est absent ou échoue, bascule automatiquement
    sur le parseur natif intégré — aucune dépendance externe requise.
    """
    # ── Tentative 1 : BioPython ──────────────────────────────────────────
    try:
        from Bio.PDB import PDBParser

        structure = PDBParser(QUIET=True).get_structure("nexora", pdb_path)
        atoms: List[Dict] = []
        for index, atom in enumerate(structure.get_atoms(), start=1):
            element = (atom.element or atom.get_name()[0]).strip().upper()
            if not element:
                continue
            coords  = atom.get_coord()
            residue = atom.get_parent()
            atoms.append({
                "atom_id":      index,
                "atom_name":    atom.get_name().strip(),
                "residue_name": residue.get_resname().strip(),
                "residue_id":   residue.id[1],
                "x":            round(float(coords[0]), 3),
                "y":            round(float(coords[1]), 3),
                "z":            round(float(coords[2]), 3),
                "element":      element,
                "charge":       0.0,
            })
        if not atoms:
            return None, "BioPython: le modèle PDB ne contient aucun atome"
        return atoms, None

    except ImportError:
        # BioPython non installé — basculer sur le parseur natif
        pass
    except Exception as exc:
        # BioPython a échoué (PDB malformé, etc.) — tenter le parseur natif
        print(
            f"[pdb_to_atom_dicts] BioPython a échoué ({exc}), "
            f"basculement sur parseur natif…",
            flush=True,
        )

    # ── Tentative 2 : Parseur natif (aucune dépendance) ──────────────────
    return _parse_pdb_native(pdb_path)


# ─────────────────────────────────────────────────────────────────────────────
# Structure externe (PDB fourni par l'administrateur)
# ─────────────────────────────────────────────────────────────────────────────

def use_external_pdb_structure(
    receptor_pdb: str,
    output_dir: str,
) -> Tuple[Optional[str], Optional[str], Dict]:
    """Use an administrator-provided PDB without running a local predictor."""
    source = Path(receptor_pdb)
    if not source.is_file():
        return None, f"PDB récepteur introuvable: {receptor_pdb}", {}

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    destination = output_path / source.name
    shutil.copy2(source, destination)

    return str(destination.resolve()), None, {
        "method":              "external PDB structure",
        "source_pdb":          str(source.resolve()),
        "confidence_available": False,
        "validated_for_docking": False,
    }
