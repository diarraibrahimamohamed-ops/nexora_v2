# Nexora v2 — Phase 1G : domaine botanique et expérimental LC-MS/MS

## 1. Objectif et frontière fonctionnelle

La Phase 1G ajoute le registre de chaîne de possession botanique et d'acquisition LC-MS/MS à N3XORA-Sahel. Elle permet de tracer le matériel végétal, les opérations d'extraction/fractionnement, les lots d'acquisition, les injections, les fichiers, les contrôles qualité et les résultats importés d'un logiciel analytique externe.

Cette phase **n'implémente pas** de moteur de lecture universel des fichiers instrumentaux, de peak-picking, d'alignement, de déconvolution, de matching spectral automatique ou de quantification. Elle enregistre les versions logicielles, paramètres, empreintes, bibliothèques et résultats fournis par des outils externes.

Une feature LC-MS n'est pas une molécule identifiée. Une annotation n'est pas une validation d'activité pharmacologique. Un statut curatoriel ne doit jamais être interprété comme une preuve clinique.

## 2. Séparation des domaines

`PhytochemicalCandidate` reste le registre existant des candidats chimiques curés. Il conserve les identifiants chimiques, SMILES, taxonomie/occurrence botanique, sources bibliographiques et décisions de curation déjà présentes.

La Phase 1G ne déplace ni ne réécrit ces données. Elle ajoute une chaîne expérimentale distincte :

```text
ResearchStudy
  └── BotanicalSpecimen
        └── PlantMaterialSample
              └── ExtractionBatch
                    └── PlantFraction
                          └── LcmsInjection
                                ├── LcmsDataArtifact
                                └── QualityControlObservation

ResearchStudy
  └── LcmsAnalyticalBatch
        └── LcmsInjection
              └── LcmsDataArtifact
                    └── (conversion / dérivé) LcmsDataArtifact

LcmsAnalyticalBatch
  └── LcmsProcessingRun
        ├── LcmsFeature
        │     ├── LcmsFeatureObservation (par injection)
        │     ├── LcmsMs2Spectrum (par injection / scan)
        │     └── LcmsCompoundAnnotation
        │            ├── PhytochemicalCandidate (référence facultative)
        │            └── LcmsSpectralLibrary (référence facultative)
        └── Provenance manifest (entrée / sortie)
```

### Entités ajoutées

| Entité | Fonction |
|---|---|
| `BotanicalSpecimen` | Voucher, taxon rapporté et accepté, source taxonomique, déterminateur, collecte, localité et accès aux coordonnées |
| `PlantMaterialSample` | Partie de plante, état frais/sec/congelé/poudre, masse, humidité et conservation |
| `ExtractionBatch` | Méthode/SOP, version, système de solvants, rapport solide/liquide, masses, température, durée, pH et rendement |
| `PlantFraction` | Lot d'extraction parent, ordre, technique de séparation, éluants, volume, masse et rendement |
| `LcmsAnalyticalBatch` | Instrument, source, colonne, phases mobiles, gradient, débit, température, méthode d'acquisition et modes de polarité |
| `LcmsInjection` | Ordre, type d'injection (sample, pooled QC, blancs, standard, calibration...), échantillon/fraction et réplicat technique |
| `LcmsDataArtifact` | Fichier brut ou dérivé, format, taille, SHA-256, stockage externe et lien vers un artefact parent |
| `LcmsQualityControlObservation` | Mesure QC, unité, statut, critères d'acceptation, méthode et justification |
| `LcmsSpectralLibrary` | Nom/version, licence, référence, empreinte de la base et décision curatoriale |
| `LcmsProcessingRun` | Outil/version, workflow/version, paramètres, artefacts d'entrée, manifeste et empreinte de sortie |
| `LcmsFeature` | m/z précurseur, temps de rétention, polarité, charge, adduit, formule candidate et flags |
| `LcmsFeatureObservation` | Intensité, aire/hauteur, m/z et temps observés pour chaque injection |
| `LcmsMs2Spectrum` | Injection, feature, scan, précurseur, énergie de collision, polarité et fragments ou fichier source |
| `LcmsCompoundAnnotation` | Annotation liée à une feature, méthode, bibliothèque, candidat, niveau MSI, score, preuves et revue |

## 3. API livrée

Toutes les routes sont sous `/api/v1/lcmsms`. Les routes d'étude exigent une authentification et vérifient que l'étude appartient à l'utilisateur. Les registres de bibliothèques et décisions curatoriales exigent l'autorisation administrateur.

