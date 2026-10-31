# Nexora v2 — Phase 1F : Provenance scientifique commune et préparation LC-MS/MS

## 1. Objet et périmètre

La Phase 1F renforce la reproductibilité, la traçabilité et la compatibilité des résultats scientifiques.

Elle ne fusionne pas arbitrairement les deux moteurs 4PL. Elle standardise leur enveloppe de provenance, leur persistance et leur consultation tout en conservant les algorithmes, les paramètres, les règles de sélection des observations et les contrats historiques.

Cette phase apporte :

- un schéma de manifeste de provenance commun et versionné ;
- le scellement SHA-256 des manifestes d'entrée et de sortie de nouveaux workflows HFSM/DAG ;
- un manifeste commun pour les analyses 4PL avancées et historiques ;
- l'idempotence de persistance du chemin 4PL historique ;
- l'enregistrement de provenance dans `ResearchEvidence` ;
- le retour des empreintes et manifestes via les routes d'historique et de consultation ;
- le cadrage du futur domaine expérimental botanique et LC-MS/MS.

Aucune nouvelle table SQL ni migration n'est ajoutée dans cette phase.

## 2. Audit des deux contrats 4PL

Le dépôt conserve deux routes de dose-réponse. Elles n'ont pas les mêmes règles scientifiques et ne doivent pas être traitées comme interchangeables.

| Dimension | Route historique | Route avancée |
|---|---|---|
| Endpoint | `POST /api/v1/research/studies/{study_id}/assays/{assay_id}/analyses/dose-response` | `POST /api/v1/research/studies/{study_id}/assays/{assay_id}/dose-response-analysis` |
| Module de calcul | `experimental_analytics.py` | `assay_statistics.py` |
| Version actuelle | `1.0.0` | `N3XORA_4PL_CLUSTER_BOOTSTRAP_V1` |
| Paramètres | `response_name`, `direction`, endpoint IC50/EC50/GI50/CC50, timepoint, bootstrap 100–1 000, seed | `response_name`, unités explicites, `response_direction`, endpoint IC50/EC50, étiquette échantillon, timepoint, bootstrap 500–2 000, seed |
| Population statistique | Réplicats déclarés biologiques ; les lignes techniques et indépendantes ne sont pas promues en réplicats biologiques | Profils biologiques complets ; mesures techniques uniquement si leur parent biologique est explicitement identifié et valide |
| Incertitude | Bootstrap par profils biologiques complets | Bootstrap par profils biologiques complets, avec contrôles de convergence et de qualité propres au moteur avancé |
| Utilisation frontend vérifiée | La méthode client API subsiste pour compatibilité | Le formulaire courant de recherche appelle `analyzeResearchAssayDoseResponseAdvanced` |

Le formulaire actuel de la page `research-analytics.js` appelle le parcours avancé. Le client `api.js` conserve les deux méthodes. La route historique reste publiée afin de préserver les intégrations et usages existants.

### Décision de compatibilité

La Phase 1F **ne redirige pas** la route historique vers le moteur avancé. Une telle redirection changerait notamment :

- la plage autorisée de bootstrap ;
- les endpoints acceptés ;
- les règles de sélection et d'inclusion des mesures techniques ;
- la structure du résultat et certains diagnostics ;
- l'identifiant de version algorithmique.

Les moteurs peuvent être comparés sur un jeu de référence partagé dans une phase distincte. Une migration algorithmique ne sera justifiée qu'après une étude des écarts numériques, des critères de qualité, des usages frontend et des contrats consommés par les clients.

## 3. Contrat de provenance `nexora.scientific-provenance/v1`

Module de référence : `backend/app/services/scientific_provenance.py`.

Chaque manifeste scellé porte :

- `schema` : `nexora.scientific-provenance/v1` ;
- `schema_version` : `1.0.0` ;
- `manifest_type` ;
- un corps métier déterministe ;
- `manifest_sha256`.

