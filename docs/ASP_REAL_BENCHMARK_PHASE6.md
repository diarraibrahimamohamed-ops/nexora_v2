# N3XORA v2 — Phase 6: benchmark externe préparé et verrouillé

## Statut
Le protocole et l'évaluateur sont maintenant implémentés. **Aucune performance externe n'est revendiquée dans cette phase**, car l'exécution complète de Vina/fpocket et l'acquisition du corpus brut n'étaient pas disponibles dans l'environnement de calcul.

## 1. Pourquoi deux tracks sont nécessaires

### Track A — prédiction de pose
Objectif : déterminer si la méthode sélectionne une pose proche de la pose expérimentale.

Endpoint principal : heavy-atom RMSD ≤ 2.0 Å.

Comparaisons :
- Vina TOP1 ;
- ASP sans clustering ;
- ASP + clustering RMSD.

Tous les candidats doivent provenir de la même exécution de génération de poses. On évalue uniquement le mécanisme de sélection/agrégation.

### Track B — virtual screening
Objectif : déterminer si l'agrégation améliore l'enrichissement des actifs parmi les décoys.

DUD-E est adapté à ce track. Le site officiel indique 102 cibles, 22 886 actifs et 50 décoys par actif en moyenne. Le sous-ensemble « Diverse » contient 8 cibles : AKT1 (3cqw), AMPC (1l2s), CP3A4 (3nxu), CXCR4 (3odu), GCR (3bqd), HIVPR (1xl2), HIVRT (3lan), KIF11 (3cjo). [Sources: DUD-E official Diverse subset; DUD-E official homepage]

## 2. Contrôle expérimental

Pour chaque cible/ligand :
1. même structure réceptrice ;
2. même préparation du récepteur ;
3. même préparation du ligand ;
4. même ensemble de poches ;
5. même paramètres Vina ;
6. mêmes poses candidates pour les trois méthodes ;
7. aucun réglage de paramètres sur le jeu de test ;
8. conservation des hashes et versions de tous les artefacts.

La méthode ne doit donc pas bénéficier d'un budget de calcul ou d'une information supplémentaire par rapport aux baselines.

## 3. Métriques

Pour le virtual screening :
- ROC-AUC ;
- PR-AUC ;
- EF@1% ;
- EF@5%.

Les différences ASP−Vina sont estimées au niveau **cible**, puis bootstrapées par rééchantillonnage des cibles afin d'éviter de traiter des ligands d'une même cible comme observations indépendantes.

Pour la pose prediction :
- success rate RMSD ≤ 2 Å ;
- médiane RMSD ;
- distribution par cible ;
- intervalle de confiance par bootstrap au niveau complexe.

## 4. Fichier d'entrée du benchmark

`benchmarks/external/README.md` documente le CSV attendu :

```csv
target,ligand_id,active,vina_top1_score,asp_no_cluster_score,asp_clustered_score
```

Le validateur refuse les lignes dupliquées `(target, ligand_id)`, les scores non finis et un benchmark réduit à une seule cible.

## 5. Outil exécutable

```bash
python tools/benchmark_external.py path/to/results.csv --out benchmark_results.json --bootstrap 2000
```

Le script ne télécharge pas les données et ne lance pas Vina : cette séparation est volontaire pour que la génération des résultats reste contrôlable et auditable.

## 6. Critère de décision

ASP ne sera considéré comme une amélioration que si son avantage est reproductible sur plusieurs cibles et compatible avec un intervalle de confiance excluant une différence nulle. Une égalité avec Vina sera rapportée comme **non-infériorité/équivalence descriptive**, et non comme supériorité.

Une dégradation conduira à retirer ASP du chemin canonique ou à le limiter à une analyse secondaire.

## 7. Limites explicitement conservées

- Le score ASP reste une agrégation de scores empiriques Vina, pas une énergie libre expérimentale.
- Une température de Boltzmann ne transforme pas automatiquement un score Vina en quantité thermodynamique.
- Les différentes poches sont traitées comme des sites de liaison alternatifs, pas comme des micro-états d'une seule partition fonctionnelle.
- Les structures expérimentales, ESMFold et ColabFold ne doivent pas être mélangées dans une statistique unique sans stratification de provenance.
