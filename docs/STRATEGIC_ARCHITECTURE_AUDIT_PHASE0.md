# N3XORA — Audit de l'architecture scientifique et stratégie d'évolution

**Document :** Phase 0 — état des lieux et décisions d'architecture  
**Date :** 31 octobre 2026  
**Branche auditée :** main  
**Commit de référence :** 8b874ff584727c2eee1ebfd8b24399b932e0c639  
**Périmètre :** backend, modèles, API, workers, frontend, migrations, documentation et intégration scientifique.

---

## 1. Décision exécutive

N3XORA possède déjà plusieurs fondations qu'il faut préserver : une architecture FastAPI/PostgreSQL/Celery, des analyses de séquences, du docking, une orchestration partielle des études de recherche, un catalogue phytochimique avec curation, un registre de preuves et une analyse dose-réponse 4PL sensible aux réplicats biologiques.

La bonne stratégie n'est donc pas une réécriture. Il faut faire évoluer le monolithe modulaire existant vers une plateforme de workflows scientifiques interconnectés, progressivement et par ajouts compatibles.

La décision proposée est :

1. généraliser progressivement le noyau d'exécution autour de HFSM + DAG + transactional outbox + registre de provenance ;
2. conserver Celery/Redis comme exécuteur asynchrone à court terme ;
3. consolider le modèle de preuve commun entre les modules ;
4. faire du module phytochimique actuel le socle de curation, puis ajouter une chaîne métabolomique expérimentale distincte ;
5. n'introduire HMMER, réseaux de Petri, GRN, FBA, ODE et ABM que pour des questions biologiques explicites.

Aucune technologie de workflow supplémentaire n'est requise pour la première étape.

## 2. État actuel constaté dans le dépôt

### 2.1 Architecture logicielle

Les éléments inspectés montrent :

- API : FastAPI et SQLAlchemy ;
- persistance : PostgreSQL ;
- exécution asynchrone : Celery avec Redis ;
- files déclarées : docking, analysis, qsa et structure ;
- interface : pages HTML et JavaScript modulaire ;
- déploiement décrit : Docker Compose.

Le fichier docker-compose.yml qualifie explicitement sa configuration d'environnement de développement (Uvicorn avec rechargement). Elle ne doit donc pas être présentée comme un profil institutionnel de production.

### 2.2 Modules scientifiques présents

| Domaine | Capacités déjà présentes dans le dépôt | État architectural |
|---|---|---|
| ADN/ARN | recherche NCBI, analyse de séquences, transcription/traduction, analyse asynchrone, alignement et phylogénie AfriBio | moteur existant à conserver |
| Variants et recherche | études, variants, conséquences nucléotidiques/protéiques, cibles, provenance structurale | workflows de recherche déjà structurés |
| Protéines et structures | résolution de structures, validation de correspondance de séquence, tâches Celery | pipeline spécialisé |
| Docking | AutoDock Vina, fpocket, préparation chimique RDKit, agrégation ASP des poses | moteur existant ; ASP n'est pas un moteur de docking autonome |
| Phytochimie | catalogue ANPDB/AfroDb/WANPDB/SAHEL_50, curation d'identité, taxonomie/occurrence, descripteurs RDKit, gate de screening | catalogue et curation, pas encore métabolomique complète |
| Résistance antimicrobienne | module AMR avec état de disponibilité AMRFinderPlus | analyse génotypique ; ne remplace pas un antibiogramme |
| HSA | indicateurs heuristiques de stabilité de séquence | outil exploratoire, pas un prédicteur validé de mutation |
| Expérimental | dossiers d'essai, mesures, événements de revue, import tabulaire, analyse 4PL et bootstrap par réplicats biologiques | socle expérimental présent |
| Evidence | registre de revendications, sources, méthodes, versions, empreintes et limites | début de couche de preuve commune |
| Recherche locale | profils de recherche, paquets hors ligne, assistant lexical/Ollama facultatif | support documentaire complémentaire |

### 2.3 Workflow FSM et exécution asynchrone

Le dépôt possède déjà :

