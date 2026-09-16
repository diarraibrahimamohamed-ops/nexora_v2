# ASP — Agrégation Statistique des Poses

> Document court. Rapport exhaustif et audit code↔littérature : **`/RAPPORT_ASP.md`** à la racine.

## Identité

| | |
|--|--|
| **Nom complet retenu** | Agrégation Statistique des Poses (ASP) |
| **Ancien nom code** | « Amarrage par Superposition Probabiliste » — **à ne plus utiliser** |
| **Nature** | Post-traitement des poses **AutoDock Vina** |
| **Pas** | Une méthode de docking autonome |

## Formule codée (`vina_runner.py`)

\[
\Delta G_{\mathrm{eff}} = -RT \ln \sum_i \exp(-s_i/RT),\quad
RT\approx 0{,}593\ \mathrm{kcal\,mol^{-1}}\ (298\,\mathrm{K}),\quad
s_i=\text{score Vina}.
\]

## Distinction critique

| Quantité | Formule | Dans Nexora ? |
|----------|--------|---------------|
| Log-sum-exp / −RT ln Z | \(-RT\ln\sum e^{-s_i/RT}\) | **Oui** (= ASP actuel) |
| Moyenne de Boltzmann des scores | \(\sum s_i e^{-\beta s_i}/\sum e^{-\beta s_i}\) | **Non** |
| ΔG° Implicit Ligand Theory | ratio de fonctions de partition + état standard | **Non** |

Conséquence : \(\Delta G_{\mathrm{eff}} \le \min_i s_i\) toujours.

## Sources d’inspiration (pas d’équivalence)

- Trott & Olson 2010 (Vina)  
- Implicit Ligand Theory, *J. Chem. Phys.* 2012  
- Exhaustive docking / partition functions, *J. Phys. Chem. B* 2012  
- Paulsen & Anderson 2009 (pondération d’ensembles — autre formule / autre objet)

## Limite une ligne

Scores Vina empiriques + PDB externe ou modèle ColabFold ⇒ ASP = **indicateur composite**, pas ΔG expérimental.


### Clustering des poses
Le clustering ASP compare les coordonnées des atomes lourds dans le repère du récepteur, sans superposition Kabsch. Cette convention évite d'annuler les différences de placement spatial entre modes de liaison distincts. La correspondance des atomes suit l'ordre des poses produites par Vina ; les symétries de ligand ne sont pas encore remappées automatiquement.
