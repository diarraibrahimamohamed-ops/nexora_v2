"""
backend/app/core/sequence/afribio_engine.py
Moteur Bio-informatique AfriBio-Core v2026 en Python Natif.

Implémente :
1. Alignement local Smith-Waterman avec pénalités de gap affines (Gotoh)
2. Matrices de substitution (NUC44 simplifiée pour nucléotides / BLOSUM62 pour protéines)
3. Détection fine de variants (SNPs transitions/transversions, insertions, délétions)
4. Exportateur VCF 4.2 dérivé de l’alignement (sans qualité de variant expérimentale)
5. Matrice de distances (Jukes-Cantor / Hamming) et Arbre Phylogénétique Neighbor-Joining / UPGMA
"""

import math
import re
from typing import Dict, List, Tuple, Any, Optional

# ── Matrices de substitution ──────────────────────────────────────────────────

NUC44_MATRIX = {
    'A': {'A': 5, 'T': -4, 'G': -4, 'C': -4, 'N': 0},
    'T': {'A': -4, 'T': 5, 'G': -4, 'C': -4, 'N': 0},
    'G': {'A': -4, 'T': -4, 'G': 5, 'C': -4, 'N': 0},
    'C': {'A': -4, 'T': -4, 'G': -4, 'C': 5, 'N': 0},
    'N': {'A': 0, 'T': 0, 'G': 0, 'C': 0, 'N': 0},
}