- research_workflow_fsm.py : machine à états déterministe ;
- research_workflow.py : transitions et contrôles de préparation d'une ResearchStudy ;
- ResearchWorkflowEvent : journal de transitions et clé d'idempotence ;
- ResearchTaskOutbox et task_outbox.py : intention de tâche persistée dans la transaction métier puis publiée vers Celery ;
- outbox-dispatcher dans Docker Compose.

La FSM actuelle concerne le cycle de vie d'une étude de recherche et reste plate : draft → data_review → analysis → results_review → completed, avec blocked et cancelled. Elle n'est pas une HFSM générique à l'échelle de l'application.

L'outbox est utilisée par plusieurs opérations du domaine Research, notamment la résolution de structure, le docking d'étude et des comparaisons WT/variant. En revanche, les routes génériques /docking/submit et /analysis/sequence publient encore directement via Celery après persistance du job. Ces chemins n'ont donc pas encore les mêmes garanties de soumission.

L'outbox assure une livraison at-least-once, pas exactement une fois. Ses consommateurs doivent être idempotents. Les tâches existantes doivent être auditées sur ce point avant généralisation : les implémentations inspectées ne montrent pas partout une barrière explicite empêchant de recalculer un job déjà terminé après une redélivrance.

### 2.4 Périmètre réel du module phytochimique actuel

Le module actuel sait déjà :

- importer des candidats depuis ANPDB CSV, AfroDb SDF, WANPDB CSV fourni par l'utilisateur et le seed SAHEL_50 ;
- archiver les fichiers sources avec SHA-256 ;
- normaliser des SMILES et calculer des descripteurs RDKit ;
- comparer CID, InChIKey, SMILES et synonymes PubChem pour la vérification d'identité ;
- résoudre un taxon via GBIF, sans prétendre que GBIF prouve l'occurrence d'un composé dans une plante ;
- demander une référence et une attestation humaine pour l'occurrence botanique ;
- conserver une revue humaine, un grade de preuve et des notes ;
- bloquer l'accès au docking tant que l'identité chimique et l'occurrence botanique ne sont pas suffisamment documentées ;
- relier les candidats curés aux parcours Research Screening et à la provenance du docking.

Cette base est utile et doit être conservée.

En revanche, l'API et les modèles inspectés ne constituent pas encore une chaîne expérimentale LC-MS/MS, GC-MS ou RMN. Il manque notamment des objets métier distincts pour :

- spécimen botanique et numéro de voucher ;
- échantillon végétal et partie de plante ;
- lot d'extraction, protocole, solvants, conditions et rendement ;
- fraction et sous-fraction ;
- lot analytique, instrument, méthode, mode ionique et contrôles qualité ;
- fichiers spectraux bruts et fichiers convertis ;
- caractéristiques spectrales (m/z, temps de rétention, adduct, isotopes, MS/MS) ;
- annotations spectrales avec références, scores et niveau de confiance ;
- standards authentiques et résultats de comparaison.

Le modèle PhytochemicalCandidate est un registre de candidats et de curation. Il ne doit pas être surchargé pour représenter à lui seul des échantillons, des extractions, des signaux instrumentaux et des identifications.

### 2.5 Cohérence de la preuve entre modules

ResearchEvidence est actuellement rattaché à une ResearchStudy et peut décrire un objet au moyen de object_type/object_id. Les candidats phytochimiques disposent aussi de leurs propres champs de curation et d'evidence grade. Des liens existent entre candidats et screening/docking, mais le graphe métier complet (spécimen → extrait → fraction → signal → annotation → composé → cible → essai) n'est pas encore modélisé explicitement.

La priorité est de construire des identifiants et relations cohérents, pas d'ajouter immédiatement une base de données graphe.

### 2.6 Éléments non observés dans le code inspecté

Aucune implémentation ni dépendance intégrée n'a été repérée pour :

- HMMER/profile-HMM ;
- réseaux de Petri ;
- réseaux de régulation génique (GRN) ;
- FBA/COBRApy ;
- modèles ODE/ABM génériques ;
- arbres de comportement ;
- pipeline de traitement spectral LC-MS/MS/GC-MS/RMN.

Ces approches sont des possibilités de roadmap, pas des capacités que Nexora possède déjà.

## 3. Décisions d'architecture à retenir

