# Nexora v2 — Phase 1I : validation analytique et reproductibilité LC-MS/MS

## 1. Objectif et frontière scientifique

La Phase 1I ajoute une couche de validation de lot fondée sur des observations de contrôle qualité explicitement enregistrées. Elle vise à rendre les critères, les données sources, les métriques et les limites d'une évaluation auditables et reproductibles.

Elle **ne réalise pas encore** :
- l'alignement des features entre des injections ou runs mzML indépendants ;
- la normalisation quantitative inter-injections ;
- la correction automatique de dérive ;
- l'estimation d'un taux global de features manquantes sans univers de features aligné et déclaré ;
- une validation inter-instrument ou inter-laboratoire.

La Phase 1H traite une injection mzML par run. Une proximité de masse et de temps de rétention entre deux runs ne constitue pas à elle seule une preuve que deux features sont la même entité. La Phase 1I ne fabrique donc pas de correspondances inter-runs.

## 2. Données sources et traçabilité

Le rapport agrège exclusivement :
- les observations `LcmsQualityControlObservation` attachées à l'identifiant du lot ;
- l'identifiant et l'ordre d'injection des injections référencées ;
- les runs LC-MS/MS terminés et leurs empreintes d'entrée et de sortie.

Les observations sources ne sont pas réécrites pendant la validation. Le rapport est un instantané immuable contenant la version du protocole, les critères, la couverture, les métriques, les limites, les identifiants des observations, les runs source, ainsi que des empreintes SHA-256.

Une modification d'une valeur QC, d'un statut, d'un ordre d'injection, d'un critère ou d'une empreinte de run produit un nouvel instantané. La répétition de la même requête sur les mêmes données renvoie le même rapport (rejeu idempotent).

### Convention de nommage des métriques

Pour les mesures qui correspondent à une feature suivie de façon stable par le producteur de la table QC, utiliser un nom explicite et identique dans les injections correspondantes, par exemple :

- `feature:<feature_key>:peak_area`
- `feature:<feature_key>:peak_height`
- `feature:<feature_key>:mz_error_ppm`
- `internal_standard:<standard_id>:peak_area`
- `system_suitability:<metric>`
- `mass_error_ppm`
- `retention_time_drift_seconds`

Les mesures doivent être exprimées dans la même unité et partager une définition analytique identique. La Phase 1I ne peut pas garantir la comparabilité de valeurs qui n'ont pas été produites ou contrôlées selon cette convention.

## 3. Métriques évaluées

### 3.1 Répétabilité des pooled-QC et standards internes

Pour chaque couple `qc_type + metric_name + unit`, une valeur par injection QC distincte est retenue. Les doublons pour une même métrique dans une même injection sont exclus du calcul et signalés ; ils ne sont jamais moyennés silencieusement.

La répétabilité est calculée avec l'écart-type d'échantillon :

`RSD (%) = 100 × s / moyenne`

où `s` est l'écart-type calculé avec `n - 1` degrés de liberté. Une moyenne non positive ou un nombre insuffisant d'injections empêche le calcul d'un RSD interprétable.

Le plancher logiciel par défaut est de trois injections QC distinctes. Il s'agit d'un minimum de calcul, pas d'une taille d'échantillon suffisante pour démontrer à elle seule la performance d'une méthode.

Le seuil RSD par défaut de 30 % est un **seuil de dépistage configurable**, non une règle universelle d'acceptation. Les usages publiés se situent souvent autour de 20–30 %, mais la littérature souligne l'absence de seuil unanimement accepté et la nécessité de critères définis pour chaque étude, instrument et méthode. Le seuil retenu doit donc être justifié et rapporté par le laboratoire.

### 3.2 Dérive dans l'ordre d'injection

Quand au moins trois mesures positives distinctes sont disponibles, une régression linéaire descriptive est calculée sur l'ordre d'injection. La pente est exprimée en pourcentage de la moyenne par dix positions d'injection, accompagnée de R².

