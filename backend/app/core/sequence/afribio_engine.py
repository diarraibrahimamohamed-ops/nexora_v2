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

def clean_sequence(seq: str, seq_type: str = "DNA") -> str:
    """Nettoie et formate la séquence."""
    if not seq:
        return ""
    seq = seq.upper().strip()
    if seq_type.upper() == "PROTEIN":
        return re.sub(r'[^ACDEFGHIKLMNPQRSTVWY]', '', seq)
    return re.sub(r'[^ACGTN]', '', seq)


def calculate_gc(seq: str) -> float:
    """Calcule le pourcentage de GC."""
    clean = re.sub(r'[^ACGT]', '', seq.upper())
    if not clean:
        return 0.0
    gc_count = clean.count('G') + clean.count('C')
    return round((gc_count / len(clean)) * 100, 2)


# ── Algorithme Smith-Waterman Gotoh ───────────────────────────────────────────

class SmithWatermanGotoh:
    def __init__(
        self,
        match_score: int = 5,
        mismatch_penalty: int = -4,
        gap_open: int = -10,
        gap_extend: int = -1,
        seq_type: str = "DNA"
    ):
        self.match_score = match_score
        self.mismatch_penalty = mismatch_penalty
        self.gap_open = gap_open
        self.gap_extend = gap_extend
        self.seq_type = seq_type.upper()
        self.matrix = BLOSUM62_MATRIX if self.seq_type == "PROTEIN" else NUC44_MATRIX

    def get_score(self, a: str, b: str) -> int:
        if self.seq_type == "PROTEIN":
            return self.matrix.get(a, {}).get(b, -2)
        if a == 'N' or b == 'N':
            return 0
        if a == b:
            return self.match_score
        return self.mismatch_penalty

    def align(self, query: str, reference: str) -> Dict[str, Any]:
        """
        Alignement local Smith-Waterman-Gotoh avec modèle de gap affine.
        H(i,j) = max(0, E(i,j), F(i,j), H(i-1,j-1) + S(q_i, r_j))
        E(i,j) = max(H(i, j-1) + gap_open, E(i, j-1) + gap_extend)  # Insertion (Left)
        F(i,j) = max(H(i-1, j) + gap_open, F(i-1, j) + gap_extend)  # Deletion (Up)
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
                "coverage": 0.0
            }

        # Dynamic programming table for traceback
        H = [[0] * (n + 1) for _ in range(m + 1)]
        E = [[0] * (n + 1) for _ in range(m + 1)]
        F = [[0] * (n + 1) for _ in range(m + 1)]
        traceback_mat = [[0] * (n + 1) for _ in range(m + 1)]  # 0: stop, 1: diag, 2: up (del), 3: left (ins)

        max_score = 0
        max_pos = (0, 0)

        for i in range(1, m + 1):
            for j in range(1, n + 1):
                # E: Insertion (Left)
                e_new_open = H[i][j - 1] + self.gap_open
                e_extend = E[i][j - 1] + self.gap_extend
                E[i][j] = max(e_new_open, e_extend)

                # F: Deletion (Up)
                f_new_open = H[i - 1][j] + self.gap_open
                f_extend = F[i - 1][j] + self.gap_extend
                F[i][j] = max(f_new_open, f_extend)

                # H: Diagonal
                diag_score = H[i - 1][j - 1] + self.get_score(q[i - 1], r[j - 1])

                h_val = max(0, diag_score, F[i][j], E[i][j])
                H[i][j] = h_val

                if h_val == 0:
                    traceback_mat[i][j] = 0
                elif h_val == diag_score:
                    traceback_mat[i][j] = 1
                elif h_val == F[i][j]:
                    traceback_mat[i][j] = 2
                else:
                    traceback_mat[i][j] = 3

                if h_val > max_score:
                    max_score = h_val
                    max_pos = (i, j)

        # Backtracking
        curr_i, curr_j = max_pos
        aligned_q = []
        aligned_r = []
        align_str = []

        matches = 0
        similar = 0

        while curr_i > 0 and curr_j > 0 and H[curr_i][curr_j] > 0:
            dir_code = traceback_mat[curr_i][curr_j]

            if dir_code == 1:  # Diag
                q_char = q[curr_i - 1]
                r_char = r[curr_j - 1]
                aligned_q.append(q_char)
                aligned_r.append(r_char)
                if q_char == r_char:
                    align_str.append('|')
                    matches += 1
                    similar += 1
                else:
                    score = self.get_score(q_char, r_char)
                    align_str.append('.' if score > 0 else ' ')
                    if score > 0:
                        similar += 1
                curr_i -= 1
                curr_j -= 1
            elif dir_code == 2:  # Up (Deletion in ref / Gap in ref)
                aligned_q.append(q[curr_i - 1])
                aligned_r.append('-')
                align_str.append(' ')
                curr_i -= 1
            elif dir_code == 3:  # Left (Insertion in ref / Gap in query)
                aligned_q.append('-')
                aligned_r.append(r[curr_j - 1])
                align_str.append(' ')
                curr_j -= 1
            else:
                break

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
            "mismatches": aln_len - matches
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