### 3.1 Une HFSM par agrégat d'exécution, pas une FSM globale monolithique

La HFSM doit contrôler les transitions et sous-états d'une exécution. Elle ne doit pas remplacer les états métier de tous les objets.

Proposition de cycle de vie d'un WorkflowRun :

- created
- validating
- ready
- queued
- running
  - running.preparing
  - running.executing
  - running.postprocessing
- quality_review
  - quality_review.pending
  - quality_review.accepted
  - quality_review.rejected
- états terminaux : succeeded, failed, cancelled
- états de contrôle : blocked, retry_wait, needs_user_action

La transition doit être vérifiée par une politique versionnée et journalisée. L'état de ResearchStudy reste distinct de l'état d'un WorkflowRun, lui-même distinct de l'état d'un WorkflowStep.

### 3.2 Le DAG décrit les dépendances scientifiques

Le DAG indique quelles étapes attendent quelles sorties, permet les branches parallèles et les points de synchronisation. La HFSM gouverne le cycle de vie ; elle ne remplace pas le graphe de dépendances.

Exemple phytochimique :

specimen_validation → extraction → analytical_import → feature_processing → annotation → curation → optional_docking → assay → 4PL_analysis → report

Le workflow doit aussi accepter les branches parallèles, par exemple le traitement indépendant de plusieurs lots analytiques, et les synchroniser avant la comparaison inter-échantillons.

### 3.3 Celery/Redis restent le mécanisme d'exécution à court terme

Ne pas ajouter simultanément Nextflow, Airflow, Temporal et un moteur maison. Consolider d'abord Celery et l'outbox :

- toute soumission asynchrone devient transactionnelle ;
- une intention de tâche est persistée avec l'objet métier ;
- chaque tâche reçoit une clé idempotente stable ;
- le worker vérifie l'état attendu avant d'exécuter et avant de valider ses sorties ;
- les reprises, échecs, annulations et expirations sont visibles ;
- les dépendances DAG ne sont libérées qu'après succès de l'étape parente et validation de ses artefacts.

Nextflow pourra être évalué plus tard comme exécuteur de pipelines lourds sur HPC/SLURM/Kubernetes. CWL pourra être évalué comme format portable de description d'outils et de workflows. Aucun des deux n'est une dépendance de la première phase.

### 3.4 Contrat commun pour chaque moteur scientifique

Chaque moteur doit exposer un contrat versionné :

- identifiant et version de l'outil ;
- schéma d'entrée et de sortie ;
- paramètres et valeurs par défaut ;
- dépendances logicielles et environnement d'exécution ;
- empreintes des entrées et des sorties ;
- contrôles de qualité et critères de rejet ;
- limites connues ;
- type de preuve produit : observation, annotation, prédiction, simulation ou validation expérimentale.

## 4. Architecture cible incrémentale

NEXORA
  |
  +-- Domaines métier
  |     +-- Genomics / Variants
  |     +-- Proteins / Structures
  |     +-- Phytochemistry / Metabolomics
  |     +-- Docking / Virtual screening
  |     +-- Experimental assays
  |     +-- Systems biology
  |
  +-- Scientific Core
        +-- Workflow definitions (versionnées)
        +-- HFSM policy
        +-- DAG dependency planner
        +-- Task outbox + Celery executor
        +-- Tool/engine registry
        +-- Artifact store + SHA-256
        +-- Provenance and evidence registry
        +-- Quality gates
        +-- Audit events

PostgreSQL reste le registre de référence des métadonnées et relations. Les fichiers volumineux sont conservés dans un stockage d'artefacts contrôlé et adressé par contenu ; la base conserve les métadonnées, l'emplacement, les droits et les empreintes. Il ne faut pas stocker de gros fichiers spectraux en colonnes JSON.

Un graphe de preuves peut être construit à partir de relations explicites et de références d'objets dans PostgreSQL. L'ajout d'une base graphe est différé jusqu'à preuve d'un besoin de requêtes qui ne peut être traité simplement.

## 5. Cible fonctionnelle phytochimie/métabolomique

### 5.1 Modèle métier à ajouter progressivement

Les nouveaux objets doivent être distincts du catalogue de candidats actuel :

