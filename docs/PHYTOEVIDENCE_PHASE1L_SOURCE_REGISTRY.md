# Phase 1L — PhytoEvidence : registre des sources botaniques et phytochimiques

**Version :** 1.0.0  
**Revue documentaire initiale :** 2 novembre 2026  
**Périmètre de cette première contribution :** registre versionné des sources, règles de licence et API en lecture seule. Aucun dataset externe n'est téléchargé ou importé.

## 1. Pourquoi une Phase 1L distincte ?

La Phase 1K conserve son objectif initial : qualification expérimentale de la chaîne LC-MS/MS 1G–1J sur des acquisitions instrumentales, avec les gates K0 à K7.

PhytoEvidence est un chantier complémentaire. Il organise les références qui relient :
- les taxons, noms acceptés et synonymes botaniques ;
- les plantes, parties utilisées et usages ethnobotaniques documentés ;
- les produits naturels et leurs structures chimiques ;
- les masses, spectres MS/MS et annotations chimiques ;
- les activités biologiques publiées et leurs protocoles ;
- les publications, licences et preuves de provenance.

La présence d'une source dans le registre ne signifie ni que son contenu a été importé, ni que toutes ses entrées sont validées, ni qu'une activité thérapeutique est démontrée.

## 2. Sources recensées et décision initiale

Le registre machine-readable est `backend/app/fixtures/phytochemical_sources.json`. L'API authentifiée en lecture seule est :

~~~http
GET /api/v1/phytochem/sources
GET /api/v1/phytochem/sources?domain=botanical_taxonomy
~~~

Chaque entrée expose son périmètre, son mode d'accès, les droits identifiés, une décision d'ingestion, les exigences de provenance et les références d'audit.

| Source | Apport | Situation des droits | Décision initiale |
|---|---|---|---|
| Dr. Duke / USDA | Plantes, composés, usages ethnobotaniques, activités rapportées | CC0-1.0 déclaré par Data.gov pour les distributions listées | Audit des tables CSV avant import |
| COCONUT | Structures de produits naturels et métadonnées chimiques | CC0 déclaré sur la page officielle de téléchargement | Audit SDF/CSV Lite avant import |
| ChEBI | Identifiants chimiques, formules, masses, synonymes, ontologie | CC BY 4.0 | Import pilote avec attribution |
| GBIF | Taxonomie et occurrences géographiques | Licence au niveau du dataset/de l'occurrence | Gate de licence par enregistrement |
| POWO/WCVP | Noms acceptés, synonymes, distribution | Licence décrite au niveau des datasets Kew ; ne pas propager la licence des seules métadonnées | Gate par dataset/version |
| ANPDB | Produits naturels africains et références biologiques | CC BY-NC 4.0 | Usage non commercial, sauf autorisation séparée |
| IMPPAT 3.0 | Plantes médicinales indiennes, composés, usages et formulations | CC BY-NC-ND 4.0 | Référence externe ; pas d'intégration dérivée sans autorisation |
| FooDB | Constituants alimentaires et spectres MS/MS expérimentaux/prédits | CC BY-NC 4.0 | Usage non commercial, sauf permission commerciale explicite |
| PhytoHub | Métabolites et références spectrales | Licence générale de réutilisation non confirmée | Consultation externe uniquement |
| p-ANAPL | Collection africaine de produits naturels | Licence générale de dataset électronique ouvert non confirmée | Référence bibliographique ; contacter les détenteurs |
| Phenol-Explorer | Polyphénols, composition alimentaire et métabolisme | Conditions actuelles de réutilisation à clarifier | Bloqué en attente de clarification |
| PhytochemDB | Structures phytochimiques et annotations de screening | Licence globale de réutilisation non confirmée | Bloqué en attente de clarification |
| Tropicos | Nomenclature botanique, synonymes et spécimens | API avec clé ; échanges massifs à coordonner | API/permission à obtenir |
| AfroDb / WANPDB / Sahel 50 | Sources déjà prises en charge ou fixture de projet | Droits dépendants du fichier et de la provenance exacts | Revue de licence par archive |

### Corrections et précautions documentaires

