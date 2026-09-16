# N3XORA — Research Phase 6: couplage structure–docking

## Objectif

Comparer la géométrie des poses obtenues par Vina pour un même ligand sur une
cible WT et sa cible variant, après mise dans un repère structurel commun.

## Méthode

1. Les deux runs doivent utiliser le même ligand et un protocole apparié.
2. Les structures WT et variant doivent déjà être acceptées par la Phase 3.
3. Les protéines sont superposées par C-alpha avec Kabsch.
4. Les coordonnées de la variante sont transformées dans le repère WT.
5. Les poses Vina sont comparées dans ce repère fixe; aucun Kabsch n'est
   appliqué aux poses elles-mêmes.
6. Le rapport fournit le RMSD de pose, les centroïdes et un fingerprint de
   résidus en contact basé sur un cutoff heavy-atom de 4.0 Å par défaut.
7. Les contacts ne sont pas nommés hydrogène/salt bridge/pi-stacking sans un
   moteur d'interactions dédié.

## Ce que le rapport ne permet pas de conclure

- un RMSD de pose ne démontre pas une bonne ou mauvaise affinité ;
- un changement de score Vina n'est pas une ΔG expérimentale ;
- une différence de contacts ne démontre pas une résistance ;
- une similarité de poche ne démontre pas que les sites sont biologiquement
  identiques.

## Justification méthodologique

L'analyse des poses peut être enrichie par la présence/absence des interactions
avec des résidus clés ; la littérature a montré que des mesures basées sur les
interactions peuvent parfois diverger du seul classement par RMSD. Pour une
classification détaillée des types d'interactions, un outil comme PLIP constitue
une référence externe possible, mais Phase 6 reste volontairement limitée à un
fingerprint géométrique conservateur. Voir Kroemer et al. (2005) pour l'analyse
interactionnelle des poses et Salentin et al. (2015, mise à jour 2025) pour PLIP.
