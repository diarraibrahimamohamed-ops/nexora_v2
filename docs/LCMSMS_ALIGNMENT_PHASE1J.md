# Nexora v2 — Phase 1J : alignement inter-injections et correction quantitative QC-RLSC

## 1. Objectifs et limites de la phase

La Phase 1H détecte des features MS1 dans un fichier mzML et crée un `LcmsProcessingRun` par injection. Les identifiants `feature_key` de ces runs ne sont pas globaux. La Phase 1J crée une couche distincte de consensus entre injections, tout en conservant les features, les observations et les fichiers mzML source.

La phase comprend deux opérations séparées et versionnées :

1. **Alignement inter-injections** : transformation des temps de rétention par polarité, puis regroupement des features compatibles en caractéristiques consensus.
2. **Correction QC-RLSC** : estimation d'une dérive d'intensité feature par feature à partir des pooled-QC et correction des intensités dans une nouvelle matrice.

L'alignement ne normalise pas les intensités. La correction QC-RLSC ne réécrit jamais les intensités originales.

### Non-objectifs explicites

Cette version ne réalise pas :
- la fusion des polarités positive et négative ;
- la résolution automatique des isotopes, adduits ou dimères ;
- l'alignement sur bibliothèque MS/MS ou identification de métabolites ;
- la normalisation sample-wise TIC, median, PQN ou la correction inter-lots ;
- l'imputation des signaux manquants à zéro ;
- l'extrapolation des temps de rétention hors de la plage couverte par les repères ;
- la démonstration de reproductibilité inter-instrument ou inter-laboratoire.

Un consensus calculé ne vaut pas identification chimique. La confirmation demeure une tâche de validation analytique distincte.

## 2. Données admises

L'API accepte une sélection explicite de traitements LC-MS/MS terminés, un seul par injection, appartenant au même lot et à la même étude.

Les injections admises pour l'alignement sont :
- `sample`
- `pooled_qc`

Les injections `solvent_blank`, `extraction_blank`, standards et tests de suitability sont exclues de cette matrice de consensus. Les données de blancs restent disponibles pour les contrôles QC de la Phase 1I.

Les traitements doivent avoir un `output_sha256` et référencer `mzml_injection_id` dans leurs paramètres. Les features doivent être en secondes et avoir une polarité connue (`positive` ou `negative`). La polarité inconnue est rejetée plutôt que devinée.

L'API vérifie les relations étude/lot/injection. Une injection ne peut être représentée qu'une fois dans la sélection. Si plusieurs traitements terminés existent pour une injection, l'utilisateur doit en sélectionner exactement un ; ils ne sont pas combinés silencieusement.

## 3. Alignement des temps de rétention

### 3.1 Référence par polarité

La référence est sélectionnée indépendamment en positif et négatif :
1. une injection pooled-QC explicitement choisie est prioritaire ;
2. sinon, Nexora choisit la pooled-QC ayant le plus grand nombre de features ;
3. à défaut de pooled-QC, la carte la plus riche est utilisée avec une provenance indiquant le repli.

Le choix est conservé dans le manifeste. Une référence explicitement demandée doit appartenir à la sélection et être de type pooled-QC.

### 3.2 Repères m/z mutuellement uniques

Pour une injection et une référence d'une même polarité, un couple de features est retenu comme repère si :
- les m/z satisfont la tolérance configurée en ppm ;
- la différence initiale de temps de rétention satisfait la tolérance de recherche ;
- la polarité est identique ;
- les charges connues ne se contredisent pas ;
- les adduits connus ne se contredisent pas ;
- le candidat est mutuellement unique : une feature source n'a qu'un candidat, et la feature de référence n'est proposée que par une seule source.

Les candidats ambigus ne servent pas de repères. Ils ne sont pas départagés arbitrairement par proximité.

### 3.3 Transformation robuste, monotone et sans extrapolation

Les repères sont ordonnés sur le temps de rétention source. La médiane des pentes positives adjacentes fournit une tendance robuste descriptive. Les résidus servent à écarter les repères aberrants ; les nœuds conservés définissent une fonction affine par morceaux, strictement croissante.