1. BotanicalSpecimen : taxon, déterminateur, numéro de voucher/herbier, référence et contexte de collecte.
2. PlantSample : spécimen parent, partie végétale, état, date et métadonnées d'échantillonnage.
3. ExtractionBatch : protocole, solvants, proportions, température, durée, méthode, masses et rendement.
4. Fraction : lot parent, méthode de fractionnement, sous-fraction et masse récupérée.
5. AnalyticalRun : instrument, méthode, mode ionique, paramètres, contrôles qualité et lots analysés.
6. RawArtifact : format, taille, stockage, SHA-256, licence/droit d'accès et provenance.
7. SpectralFeature : m/z, temps de rétention, adduct, isotopes, aire, intensité, MS/MS et lien au lot.
8. CompoundAnnotation : structures candidates, références, bibliothèque/version, score/métrique, méthode et niveau de confiance.
9. Liens explicites avec PhytochemicalCandidate, ResearchStudy, ResearchAssay, ResearchTarget et les résultats de docking.

Ces entités peuvent être introduites par migrations additives. Il faut éviter de remodeler les tables existantes tant que le nouveau flux n'a pas été validé.

### 5.2 Pipeline scientifique proposé

1. enregistrer le spécimen et vérifier l'identité taxonomique ;
2. enregistrer l'échantillon, la partie végétale et le protocole d'extraction ;
3. archiver les fichiers instrumentaux et vérifier intégrité, format et droits ;
4. contrôler les métadonnées des blancs, standards, contrôles qualité et réplicats ;
5. traiter les données spectrales avec un outil spécialisé ;
6. aligner les signaux et détecter les caractéristiques moléculaires ;
7. proposer des formules/structures et rapprocher les spectres des bibliothèques ;
8. faire examiner les annotations et conserver plusieurs candidats lorsque l'identité n'est pas résolue ;
9. relier seulement les structures suffisamment caractérisées aux analyses RDKit et, de manière optionnelle, au docking ;
10. relier les hypothèses aux essais expérimentaux existants et à l'analyse 4PL.

Le premier pilote doit cibler un seul type de données — idéalement LC-MS/MS en format ouvert — et une étude documentée. GC-MS et RMN seront ajoutés séparément, avec des workflows propres à leurs données et méthodes.

### 5.3 Intégrations à évaluer, sans réimplémenter les algorithmes

- MZmine : prétraitement, détection/alignement de caractéristiques, annotation et export ; l'intégration doit passer par un adaptateur isolé (CLI/batch ou conteneur), pas par une importation de ses bibliothèques dans le backend Python.
- GNPS : recherche et comparaison spectrale, bibliothèques et molecular networking, sous réserve de maîtriser le partage des données et les limites des services distants.
- SIRIUS : candidat pour formule et annotation structurale MS/MS. Ses services web ont des conditions de licence et de compte propres ; valider les droits académique/commercial et les conditions de redistribution avant toute intégration ou offre institutionnelle.
- RDKit : conserver comme outil de manipulation et de calcul sur structures chimiques, sans confondre descripteurs ou scores de natural-product-likeness avec preuve d'activité.
- HMMER/Pfam : utiliser pour l'annotation de familles et domaines d'enzymes de biosynthèse après identification de séquences protéiques pertinentes ; ne pas l'utiliser comme moteur d'identification directe d'un spectre de masse.

### 5.4 Niveaux de confiance des annotations

Le niveau d'identification métabolomique doit être stocké séparément du grade de preuve biologique A–D déjà utilisé par Nexora.

Pour une annotation de métabolite, conserver au minimum :

- niveau d'identification/annotation ;
- caractéristiques mesurées et propriétés comparées ;
- source de référence et version de bibliothèque ;
- présence ou non d'un standard authentique analysé dans des conditions comparables ;
- méthode, score, avertissements et curateur.

Un niveau 1 ne doit pas être attribué sur le seul m/z ou sur une correspondance de base de données. Il nécessite une comparaison avec un standard authentique et des propriétés orthogonales concordantes selon le protocole analytique retenu. Ne jamais convertir un score de similarité spectral en probabilité de certitude sans calibration.

## 6. Où placer les formalismes biologiques

