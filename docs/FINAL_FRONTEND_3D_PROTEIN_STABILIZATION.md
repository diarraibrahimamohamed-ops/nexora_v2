# N3XORA v2 — Stabilisation finale frontend / 3D / propriétés protéiques

## Corrections

1. FASTA : branchement sur l'uploader déjà présent dans « Import FASTA » (`#dropArea`, `#fastaFile`), sans injection dans « Analyse ».
2. `updateInterpretationChart` : suppression de l'export exécuté trop tôt dans `nexora-events.js`; export après déclaration dans la page.
3. Complexité : utilisation de `#dnaVisualization` et de son parent; suppression de la référence obsolète `#dna`.
4. Historique : le renderer legacy ne plante plus lorsque `#historyList` est absent; l'onglet Historique existant reste maître de l'affichage.
5. Statut IA : garde DOM lorsque `#aiAnalysisStatus` n'existe pas.
6. Propriétés protéiques : calcul backend déterministe via BioPython `ProteinAnalysis` pour masse, pI, GRAVY, aromaticité, indice d'instabilité, coefficient d'extinction et indice d'aliphaticité.
7. Structure secondaire : H/E/C affichés uniquement comme classification heuristique transparente, pas comme prédiction structurale.
8. 3D ADN/protéine : gardes renderer/caméra, dimensions minimales, ratio d'aspect corrigé et lumières ajoutées une seule fois afin d'éviter leur accumulation lors des reconstructions.
9. Géométrie protéique legacy backend : suppression des tirages pseudo-aléatoires pour assurer la reproductibilité; ce modèle reste une visualisation et non une structure prédite.
10. Docker API/worker/Flower : `PYTHONPATH=/app` explicite pour sécuriser les imports `app.core`.
11. AfriBio-Core, alignement Smith-Waterman-Gotoh, phylogénie, VCF et les visualisations 3D sont conservés.

## Limites scientifiques

- Une visualisation 3D générée à partir d'une séquence n'est pas une structure expérimentale.
- BioPython `ProteinAnalysis` calcule des propriétés de séquence; cela ne constitue pas une prédiction de fonction, d'efficacité ou d'affinité.
- H/E/C est une classification heuristique; une vraie prédiction de structure secondaire nécessite un prédicteur dédié.