La fonction n'est évaluée que dans l'intervalle source couvert par les repères. Pour une feature située avant ou après cet intervalle :
- aucun temps transformé n'est inventé ;
- son temps d'origine est conservé ;
- elle est enregistrée comme singleton non aligné ;
- elle ne peut jamais rejoindre un consensus par proximité RT.

Au moins huit repères monotones et une couverture suffisante de la plage RT source et référence sont exigés par défaut. L'échec d'une injection entraîne l'échec explicite du run d'alignement ; Nexora ne présente pas un résultat partiel comme un alignement réussi de lot.

### 3.4 Regroupement des features consensus

Après transformation des temps de rétention, les features sont regroupées par polarité, m/z, charge et adduit lorsque ces attributs sont connus. Une seule feature issue d'une injection peut appartenir à un groupe consensus.

Chaque consensus comporte :
- son m/z médian ;
- son temps de rétention aligné médian ;
- sa polarité ;
- le nombre de membres et d'injections attendus ;
- son statut `aligned`, `partial` ou `singleton` ;
- la liste des features source, traitements, injections et observations d'origine.

Les groupes incomplets ne sont pas présentés comme complets. Les features hors plage restent des singletons. L'algorithme est déterministe sur une même sélection, un même ordre de données et des paramètres identiques.

## 4. Correction QC-RLSC (LOESS robuste)

### 4.1 Principe

La correction est exécutée uniquement sur une exécution d'alignement terminée et scellée.

Pour chaque feature consensus, le moteur :
1. rassemble les intensités brutes de ses membres par injection ;
2. sélectionne uniquement les injections `pooled_qc` avec intensité strictement positive ;
3. transforme les intensités en logarithme ;
4. estime le signal QC attendu en fonction de l'ordre réel d'injection par LOESS local linéaire à noyau tricube ;
5. applique des poids robustes fondés sur les résidus pour réduire l'influence de points aberrants ;
6. calcule le facteur par rapport à la médiane des intensités log QC ;
7. conserve les valeurs brutes et enregistre les valeurs corrigées dans une table distincte.

L'estimateur est implémenté dans Nexora en Python ; il ne prétend pas être une exécution du package OpenMS ni une reproduction bit à bit d'un autre outil RLSC.

### 4.2 Seuils d'éligibilité

Par défaut :
- au moins 8 pooled-QC détectés par feature ;
- couverture QC d'au moins 60 % de l'étendue d'ordre d'injection du lot ;
- au moins 8 points QC distincts pour la correction ;
- fraction de voisinage LOESS de 0,75 ;
- deux itérations robustes ;
- facteur de correction entre 0,2 et 5,0.

Ces valeurs sont des paramètres initiaux conservateurs et configurables, non des seuils universels de validation analytique. Le protocole du laboratoire doit justifier ses propres réglages.

### 4.3 Contrôles après correction

Pour chaque feature, le rapport conserve :
- RSD des pooled-QC avant correction ;
- RSD des pooled-QC après correction ;
- nombre de pooled-QC retenus ;
- couverture QC de la séquence ;
- fraction LOESS et nombre d'itérations robustes ;
- facteurs de correction et statuts par cellule.

La correction est refusée pour une feature si les données QC sont insuffisantes, si la couverture est trop courte, si les facteurs QC dépassent les limites ou si le RSD QC corrigé se dégrade au-delà de la tolérance configurée.

Si le facteur reste proche de 1 dans la limite définie, le statut `no_correction_needed` est conservé et l'intensité brute est recopiée dans la colonne corrigée avec facteur 1, afin de signaler explicitement l'absence de correction.

### 4.4 Valeurs manquantes et absence d'extrapolation

Une feature non détectée reste `not_detected`, sans valeur et sans imputation à zéro. Une injection située hors de la plage d'ordre couverte par les pooled-QC reste `outside_qc_range` et n'est pas corrigée. La Phase 1J ne crée donc pas de quantification artificielle en dehors de la fenêtre expérimentale observée.

## 5. Traçabilité et immutabilité

Les tables `lcms_features` et `lcms_feature_observations` ne sont jamais modifiées.

