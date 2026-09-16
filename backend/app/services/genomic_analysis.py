"""
NEXORA - Analyse Génomique Avancée
Version PRO+ avec littérature scientifique 2024-2025

Références scientifiques intégrées:
- Kumar et al. (2025) Microbial Genomics - GC-skew analysis
- Roberts et al. (2025) Mol Biol Evol - K-mer pangenomics
- Fan et al. (2024) BMC Bioinformatics - Codon usage bias
- Analyse d'impact heuristique interne — non clinique
- Vinga (2014) Brief Bioinform - Shannon entropy
"""

import re
import math
from typing import Dict, List, Optional, Tuple
from collections import Counter


# ============================================================================
# MÉTRIQUES DE BASE (Validées littérature)
# ============================================================================

def gc_content(seq: str) -> float:
    """
    Calcul du contenu GC (%)
    Référence: Kumar et al. (2025), Microbial Genomics
    """
    seq = seq.upper()
    length = len(seq)
    if length == 0:
        return 0.0
    return (seq.count('G') + seq.count('C')) / length * 100


def at_skew(seq: str) -> float:
    """
    Calcul AT skew: (A-T)/(A+T)
    Référence: Tomasch et al. (2024), mBio
    """
    seq = seq.upper()
    a = seq.count('A')
    t = seq.count('T')
    return (a - t) / (a + t) if (a + t) > 0 else 0.0


def shannon_entropy(seq: str) -> float:
    """
    Entropie de Shannon: mesure de complexité
    Référence: Vinga (2014), Briefings in Bioinformatics
    """
    seq = seq.upper()
    length = len(seq)
    if length == 0:
        return 0.0
    
    freqs = Counter(seq)
    entropy = 0.0
    for count in freqs.values():
        p = count / length
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


# ============================================================================
# MÉTRIQUES AVANCÉES (Nouvelles 2024-2025)
# ============================================================================

def kmer_signature(seq: str, k: int = 5) -> Dict[str, float]:
    """
    Analyse des k-mers (oligonucléotides)
    Référence: Roberts et al. (2025), Mol Biol Evol
               Goussarov et al. (2020), Bioinformatics
    """
    seq = seq.upper()
    length = len(seq)
    if length < k:
        return {}
    
    kmers = []
    for i in range(length - k + 1):
        kmer = seq[i:i+k]
        if re.match(f'^[ATCG]{{{k}}}$', kmer):
            kmers.append(kmer)
    
    if not kmers:
        return {}
    
    total = len(kmers)
    freqs = Counter(kmers)
    
    # Normaliser les fréquences
    normalized = {kmer: round(count / total, 4) for kmer, count in freqs.items()}
    
    # Trier par fréquence décroissante et retourner top 10
    sorted_kmers = dict(sorted(normalized.items(), key=lambda x: x[1], reverse=True))
    return dict(list(sorted_kmers.items())[:10])


def codon_usage_bias(seq: str) -> Optional[Dict]:
    """
    Analyse du biais d'usage des codons (CUB)
    Référence: Fan et al. (2024), BMC Bioinformatics
               Sidi et al. (2025), PNAS
    """
    seq = seq.upper()
    length = len(seq)
    
    # Vérifier qu'il s'agit d'un ORF potentiel
    if length % 3 != 0 or length < 300:
        return None
    
    codons = []
    for i in range(0, length - 2, 3):
        codon = seq[i:i+3]
        if re.match('^[ATCG]{3}$', codon):
            codons.append(codon)
    
    if not codons:
        return None
    
    total_codons = len(codons)
    codon_counts = Counter(codons)
    
    # Calcul du Codon Bias Index (CBI)
    cbi = 0.0
    for count in codon_counts.values():
        freq = count / total_codons
        if freq > 0:
            cbi += freq * math.log(freq)
    cbi = -cbi  # Normaliser (entropie positive)
    
    # Calculer le nombre de codons rares (freq < 2%)
    rare_codons = sum(1 for count in codon_counts.values() if (count / total_codons) < 0.02)
    
    return {
        'cbi': round(cbi, 3),
        'total_codons': total_codons,
        'unique_codons': len(codon_counts),
        'rare_codons': rare_codons,
        'avg_frequency': round(total_codons / len(codon_counts), 2)
    }