| Formalisme | Emplacement recommandé | Priorité |
|---|---|---|
| FSM/HFSM | Cycle de vie des études, workflows, étapes, validations et reprise | Phase 1 |
| DAG | Dépendances, parallélisme et synchronisation des tâches | Phase 1 |
| HMM/profile-HMM | Familles protéiques, domaines conservés, annotation d'enzymes et éléments génomiques | Phase 3 |
| Pair-HMM | Alignement probabiliste, insertion/suppression et incertitude d'alignement, seulement si un besoin justifie un moteur spécifique | Phase 3/4 |
| Réseaux de Petri | Réactions biochimiques et concurrence entre réactions | Phase 4 |
| GRN booléens/probabilistes | Régulation d'expression et états cellulaires, à partir de données adaptées | Phase 4 |
| FBA | Flux métaboliques sous contraintes | Phase 4 |
| ODE | Cinétiques et concentrations temporelles avec paramètres mesurés/estimables | Phase 4/5 |
| ABM | Interactions et émergence à l'échelle de populations/cellules | Phase 5 |
| Behavior Trees | Décision et comportement d'agents logiciels dans des simulateurs ; pas un modèle moléculaire | Conditionnel |

## 7. Feuille de route, livrables et critères de sortie

### Phase 0 — Audit et décision (présent document)

Livrable : état des lieux réel, décisions d'architecture, périmètre et ordre de mise en œuvre.

Critère de sortie : aucun nouveau moteur scientifique n'est ajouté avant validation de cette architecture et des invariants de données.

### Phase 1 — Scientific Workflow Core

À réaliser en petits incréments :

- ajouter un registre de définitions de workflow versionnées ;
- introduire WorkflowRun, WorkflowStep et un journal d'événements d'exécution ;
- séparer l'état de l'étude, de l'exécution et de chaque étape ;
- généraliser l'outbox transactionnelle aux routes de soumission asynchrone ;
- ajouter des garde-fous d'idempotence côté workers ;
- introduire les dépendances DAG, fan-out/fan-in et les contrôles d'artefacts ;
- conserver des adaptateurs vers les statuts et endpoints existants ;
- produire un manifeste de provenance reproductible.

Critères de sortie : tests de redélivrance, reprise après crash, retry, annulation, concurrence, échec de publication, absence de double effet et compatibilité des anciennes routes.

### Phase 2 — Phytochimie expérimentale pilote

- valider le schéma botanique, échantillon, extraction, fraction et lot analytique ;
- choisir les formats d'entrée ouverts et le stockage d'artefacts ;
- intégrer un seul pipeline LC-MS/MS ;
- établir les contrôles qualité et les niveaux d'annotation ;
- relier les composés annotés au catalogue curé, puis aux essais et résultats existants.

Critères de sortie : rejeu d'un jeu de référence, conservation des fichiers originaux, traçabilité de chaque annotation, contrôle des blancs/QC et revue humaine des identifications.

### Phase 3 — Annotation génomique/protéique

- adapter HMMER/profile-HMM et bases de profils versionnées ;
- documenter seuils, couverture, e-value, version de base et date ;
- relier protéines/familles annotées aux cibles de recherche et aux voies de biosynthèse.

Critères de sortie : jeux de séquences de référence, faux positifs/faux négatifs documentés, sorties versionnées.

### Phase 4 — Biologie des systèmes

- importer d'abord des modèles publiés et vérifiables ;
- ajouter un moteur Petri, un moteur GRN et un outil FBA comme adaptateurs séparés ;
- ajouter ODE uniquement avec paramètres et unités définis ;
- comparer simulation et données de référence.

Critères de sortie : cohérence dimensionnelle, stabilité numérique, analyses de sensibilité et validation externe des modèles.

### Phase 5 — Modèles hybrides et multi-échelles

- définir une question biologique précise ;
- choisir ABM et/ou équations physiques en fonction de cette question ;
- calibrer les paramètres ;
- comparer les sorties à des mesures expérimentales.

Critères de sortie : validation sur des observations indépendantes et documentation des domaines de validité.

### Phase 6 — Déploiement scientifique/institutionnel

