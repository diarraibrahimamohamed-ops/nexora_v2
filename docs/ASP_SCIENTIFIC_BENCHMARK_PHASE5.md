# N3XORA v2 — Phase 5: validation scientifique stricte

## Statut
Cette phase fournit un **harness reproductible de benchmark synthétique** et un protocole de benchmark externe. Elle ne constitue pas une preuve de supériorité d'ASP sur AutoDock Vina.

## 1. Ce qui a réellement été testé
- 2 000 cas synthétiques déterministes (`seed=20260913`).
- comparaison de Vina TOP1, ASP log-mean sans clustering et ASP avec clustering RMSD 2 Å.
- vérité terrain synthétique : pose native-like définie par RMSD synthétique ≤ 2 Å et poche native définie par construction.

### Résultat synthétique
Les taux observés sont :
- Vina TOP1 : 14,7 % de hits RMSD ≤ 2 Å ; 29,75 % de sélection de la poche native.
- ASP sans clustering : 8,7 % ; 17,15 %.
- ASP + clustering 2 Å : 14,7 % ; 29,75 %.

**Interprétation :** sur ce scénario artificiel, ASP+clustering ne surpasse pas TOP1 ; le résultat est égal à TOP1. ASP sans clustering est inférieur. Ces valeurs ne doivent pas être extrapolées aux vraies performances de docking.

## 2. Pourquoi ce résultat n'est pas une validation scientifique du modèle
Le scénario est généré artificiellement, avec des distributions de RMSD et de scores choisies pour tester le comportement de l'agrégateur. Il ne reproduit pas la physique du récepteur, la fonction de score complète de Vina, la flexibilité réelle, les erreurs de protonation, ni la diversité des complexes expérimentaux.

## 3. Point contractuel important : score global ≠ pose unique
ASP agrège une famille de représentants de clusters. Une implémentation sérieuse doit néanmoins indiquer quelle pose est affichée lorsque l'interface visualise une seule structure. Le contrat actuel expose désormais :
- `selected_pose_id`
- `selected_pose_score`
- `selected_pose_boltzmann_weight`

Dans la formulation actuelle, la pose sélectionnée est le représentant au poids Boltzmann maximal dans la poche gagnante ; comme le poids est monotone avec le score Vina, cela correspond au représentant de plus bas score de cette poche.

## 4. Ce qui doit être fait sur des données externes
Le benchmark final doit être réalisé sur des complexes réels avec pose expérimentale connue. Pour la pose prediction, utiliser un corpus de complexes avec ligand co-cristallisé et comparer le RMSD de la pose retenue. Pour le virtual screening, utiliser un corpus actif/décoy avec labels indépendants et mesurer ROC-AUC, PR-AUC, enrichment factor et une métrique d'enrichissement précoce.

DUD-E est un corpus de référence pertinent pour la partie virtual screening : 102 cibles, 22 886 actifs et 50 décoys par actif en moyenne, avec des propriétés physico-chimiques appariées. citeturn728454search0turn728454search3

## 5. Protocole externe à verrouiller
Pour chaque cible :
1. structure réceptrice et ligand natif documentés ;
2. mêmes paramètres Vina pour toutes les méthodes ;
3. même génération ligand/PDBQT ;
4. même ensemble de poches candidates ;
5. méthodes comparées : TOP1, ASP sans clustering, ASP + clustering ;
6. aucun tuning sur le jeu de test ;
7. bootstrap au niveau des complexes pour les intervalles de confiance ;
8. rapport séparé pour structures expérimentales et structures prédites/fallback.

## 6. Critère de décision
- amélioration robuste + intervalle compatible avec une vraie différence → conserver ASP comme méthode candidate validée ;
- résultat statistiquement équivalent → présenter ASP comme agrégation/robustesse, pas comme amélioration ;
- dégradation → retirer ASP du chemin canonique.

## 7. Blocage actuel
Le benchmark externe complet n'a pas été exécuté dans cet environnement car l'accès réseau depuis le runtime de calcul est indisponible et `vina`/`fpocket` ne sont pas installés dans cet environnement. Le rapport ne fabrique donc aucune métrique externe.

## 8. Verdict actuel
**N3XORA dispose maintenant d'un agrégateur ASP mathématiquement cohérent au niveau logiciel et d'un protocole de validation falsifiable. Il n'est pas encore scientifiquement démontré qu'ASP améliore Vina.**