| Action | Route |
|---|---|
| Créer / lister un spécimen | `POST|GET /studies/{study_id}/specimens` |
| Revue botanique du spécimen | `POST /specimens/{specimen_id}/review` |
| Enregistrer / consulter les échantillons | `POST /studies/{study_id}/samples`, `GET /studies/{study_id}/samples` |
| Enregistrer / consulter les extractions | `POST /studies/{study_id}/extractions`, `GET /studies/{study_id}/extractions` |
| Enregistrer / consulter les fractions | `POST /extractions/{extraction_id}/fractions`, `GET /studies/{study_id}/fractions` |
| Enregistrer / consulter les lots analytiques | `POST /studies/{study_id}/analytical-batches`, `GET /studies/{study_id}/analytical-batches` |
| Enregistrer / consulter les injections | `POST /batches/{batch_id}/injections`, `GET /batches/{batch_id}/injections` |
| Archiver / lister un artefact | `POST /batches/{batch_id}/injections/{injection_id}/artifacts?artifact_role=raw_data`, `GET /batches/{batch_id}/artifacts` |
| Télécharger un artefact | `GET /artifacts/{artifact_id}/download` |
| Ajouter / consulter les QC | `POST /batches/{batch_id}/quality-controls`, `GET /batches/{batch_id}/quality-controls` |
| Enregistrer / revoir une bibliothèque | `POST /spectral-libraries`, puis `POST /spectral-libraries/{library_id}/review` |
| Enregistrer / consulter un traitement externe | `POST /studies/{study_id}/batches/{batch_id}/processing-runs`, `GET /studies/{study_id}/processing-runs` |
| Importer / consulter des features | `POST /processing-runs/{processing_run_id}/features`, `GET /processing-runs/{processing_run_id}/features` |
| Ajouter / consulter les observations | `POST|GET /features/{feature_id}/observations` |
| Ajouter / consulter les spectres MS2 | `POST|GET /features/{feature_id}/ms2-spectra` |
| Ajouter / consulter / revoir une annotation | `POST|GET /features/{feature_id}/annotations`, puis `POST /annotations/{annotation_id}/review` |
| Synthèse d'étude | `GET /studies/{study_id}/overview` |

Les méthodes frontend correspondantes sont exposées dans `frontend/js/api.js` sous les noms `createLcmsSpecimen`, `createLcmsPlantSample`, `createLcmsExtraction`, `createLcmsFraction`, `createLcmsAnalyticalBatch`, `createLcmsInjection`, `uploadLcmsArtifact`, `registerLcmsProcessingRun`, `importLcmsFeatures`, `addLcmsFeatureObservation`, `addLcmsMs2Spectrum` et `addLcmsCompoundAnnotation`.

## 4. Stockage, intégrité et conservation

Les fichiers ne sont pas stockés dans PostgreSQL. Ils sont copiés de manière streamée vers `LCMS_STORAGE_DIR` (défaut `/storage/lcmsms`), qui est couvert par le volume `./storage:/storage` déjà monté dans les services API et worker de `docker-compose.yml`.

Chaque fichier est :
- limité par `LCMS_MAX_ARTIFACT_BYTES` (défaut 1 GiB, maximum aligné sur la colonne PostgreSQL `INTEGER`) ;
- écrit dans un fichier temporaire créé côté serveur ;
- haché SHA-256 pendant le transfert ;
- rangé sous un chemin content-addressed dérivé de son empreinte ;
- re-vérifié au téléchargement.

Le SHA-256 établit l'intégrité octet-à-octet. Il ne valide pas le schéma XML mzML, la sémantique du fichier, le nombre de scans ni la qualité instrumentale. Le champ `validation_status=checksum_verified` ne signifie donc pas « fichier mzML scientifiquement validé ».

Les coordonnées GPS sont `restricted` par défaut. Les réponses API les masquent pour ce statut ; le statut `coarse` ne retourne que des coordonnées arrondies à deux décimales ; le statut `public` expose les coordonnées exactes. L'ouverture de coordonnées précises doit rester explicite et tenir compte de la sensibilité de la collecte.

### Formats acceptés par le dépôt Phase 1G

Le registre accepte actuellement les extensions `.raw`, `.wiff`, `.wiff2`, `.mzml`, `.mzxml`, `.imzml`, `.cdf`, `.mgf`, `.msp`, `.mztab`, `.csv`, `.tsv`, `.json` et `.zip`.

