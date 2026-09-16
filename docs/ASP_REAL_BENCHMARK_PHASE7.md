# N3XORA v2 — Phase 7: benchmark réel verrouillé / auditable

## Verdict de la phase
Cette phase ne revendique **aucun gain externe** : les données DUD-E brutes et les exécutables de docking ne sont pas présents dans l'environnement de validation. Le travail porte sur la robustesse de l'évaluateur et sur l'absence de raccourcis statistiques.

## Corrections par rapport à la phase précédente

1. **PR-AUC** : le calcul a été aligné sur la définition Average Precision (intégration aux sauts de rappel), au lieu d'une interpolation trapézoïdale qui peut modifier le score sur une courbe discrète.
2. **Inférence au niveau cible** : les métriques principales sont maintenant calculées par cible puis moyennées (macro-target). Le bootstrap rééchantillonne les cibles, qui constituent les unités indépendantes du benchmark.
3. **Seuil minimal** : au moins 5 cibles indépendantes sont exigées. Cela reste un minimum technique ; le sous-ensemble Diverse de DUD-E fournit 8 cibles.
4. **Validation par cible** : chaque cible doit contenir au moins des actifs et des décoys pour le track de virtual screening.
5. **Corrections multiples** : Holm-Bonferroni est appliqué aux comparaisons testées dans le rapport exploratoire.
6. **Track pose** : l'évaluateur accepte un CSV distinct et rapporte taux de succès RMSD ≤ 2 Å et médiane RMSD, avec bootstrap par cible.

## Contrôle de cohérence de l'implémentation

Les fonctions ROC-AUC et Average Precision ont été comparées à scikit-learn sur 100 jeux aléatoires. Les écarts observés sont nuls à la précision numérique de la validation.

## Données externes retenues

Le sous-ensemble DUD-E « Diverse » contient 8 cibles : AKT1/3cqw, AMPC/1l2s, CP3A4/3nxu, CXCR4/3odu, GCR/3bqd, HIVPR/1xl2, HIVRT/3lan et KIF11/3cjo. Le site officiel DUD-E indique également que ce sous-ensemble est proposé pour réduire le coût des benchmarks répétés. Source : https://dude.docking.org/subsets/diverse

DUD-E est un benchmark de virtual screening : il contient des actifs issus de données d'affinité et des décoys générés pour avoir des propriétés physicochimiques similaires. Les décoys ne doivent donc pas être décrits comme des « inactifs expérimentalement vérifiés ».

## Protocole final

Pour chaque cible et ligand :
- même récepteur ;
- même préparation ;
- même ensemble de poches ;
- mêmes paramètres Vina ;
- mêmes poses candidates ;
- aucune information additionnelle donnée à ASP ;
- mêmes transformations de score pour tous les baselines ;
- conservation des hashes et versions.

Comparaisons :
- Vina TOP1 ;
- ASP sans clustering ;
- ASP + clustering RMSD.

### Virtual screening

Métriques primaires : ROC-AUC, PR-AUC/AP, EF@1 %, EF@5 %.

Les résultats seront présentés à deux niveaux :
- métriques par cible ;
- moyenne macro sur les cibles.

L'inférence principale compare les méthodes par bootstrap apparié au niveau cible. Les valeurs p exploratoires sont ajustées par Holm-Bonferroni.

### Pose prediction

Métriques :
- proportion RMSD ≤ 2 Å ;
- médiane RMSD ;
- résultats par cible ;
- bootstrap par cible/complexe selon la structure de l'échantillon.

## Règle de décision

ASP ne sera qualifié de **supérieur à Vina** que si l'amélioration est reproductible sur plusieurs cibles et que l'intervalle de confiance de la différence pertinente exclut zéro, avec une analyse par cible cohérente.

Une différence compatible avec zéro sera rapportée comme absence de preuve de supériorité. Une baisse de performance conduira à considérer ASP comme méthode secondaire ou à le retirer du chemin canonique.

Aucune conversion du score ASP en ΔG expérimentale, Kd ou probabilité d'occupation n'est autorisée sans calibration indépendante.

## Limite actuelle

La phase 7 ne doit pas être citée comme « validation expérimentale de l'ASP ». Elle valide le **protocole et l'outil de mesure**. La validation scientifique de l'ASP exige l'exécution sur le corpus réel.
