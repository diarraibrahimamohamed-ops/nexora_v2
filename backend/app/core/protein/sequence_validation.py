"""Strict amino-acid sequence validation shared by molecular-structure workflows.

Only whitespace is normalized. Unsupported residues are never silently deleted,
because doing so changes the biological target and can invalidate docking.
"""
from __future__ import annotations

from typing import Optional

STANDARD_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")


class ProteinSequenceValidationError(ValueError):
    """Raised when a sequence cannot safely be used as a standard protein."""


def normalize_protein_sequence(
    sequence: Optional[str],
    *,
    min_length: int = 3,
    max_length: int = 1000,
) -> str:
    """Normalize whitespace/case and reject non-canonical amino-acid symbols.

    FASTA headers and ambiguity symbols (B, J, X, Z, U, O) are rejected.
    Positions in the error are 1-based in the normalized (whitespace-free)
    sequence so a user can locate the exact offending symbol.
    """
    if not isinstance(sequence, str):
        raise ProteinSequenceValidationError("La séquence protéique doit être une chaîne de caractères.")

    normalized = "".join(sequence.split()).upper()
    if not normalized:
        raise ProteinSequenceValidationError("La séquence protéique est vide.")
    if len(normalized) < min_length:
        raise ProteinSequenceValidationError(
            f"Séquence protéique trop courte : {len(normalized)} aa (minimum {min_length})."
        )
    if len(normalized) > max_length:
        raise ProteinSequenceValidationError(
            f"Séquence protéique trop longue : {len(normalized)} aa (maximum {max_length})."
        )

    invalid = [(index + 1, symbol) for index, symbol in enumerate(normalized)
               if symbol not in STANDARD_AMINO_ACIDS]
    if invalid:
        preview = ", ".join(f"{symbol!r} à la position {position}" for position, symbol in invalid[:8])
        suffix = f" (+{len(invalid) - 8} autres)" if len(invalid) > 8 else ""
        raise ProteinSequenceValidationError(
            "Résidus non standards ou caractères invalides : "
            f"{preview}{suffix}. Les 20 acides aminés standards sont requis; "
            "les symboles ambigus (X, B, Z, J, U, O) ne sont pas supprimés automatiquement."
        )

    return normalized
