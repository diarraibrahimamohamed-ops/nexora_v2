# N3XORA v2 — Phase 8 : validation externe et gate d'interprétation

## Objectif

Cette phase ne cherche pas à produire un chiffre flatteur. Elle verrouille les conditions dans lesquelles un résultat Vina/ASP pourra être interprété comme une observation de benchmark.

Les recommandations récentes de benchmarking de virtual screening insistent sur les splits de déploiement, le contrôle de la fuite chimique/cible, les métriques de reconnaissance précoce et l'incertitude au niveau cible. citeturn325267search0

## 1. Deux tracks, deux questions

### Pose selection
Question : la méthode sélectionne-t-elle une pose proche de la pose expérimentale ?

Mesures : heavy-atom RMSD, taux de succès ≤ 2 Å, médiane RMSD, analyse par cible.

CASF-2016 rappelle que scoring, ranking, docking/pose selection et screening sont des capacités différentes et doivent être évaluées séparément. citeturn325267search3

### Virtual screening
Question : les actifs sont-ils enrichis par rapport aux décoys ?

Mesures principales : AP/PR-AUC, EF@1 %, EF@5 % et ROC-AUC comme métrique secondaire.

DUD-E contient 102 cibles, 22 886 actifs et environ 50 décoys par actif. Les décoys sont des composés conçus pour apparier certaines propriétés physico-chimiques tout en différant topologiquement ; ils ne doivent pas être décrits comme des inactifs expérimentalement prouvés. citeturn325267search2

## 2. Gate anti-fuite

Avant tout calcul interprétable :

- aucune duplication `(target, ligand)` dans un fichier de benchmark ;
- labels actifs/décoys présents dans chaque cible ;
- inventaire des ligands présents sur plusieurs cibles rendu visible ;
- version et hash des récepteurs/ligands enregistrés ;
- préparation des ligands identique entre méthodes ;
- préparation du récepteur identique entre méthodes ;
- paramètres Vina identiques entre méthodes comparées ;
- même ensemble de poches ;
- aucun réglage sur les cibles finales ;
- structures expérimentales et prédites rapportées séparément.

Les travaux méthodologiques récents montrent que les analogues chimiques, les homologues de cibles et la réutilisation de voisinages peuvent gonfler artificiellement les performances apparentes ; les splits scaffold/target et les contrôles de fuite doivent donc être explicités. citeturn325267search0turn325267search1

## 3. Comparaison ASP vs Vina

Comparer au minimum :

- Vina TOP1
- ASP sans clustering
- ASP + clustering

L'unité d'inférence est la cible, pas une ligne individuelle considérée comme indépendante.

La décision est descriptive jusqu'à ce que le nombre de cibles et l'intervalle de confiance permettent une conclusion robuste.

## 4. Ce que cette phase ne permet pas de conclure

Un meilleur ROC-AUC/EF ou un meilleur RMSD sur DUD-E/CASF ne constitue pas une preuve d'efficacité thérapeutique.

Un meilleur score Vina n'est pas une mesure expérimentale de ΔG° ou de Kd.

Un variant qui modifie le docking n'est pas automatiquement un variant de résistance.

## 5. Gate final

Le rapport final doit contenir :

1. version exacte de N3XORA ;
2. version Vina/fpocket/RDKit ;
3. hashes des datasets et structures ;
4. paramètres complets ;
5. méthodes et baselines ;
6. résultats par cible ;
7. moyenne macro ;
8. intervalles de confiance ;
9. liste des exclusions ;
10. limites ;
11. conclusion écrite séparément pour chaque endpoint.

**La sortie peut être `OPEN` seulement si ces contrôles sont remplis.**