- profil Docker de production distinct du Compose développement ;
- migrations explicites et ordonnées avec suivi de version ;
- stockage d'artefacts contrôlé, sauvegardes et restauration testée ;
- contrôle d'accès et audit ;
- documentation d'exploitation et gestion des licences/outils ;
- pilotes avec laboratoires partenaires.

## 8. Risques à traiter avant la montée en charge

1. Publication asynchrone non homogène : les endpoints génériques de séquence et de docking n'utilisent pas encore l'outbox.
2. At-least-once : la redélivrance doit être sûre ; Celery task_id seul n'est pas une preuve d'exécution exactement une fois.
3. Workflow trop centralisé : ne pas transformer la FSM de ResearchStudy en une machine universelle qui mélange étude, tâche, validation et artefact.
4. Catalogue phyto surchargé : ne pas ajouter les spectres, échantillons et extractions directement dans PhytochemicalCandidate.
5. Identification abusive : distinguer détection, annotation putative, identification confirmée, prédiction de docking et activité mesurée.
6. Licences et accès aux données : auditer ANPDB/AfroDb/WANPDB, MZmine, GNPS, SIRIUS, les bases spectrales et les conditions de services distants avant redistribution ou usage commercial.
7. Migrations : deux fichiers portent actuellement le préfixe 012_ dans infra/postgres/migrations ; un registre de migration et une numérotation non ambiguë doivent être établis avant d'automatiser leur application.
8. Déploiement : le Compose actuel est explicitement orienté développement ; le profil de production devra être défini et testé séparément.
9. Ressources matérielles : les traitements spectraux volumineux et les simulations ne doivent pas être exécutés dans le cycle HTTP ni partager aveuglément la même concurrence worker.
10. Données sensibles/traditionnelles : les coordonnées de collecte, les connaissances traditionnelles et les données soumises par les laboratoires doivent avoir des règles d'accès, de partage et de consentement explicites.

## 9. Ce qu'il ne faut pas faire maintenant

- ne pas ajouter toutes les méthodes HMM, Petri, GRN, ODE et ABM dans la même livraison ;
- ne pas remplacer Celery par Nextflow avant d'avoir démontré un besoin HPC que le noyau existant ne peut satisfaire ;
- ne pas construire une base graphe avant d'avoir stabilisé les identifiants et relations ;
- ne pas lancer de docking sur des annotations spectrales non curées comme si elles étaient des identités chimiques confirmées ;
- ne pas promettre l'identification de molécules à partir d'un seul signal m/z ;
- ne pas présenter des prédictions informatiques comme validation pharmacologique ;
- ne pas casser les routes, tables ou expériences existantes pour imposer le nouveau modèle.

## 10. Références techniques et scientifiques à prendre en compte

- HMMER — profile Hidden Markov Models : https://hmmer.org/
- MZmine documentation — traitement de données MS et workflows : https://mzmine.github.io/mzmine_documentation/latest/
- GNPS documentation — Feature-Based Molecular Networking : https://ccms-ucsd.github.io/GNPSDocumentation/featurebasedmolecularnetworking/
- SIRIUS documentation — licences et fonctionnalités web : https://v6.docs.sirius-ms.io/account-and-license/
- Nextflow — workflows reproductibles, reprise et exécution HPC : https://nextflow.io/
- Common Workflow Language — description portable des outils/workflows : https://www.commonwl.org/
- Workflow Run RO-Crate — provenance d'exécution : https://pmc.ncbi.nlm.nih.gov/articles/PMC11386446/
- MSI — standards minimaux d'identification des métabolites : https://pmc.ncbi.nlm.nih.gov/articles/PMC3772505/
- FAIR data principles : https://doi.org/10.1038/sdata.2016.18

---

## Conclusion

N3XORA dispose déjà d'un socle fonctionnel sérieux. Son prochain saut qualitatif ne viendra pas du nombre d'algorithmes ajoutés, mais de la capacité à faire circuler des données fiables entre les modules, à exécuter les traitements de manière robuste et à conserver la preuve de chaque résultat.

La priorité est donc : unifier l'exécution et la provenance, compléter la phytochimie expérimentale, renforcer ensuite l'annotation génomique/protéique, puis ouvrir progressivement la biologie des systèmes.