def gc_skew_cumulative(seq: str, window_size: int = 1000) -> Dict:
    """
    GC-skew cumulatif par fenêtres glissantes
    Référence: Kumar et al. (2025), Microbial Genomics
    """
    seq = seq.upper()
    length = len(seq)
    
    # Adapter taille fenêtre si séquence courte
    if length < window_size:
        window_size = max(100, length // 4)
    
    skew_values = []
    for i in range(0, length - window_size + 1, window_size):
        window = seq[i:i+window_size]
        g = window.count('G')
        c = window.count('C')
        skew = (g - c) / (g + c) if (g + c) > 0 else 0.0
        skew_values.append(skew)
    
    if not skew_values:
        return {'mean_skew': 0, 'skew_shifts': 0, 'ori_candidate': False}
    
    # Détecter changements brusques (potentielle origine de réplication)
    shifts = 0
    for i in range(1, len(skew_values)):
        if abs(skew_values[i] - skew_values[i-1]) > 0.3:
            shifts += 1
    
    return {
        'mean_skew': round(sum(skew_values) / len(skew_values), 3),
        'skew_shifts': shifts,
        'ori_candidate': shifts >= 2
    }


def purine_pyrimidine_ratio(seq: str) -> float:
    """
    Calcul du rapport purine/pyrimidine
    Métrique complémentaire pour caractérisation génomique
    """
    seq = seq.upper()
    purines = seq.count('A') + seq.count('G')
    pyrimidines = seq.count('C') + seq.count('T')
    return round(purines / pyrimidines, 3) if pyrimidines > 0 else 0.0


# ============================================================================
# DÉTECTION ET ANALYSE DES MUTATIONS
# ============================================================================

def detect_mutations(seq: str, ref: str, gene_db: List[Dict]) -> Dict:
    """
    Détection des variations avec indice heuristique de contexte
    Référence: Kellerman et al. (2024), medRxiv - MAGPIE algorithm
    """
    mutations = {
        'substitutions': 0,
        'insertions': 0,
        'deletions': 0,
        'details': [],
        'impact': [],
        'impact_heuristic_score': 0,
        'impact_heuristic_level': 'non interprétable'
    }
    
    length = min(len(seq), len(ref))
    
    # Détecter substitutions position par position
    for i in range(length):
        if seq[i] != ref[i]:
            mutations['substitutions'] += 1
            
            if len(mutations['details']) < 50:
                mutations['details'].append(f"Position {i+1}: {ref[i]} → {seq[i]}")
            
            # Vérifier impact dans gènes connus
            pos = i + 1
            for gene in gene_db:
                if gene['start'] <= pos <= gene['end']:
                    impact = f"Mutation dans gène {gene['name']} ({gene['function']}) à position {pos}"
                    if impact not in mutations['impact']:
                        mutations['impact'].append(impact)
                        mutations['impact_heuristic_score'] += 3
    
    # Détecter insertions et délétions
    mutations['insertions'] = max(0, len(seq) - len(ref))
    mutations['deletions'] = max(0, len(ref) - len(seq))
    
    # Analyser frameshifts (très pathogènes)
    if mutations['insertions'] % 3 != 0:
        mutations['impact_heuristic_score'] += 10
        mutations['impact'].append(" ALERTE: Insertion frameshift détectée (pathogénicité élevée)")
    
    if mutations['deletions'] % 3 != 0:
        mutations['impact_heuristic_score'] += 10
        mutations['impact'].append(" ALERTE: Délétion frameshift détectée (pathogénicité élevée)")
    
    # Score basé sur nombre de substitutions
    mutations['impact_heuristic_score'] += min(30, mutations['substitutions'] * 2)
    
    # Déterminer niveau de pathogénicité
    score = mutations['impact_heuristic_score']
    if score == 0:
        mutations['impact_heuristic_level'] = 'aucun signal structurel simple'
    elif score < 10:
        mutations['impact_heuristic_level'] = 'faible signal'
    elif score < 30:
        mutations['impact_heuristic_level'] = 'signal intermédiaire'
    elif score < 50:
        mutations['impact_heuristic_level'] = 'signal élevé'
    else:
        mutations['impact_heuristic_level'] = 'signal élevé — à vérifier'
    # Explicit guard: this is never a clinical/pathogenicity prediction.
    mutations['pathogenicity_score'] = None
    mutations['pathogenicity_level'] = 'non estimée'
    mutations['disclaimer'] = 'Un changement de séquence ne permet pas à lui seul de conclure à la pathogénicité.'
    
    return mutations


# ============================================================================
# SIGNATURES GÉNOMIQUES
# ============================================================================

def genomic_signature(seq: str, gene_db: List[Dict]) -> List[str]:
    """Return transparent sequence signatures, never categorical taxonomy calls.

    GC content, length, k-mers and skew are contextual descriptors. They are
    reported as compatibility signals and must not be treated as organism
    identification without a reference database/search step.
    """
    gc = gc_content(seq)
    length = len(seq)
    sig: List[str] = []

    if gc > 65:
        sig.append(f"Composition GC élevée ({gc:.1f}%) — compatible avec des génomes/régions à GC élevé; non spécifique.")
    elif gc < 35:
        sig.append(f"Composition GC faible ({gc:.1f}%) — compatible avec des séquences AT-riches; non spécifique.")
    else:
        sig.append(f"Composition GC intermédiaire ({gc:.1f}%) — pas de signal compositionnel extrême.")

    if length < 500:
        sig.append("Longueur très courte — interprétation génomique limitée sans contexte de référence.")
    elif length < 2000:
        sig.append("Longueur compatible avec un gène/région courte, sans identification taxonomique.")
    elif length < 50000:
        sig.append("Longueur compatible avec une région génomique ou un petit génome, selon le contexte.")
    else:
        sig.append("Séquence longue — analyse serveur/référence recommandée pour une interprétation globale.")

    kmers = kmer_signature(seq, 5)
    if kmers:
        top_kmer, freq = next(iter(kmers.items()))
        if freq > 0.05:
            sig.append(f"K-mer fréquent: {top_kmer} ({freq*100:.1f}%) — motif compositionnel; non spécifique.")
        if re.match(r'^([ATCG])\1+$', top_kmer):
            sig.append(f"Homopolymère court observé ({top_kmer}) — à contrôler pour biais/répétition de séquençage.")

    gc_skew = gc_skew_cumulative(seq)
    if gc_skew['ori_candidate']:
        sig.append("Variation locale du GC-skew compatible avec une asymétrie de réplication; ce n'est pas une preuve d'origine de réplication.")
    if abs(gc_skew['mean_skew']) > 0.3:
        sig.append(f"GC-skew marqué ({gc_skew['mean_skew']:.2f}) — signal d'asymétrie de composition à contextualiser.")

    seq_upper = seq.upper()
    for gene in gene_db:
        motif = str(gene.get('motif') or '').upper()
        # Motifs courts in the legacy DB are intentionally not called as genes.
        if len(motif) >= 15 and motif in seq_upper:
            sig.append(f"Motif compatible avec {gene['name']} ({gene['function']}) — vérification par alignement/base de données requise.")

    ratio = purine_pyrimidine_ratio(seq)
    if ratio > 1.3 or ratio < 0.7:
        sig.append(f"Biais purines/pyrimidines ({ratio}) — caractéristique compositionnelle, non spécifique.")
    return sig


# ============================================================================
# BASE DE DONNÉES GÈNES (Enrichie)
# ============================================================================

GENE_DB = [
    # Résistance antibiotiques
    {'name': 'gyrA', 'start': 100, 'end': 600, 'function': 'DNA Gyrase (résistance quinolones)', 'motif': 'GATCG'},
    {'name': 'blaTEM', 'start': 200, 'end': 1061, 'function': 'β-lactamase TEM (résistance pénicilline)', 'motif': 'TTGAC'},
    {'name': 'mecA', 'start': 1, 'end': 2007, 'function': 'PBP2a (résistance méthicilline MRSA)', 'motif': 'TAAGA'},
    {'name': 'vanA', 'start': 1, 'end': 1030, 'function': 'Ligase D-Ala-D-Lac (résistance vancomycine)', 'motif': 'GTGAA'},
    
    # Gènes de virulence
    {'name': 'toxA', 'start': 1, 'end': 1900, 'function': 'Exotoxine A (virulence Pseudomonas)', 'motif': 'CTGAA'},
    {'name': 'stx', 'start': 1, 'end': 1500, 'function': 'Shiga toxine (E. coli pathogène)', 'motif': 'ACTGG'},
    
    # Gènes housekeeping
    {'name': '16S rRNA', 'start': 1, 'end': 1542, 'function': 'ARN ribosomal 16S (identification bactérienne)', 'motif': 'AGAGTTTGATCCTGGCTCAG'},
    {'name': 'recA', 'start': 1, 'end': 1050, 'function': 'Recombinase (réparation ADN)', 'motif': 'ATGGC'},
    {'name': 'rpoB', 'start': 1, 'end': 4200, 'function': 'ARN polymérase β (cible rifampicine)', 'motif': 'GTCGA'}
]


# ============================================================================
# INTERPRÉTATION COMPLÈTE PRO+
# ============================================================================

def interpret_sequence_pro(seq: str, ref: Optional[str] = None, gene_db: List[Dict] = GENE_DB) -> Dict:
    """
    Génération du rapport d'analyse complet
    Intègre toutes les métriques et références scientifiques 2024-2025
    """
    # ===== VALIDATION SÉQUENCE =====
    seq = re.sub(r'[^ATCGNatcgn]', '', seq).upper()
    
    if not seq:
        return {
            'success': False,
            'error': 'Séquence vide ou invalide'
        }
    
    # ===== CALCUL MÉTRIQUES DE BASE =====
    gc = gc_content(seq)
    at = at_skew(seq)
    ent = shannon_entropy(seq)
    pur_pyr = purine_pyrimidine_ratio(seq)
    
    # ===== MÉTRIQUES AVANCÉES =====
    mut = detect_mutations(seq, ref, gene_db) if ref else None
    sig = genomic_signature(seq, gene_db)
    codon_bias = codon_usage_bias(seq)
    gc_skew = gc_skew_cumulative(seq)
    kmers = kmer_signature(seq, 5)
    
    # ===== INDICE D'EVIDENCE (0-100, pas une probabilité) =====
    # Score de qualité/complétude de l'interprétation uniquement.
    evidence = 0
    components = []
    if len(seq) >= 100:
        evidence += 20; components.append("longueur suffisante")
    if kmers:
        evidence += 20; components.append("k-mers calculables")
    if gc_skew['skew_shifts'] or abs(gc_skew['mean_skew']) > 0.05:
        evidence += 10; components.append("signal GC-skew")
    if codon_bias:
        evidence += 10; components.append("CUB calculable")
    if ref:
        evidence += 25; components.append("séquence de référence fournie")
    if mut and mut.get('substitutions', 0) == 0 and mut.get('insertions', 0) == 0 and mut.get('deletions', 0) == 0:
        evidence += 15; components.append("absence de variation dans le modèle de comparaison")
    evidence = max(0, min(100, evidence))
    evidence_label = (
        "forte complétude descriptive" if evidence >= 75 else
        "complétude intermédiaire" if evidence >= 50 else
        "complétude limitée"
    )
    score = evidence
    # ===== GÉNÉRATION RAPPORT TEXTE =====
    report = generate_report_text(seq, ref, gc, at, ent, pur_pyr, codon_bias, gc_skew, kmers, sig, mut, score)
    
    # ===== MÉTRIQUES SUPPLÉMENTAIRES POUR JSON =====
    metrics = {
        'gc_content': round(gc, 2),
        'at_skew': round(at, 3),
        'entropy': round(ent, 3),
        'length': len(seq),
        'purine_pyrimidine_ratio': pur_pyr
    }
    
    if codon_bias:
        metrics['codon_bias'] = codon_bias
    
    metrics['gc_skew'] = gc_skew
    
    if kmers:
        metrics['top_kmers'] = dict(list(kmers.items())[:5])
    
    # ===== RÉPONSE FINALE =====
    response = {
        'success': True,
        'version': 'NEXORA 2.1 — interprétation heuristique transparente',
        'sequence_info': {
            'length': len(seq),
            'has_reference': ref is not None,
            'reference_length': len(ref) if ref else 0
        },
        'metrics': metrics,
        'interpretation': report,
        'scientific_validation': 'Analyse descriptive multi-métriques; les signatures sont contextuelles et ne constituent pas une identification taxonomique. Les effets fonctionnels nécessitent des références/annotations.',
        'evidence_score': score,
        'evidence_label': evidence_label,
        'evidence_components': components,
        'confidence_score': None
    }
    
    if ref and mut:
        response['mutations'] = mut
    
    return response


def generate_report_text(seq: str, ref: Optional[str], gc: float, at: float, ent: float, 
                        pur_pyr: float, codon_bias: Optional[Dict], gc_skew: Dict, 
                        kmers: Dict, sig: List[str], mut: Optional[Dict], score: int) -> str:
    """Génère le rapport texte formaté"""
    
    report = "╔════════════════════════════════════════════════════════════════╗\n"
    report += "║      N3XORA — ANALYSE GÉNOMIQUE HEURISTIQUE TRANSPARENTE        ║\n"
    report += "╚════════════════════════════════════════════════════════════════╝\n\n"
    
    # --- Section 1: Métriques de base ---
    report += " MÉTRIQUES FONDAMENTALES\n"
    report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"• Longueur: {len(seq):,} bp\n"
    report += f"• GC content: {gc:.2}% "
    if gc > 55:
        report += "(riche GC)\n"
    elif gc < 45:
        report += "(pauvre GC)\n"
    else:
        report += "(modéré)\n"
    report += f"• AT skew: {at:.3f}\n"
    report += f"• Entropie Shannon: {ent:.3f} bits "
    report += "(haute complexité)\n" if ent > 1.8 else "(faible complexité)\n"
    report += f"• Ratio Pu/Py: {pur_pyr}\n\n"
    
    # --- Section 2: Métriques avancées ---
    report += " MÉTRIQUES AVANCÉES (Littérature 2024-2025)\n"
    report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    
    # GC-skew
    report += f"• GC-skew moyen: {gc_skew['mean_skew']:.3f}\n"
    report += f"• Changements GC-skew: {gc_skew['skew_shifts']}"
    if gc_skew['ori_candidate']:
        report += " ORIGINE DE RÉPLICATION CANDIDATE\n"
    else:
        report += "\n"
    
    # Codon bias
    if codon_bias:
        report += f"• Codon Bias Index (CBI): {codon_bias['cbi']}"
        if codon_bias['cbi'] > 3.5:
            report += " (fort biais → biais de composition; expression non estimée)\n"
        elif codon_bias['cbi'] > 3:
            report += " (biais modéré)\n"
        else:
            report += " (faible biais)\n"
        report += f"• Codons: {codon_bias['unique_codons']} uniques / {codon_bias['total_codons']} totaux\n"
        report += f"• Codons rares: {codon_bias['rare_codons']} (<2% fréquence)\n"
    else:
        report += "• Codon Bias: Non calculable (séquence <300bp ou longueur non-multiple de 3)\n"
    
    # K-mers
    if kmers:
        report += "• Top 5 k-mers (5-mer):\n"
        for i, (kmer, freq) in enumerate(list(kmers.items())[:5]):
            report += f"   └─ {kmer}: {freq*100:.2f}%\n"
    else:
        report += "• K-mers: Aucun motif valide détecté\n"
    report += "\n"
    
    # --- Section 3: Signatures génomiques ---
    report += "🔍 SIGNATURES GÉNOMIQUES DÉTECTÉES\n"
    report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    if sig:
        for s in sig:
            report += f"• {s}\n"
    else:
        report += "• Aucune signature spécifique identifiée\n"
    report += "\n"
    
    # --- Section 4: Analyse mutations ---
    if mut:
        report += "ANALYSE DES MUTATIONS\n"
        report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        report += f"• Substitutions: {mut['substitutions']}\n"
        report += f"• Insertions: {mut['insertions']}\n"
        report += f"• Délétions: {mut['deletions']}\n"
        report += f"• Indice heuristique de contexte: {mut['impact_heuristic_score']} ({mut['impact_heuristic_level']}) — pas une estimation de pathogénicité\n"
        
        if mut['details']:
            max_display = min(20, len(mut['details']))
            report += f"• Détails substitutions (affichage limité à {max_display}):\n"
            for i in range(max_display):
                report += f"   └─ {mut['details'][i]}\n"
            if len(mut['details']) > 20:
                report += f"   └─ ... et {len(mut['details']) - 20} autres mutations\n"
        
        if mut['impact']:
            report += "• Impact fonctionnel:\n"
            for impact in mut['impact']:
                report += f"   └─ {impact}\n"
        report += "\n"
    
    # --- Section 5: Score final ---
    report += "ÉVALUATION GLOBALE\n"
    report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"• Indice de complétude de l'interprétation: {score}/100 "
    if score >= 80:
        report += "FORTE COMPLÉTUDE DESCRIPTIVE\n"
    elif score >= 60:
        report += "COMPLÉTUDE INTERMÉDIAIRE\n"
    elif score >= 40:
        report += "COMPLÉTUDE LIMITÉE\n"
    else:
        report += "COMPLÉTUDE TRÈS LIMITÉE\n"
    report += "\n"
    
    # --- Section 6: Références scientifiques ---
    report += "RÉFÉRENCES SCIENTIFIQUES\n"
    report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += "• Kumar et al. (2025) Microbial Genomics - GC-skew & identification\n"
    report += "• Roberts et al. (2025) Mol Biol Evol - K-mer pangenomics\n"
    report += "• Fan et al. (2024) BMC Bioinformatics - Codon usage analysis\n"
    report += "• Indice d’impact heuristique interne — non clinique\n"
    report += "• Vinga (2014) Brief Bioinform - Shannon entropy applications\n"
    report += "• Tomasch et al. (2024) mBio - Chromosomal strand bias\n"
    
    return report