BLOSUM62_MATRIX = {
    'A': {'A': 4, 'R': -1, 'N': -2, 'D': -2, 'C': 0, 'Q': -1, 'E': -1, 'G': 0, 'H': -2, 'I': -1, 'L': -1, 'K': -1, 'M': -1, 'F': -2, 'P': -1, 'S': 1, 'T': 0, 'W': -3, 'Y': -2, 'V': 0},
    'R': {'A': -1, 'R': 5, 'N': 0, 'D': -2, 'C': -3, 'Q': 1, 'E': 0, 'G': -2, 'H': 0, 'I': -3, 'L': -2, 'K': 2, 'M': -1, 'F': -3, 'P': -2, 'S': -1, 'T': -1, 'W': -3, 'Y': -2, 'V': -3},
    'N': {'A': -2, 'R': 0, 'N': 6, 'D': 1, 'C': -3, 'Q': 0, 'E': 0, 'G': 0, 'H': 1, 'I': -3, 'L': -3, 'K': 0, 'M': -2, 'F': -3, 'P': -2, 'S': 1, 'T': 0, 'W': -4, 'Y': -2, 'V': -3},
    'D': {'A': -2, 'R': -2, 'N': 1, 'D': 6, 'C': -3, 'Q': 0, 'E': 2, 'G': -1, 'H': -1, 'I': -3, 'L': -4, 'K': -1, 'M': -3, 'F': -3, 'P': -1, 'S': 0, 'T': -1, 'W': -4, 'Y': -3, 'V': -3},
    'C': {'A': 0, 'R': -3, 'N': -3, 'D': -3, 'C': 9, 'Q': -3, 'E': -4, 'G': -3, 'H': -3, 'I': -1, 'L': -1, 'K': -3, 'M': -1, 'F': -2, 'P': -3, 'S': -1, 'T': -1, 'W': -2, 'Y': -2, 'V': -1},
    'Q': {'A': -1, 'R': 1, 'N': 0, 'D': 0, 'C': -3, 'Q': 5, 'E': 2, 'G': -2, 'H': 0, 'I': -3, 'L': -2, 'K': 1, 'M': 0, 'F': -3, 'P': -1, 'S': 0, 'T': -1, 'W': -2, 'Y': -1, 'V': -2},
    'E': {'A': -1, 'R': 0, 'N': 0, 'D': 2, 'C': -4, 'Q': 2, 'E': 5, 'G': -2, 'H': 0, 'I': -3, 'L': -3, 'K': 1, 'M': -2, 'F': -3, 'P': -1, 'S': 0, 'T': -1, 'W': -3, 'Y': -2, 'V': -2},
    'G': {'A': 0, 'R': -2, 'N': 0, 'D': -1, 'C': -3, 'Q': -2, 'E': -2, 'G': 6, 'H': -2, 'I': -4, 'L': -4, 'K': -2, 'M': -3, 'F': -3, 'P': -2, 'S': 0, 'T': -2, 'W': -2, 'Y': -3, 'V': -3},
    'H': {'A': -2, 'R': 0, 'N': 1, 'D': -1, 'C': -3, 'Q': 0, 'E': 0, 'G': -2, 'H': 8, 'I': -3, 'L': -3, 'K': -1, 'M': -2, 'F': -1, 'P': -2, 'S': -1, 'T': -2, 'W': -2, 'Y': 2, 'V': -3},
    'I': {'A': -1, 'R': -3, 'N': -3, 'D': -3, 'C': -1, 'Q': -3, 'E': -3, 'G': -4, 'H': -3, 'I': 4, 'L': 2, 'K': -3, 'M': 1, 'F': 0, 'P': -3, 'S': -2, 'T': -1, 'W': -3, 'Y': -1, 'V': 3},
    'L': {'A': -1, 'R': -2, 'N': -3, 'D': -4, 'C': -1, 'Q': -2, 'E': -3, 'G': -4, 'H': -3, 'I': 2, 'L': 4, 'K': -2, 'M': 2, 'F': 0, 'P': -3, 'S': -2, 'T': -1, 'W': -2, 'Y': -1, 'V': 1},
    'K': {'A': -1, 'R': 2, 'N': 0, 'D': -1, 'C': -3, 'Q': 1, 'E': 1, 'G': -2, 'H': -1, 'I': -3, 'L': -2, 'K': 5, 'M': -1, 'F': -3, 'P': -1, 'S': 0, 'T': -1, 'W': -3, 'Y': -2, 'V': -2},
    'M': {'A': -1, 'R': -1, 'N': -2, 'D': -3, 'C': -1, 'Q': 0, 'E': -2, 'G': -3, 'H': -2, 'I': 1, 'L': 2, 'K': -1, 'M': 5, 'F': 0, 'P': -2, 'S': -1, 'T': -1, 'W': -1, 'Y': -1, 'V': 1},
    'F': {'A': -2, 'R': -3, 'N': -3, 'D': -3, 'C': -2, 'Q': -3, 'E': -3, 'G': -3, 'H': -1, 'I': 0, 'L': 0, 'K': -3, 'M': 0, 'F': 6, 'P': -4, 'S': -2, 'T': -2, 'W': 1, 'Y': 3, 'V': -1},
    'P': {'A': -1, 'R': -2, 'N': -2, 'D': -1, 'C': -3, 'Q': -1, 'E': -1, 'G': -2, 'H': -2, 'I': -3, 'L': -3, 'K': -1, 'M': -2, 'F': -4, 'P': 7, 'S': -1, 'T': -1, 'W': -4, 'Y': -3, 'V': -2},
    'S': {'A': 1, 'R': -1, 'N': 1, 'D': 0, 'C': -1, 'Q': 0, 'E': 0, 'G': 0, 'H': -1, 'I': -2, 'L': -2, 'K': 0, 'M': -1, 'F': -2, 'P': -1, 'S': 4, 'T': 1, 'W': -3, 'Y': -2, 'V': 0},
    'T': {'A': 0, 'R': -1, 'N': 0, 'D': -1, 'C': -1, 'Q': -1, 'E': -1, 'G': -2, 'H': -2, 'I': -1, 'L': -1, 'K': -1, 'M': -1, 'F': -2, 'P': -1, 'S': 1, 'T': 5, 'W': -2, 'Y': -2, 'V': 0},
    'W': {'A': -3, 'R': -3, 'N': -4, 'D': -4, 'C': -2, 'Q': -2, 'E': -3, 'G': -2, 'H': -2, 'I': -3, 'L': -2, 'K': -3, 'M': -1, 'F': 1, 'P': -4, 'S': -3, 'T': -2, 'W': 11, 'Y': 2, 'V': -3},
    'Y': {'A': -2, 'R': -2, 'N': -2, 'D': -3, 'C': -2, 'Q': -1, 'E': -2, 'G': -3, 'H': 2, 'I': -1, 'L': -1, 'K': -2, 'M': -1, 'F': 3, 'P': -3, 'S': -2, 'T': -2, 'W': 2, 'Y': 7, 'V': -1},
    'V': {'A': 0, 'R': -3, 'N': -3, 'D': -3, 'C': -1, 'Q': -2, 'E': -2, 'G': -3, 'H': -3, 'I': 3, 'L': 1, 'K': -2, 'M': 1, 'F': -1, 'P': -2, 'S': 0, 'T': 0, 'W': -3, 'Y': -1, 'V': 4},
}