- La publication fondatrice de p-ANAPL décrit une collection d'environ **534 échantillons de composés**, pas une base ouverte de plus de 4 000 espèces végétales.
- Un accès gratuit ou un bouton de téléchargement ne constitue pas automatiquement une permission de redistribution ou d'usage commercial.
- Pour GBIF, la licence doit être conservée au niveau du dataset/de l'occurrence. Les enregistrements CC BY-NC ne sont pas admissibles à une réutilisation commerciale.
- Pour POWO/WCVP, Kew indique que les conditions de licence sont décrites dans les métadonnées du dataset ; la licence des métadonnées ne doit pas être propagée automatiquement aux données sous-jacentes.
- La licence d'un article scientifique décrivant une base ne remplace pas la licence applicable à la base elle-même.
- Les conditions d'une source doivent être revérifiées au moment de chaque téléchargement : une revue documentaire datée n'est pas un avis juridique permanent.

## 3. Principes d'architecture

Le registre est distinct de `PhytochemicalCandidate`.

- `PhytochemicalCandidate` reste le registre opérationnel des candidats importés et curés.
- `phytochemical_sources.json` décrit les sources, leurs périmètres, les droits identifiés et les décisions d'ingestion.
- Le nouvel endpoint ne modifie aucune table PostgreSQL et ne déclenche aucune requête réseau.
- Les prochains adaptateurs devront appliquer la politique de droits avant téléchargement, transformation ou redistribution.
- Les permissions d'un dataset ou d'un enregistrement priment sur une licence générique de portail.
- Les données brutes, les références transformées et les annotations calculées doivent conserver des provenances distinctes.

## 4. Niveaux de preuve à séparer

1. **Occurrence botanique** : une observation ou source taxonomique documente un taxon dans un lieu/à une date. Elle ne prouve pas la présence d'un composé.
2. **Occurrence composé–plante** : une publication ou une mesure source affirme qu'un composé a été observé dans une espèce et une partie végétale précises.
3. **Identification chimique** : masse, formule, spectre, standard ou autre preuve analytique, avec un niveau d'identification explicite.
4. **Activité biologique** : essai, système expérimental, endpoint, unité, conditions et référence.
5. **Usage traditionnel** : information ethnobotanique contextualisée par la langue, la région, la partie utilisée et la source. Ce n'est pas une preuve d'efficacité clinique.

Une annotation ou un score de docking ne doit jamais être transformé automatiquement en activité biologique confirmée, innocuité ou efficacité clinique.

## 5. État de livraison PhytoEvidence au 3 novembre 2026

Les étapes ci-dessous décrivent l'état intégré dans `main`. Elles ne remplacent pas la qualification instrumentale externe de la Phase 1K.

### 1L-B — Identité botanique et résolution taxonomique — intégrée

- Résolution des noms via GBIF ChecklistBank v2 avec ciblage explicite WCVP v16.
- Conservation des synonymes, noms acceptés, réponse source, version, provenance et SHA-256.
- Revue indépendante avec identifiants numériques du créateur et du réviseur ; auto-revue bloquée par le service et PostgreSQL.
- La nomenclature acceptée ne certifie pas l'identité physique d'un spécimen.

Voir [la documentation Phase 1L-B](PHYTOEVIDENCE_PHASE1L_TAXONOMIC_RESOLUTION.md).

### 1L-C — Preuves d'occurrence plante–composé — intégrée

- Enregistrement séparé des preuves bibliographiques, liées à un spécimen et liées à une observation LC-MS/MS.
- Conservation de la chaîne spécimen → échantillon → extraction → fraction.
- Les preuves LC-MS/MS exigent une annotation MSI niveau 1 et des mesures appariées échantillon/standard avec recalcul des écarts de masse et de temps de rétention.
- Revue curatoriale indépendante avant acceptation ; une occurrence rapportée n'est pas automatiquement une observation expérimentale Nexora.

Voir [la documentation Phase 1L-C](PHYTOEVIDENCE_PHASE1L_OCCURRENCE_EVIDENCE.md).

### 1L-D — Vue intégrée des preuves — intégrée

