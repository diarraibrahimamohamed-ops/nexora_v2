"""Deterministic variant-to-protein consequence helpers.

This module deliberately refuses to infer a protein consequence unless the
necessary coordinate information is explicit. A nucleotide variant without a
CDS/frame/strand mapping is kept as an observed genomic variant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_AA = set("ACDEFGHIKLMNPQRSTVWY")
_AA_CHANGE_RE = re.compile(r"^(?P<ref>[A-Z])(?P<pos>[0-9]+)(?P<alt>[A-Z])$")

# Standard nuclear genetic code (NCBI translation table 1).
CODON_TABLE = {
    "TTT":"F","TTC":"F","TTA":"L","TTG":"L","TCT":"S","TCC":"S","TCA":"S","TCG":"S",
    "TAT":"Y","TAC":"Y","TAA":"*","TAG":"*","TGT":"C","TGC":"C","TGA":"*","TGG":"W",
    "CTT":"L","CTC":"L","CTA":"L","CTG":"L","CCT":"P","CCC":"P","CCA":"P","CCG":"P",
    "CAT":"H","CAC":"H","CAA":"Q","CAG":"Q","CGT":"R","CGC":"R","CGA":"R","CGG":"R",
    "ATT":"I","ATC":"I","ATA":"I","ATG":"M","ACT":"T","ACC":"T","ACA":"T","ACG":"T",
    "AAT":"N","AAC":"N","AAA":"K","AAG":"K","AGT":"S","AGC":"S","AGA":"R","AGG":"R",
    "GTT":"V","GTC":"V","GTA":"V","GTG":"V","GCT":"A","GCC":"A","GCA":"A","GCG":"A",
    "GAT":"D","GAC":"D","GAA":"E","GAG":"E","GGT":"G","GGC":"G","GGA":"G","GGG":"G",
}


def normalize_protein(sequence: str) -> str:
    """Normalize a protein sequence and reject non-standard residues."""
    compact = re.sub(r"\s+", "", sequence or "").upper()
    if not compact or any(aa not in _AA for aa in compact):
        raise ValueError("Séquence protéique invalide: acides aminés standards uniquement")
    return compact


def parse_protein_change(notation: str) -> tuple[str, int, str]:
    """Parse a compact substitution such as K76T into (ref, 1-based pos, alt)."""
    compact = (notation or "").strip().upper()
    match = _AA_CHANGE_RE.fullmatch(compact)
    if not match:
        raise ValueError("Notation protéique attendue: ex. K76T")
    return match.group("ref"), int(match.group("pos")), match.group("alt")


def apply_protein_substitution(sequence: str, *, ref_aa: str, position: int, alt_aa: str) -> str:
    """Return a deterministic one-substitution protein sequence after validation."""
    protein = normalize_protein(sequence)
    ref = (ref_aa or "").upper()
    alt = (alt_aa or "").upper()
    if ref not in _AA or alt not in _AA:
        raise ValueError("Acide aminé de référence ou alternatif invalide")
    if position < 1 or position > len(protein):
        raise ValueError(f"Position protéique hors limites: {position} / {len(protein)}")
    observed = protein[position - 1]
    if observed != ref:
        raise ValueError(
            f"Discordance WT à la position {position}: attendu {ref}, observé {observed}"
        )
    if ref == alt:
        raise ValueError("La substitution ne change pas l'acide aminé")
    chars = list(protein)
    chars[position - 1] = alt
    return "".join(chars)


def normalize_cds(sequence: str) -> str:
    compact = re.sub(r"\s+", "", sequence or "").upper().replace("U", "T")
    if not compact or any(nt not in "ACGT" for nt in compact):
        raise ValueError("CDS invalide: utilisez uniquement A/C/G/T (séquence 5'→3' codante)")
    if len(compact) % 3 != 0:
        raise ValueError("CDS invalide: longueur non multiple de 3")
    return compact


def translate_cds(cds: str) -> str:
    cds = normalize_cds(cds)
    return "".join(CODON_TABLE[cds[i:i+3]] for i in range(0, len(cds), 3))


@dataclass(frozen=True)
class NucleotideConsequence:
    position: int
    ref_nt: str
    alt_nt: str
    codon_ref: str
    codon_alt: str
    aa_ref: str
    aa_alt: str
    protein_position: int
    consequence: str
    coding_sequence_length: int
    method: str = "deterministic_cds_consequence_v1"


def build_nucleotide_consequence(
    cds_sequence: str,
    *,
    nucleotide_position: int,
    ref_nt: str,
    alt_nt: str,
) -> NucleotideConsequence:
    cds = normalize_cds(cds_sequence)
    pos = int(nucleotide_position)
    ref = (ref_nt or "").upper().replace("U", "T")
    alt = (alt_nt or "").upper().replace("U", "T")
    if pos < 1 or pos > len(cds):
        raise ValueError(f"Position CDS hors limites: {pos} / {len(cds)}")
    if ref not in "ACGT" or alt not in "ACGT":
        raise ValueError("Nucléotide de référence ou alternatif invalide")
    observed = cds[pos - 1]
    if observed != ref:
        raise ValueError(f"Discordance CDS à la position {pos}: attendu {ref}, observé {observed}")
    if ref == alt:
        raise ValueError("La substitution nucléotidique ne change pas la base")

    codon_start = ((pos - 1) // 3) * 3
    codon_end = codon_start + 3
    codon_ref = cds[codon_start:codon_end]
    mutated = list(cds)
    mutated[pos - 1] = alt
    mutated_cds = "".join(mutated)
    codon_alt = mutated_cds[codon_start:codon_end]
    aa_ref = CODON_TABLE[codon_ref]
    aa_alt = CODON_TABLE[codon_alt]
    protein_position = codon_start // 3 + 1

    if aa_ref == aa_alt:
        consequence = "synonymous"
    elif aa_alt == "*":
        consequence = "nonsense"
    elif aa_ref == "*":
        consequence = "stop_loss"
    else:
        consequence = "missense"

    return NucleotideConsequence(
        position=pos, ref_nt=ref, alt_nt=alt, codon_ref=codon_ref, codon_alt=codon_alt,
        aa_ref=aa_ref, aa_alt=aa_alt, protein_position=protein_position,
        consequence=consequence, coding_sequence_length=len(cds),
    )


@dataclass(frozen=True)
class ProteinConsequence:
    notation: str
    ref_aa: str
    alt_aa: str
    protein_position: int
    reference_protein_length: int
    variant_protein_length: int
    variant_protein_sequence: str
    consequence: str
    method: str = "deterministic_protein_substitution_v1"


def build_protein_consequence(
    reference_sequence: str,
    *,
    notation: Optional[str] = None,
    ref_aa: Optional[str] = None,
    alt_aa: Optional[str] = None,
    protein_position: Optional[int] = None,
) -> ProteinConsequence:
    protein = normalize_protein(reference_sequence)

    if notation:
        parsed_ref, parsed_pos, parsed_alt = parse_protein_change(notation)
        ref_aa = ref_aa or parsed_ref
        alt_aa = alt_aa or parsed_alt
        protein_position = protein_position or parsed_pos

    if not ref_aa or not alt_aa or not protein_position:
        raise ValueError("Il faut une notation protéique ou ref_aa/alt_aa/protein_position")

    variant = apply_protein_substitution(
        protein,
        ref_aa=ref_aa,
        position=protein_position,
        alt_aa=alt_aa,
    )
    ref = ref_aa.upper()
    alt = alt_aa.upper()
    notation_value = f"{ref}{protein_position}{alt}"
    return ProteinConsequence(
        notation=notation_value,
        ref_aa=ref,
        alt_aa=alt,
        protein_position=protein_position,
        reference_protein_length=len(protein),
        variant_protein_length=len(variant),
        variant_protein_sequence=variant,
        consequence="missense_substitution",
    )