# ── Utilitaires Nettoyage Séquence ──────────────────────────────────────────

DNA_IUPAC_BASES = {
    "A": frozenset("A"), "C": frozenset("C"), "G": frozenset("G"), "T": frozenset("T"),
    "R": frozenset("AG"), "Y": frozenset("CT"), "S": frozenset("GC"), "W": frozenset("AT"),
    "K": frozenset("GT"), "M": frozenset("AC"), "B": frozenset("CGT"), "D": frozenset("AGT"),
    "H": frozenset("ACT"), "V": frozenset("ACG"), "N": frozenset("ACGT"),
}
PROTEIN_IUPAC_BASES = {
    amino_acid: frozenset((amino_acid,)) for amino_acid in BLOSUM62_MATRIX
}
PROTEIN_IUPAC_BASES.update({
    "B": frozenset("DN"),
    "Z": frozenset("EQ"),
    "J": frozenset("IL"),
    "X": frozenset(BLOSUM62_MATRIX),
})
DEFAULT_MAX_ALIGNMENT_CELLS = 5_000_000


def clean_sequence(seq: str, seq_type: str = "DNA") -> str:
    """Normalize whitespace/case while preserving supported ambiguity symbols.

    Unsupported symbols are rejected instead of being silently deleted. RNA U
    is normalized to T because this pairwise nucleotide aligner uses DNA notation.
    """
    if not seq:
        return ""
    normalized_type = seq_type.upper()
    normalized = "".join(seq.upper().split())

    if normalized_type == "RNA":
        normalized = normalized.replace("U", "T")
        allowed = set(DNA_IUPAC_BASES)
    elif normalized_type == "DNA":
        allowed = set(DNA_IUPAC_BASES)
    elif normalized_type == "PROTEIN":
        allowed = set(PROTEIN_IUPAC_BASES)
    else:
        raise ValueError("Type de séquence non pris en charge (DNA, RNA ou PROTEIN attendu)")

    invalid = sorted(set(normalized) - allowed)
    if invalid:
        alphabet = "nucléotidique IUPAC" if normalized_type in {"DNA", "RNA"} else "protéique"
        raise ValueError(
            f"Caractères invalides pour une séquence {alphabet}: {''.join(invalid)}"
        )
    return normalized


def calculate_gc(seq: str) -> float:
    """Calculate GC percentage over unambiguous A/C/G/T positions only."""
    clean = re.sub(r"[^ACGT]", "", seq.upper())
    if not clean:
        return 0.0
    gc_count = clean.count("G") + clean.count("C")
    return round((gc_count / len(clean)) * 100, 2)


# ── Algorithme Smith-Waterman Gotoh ───────────────────────────────────────────