La dérive n'est classée que si une limite spécifique à la métrique est fournie. Une pente linéaire ne prouve pas à elle seule une dérive instrumentale causale ; elle signale une tendance à examiner avec les chromatogrammes, les standards internes et les journaux d'instrument.

### 3.3 Contamination des blancs

Quand le même nom de métrique existe dans les blancs solvant/extraction et dans les pooled-QC, le rapport peut calculer :

`100 × max(|signal blanc|) / médiane(signal pooled-QC)`

Le ratio est évalué uniquement si une limite propre à cette métrique est fournie et si la référence QC est strictement positive. Sans seuil, le résultat reste `not_assessed`. Le ratio est un indicateur de contamination, pas une preuve d'identité chimique.

### 3.4 Exactitude de masse, rétention et suitability

Les observations `mass_accuracy`, `retention_time_drift`, `system_suitability`, `carryover` et `calibration` peuvent être comparées aux limites `min`, `max` ou `max_abs` renseignées par méthode. Nexora ne présume pas une tolérance universelle en ppm ou en secondes : les limites doivent venir du protocole instrument/méthode et être archivées dans la requête.

### 3.5 Couverture et données insuffisantes

Les rapports exposent le nombre d'observations, les valeurs non numériques, les injections pooled-QC et blancs ainsi que les références d'injection non résolues.

Les taux de features manquantes et la reproductibilité feature-par-feature entre runs restent non évalués jusqu'à la disponibilité d'une table d'alignement validée avec un univers de features explicite.

## 4. Critères configurables

Exemple de requête :

```json
{
  "min_pooled_qc_injections": 3,
  "max_pooled_qc_rsd_pct": 30.0,
  "metric_limits": {
    "feature:F-001:peak_area": {
      "max_rsd_pct": 20.0,
      "max_blank_to_pooled_qc_pct": 10.0,
      "max_abs_drift_pct_per_10_injections": 15.0
    },
    "mass_error_ppm": {
      "max_abs": 5.0
    },
    "retention_time_drift_seconds": {
      "max_abs": 3.0
    }
  }
}
```

Ces valeurs sont illustratives. Elles ne sont pas des seuils universels recommandés par Nexora. Le laboratoire doit déclarer des limites adaptées à ses instruments, colonnes, matrices, modes d'ionisation, analytes et objectifs.

Les statuts sont :
- `pass` : toutes les métriques effectivement évaluées respectent les limites configurées, sans métrique insuffisante ;
- `warning` : au moins une mesure est insuffisante ou une observation QC préexistante est en avertissement, sans échec déclaré ;
- `fail` : au moins un critère évalué échoue ou une observation QC source est déjà en échec ;
- `insufficient_data` : aucune métrique d'acceptation n'a pu être évaluée.

Un statut `pass` signifie uniquement que les critères configurés et effectivement évalués sont satisfaits. Il ne vaut ni certification de méthode, ni validation réglementaire, ni preuve de performance inter-laboratoire.

## 5. API

- `POST /api/v1/lcmsms/batches/{batch_id}/validation-reports` : produit ou rejoue un rapport.
- `GET /api/v1/lcmsms/batches/{batch_id}/validation-reports` : liste les instantanés d'un lot.
- `GET /api/v1/lcmsms/validation-reports/{report_id}` : consulte un rapport.

La création exige au moins un traitement LC-MS/MS terminé avec empreinte de sortie. Un lot sans données QC suffisantes peut néanmoins produire un rapport `insufficient_data`, explicitant ce qui manque.

## 6. Plan de validation externe

### Candidat de jeu de données

**MTBLS15740 — Untargeted LC-MS metabolomics of feces, intestinal contents and serum from mice** est retenu comme candidat d'inventaire. Sa fiche publique décrit des acquisitions LC-MS en phase inverse, en modes positif et négatif, avec échantillons pooled-QC et blancs analytiques.

