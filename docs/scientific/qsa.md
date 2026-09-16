# QSA / HSA — documentation scientifique

## Clarification de nomenclature

| Ancien | Problème | Nom retenu |
|--------|----------|------------|
| **QSA** — Quantum Stability Analysis / Quantum Sequence Analyzer | Aucun calcul de **mécanique quantique** (pas d’équation de Schrödinger, DFT, Hartree–Fock, semi-empirique QM/MM) | **HSA** — **Heuristic Stability Analysis** (Analyse Heuristique de Stabilité) |

Le fichier historique `quantum_analyzer.py` et l’UI `qsa.html` portent un vocabulaire **trompeur** pour un jury de bioinformatique. La reformulation HSA est obligatoire pour un mémoire crédible.

Implémentation actuelle cible : `backend/app/core/hsa/stability_analyzer.py`.

---

## Ce que calcule réellement le code HSA-2.0

Entrées : séquence (caractères A/C/G/T/U comptés) + `temperature`, `ph`, `stress_factor`.

| Sortie | Construction | Statut scientifique |
|--------|--------------|---------------------|
| `gc_content` | Comptage G+C | OK descriptif |
| `gc_content`, `gc_skew` | Comptages GC et \((G-C)/(G+C)\) | Descripteurs descriptifs |
| `shannon_entropy_bits` | \(-\sum p_b\log_2(p_b)\) | Descripteur classique de complexité |
| `nearest_neighbor_tm_celsius` | `Biopython Tm_NN`, DNA_NN3, Na=50 mM, \(C_1=C_2=250\) nM | Modèle nearest-neighbor pour fenêtres courtes |
| `local_heterogeneity` | Étendue locale GC/Tm normalisée | Indice descriptif propre à HSA |
| `vulnerability_index`, `global_score` | Combinaison bornée documentée ci-dessous | Indice composite heuristique, non validé |

La sortie `mutation_prob` vaut désormais `null`: une probabilité biologique ne
peut pas être déduite de cette séquence et d'un facteur de stress arbitraire.
Le pH est rapporté mais non utilisé, faute de modèle de tampon, force ionique,
pKa et modifications chimiques.

## Formulation mathématique

\[
f_{GC}=\frac{G+C}{N},\qquad E=-\sum_b p_b\log_2(p_b),\qquad E_n=\min(1,E/2)
\]

\[
GC_{skew}=\frac{G-C}{G+C},\qquad
m_T=\sigma\left(\frac{T_m-T_{env}}{5}\right),\quad
\sigma(x)=\frac{1}{1+e^{-x}}
\]

L'hétérogénéité locale est :

\[
h=\min\left(1,\frac{\Delta GC}{2}+\frac{\min(1,\Delta T_m/50)}{2}\right)
\]

L'indice retourné est :

\[
V=100\,\min(1,s[0.30(1-E_n)+0.20\min(1,|GC_{skew}|)
 +0.25(1-m_T)+0.25h]),\qquad S=100-V
\]

où \(s\) est `stress_factor`, borné à 2. Ces coefficients définissent un
indice transparent Nexora; ils ne sont pas des constantes biologiques publiées.

---

## Nearest-neighbor et références

- SantaLucia J. *A unified view of polymer, dumbbell, and oligonucleotide DNA nearest-neighbor thermodynamics*. PNAS. 1998;95:1460-1465. DOI: [10.1073/pnas.95.4.1460](https://doi.org/10.1073/pnas.95.4.1460).
- Vinga S. *Information theory applications for biological sequence analysis*. Brief Bioinform. 2014;15:376-389. DOI: [10.1093/bib/bbt053](https://doi.org/10.1093/bib/bbt053).
- Biopython `Bio.SeqUtils.MeltingTemp.Tm_NN`: https://biopython.org/docs/latest/api/Bio.SeqUtils.MeltingTemp.html

HSA ne prétend pas résoudre la thermodynamique d'un génome long; les fenêtres
nearest-neighbor sont utilisées comme descripteurs locaux.

---

## Ce qu’il ne faut pas écrire

- « Analyse quantique », « superposition quantique de l’ADN », « prédiction QM de mutations ».  
- Validation comme outil prédictif de stabilité génomique sans benchmark.

## Formulation mémoire

> Le module anciennement nommé QSA est HSA-2.0, un indice heuristique transparent combinant descripteurs GC, entropie, GC-skew, Tm nearest-neighbor locale et hétérogénéité. Il ne constitue ni une analyse quantique, ni une probabilité de mutation, ni une validation thermodynamique complète.
