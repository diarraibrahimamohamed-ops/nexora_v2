# Genomics / analyse de séquences — documentation scientifique

## Périmètre Nexora

Module principal : analyse ADN/ARN (composition, %GC, transcription/traduction, comparaison à une référence), upload FASTA, accès NCBI Entrez (cache PostgreSQL).

Fichiers représentatifs : `backend/app/api/v1/analysis.py`, `frontend/js/nexora-core.js`, workers `analysis_tasks.py`.

---

## Algorithmes / procédures (noms complets)

### 1. Composition nucléotidique et contenu GC

- **Nom** : comptage de fréquences / GC content  
- **Définition** : \(\%\mathrm{GC} = 100\times (n_G+n_C)/(n_A+n_C+n_G+n_T)\)  
- **Statut** : statistique descriptive standard (aucune prédiction évolutive).

### 2. Transcription ADN → ARN

- Remplacement T→U sur brin analysé (convention pédagogique).  
- Nexora ne modélise pas l’ARN polymérase, les promoteurs ni le splicing.

### 3. Traduction — code génétique standard (table des codons)

- **Nom complet** : Standard Genetic Code (code génétique universel / code IUB)  
- Lecture par triplets (3 cadres de lecture dans l’API chunkée).  
- Stop : `*`. Codons inconnus → `X` dans le helper API.  
- **Ce n’est pas** : codon usage optimization, ORF finder HMM, GeneMark, Prodigal.

### 4. « Détection de mutations »

- Comparaison **positionnelle** séquence vs référence de même index (pas d’alignement pairwise global/local type Needleman–Wunsch / Smith–Waterman / BLAST).  
- **Statut** : heuristique d’UI ; insuffisante pour appeler des variants au sens clinique (VCF, HGVS).

### 5. Traitement de grandes séquences / FASTA

- Découpage sync (~20 kb) ; au-delà du seuil navigateur (~100 kb) → file Celery `analysis`.  
- **Ingénierie**, pas un algorithme bioinformatique nommé.

### 6. NCBI Entrez

- Accès distant + cache local PostgreSQL (`NcbiSequence`).  
- Réf. outil : NCBI E-utilities — https://www.ncbi.nlm.nih.gov/books/NBK25501/

---

## Ce que Nexora ne fait pas (à ne pas écrire dans un mémoire)

- Alignements multiples (Clustal Omega, MAFFT, MUSCLE)  
- Phylogénie (IQ-TREE, RAxML, MrBayes)  
- Assemblage / mapping NGS  
- Annotation génomique complète (Prokka, Bakta)

---

## Formulation recommandée

> Nexora fournit une chaîne pédagogique d’analyse de composition, transcription/traduction selon le code génétique standard, et comparaison positionnelle à une référence. Les analyses lourdes sont déléguées au serveur de façon asynchrone au-delà d’un seuil de longueur.