Les répertoires instrumentaux tels que `.d` ne sont pas téléversables comme fichiers unitaires. Il faut les compresser séparément en ZIP. Le serveur ne décompresse pas et n'inspecte pas automatiquement l'archive lors de cette phase.

Le format ouvert **mzML** est destiné à archiver et partager données MS acquises et contexte instrumental ; le dépôt HUPO-PSI maintient la spécification mzML 1.1.0 : [HUPO-PSI mzML](https://github.com/HUPO-PSI/mzML).

**mzTab-M** est un format tabulé de rapport des résultats métabolomiques après traitement. Le dépôt HUPO-PSI indique actuellement une version 2.1.0 en développement et la version 2.0.0 comme base publiée/stable : [HUPO-PSI mzTab-M](https://github.com/HUPO-PSI/mzTab-M). Nexora n'implémente pas encore son validateur complet ; l'extension `.mztab` est enregistrée comme artefact, pas déclarée conforme automatiquement.

Les métadonnées d'étude, d'échantillon et d'essai s'alignent conceptuellement sur la structuration ISA utilisée par MetaboLights. Ses guides recommandent de relier chaque échantillon à ses fichiers bruts et dérivés dans la feuille d'essai, et d'organiser distinctement les fichiers raw/derived : [MetaboLights — Assay](https://ebi-metabolights.github.io/guides/Assay/) et [MetaboLights — Files](https://ebi-metabolights.github.io/guides/Files/).

## 5. Qualité analytique

Nexora ne définit aucun seuil universel de dérive RT, d'erreur ppm, de CV des pooled-QC ou de carry-over. Ces seuils dépendent notamment de la plateforme, de la matrice, de la méthode, des analytes et du protocole du laboratoire.

Chaque observation QC enregistre :
- un type (pooled QC, solvent blank, extraction blank, internal standard, system suitability, carry-over, mass accuracy, retention-time drift, calibration ou other) ;
- un nom de métrique, une valeur et une unité ;
- un statut `not_assessed`, `pass`, `warning` ou `fail` ;
- la méthode d'évaluation, les critères d'acceptation et la justification.

Les critères doivent être fixés dans le protocole analytique avant acquisition et associés au lot. Le statut manuel/importé n'est pas confondu avec une mesure calculée par Nexora. Aucun lot ne reçoit automatiquement un statut « validé » sur la base du nombre de QC saisis.

Le rendement d'extraction/fraction est calculé comme rapport masse récupérée / masse d'entrée × 100. Les valeurs supérieures à 100 % sont conservées avec avertissement : elles doivent conduire à vérifier humidité, solvant résiduel, tare, unités et méthode de pesée ; elles ne sont ni clampées ni interprétées comme un rendement valide.

## 6. Annotations chimiques et niveaux MSI

Les niveaux MSI de confiance d'identification sont distincts des grades d'évidence Nexora.

- **MSI 1** : identification basée sur un standard authentique, analysé dans les mêmes conditions, avec au moins deux propriétés analytiques indépendantes et orthogonales concordantes.
- **MSI 2** : annotation probable par comparaison à une bibliothèque/référence sans standard authentique analysé dans le même laboratoire.
- **MSI 3** : appartenance probable à une classe de composé.
- **MSI 4** : feature inconnue non caractérisée.

Référence de fond : Sumner et al., *Proposed minimum reporting standards for chemical analysis* (2007), [texte intégral PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC3772505/).

Pour MSI 1, le contrat Nexora exige :
1. une injection de type `reference_standard` du même lot LC-MS ;
2. les attestations de standard authentique et de même méthode ;
3. au moins deux propriétés distinctes parmi les propriétés actuellement documentables dans le modèle : masse exacte, temps de rétention et spectre MS2 ;
4. une mesure m/z pour le standard si la masse exacte est invoquée, avec `accurate_mass_abs_error_ppm` et `accurate_mass_tolerance_ppm` ;
5. une mesure RT pour le standard si RT est invoqué, avec `retention_time_abs_delta_seconds` et `retention_time_tolerance_seconds` ;
6. un spectre MS2 rattaché au standard si MS2 est invoqué, avec `ms2_match_score` et `ms2_match_score_type` ;
7. les drapeaux de concordance associés (`accurate_mass_match`, `retention_time_match`, `ms2_match`) ;
8. une revue admin motivée avant le statut `accepted`.

Les valeurs et tolérances sont déclarées et revues selon le protocole de la méthode : `accurate_mass_abs_error_ppm` / `accurate_mass_tolerance_ppm`, `retention_time_abs_delta_seconds` / `retention_time_tolerance_seconds`, ou `ms2_match_score` / `ms2_match_score_type`. Phase 1G ne calcule pas encore automatiquement le ppm, la similarité de spectre, l'alignement RT ou le score de bibliothèque. Les mesures de standard doivent toutefois être réellement référencées dans les observations/spectres Nexora. Le curateur conserve la responsabilité de vérifier que les tolérances et scores conviennent à la méthode.

Une bibliothèque spectrale ne passe au statut `reviewed` qu'avec un SHA-256 de son jeu de référence, une licence déclarée et une citation ou source. La licence et la référence sont conservées dans le manifeste du traitement qui l'utilise.

Même une annotation acceptée MSI 1 ne signifie pas activité pharmacologique. Chaque annotation crée aussi une entrée `ResearchEvidence` de grade D, statut `computational_hypothesis`, liée à la feature et à l'étude. La revue ajoute le statut et le curateur, mais n'augmente pas automatiquement le grade D ni ne crée une preuve d'efficacité.

## 7. Provenance du traitement

Un `LcmsProcessingRun` référence les artefacts d'entrée exacts, leur SHA-256, le logiciel et la version, la méthode de traitement, les paramètres et la bibliothèque éventuelle.

Le manifeste d'entrée utilise le schéma scellé partagé `nexora.scientific-provenance/v1`. L'import des features calcule le SHA-256 du payload JSON canonique des features et génère un manifeste de sortie distinct avec le lien au hash d'entrée.

**Il n'y a pas d'algorithme Nexora de détection ou d'annotation automatique** dans cette phase. Les features et spectres sont importés depuis un moteur analytique externe déclaré. Le registre ne prétend pas reproduire le logiciel qui a fourni le tableau.

## 8. Migration et déploiement

Migration additive :
`infra/postgres/migrations/20261101_lcmsms_experimental_domain.sql`

Le schéma des nouvelles installations est synchronisé dans `infra/postgres/init.sql`. `ensure_schema()` peut créer les tables LC-MS/MS manquantes sur des volumes existants. La migration SQL doit néanmoins être appliquée pour les contraintes et colonnes ajoutées dans un volume existant ; aucune table historique `phytochem_candidates` ou `research_assays` n'est supprimée ou réécrite.

Configuration :
- `LCMS_STORAGE_DIR=/storage/lcmsms`
- `LCMS_MAX_ARTIFACT_BYTES=1073741824`

Le volume `./storage:/storage` est déjà monté par Docker Compose pour l'API et les workers. Il n'est pas nécessaire d'ajouter un second volume ; il faut toutefois sauvegarder ce répertoire avec PostgreSQL pour conserver la cohérence des références artefacts.

Nginx autorise les requêtes API jusqu'à 1 GiB et applique des délais plus longs aux transferts volumineux. La limite du proxy et celle de l'API doivent rester alignées.

## 9. Hors périmètre explicite

- parseurs propriétaires natifs et lecture garantie de tous les formats constructeur ;
- décompression récursive et inspection sécurisée de chaque archive ;
- conversion mzML, chromatogrammes et spectres par moteur intégré ;
- peak picking, détection d'isotopes, alignement inter-injection, déconvulution ;
- normalisation, imputation, quantification et statistiques QC automatiques ;
- correspondance MS/MS aux bibliothèques, score spectral ou identification automatique ;
- export/validation complet mzTab-M / ISA-Tab ;
- UI complète de laboratoire et gestion avancée des droits par rôle expérimental.

Ces fonctionnalités nécessitent un pilote avec méthodes d'acquisition, fichiers réels, choix de logiciels ouverts, standards de référence, critères QC et bibliothèque(s) sous licence compatible.

## 10. Critères de recette Phase 1G

Les tests automatisés couvrent une chaîne synthétique bout-en-bout : spécimen, curation taxonomique, échantillon, extraction, fraction, batch, injections, artefact externe, vérification du hash, QC, bibliothèque versionnée/curée, manifeste de traitement, import features, observations, MS2, annotation MSI 1 sur deux propriétés orthogonales et lien vers `ResearchEvidence`.

Les tests ne simulent pas une vraie acquisition instrumentale et ne valident pas chimiquement la feature synthétique.