### Manifeste d'analyse

Le manifeste d'une analyse décrit :

```json
{
  "schema": "nexora.scientific-provenance/v1",
  "schema_version": "1.0.0",
  "manifest_type": "scientific_analysis",
  "algorithm": {
    "name": "four_parameter_logistic",
    "version": "..."
  },
  "inputs": [
    {
      "role": "selected_biological_measurements",
      "object_type": "research_assay_measurements",
      "object_id": "assay:<id>",
      "sha256": "...",
      "record_count": 21
    }
  ],
  "parameters": {},
  "parameters_sha256": "...",
  "output": {
    "format": "application/json",
    "sha256": "..."
  },
  "execution": {},
  "software": {
    "application": "Nexora v2",
    "engine_module": "...",
    "python_version": "...",
    "dependencies": {
      "numpy": "...",
      "scipy": "..."
    }
  },
  "interpretation_limit": "...",
  "manifest_sha256": "..."
}
```

Les entrées exactes varient selon le domaine. Le manifeste ne suppose pas que tous les moteurs prennent le même type de données.

### Règles de calcul des empreintes

Les règles communes sont :

1. Sérialisation JSON UTF-8 canonique, avec clés triées, séparateurs compacts et rejet de NaN/Infinity.
2. SHA-256 calculé sur les octets canoniques.
3. `manifest_sha256` calculé sur le manifeste sans son propre champ `manifest_sha256`.
4. `output.sha256` calculé sur le résultat scientifique avant l'ajout de l'enveloppe de provenance. Il n'est donc pas circulaire.
5. Les paramètres sont conservés dans le manifeste et disposent de leur propre empreinte.
6. Les versions de Python, NumPy et SciPy, ainsi que le module moteur, sont enregistrés lorsqu'ils affectent le calcul.

Les champs `input_sha256` historiques des tables d'analyse ne sont pas réinterprétés rétroactivement : leur sens exact est décrit par le manifeste du moteur correspondant. Le manifeste ajoute une empreinte des paramètres et la référence au manifeste de soumission.

## 4. Persistance et idempotence des analyses 4PL

### 4PL avancé

Le service partagé `assay_analytics.execute_dose_response_analysis` appelle toujours `fit_dose_response_4pl`. Il conserve les contrôles curatoriaux et persiste :

- `ResearchAssayAnalysis` avec `analysis_key`, `input_sha256` et `output_sha256` ;
- le manifeste sous `result.provenance.manifest` ;
- le même manifeste dans `ResearchEvidence.metadata.provenance_manifest` ;
- l'empreinte du manifeste dans l'événement de revue et les métadonnées de preuve.

Quand l'appel vient du workflow `experimental.run_4pl`, le manifeste identifie le mode `workflow`, la tâche et le `workflow_run_id). L'appel synchrone avancé identifie son mode d'exécution sans inventer de run HFSM.

### 4PL historique

La route historique conserve son moteur et son enveloppe de réponse existante. Elle gagne :

- une `analysis_key` déterministe, liée au dossier, à la version du moteur, au hash des observations, au manifeste de soumission et aux paramètres ;
- un `output_sha256` ;
- un manifeste de provenance du même schéma ;
- un enregistrement `ResearchEvidence` grade D ;
- une réponse idempotente qui retourne l'analyse déjà persistée au lieu de créer un deuxième endpoint ou une deuxième analyse.

Le résultat scientifique historique n'est pas transformé en résultat avancé : ses champs `estimate`, `confidence_interval`, `data_summary` et autres conservent leur sens actuel. La provenance est ajoutée sous `result.provenance`.

### Historique antérieur à la Phase 1F

Les analyses déjà enregistrées ne sont ni recalculées ni réécrites. Elles peuvent ne pas disposer de `output_sha256`, d'`analysis_key` ou de manifeste. L'API expose un manifeste absent comme absent ; elle ne fabrique pas une provenance historique à partir de paramètres ou de valeurs supposées.