class SmithWatermanGotoh:
    def __init__(
        self,
        match_score: int = 5,
        mismatch_penalty: int = -4,
        gap_open: int = -10,
        gap_extend: int = -1,
        seq_type: str = "DNA",
        max_alignment_cells: int = DEFAULT_MAX_ALIGNMENT_CELLS,
    ):
        self.match_score = match_score
        self.mismatch_penalty = mismatch_penalty
        self.gap_open = gap_open
        self.gap_extend = gap_extend
        self.seq_type = seq_type.upper()
        if self.seq_type not in {"DNA", "RNA", "PROTEIN"}:
            raise ValueError("seq_type doit être DNA, RNA ou PROTEIN")
        if isinstance(max_alignment_cells, bool) or not isinstance(max_alignment_cells, int) or max_alignment_cells < 1:
            raise ValueError("max_alignment_cells doit être un entier strictement positif")
        self.max_alignment_cells = max_alignment_cells
        self.matrix = BLOSUM62_MATRIX if self.seq_type == "PROTEIN" else NUC44_MATRIX

    def get_score(self, a: str, b: str) -> int:
        if self.seq_type == "PROTEIN":
            if a == "X" or b == "X":
                return 0
            possibilities_a = PROTEIN_IUPAC_BASES[a]
            possibilities_b = PROTEIN_IUPAC_BASES[b]
            scores = [
                self.matrix[aa].get(bb, -2)
                for aa in possibilities_a
                for bb in possibilities_b
            ]
            return int(round(sum(scores) / len(scores)))

        possibilities_a = DNA_IUPAC_BASES[a]
        possibilities_b = DNA_IUPAC_BASES[b]
        if len(possibilities_a) == 1 and len(possibilities_b) == 1:
            return self.match_score if possibilities_a == possibilities_b else self.mismatch_penalty
        # An ambiguous symbol is not counted as an exact match. An overlap is
        # neutral; disjoint possible-base sets are treated as a mismatch.
        return 0 if possibilities_a.intersection(possibilities_b) else self.mismatch_penalty

    def align(self, query: str, reference: str) -> Dict[str, Any]:
        """Local Smith-Waterman-Gotoh alignment with affine gaps.

        H(i,j) = max(0, H(i-1,j-1) + S(q_i,r_j), E(i,j), F(i,j))
        E(i,j) = max(H(i,j-1) + gap_open, E(i,j-1) + gap_extend)
        F(i,j) = max(H(i-1,j) + gap_open, F(i-1,j) + gap_extend)

        Scores use rolling rows. Three compact byte arrays preserve the H/E/F
        traceback states. The cell budget is checked before allocating them.
        """
        q = clean_sequence(query, self.seq_type)
        r = clean_sequence(reference, self.seq_type)

        m, n = len(q), len(r)
        if m == 0 or n == 0:
            return {
                "aligned_query": "",
                "aligned_reference": "",
                "alignment_string": "",
                "score": 0,
                "length": 0,
                "identity": 0.0,
                "similarity": 0.0,
                "coverage": 0.0,
                "query_length": m,
                "reference_length": n,
                "matches": 0,
                "mismatches": 0,
            }

        width = n + 1
        cell_count = (m + 1) * width
        if cell_count > self.max_alignment_cells:
            raise ValueError(
                "Alignement refusé avant allocation : "
                f"{m} × {n} = {cell_count:,} cellules, "
                f"limite configurée = {self.max_alignment_cells:,}. "
                "Réduisez les séquences ou utilisez un moteur d'alignement adapté aux grands volumes."
            )

        negative_infinity = -(10 ** 15)
        h_previous = [0] * width
        h_current = [0] * width
        e_current = [negative_infinity] * width
        f_previous = [negative_infinity] * width
        f_current = [negative_infinity] * width

        # H: 0 stop, 1 diagonal, 2 E (left), 3 F (up).
        # E/F: 0 opened from H, 1 extended from the same gap state.
        traceback_h = bytearray(cell_count)
        traceback_e = bytearray(cell_count)
        traceback_f = bytearray(cell_count)

        max_score = 0
        max_pos = (0, 0)

        for i in range(1, m + 1):
            h_current[0] = 0
            e_current[0] = negative_infinity
            f_current[0] = negative_infinity

            for j in range(1, n + 1):
                index = i * width + j

                e_open = h_current[j - 1] + self.gap_open
                e_extend = e_current[j - 1] + self.gap_extend
                if e_open >= e_extend:
                    e_current[j] = e_open
                    traceback_e[index] = 0
                else:
                    e_current[j] = e_extend
                    traceback_e[index] = 1

                f_open = h_previous[j] + self.gap_open
                f_extend = f_previous[j] + self.gap_extend
                if f_open >= f_extend:
                    f_current[j] = f_open
                    traceback_f[index] = 0
                else:
                    f_current[j] = f_extend
                    traceback_f[index] = 1

                diagonal_score = h_previous[j - 1] + self.get_score(q[i - 1], r[j - 1])
                h_value = max(0, diagonal_score, e_current[j], f_current[j])
                h_current[j] = h_value

                if h_value == 0:
                    traceback_h[index] = 0
                elif h_value == diagonal_score:
                    traceback_h[index] = 1
                elif h_value == e_current[j]:
                    traceback_h[index] = 2
                else:
                    traceback_h[index] = 3

                if h_value > max_score:
                    max_score = h_value
                    max_pos = (i, j)

            h_previous, h_current = h_current, h_previous
            f_previous, f_current = f_current, f_previous

        # State-aware traceback: a gap extension follows E/F pointers, never
        # the direction selected by H at an intermediate gap cell.
        curr_i, curr_j = max_pos
        state = "H"
        aligned_q = []
        aligned_r = []
        align_str = []
        matches = 0
        similar = 0

        while True:
            if state == "H":
                if curr_i <= 0 or curr_j <= 0:
                    break
                direction = traceback_h[curr_i * width + curr_j]
                if direction == 0:
                    break
                if direction == 1:
                    q_char = q[curr_i - 1]
                    r_char = r[curr_j - 1]
                    aligned_q.append(q_char)
                    aligned_r.append(r_char)
                    score = self.get_score(q_char, r_char)
                    if (
                        self.seq_type in {"DNA", "RNA"}
                        and len(DNA_IUPAC_BASES[q_char]) == 1
                        and len(DNA_IUPAC_BASES[r_char]) == 1
                        and q_char == r_char
                    ):
                        align_str.append("|")
                        matches += 1
                        similar += 1
                    elif score > 0:
                        align_str.append(".")
                        similar += 1
                    else:
                        align_str.append(" ")
                    curr_i -= 1
                    curr_j -= 1
                elif direction == 2:
                    state = "E"
                else:
                    state = "F"

            elif state == "E":
                if curr_j <= 0:
                    break
                index = curr_i * width + curr_j
                extend = traceback_e[index]
                aligned_q.append("-")
                aligned_r.append(r[curr_j - 1])
                align_str.append(" ")
                curr_j -= 1
                if extend == 0:
                    state = "H"

            else:  # state == "F"
                if curr_i <= 0:
                    break
                index = curr_i * width + curr_j
                extend = traceback_f[index]
                aligned_q.append(q[curr_i - 1])
                aligned_r.append("-")
                align_str.append(" ")
                curr_i -= 1
                if extend == 0:
                    state = "H"

        aligned_q.reverse()
        aligned_r.reverse()
        align_str.reverse()

        str_q = "".join(aligned_q)
        str_r = "".join(aligned_r)
        str_align = "".join(align_str)
        aln_len = len(str_q)

        identity = round((matches / aln_len * 100), 2) if aln_len > 0 else 0.0
        similarity = round((similar / aln_len * 100), 2) if aln_len > 0 else 0.0
        coverage = round((aln_len / max(m, n) * 100), 2) if max(m, n) > 0 else 0.0

        return {
            "aligned_query": str_q,
            "aligned_reference": str_r,
            "alignment_string": str_align,
            "score": max_score,
            "length": aln_len,
            "identity": identity,
            "similarity": similarity,
            "coverage": coverage,
            "query_length": m,
            "reference_length": n,
            "matches": matches,
            "mismatches": aln_len - matches,
        }