La Phase 1J ajoute :
- `LcmsFeatureAlignmentRun` : sélection source, paramètres, empreinte d'entrée/sortie et manifeste ;
- `LcmsAlignedFeature` : consensus de chaque groupe ;
- `LcmsAlignedFeatureMember` : lien immuable entre consensus et feature par injection ;
- `LcmsQuantificationRun` : exécution QC-RLSC sur un alignement donné ;
- `LcmsNormalizedFeatureQuantification` : cellule brute/corrigée de la matrice feature × injection.

Les manifestes enregistrent les SHA-256 des traitements sources, les IDs de traitement/injection/feature/observation, les paramètres et les diagnostics. Si les observations sources changent avant l'exécution, le hash recalculé ne correspond plus et le calcul échoue explicitement.

Les workflows passent par les contrats autorisés, le dispatcher scientifique, l'outbox transactionnelle et le worker Celery `lcmsms`. Les deux opérations requièrent une revue qualité après exécution.

## 6. API

### Alignement

`POST /api/v1/lcmsms/batches/{batch_id}/feature-alignments`

Exemple :

```json
{
  "processing_run_ids": [41, 42, 43],
  "reference_injection_ids": {"positive": 41, "negative": 41},
  "anchor_mass_tolerance_ppm": 8.0,
  "max_initial_rt_delta_seconds": 120.0,
  "max_anchor_residual_seconds": 20.0,
  "minimum_anchors": 8,
  "minimum_injections": 3,
  "minimum_anchor_rt_coverage": 0.5,
  "group_mass_tolerance_ppm": 10.0,
  "group_rt_tolerance_seconds": 10.0
}
```

- `GET /api/v1/lcmsms/batches/{batch_id}/feature-alignments`
- `GET /api/v1/lcmsms/feature-alignments/{alignment_run_id}`
- `GET /api/v1/lcmsms/feature-alignments/{alignment_run_id}/features`

### Correction QC-RLSC

`POST /api/v1/lcmsms/feature-alignments/{alignment_run_id}/quantifications`

Exemple :

```json
{
  "minimum_qc_injections": 8,
  "minimum_qc_span_ratio": 0.6,
  "loess_span_fraction": 0.75,
  "robust_iterations": 2,
  "minimum_correction_factor": 0.2,
  "maximum_correction_factor": 5.0,
  "maximum_qc_rsd_worsening_pct": 0.0,
  "no_correction_threshold_pct": 2.0
}
```

- `GET /api/v1/lcmsms/quantification-runs/{quantification_run_id}`
- `GET /api/v1/lcmsms/quantification-runs/{quantification_run_id}/matrix`

Les soumissions identiques sont idempotentes. Un alignement différent ou une autre configuration de correction génère une exécution distincte.

## 7. Validation et benchmarks

Les tests synthétiques contrôlent :
- correspondance des polarités positives/négatives sans fusion ;
- correction d'un décalage RT monotone ;
- rejet des repères ambigus et des polarités inconnues ;
- nombre minimal d'injections/repères ;
- maintien des features hors plage en singletons ;
- statut partiel des groupes sans membre dans toutes les injections ;
- correction d'une dérive log-linéaire ;
- absence de correction inutile sur signal stable ;
- maintien des données manquantes ;
- refus d'extrapoler hors de l'étendue QC.

Ces tests vérifient le comportement logiciel ; ils ne constituent pas une validation analytique sur données instrumentales réelles.

Le candidat externe MTBLS15740 reste un candidat d'inventaire de Phase 1I. Avant de le déclarer benchmark Phase 1J, il faut confirmer ses fichiers mzML, la licence, les modes, les identifiants d'injection, les pooled-QC, l'ordre d'acquisition et les empreintes.

## 8. Étapes nécessaires avant usage de recherche sur données réelles

1. Exécuter l'alignement sur une petite sélection d'injections d'une seule méthode et d'un seul lot.
2. Contrôler visuellement les cartes RT, le nombre de repères, les résidus et les features hors plage.
3. Vérifier un échantillon de consensus avec les chromatogrammes et spectres d'origine.
4. Comparer les valeurs QC avant/après correction, et refuser tout résultat dégradé.
5. Faire examiner les critères par le responsable de la méthode LC-MS.
6. Archiver les jeux de données autorisés, paramètres, manifestes, rapports et décisions de revue.

Aucune identité de composé, activité biologique ou validation réglementaire ne découle automatiquement de l'alignement ni de QC-RLSC.