## 5. Provenance HFSM/DAG commune

Le noyau `create_workflow_run` scelle désormais le manifeste d'entrée tout en conservant les clés métier déjà présentes, par exemple `sequence_sha256`, `protein_sha256` ou `measurement_snapshot_sha256`.

Lorsque toutes les étapes du DAG réussissent, `complete_step` construit un manifeste de sortie qui référence :

- l'identifiant, la clé et la version de définition du workflow ;
- les steps exécutés ;
- leurs clés, tâches, files et sorties persistées ;
- l'état de fin des étapes et l'indication qu'une revue qualité est requise.

Le manifeste indique `steps_succeeded`. Il ne préjuge pas du résultat d'une revue curatoriale ultérieure.

La route authentifiée `GET /api/v1/scientific-workflows/{run_id}` expose désormais `input_manifest` et `output_manifest`. Le manifeste de sortie reste compact : il conserve les identifiants et tâches des steps, les clés présentes dans leurs sorties et le SHA-256 de chaque sortie ; les résultats complets restent dans les steps et tables métier. Le step 4PL expose aussi le hash du manifeste de l'analyse qu'il a produite, pour relier les deux niveaux. Les lignes de workflow historiques ne sont pas rétroactivement scellées.

## 6. Préparation du domaine phytochimique LC-MS/MS

Le modèle actuel `PhytochemicalCandidate` est un **registre de candidats chimiques curés**. Il contient notamment le nom, les identifiants chimiques, les SMILES, la taxonomie, les sources, les références d'occurrence, les statuts de vérification et un grade d'évidence.

Il ne représente pas un spécimen végétal, une extraction, une fraction, une injection analytique, un fichier brut ou un pic LC-MS/MS. Ces objets doivent rester séparés.

### Modèle métier proposé

```text
BotanicalSpecimen
    └── PlantMaterialSample
          └── ExtractionBatch
                └── Fraction
                      └── AnalyticalRun
                            ├── RawDataArtifact
                            ├── QualityControlObservation
                            └── SpectralFeature
                                  ├── MS2Spectrum / FragmentEvidence
                                  └── CompoundAnnotation
                                         └── PhytochemicalCandidate (référence curée)
```

| Entité proposée | Responsabilité et données principales |
|---|---|
| `BotanicalSpecimen` | Identification botanique, taxon accepté, identifiant de voucher/herbier, déterminateur, source taxonomique, date et statut de vérification |
| `PlantMaterialSample` | Spécimen parent, organe/partie végétale, collecte, localité, date, collecteur, état de conservation, code d'échantillon |
| `ExtractionBatch` | Échantillon parent, masse initiale, solvant(s), proportion solide/liquide, méthode/SOP/version, température, durée, pH si pertinent, opérateur, masse extraite et rendement |
| `Fraction` | Lot d'extraction parent, méthode de fractionnement, système de solvants/gradient, collecte, volume, masse et rendement |
| `AnalyticalRun` | Fraction injectée, instrument, source ionique, polarité, colonne, méthode LC, gradient, débit, méthode MS/MS, ordre d'injection, opérateur, versions logicielles et lot analytique |
| `RawDataArtifact` | Fichier natif fournisseur et dérivés ouverts, type/MIME, format, taille, URI de stockage, SHA-256, statut de conversion et provenance de conversion |
| `QualityControlObservation` | Blancs, standards, échantillons QC, étalons internes, injections répétées, dérive, carry-over et critères d'acceptation du lot |
| `SpectralFeature` | Run parent, m/z, temps de rétention, intensité, polarité, adduit, charge, cluster isotopique, aire, score de détection et références aux spectres MS/MS |
| `MS2Spectrum / FragmentEvidence` | Spectre MS/MS lié à une feature, énergie de collision, fragments, intensités, méthode d'acquisition et empreinte de l'artefact/source |
| `CompoundAnnotation` | Feature(s) parent(s), candidat composé, bibliothèque et version, identifiant de référence, score de correspondance, fragments soutenant l'annotation, niveau de confiance et revue curatoriale |