Avant son adoption comme benchmark, il faut encore vérifier :
1. le statut public et les conditions de réutilisation exactes ;
2. l'inventaire des fichiers bruts et dérivés, et la présence réelle de mzML ;
3. les métadonnées ISA-Tab d'échantillon, d'essai, de type QC, d'ordre d'injection et de mode ;
4. les empreintes SHA-256 et le lien entre chaque fichier et son injection ;
5. la possibilité de reproduire une sélection documentée du workflow publié.

La fiche descriptive du dataset ne suffit pas à établir que des mzML sont disponibles. Aucun fichier externe n'est déclaré ici comme téléchargé ou validé.

### Étapes

1. **Inventaire** : télécharger les métadonnées et l'index des fichiers, vérifier licence, format et rôles QC ; ne pas importer un fichier dont l'identité ou la licence est incertaine.
2. **Sélection** : choisir un petit sous-ensemble positif/négatif comprenant pooled-QC, blancs et échantillons représentatifs ; conserver les fichiers originaux et leurs hashes.
3. **Conversion** : si seuls des formats propriétaires sont fournis, enregistrer le logiciel/version/paramètres de conversion et lier chaque mzML comme artefact dérivé de son parent ; ne pas mélanger modes ou méthodes.
4. **Reprocessing** : fixer paramètres et versions, réexécuter les injections choisies et conserver tous les manifestes d'entrée/sortie.
5. **QC** : importer les observations avec identifiants et ordre d'injection ; générer des rapports à critères enregistrés ; examiner chaque alerte avec l'expert instrumental.
6. **Reproductibilité** : répéter le même traitement sur les mêmes fichiers/paramètres et comparer les empreintes/sorties ; cette répétabilité computationnelle doit être distinguée de la reproductibilité analytique des injections.
7. **Publication** : déposer les métadonnées, fichiers convertis autorisés, paramètres, contrôles QC et rapports pour permettre la réinterrogation.

### Jeux de test synthétiques

Les tests déterministes de la Phase 1I valident les calculs de RSD, le traitement des doublons, le ratio blanc/QC, les limites de masse, la dérive et la stabilité des empreintes. Ils servent à vérifier le logiciel uniquement ; ils ne sont pas présentés comme une validation analytique sur données réelles.

## 7. Limites résiduelles et travaux ultérieurs

- Alignement inter-injections sur m/z, temps de rétention, isotopie/adduits, polarité et tolérances validées.
- Taux de détection et missingness sur un univers de features explicitement défini.
- Normalisation et correction de dérive après démonstration de la stabilité du QC, avec conservation des valeurs brutes et corrigées.
- Tests avec standards authentiques et matériels de référence adaptés.
- Validation multi-lots, multi-jours, opérateurs et instruments.

## 8. Références

1. Davis C. et al. (2023). *Current Practices in LC-MS Untargeted Metabolomics: A Scoping Review on the Use of Pooled Quality Control Samples*. Analytical Chemistry. DOI: 10.1021/acs.analchem.3c02924. https://pmc.ncbi.nlm.nih.gov/articles/PMC10753522/
2. Broadhurst D. et al. (2018). *Guidelines and considerations for the use of system suitability and quality control samples in mass spectrometry assays applied in untargeted clinical metabolomic studies*. Metabolomics. https://pmc.ncbi.nlm.nih.gov/articles/PMC5960010/
3. Sumner L. W. et al. (2007). *Proposed minimum reporting standards for chemical analysis: Chemical Analysis Working Group (CAWG) Metabolomics Standards Initiative (MSI)*. Metabolomics 3, 211–221. DOI: 10.1007/s11306-007-0082-2. https://pmc.ncbi.nlm.nih.gov/articles/PMC3772505/
4. MetaboLights. *Getting data from MetaboLights*. Les études publiques et leurs fichiers associés sont disponibles via la page d'étude, FTP, API ou Globus ; les métadonnées suivent ISA-Tab. https://www.ebi.ac.uk/training/online/courses/metabolights-quick-tour/getting-data-from-metabolights/
5. MTBLS15740, fiche descriptive agrégée sur OmicsDI (consultée le 1 novembre 2026). https://www.omicsdi.org/dataset/metabolights_dataset/MTBLS15740
