# N3XORA v2 — Audit scientifique ASP/Boltzmann — Phase 4

## Objet
Cette phase ne cherche pas à démontrer que l'ASP est « meilleur ». Elle vérifie ce que l'implémentation actuelle démontre réellement, puis mesure son comportement sur les résultats Vina historiques embarqués et sur des cas synthétiques contrôlés.

## 1. Question scientifique
Comparer quatre niveaux :

1. TOP1 Vina : minimum du score Vina.
2. ASP sans clustering : agrégation Boltzmann sur toutes les poses.
3. ASP avec clustering RMSD : un représentant de meilleure énergie par grappe.
4. Agrégation par poche : ASP calculé séparément dans chaque poche, puis sélection de la poche ayant le meilleur score ASP.

La présente implémentation canonique correspond au niveau 4.

## 2. Résultats sur les 4 jobs historiques

### job_822
- 59 poses, 3 poches.
- Poche 0 : 19 poses → 1 cluster → TOP1 = -5.372, ASP = -5.372.
- Poche 1 : 20 poses → 1 cluster → TOP1 = -6.952, ASP = -6.952.
- Poche 2 : 20 poses → 1 cluster → TOP1 = -6.996, ASP = -6.996.
- Poche gagnante : 2.

### job_823
- 39 poses, 2 poches.
- Poche 0 : 20 poses → 1 cluster → TOP1 = -5.603, ASP = -5.603.
- Poche 1 : 19 poses → 1 cluster → TOP1 = -5.594, ASP = -5.594.
- Poche gagnante : 0.

### job_824
- 39 poses, 2 poches.
- Poche 0 : 19 poses → 1 cluster → TOP1 = -5.080, ASP = -5.080.
- Poche 1 : 20 poses → 1 cluster → TOP1 = -4.892, ASP = -4.892.
- Poche gagnante : 0.

### job_858
- 40 poses, 2 poches.
- Poche 0 : 20 poses → 1 cluster → TOP1 = -3.189, ASP = -3.189.
- Poche 1 : 20 poses → 1 cluster → TOP1 = -4.797, ASP = -4.797.
- Poche gagnante : 1.

### Conclusion empirique
Sur ces quatre résultats, le seuil RMSD de 2 Å produit une seule grappe par poche. Dans ces cas précis, l'ASP canonique est donc exactement égal au TOP1 de la poche. Ces données **ne montrent aucune amélioration de ranking** apportée par l'ASP.

## 3. Test mathématique contrôlé

Avec un score empirique traité comme une énergie d'agrégation et `RT = 0.00198720425864083 × T` :

- [-8.0] → ASP = -8.0.
- [-8.0, -7.9] → ASP ≈ -7.952 à 298.15 K.
- [-8.0, -7.0] → ASP ≈ -7.690 à 298.15 K.
- duplication exacte de [-8.0] → même ASP que [-8.0].

Les poids restent normalisés à 1 et le calcul est numériquement stable pour les scores testés.

## 4. Sensibilité à la température

Pour [-8, -7, -6] :

- 280 K → ASP ≈ -7.487
- 298.15 K → ASP ≈ -7.467
- 310 K → ASP ≈ -7.454
- 350 K → ASP ≈ -7.415
- 400 K → ASP ≈ -7.374

La température modifie donc le compromis entre le meilleur représentant et les représentants moins favorables. Le choix de 298.15 K est actuellement une convention d'agrégation, **pas une température biologiquement validée pour la performance de classement**.

## 5. Point scientifique critique

Le calcul

`-RT ln(mean(exp(-score/RT)))`

est mathématiquement bien défini comme fonction d'agrégation. En revanche, avec des scores Vina, il ne faut pas lui attribuer automatiquement le sens d'une énergie libre de liaison ou d'un véritable potentiel thermodynamique. L'implémentation actuelle a correctement retiré cette interprétation.

Le terme « Boltzmann » décrit donc ici une **pondération de type Boltzmann appliquée à des scores empiriques**.

## 6. Pourquoi le benchmark actuel n'est pas suffisant

Les quatre jobs historiques sont trop faibles pour conclure sur la valeur scientifique de l'ASP :

- seulement 4 exécutions ;
- absence de vérité terrain expérimentale ;
- une seule grappe RMSD par poche dans tous les cas observés ;
- provenance de structure hétérogène sur certains anciens jobs ;
- pas de comparaison blindée entre ligands actifs/inactifs ;
- pas de métrique statistique de ranking.

## 7. Benchmark scientifique requis avant toute revendication

Le protocole final doit utiliser un jeu de complexes de référence avec pose native connue et, idéalement, affinité expérimentale ou labels actifs/inactifs.

Pour chaque complexe :

1. Générer plusieurs poses avec exactement les mêmes paramètres Vina.
2. Conserver la pose native comme référence lorsque disponible.
3. Calculer TOP1, TOP-N, ASP sans clustering et ASP avec clustering.
4. Mesurer RMSD de la pose sélectionnée à la pose native.
5. Pour le virtual screening, calculer au minimum ROC-AUC, PR-AUC, enrichment factor et BEDROC/metric équivalent adapté au cas d'usage.
6. Comparer les méthodes avec intervalles de confiance et tests statistiques adaptés, idéalement par bootstrap au niveau des complexes.
7. Séparer les résultats par source de structure : expérimentale, modèle prédictif, fallback.

## 8. Critère de décision

N3XORA ne doit pas annoncer que « ASP améliore Vina » tant que le benchmark ne montre pas une amélioration reproductible sur un protocole indépendant.

Trois conclusions sont possibles :

- ASP améliore significativement les métriques → conservation et justification expérimentale.
- ASP est équivalent à TOP1 mais plus stable sur certains cas → le présenter comme méthode d'agrégation/robustesse, pas comme amélioration générale.
- ASP dégrade les métriques → retirer son statut canonique et conserver TOP1 ou une variante validée.

## 9. État de validation actuel

### Validé au niveau implémentation
- séparation des poches pour l'agrégation canonique ;
- clustering RMSD heavy-atom en repère fixe, sans Kabsch ;
- pondération normalisée ;
- stabilité numérique de base ;
- invariance à la duplication exacte ;
- absence de conversion Vina → Kd/ΔG expérimentale ;
- traçabilité des fallbacks.

### Non validé scientifiquement
- supériorité sur TOP1 Vina ;
- choix optimal de 298.15 K ;
- choix optimal du seuil RMSD = 2 Å ;
- pertinence thermodynamique de l'analogie de Boltzmann ;
- supériorité de l'agrégation par poche sur d'autres stratégies de sélection ;
- bénéfice sur un vrai jeu de virtual screening.