# ── Détection de Variants & VCF 4.2 ──────────────────────────────────────────

class VariantCaller:
    @staticmethod
    def is_transition(ref: str, alt: str) -> bool:
        transitions = {('A', 'G'), ('G', 'A'), ('C', 'T'), ('T', 'C')}
        return (ref, alt) in transitions

    @classmethod
    def call_variants(cls, alignment: Dict[str, Any]) -> List[Dict[str, Any]]:
        query_aln = alignment.get("aligned_query", "")
        ref_aln = alignment.get("aligned_reference", "")
        if not query_aln or not ref_aln:
            return []

        variants = []
        q_pos = 1
        r_pos = 1

        for i in range(len(query_aln)):
            q_char = query_aln[i]
            r_char = ref_aln[i]

            if q_char == '-' and r_char != '-':
                # Insertion dans la référence
                variants.append({
                    "type": "deletion",  # Délétion dans la séquence query par rapport au ref
                    "position": r_pos,
                    "reference": r_char,
                    "alternate": "-",
                    "quality": 35,
                    "transition": False,
                    "description": f"Délétion de {r_char} à la pos {r_pos}"
                })
                r_pos += 1
            elif q_char != '-' and r_char == '-':
                # Insertion dans la séquence query
                variants.append({
                    "type": "insertion",
                    "position": q_pos,
                    "reference": "-",
                    "alternate": q_char,
                    "quality": 35,
                    "transition": False,
                    "description": f"Insertion de {q_char} à la pos {q_pos}"
                })
                q_pos += 1
            elif q_char != r_char and q_char != '-' and r_char != '-':
                # SNP
                is_trans = cls.is_transition(r_char, q_char)
                qual = 40 if is_trans else 30
                variants.append({
                    "type": "SNP",
                    "position": r_pos,
                    "reference": r_char,
                    "alternate": q_char,
                    "quality": qual,
                    "transition": is_trans,
                    "description": f"SNP {r_char}->{q_char} ({'Transition' if is_trans else 'Transversion'}) à la pos {r_pos}"
                })
                q_pos += 1
                r_pos += 1
            else:
                q_pos += 1
                r_pos += 1

        return variants

    @staticmethod
    def generate_vcf(variants: List[Dict[str, Any]], sample_name: str = "Nexora_Sample") -> str:
        lines = [
            "##fileformat=VCFv4.2",
            "##source=AfriBio-Core_Nexora_v2",
            "##NEXORA_note=Variants inferred from a pairwise local alignment; coordinates are alignment-reference coordinates, not genomic coordinates.",
            "##NEXORA_quality=QUAL is intentionally unset because no sequencing/read evidence is available.",
            "##NEXORA_filter=PASS is not assigned from alignment alone.",
            "##contig=<ID=alignment_reference>",
            '##INFO=<ID=TS,Number=0,Type=Flag,Description="Transition substitution inferred from alignment">',
            '##FILTER=<ID=UNVERIFIED,Description="Variant inferred from pairwise alignment without independent evidence">',
            f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample_name}"
        ]

        for idx, var in enumerate(variants, 1):
            chrom = "alignment_reference"
            pos = var.get("position", 1)
            ref = var.get("reference", "N")
            alt = var.get("alternate", "N")
            # No experimental/read-derived quality or genotype is available.
            qual = "."
            filt = "UNVERIFIED"
            info_flag = "TS" if var.get("transition") else "."
            lines.append(f"{chrom}\t{pos}\tvar_{idx}\t{ref}\t{alt}\t{qual}\t{filt}\t{info_flag}\tGT\t./.")

        return "\n".join(lines)