### Règles de séparation

- `PhytochemicalCandidate` reste le registre d'identité chimique et de curation ; il ne reçoit pas les mesures de lots, les chromatogrammes ou les spectres bruts.
- Une détection de feature n'est pas une identification de composé.
- Une annotation de bibliothèque ne valide pas seule l'identité d'un composé dans le spécimen.
- Les statuts d'identité chimique, d'occurrence botanique, de qualité analytique et de revue curatoriale restent distincts.
- Une présence rapportée dans une plante ne prouve ni l'abondance dans le lot extrait ni une activité biologique.
- Les liens vers les essais pharmacologiques passent par des références explicites vers les fractions/candidats et les dossiers `ResearchAssay`, jamais par une jointure implicite fondée uniquement sur le nom d'un composé.

### Provenance du pipeline LC-MS/MS

Chaque transformation doit être enregistrée comme un artefact dérivé avec :

- hash du fichier source et hash du fichier transformé ;
- format et version de conversion ;
- logiciel, version, paramètres et référence de bibliothèque ;
- lot analytique, spécimen/échantillon/fraction parent ;
- historique de revue et limitations.

Le manifeste commun de Phase 1F peut encadrer les futures étapes de conversion, détection et annotation, mais la Phase 1F ne crée pas encore les tables LC-MS/MS.

## 7. Critères d'acceptation du futur pilote LC-MS/MS

Avant d'autoriser un premier lot pilote :

1. Les spécimens disposent d'une identification botanique et d'une provenance documentées.
2. Chaque échantillon et extraction dispose d'un identifiant stable et d'un protocole versionné.
3. Les fractions sont reliées au lot d'extraction parent et leurs rendements sont calculables.
4. Les runs incluent blancs, standards et QC selon un plan défini avant acquisition.
5. Les fichiers bruts sont immuables, stockés hors PostgreSQL et vérifiés par SHA-256.
6. Les conversions et détections sont reproductibles à partir des fichiers bruts et paramètres conservés.
7. Les annotations indiquent clairement la bibliothèque, sa version, le score, le niveau de confiance et les limites.
8. Les validations curatoriales ne transforment pas automatiquement une annotation en identité confirmée.
9. Les liens fraction/annotation/assay sont traçables jusqu'au dossier expérimental et à ses observations brutes.
10. Les accès, téléchargements, décisions de curation et transitions sont audités.

## 8. Déploiement et migration

Aucune migration SQL n'est nécessaire pour les fonctionnalités de provenance de cette phase : les tables `ResearchAssayAnalysis`, `ResearchEvidence`, `ScientificWorkflowRun` et `ScientificWorkflowStep` contiennent déjà les champs exploités.

Les nouveaux manifestes sont ajoutés aux nouvelles exécutions uniquement. Les anciennes analyses et anciens workflows sont conservés tels quels.

La création des entités botaniques et LC-MS/MS est différée jusqu'à validation du dictionnaire de données, de la politique de conservation des artefacts, des critères QC et du pilote analytique.

## 9. Tests

La suite ajoute `backend/tests/test_scientific_provenance.py` et étend les tests de workflow et 4PL pour vérifier :

- le JSON canonique et le rejet de valeurs non finies ;
- le scellement et la vérification d'intégrité des manifestes ;
- la conservation des clés métier des manifestes workflow ;
- la génération du manifeste de sortie après réussite de toutes les étapes ;
- la présence de la version des outils et du runtime ;
- les manifestes des deux moteurs 4PL ;
- l'idempotence de l'API historique et l'absence de duplication d'analyse ou d'endpoint ;
- le rattachement de la provenance à `ResearchEvidence`.

La CI exécute la nouvelle suite de provenance en plus des tests BioEvidence et HFSM/DAG existants.
