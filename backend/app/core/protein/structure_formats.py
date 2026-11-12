"""Shared coordinate-file normalization for the current PDB-only docking parsers."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple


def normalize_mmcif_to_pdb(path: str) -> Tuple[str, Dict[str, Any]]:
    """Convert a text mmCIF model into PDB while retaining atom B-factors.

    The current structure validator and receptor atom parser operate on PDB.
    Biopython parses author identifiers first, then retries with label
    identifiers. The first model is selected so alternative models are not
    combined into one receptor.
    """
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in {".cif", ".mmcif"}:
        fmt = suffix.lstrip(".") or "unknown"
        return str(source), {"source_format": fmt, "normalized_format": fmt}

    try:
        from Bio.PDB import MMCIFParser, PDBIO
    except ImportError as exc:
        raise RuntimeError("Biopython est requis pour normaliser un artefact mmCIF.") from exc

    structure = None
    parser_errors = []
    for auth_ids in (True, False):
        try:
            parser = MMCIFParser(
                QUIET=True,
                auth_chains=auth_ids,
                auth_residues=auth_ids,
            )
            candidate = parser.get_structure("nexora_structure", str(source))
            if sum(1 for _ in candidate.get_atoms()) > 0:
                structure = candidate
                break
        except Exception as exc:
            parser_errors.append(f"{type(exc).__name__}: {exc}")

    if structure is None:
        detail = "; ".join(parser_errors[-2:]) or "aucun atome exploitable"
        raise ValueError(f"Impossible de lire le mmCIF ({detail}).")

    try:
        model = next(structure.get_models())
    except StopIteration as exc:
        raise ValueError("Le mmCIF ne contient aucun modèle atomique.") from exc

    destination = source.with_name(f"{source.stem}_normalized.pdb")
    writer = PDBIO()
    writer.set_structure(model)
    writer.save(str(destination))

    with destination.open("r", encoding="utf-8", errors="replace") as handle:
        has_protein_atoms = any(line.startswith("ATOM  ") for line in handle)
    if not has_protein_atoms:
        destination.unlink(missing_ok=True)
        raise ValueError("La conversion mmCIF n'a produit aucun atome protéique PDB.")

    return str(destination.resolve()), {
        "source_format": suffix.lstrip("."),
        "normalized_format": "pdb",
        "normalization": "Biopython MMCIFParser/PDBIO",
    }