- Projection candidate-centred, authentifiée et en lecture seule.
- Liaison des domaines taxonomique, botanique, chimique, LC-MS/MS et QC-RLSC sans fusionner leurs statuts.
- Isolation des études par propriétaire, masquage des coordonnées botaniques fines et contrôle des revues qualité indépendantes pour exposer les signaux QC-RLSC.

Voir [la documentation Phase 1L-D](PHYTOEVIDENCE_PHASE1L_INTEGRATION.md).

### 1L-E — Liaison aux essais expérimentaux et à la bioactivité — intégrée

- Raccordement des dossiers expérimentaux par identifiants screening-compound/candidat explicites (candidats primaire et secondaires), jamais par rapprochement de noms ou de SMILES.
- Exposition des mesures sélectionnées, réplicats, endpoints, analyses 4PL déjà stockées, intervalles bootstrap et métadonnées de provenance.
- Revue `accepted_for_analysis` réservée à un administrateur actif distinct du créateur ; la projection revérifie cette indépendance.
- Aucun second moteur 4PL ni nouvelle table/migration ; aucune efficacité pharmacologique ou clinique n'est déduite.

Voir [la documentation Phase 1L-E](PHYTOEVIDENCE_PHASE1L_BIOACTIVITY_LINKAGE.md).

Les phases 1L-B à 1L-E ont été intégrées dans `main` par la PR [#30](https://github.com/diarraibrahamimohamed-ops/nexora_v2/pull/30), commit de fusion `2f33d06d80f06e34cbc8c30aa48a831865ca6b94`. La CI post-fusion du commit est verte ([run 41719640849](https://github.com/diarraibrahamimohamed-ops/nexora_v2/actions/runs/41719640849)). Les tests utilisent des jeux synthétiques pour les nouvelles projections : ils valident les contrôles logiciels, pas une activité mesurée sur un composé réel.

### Gate distincte — Phase 1K

La Phase 1K reste conditionnelle : la qualification de MTBLS13040 exige encore une revue humaine de licence, d'inventaire complet, de correspondance échantillon–fichier, de rôles pooled-QC/blancs, de polarité et d'ordre d'injection. Une CI verte ne valide pas ces éléments expérimentaux.

## 6. Critères d'acceptation de cette première contribution

- JSON valide, versionné, avec identifiants uniques.
- Chaque source possède périmètre, statut des droits, décision d'ingestion et références.
- Aucun téléchargement automatique ni effet de bord depuis l'API.
- Les droits au niveau dataset/enregistrement restent conditionnels.
- Les licences NC/ND ne sont pas présentées comme compatibles avec un usage commercial libre.
- API authentifiée, filtrable par domaine et en lecture seule.
- Aucun changement des gates K0–K7 ni des tables LC-MS/MS existantes.

Cette contribution ne revendique aucune validation phytochimique, spectrométrique, pharmacologique ou clinique.

## 7. Références officielles

- [Dr. Duke / USDA Data.gov](https://catalog.data.gov/dataset/dr-dukes-phytochemical-and-ethnobotanical-databases)
- [COCONUT downloads](https://coconut.naturalproducts.net/download)
- [ChEBI licensing](https://www.ebi.ac.uk/chebi/about) · [downloads](https://www.ebi.ac.uk/chebi/downloads)
- [GBIF data licensing](https://www.gbif.org/publishing-data)
- [Kew terms of use](https://www.kew.org/science/collections-and-resources/data-and-digital/terms-of-use) · [POWO citation](https://powo.science.kew.org/cite-us)
- [ANPDB license](https://phabidb.vm.uni-freiburg.de/anpdb/license/)
- [IMPPAT 3.0](https://cb.imsc.res.in/imppat/) · [downloads and license](https://cb.imsc.res.in/imppat/download)
- [FooDB terms](https://foodb.ca/about) · [downloads](https://foodb.ca/downloads)
- [PhytoHub](https://phytohub.eu/)
- [p-ANAPL publication](https://pmc.ncbi.nlm.nih.gov/articles/PMC3944075/)
- [Phenol-Explorer downloads](https://phenol-explorer.eu/downloads)
- [PhytochemDB](https://www.phytochemdb.com/about)
- [Tropicos API](https://services.tropicos.org/help) · [terms](https://legacy.tropicos.org/TermsOfUse.aspx)
