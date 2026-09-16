# N3XORA v2 — Audit ASP / Boltzmann — Phase 8

## Objet
Cette phase corrige un problème méthodologique important du clustering ASP : l'utilisation d'un RMSD avec superposition de Kabsch pour comparer des poses de docking.

## Correction principale
Les poses Vina sont déjà exprimées dans le même repère cartésien que le récepteur. Pour le clustering des modes de liaison, appliquer Kabsch supprime les différences de translation et de rotation entre deux placements du ligand. Deux poses pouvant occuper des régions spatiales différentes peuvent alors devenir artificiellement quasi identiques après superposition.

Le clustering ASP utilise désormais un **RMSD heavy-atom en repère fixe**, sans superposition Kabsch. Ainsi, une différence spatiale réelle entre deux poses reste visible.

## Contrat actuel
- comparaison uniquement des atomes lourds ;
- coordonnées comparées dans le repère du récepteur ;
- seuil par défaut : 2,0 Å ;
- tri par score Vina croissant ;
- le meilleur score de chaque cluster est le représentant ;
- agrégation Boltzmann-like effectuée séparément dans chaque poche ;
- aucune partition-fonction globale artificielle entre poches.

## Robustesse des données d'entrée
Le parseur Vina expose maintenant explicitement `element` et `atom_name`, ce qui permet d'exclure correctement les hydrogènes du clustering. Les scores non finis et les poses sans coordonnées lourdes finies sont rejetés par l'agrégateur.

## Limitation restante
La correspondance atomique repose sur l'ordre des atomes fourni par le docking. Le module ne réalise pas encore de remappage explicite des atomes chimiquement équivalents pour traiter toutes les symétries de ligands. Le résultat doit donc être présenté comme un clustering RMSD déterministe et non comme une métrique de similarité parfaite pour toutes les topologies symétriques.

## Validation logicielle ajoutée
Les tests couvrent maintenant :
- invariance de l'agrégation pour une pose unique ;
- stabilité des poids de Boltzmann ;
- invariance du `log-mean-exp` ;
- séparation de poches distinctes ;
- séparation de deux poses translatées de 10 Å dans le même site ;
- exclusion des hydrogènes ;
- rejet des scores non finis.

## Conclusion
Le changement rend le clustering cohérent avec l'objectif de distinguer des modes de liaison dans le repère structural du récepteur. Il ne démontre en lui-même **aucune supériorité scientifique d'ASP sur Vina TOP1**. Cette question reste du ressort du benchmark externe réel.