# ── Phylogénie : Matrices de Distance & Neighbor-Joining ──────────────────────

class PhylogeneticEngine:
    @staticmethod
    def calculate_distance(s1: str, s2: str) -> float:
        """Calcul de distance JC sur deux séquences déjà alignées de même longueur.

        Cette fonction ne tronque plus silencieusement les séquences de longueurs
        différentes : une matrice phylogénétique doit comparer des colonnes
        homologues d'un alignement multiple, pas des préfixes arbitraires.
        """
        if not s1 or not s2:
            return 1.0
        if len(s1) != len(s2):
            raise ValueError("Jukes-Cantor nécessite des séquences alignées de même longueur")
        comparable = [(a, b) for a, b in zip(s1.upper(), s2.upper()) if a in "ACGT" and b in "ACGT"]
        if not comparable:
            return 1.0
        diffs = sum(1 for a, b in comparable if a != b)
        p = diffs / len(comparable)
        if p >= 0.75:
            return 2.0  # Saturation
        if p == 0:
            return 0.0
        return round(-0.75 * math.log(1 - (4.0 / 3.0) * p), 4)

    @classmethod
    def distance_matrix(cls, sequences: List[Dict[str, str]]) -> Dict[str, Any]:
        n = len(sequences)
        matrix = [[0.0] * n for _ in range(n)]
        labels = [s.get("name", f"Seq_{i+1}") for i, s in enumerate(sequences)]

        for seq in sequences:
            if not seq.get("sequence"):
                raise ValueError("Chaque séquence phylogénétique doit contenir une séquence non vide")
        for i in range(n):
            for j in range(i + 1, n):
                d = cls.calculate_distance(sequences[i]["sequence"], sequences[j]["sequence"])
                matrix[i][j] = d
                matrix[j][i] = d

        return {"labels": labels, "matrix": matrix}

    @classmethod
    def build_nj_tree(cls, sequences: List[Dict[str, str]]) -> Dict[str, Any]:
        """Construction d'arbre Neighbor-Joining (NJ)."""
        if len(sequences) < 2:
            return {"newick": "", "svg": "", "num_sequences": len(sequences)}

        dist_data = cls.distance_matrix(sequences)
        labels = list(dist_data["labels"])
        matrix = [row[:] for row in dist_data["matrix"]]

        newick_parts = {i: name for i, name in enumerate(labels)}
        next_id = len(labels)

        while len(matrix) > 2:
            n_curr = len(matrix)
            r = [sum(matrix[i]) for i in range(n_curr)]

            min_q = float('inf')
            pair = (0, 1)

            for i in range(n_curr):
                for j in range(i + 1, n_curr):
                    q_val = (n_curr - 2) * matrix[i][j] - r[i] - r[j]
                    if q_val < min_q:
                        min_q = q_val
                        pair = (i, j)

            i, j = pair
            d_ij = matrix[i][j]

            dist_i = 0.5 * d_ij + (r[i] - r[j]) / (2 * (n_curr - 2)) if n_curr > 2 else 0.5 * d_ij
            dist_j = d_ij - dist_i

            dist_i = max(0.001, round(dist_i, 4))
            dist_j = max(0.001, round(dist_j, 4))

            merged_newick = f"({newick_parts[i]}:{dist_i},{newick_parts[j]}:{dist_j})"

            keys = list(range(n_curr))
            keys.remove(i)
            keys.remove(j)

            rem_matrix = []
            for r_idx in keys:
                row = []
                for c_idx in keys:
                    row.append(matrix[r_idx][c_idx])
                d_new = 0.5 * (matrix[i][r_idx] + matrix[j][r_idx] - d_ij)
                row.append(d_new)
                rem_matrix.append(row)

            new_last_row = []
            for c_idx in keys:
                d_new = 0.5 * (matrix[i][c_idx] + matrix[j][c_idx] - d_ij)
                new_last_row.append(d_new)
            new_last_row.append(0.0)
            rem_matrix.append(new_last_row)

            new_newick_parts = {idx: newick_parts[k] for idx, k in enumerate(keys)}
            new_newick_parts[len(keys)] = merged_newick

            matrix = rem_matrix
            newick_parts = new_newick_parts

        if len(matrix) == 2:
            d_final = max(0.001, round(matrix[0][1] / 2, 4))
            final_newick = f"({newick_parts[0]}:{d_final},{newick_parts[1]}:{d_final});"
        else:
            final_newick = f"{newick_parts[0]};"

        svg = cls.render_tree_svg(labels)

        return {
            "newick": final_newick,
            "svg": svg,
            "labels": labels,
            "matrix": dist_data["matrix"],
            "num_sequences": len(sequences)
        }

    @staticmethod
    def render_tree_svg(labels: List[str]) -> str:
        """Génère un graphique SVG de l'arbre phylogénétique."""
        w = 340
        h = 200
        n = len(labels)
        if n == 0:
            return ""

        spacing = h / (n + 1)
        center_x = 40
        center_y = h / 2

        svg_elements = [
            f'<svg width="100%" height="100%" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
            f'<circle cx="{center_x}" cy="{center_y}" r="6" fill="#00f3ff"/>',
            f'<text x="{center_x}" y="{center_y - 10}" fill="#00f3ff" font-size="11" font-weight="bold" text-anchor="middle">Racine</text>'
        ]

        colors = ['#22c55e', '#3b82f6', '#a855f7', '#eab308', '#ec4899', '#f97316']

        for i, label in enumerate(labels):
            y_pos = spacing * (i + 1)
            x_pos = w - 90
            col = colors[i % len(colors)]

            svg_elements.append(
                f'<path d="M {center_x} {center_y} C {center_x + 60} {center_y}, {x_pos - 40} {y_pos}, {x_pos} {y_pos}" '
                f'stroke="{col}" stroke-width="2" fill="none"/>'
            )
            svg_elements.append(f'<circle cx="{x_pos}" cy="{y_pos}" r="5" fill="{col}"/>')
            svg_elements.append(
                f'<text x="{x_pos + 10}" y="{y_pos + 4}" fill="#e2e8f0" font-size="11" font-family="monospace">{label}</text>'
            )

        svg_elements.append('</svg>')
        return "".join(svg_elements)
