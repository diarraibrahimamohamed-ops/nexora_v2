# Scientific Workflow Core — Phase 1C: moteur adapters

**Statut :** implémentation candidate, non fusionnée  
**Base empilée :** `feature/scientific-workflow-core-phase1b`  
**Objectif :** connecter les moteurs existants au noyau HFSM/DAG sans réécrire les calculs scientifiques.

## 1. Chaîne d'exécution

Une soumission API suit désormais ce parcours :

1. L'API valide les données et crée l'enregistrement métier (analyse, job de docking, HSA ou RNA).
2. Dans la même transaction PostgreSQL, elle crée un `ScientificWorkflowRun` et son `ScientificWorkflowStep`.
3. Le job métier conserve une FK nullable `workflow_run_id`. Les endpoints historiques de statut continuent de fonctionner.
4. Le service `outbox-dispatcher` appelle `dispatch_ready_workflow_steps()`.
5. Le scheduler revendique atomiquement une étape DAG prête (lease + jeton de fencing) et écrit l'intention `scientific_workflow.execute_step` dans `research_task_outbox`.
6. Le dispatcher publie ensuite le message Celery dans la file `workflows`.
7. Le worker spécialisé vérifie le bail, marque le début d'exécution une seule fois, appelle le moteur enregistré, renouvelle le lease, vérifie le résultat, puis enregistre une sortie compacte, son SHA-256 et l'événement d'audit.
8. Le scheduler détecte ensuite les étapes devenues prêtes et poursuit le DAG.

Le claim du step et l'intention de publication sont enregistrés dans la même transaction. Une panne avant commit ne laisse ni lease ni message partiel ; une panne après commit laisse une intention d'outbox récupérable.

## 2. Adaptateurs actifs

| Domaine | Nom de tâche workflow | Entrée transportée | Données chargées par le worker |
|---|---|---|---|
| Analyse ADN | `analysis.run_persisted` | `analysis_id` | séquence liée dans `sequences` |
| Docking Vina | `docking.run_vina_persisted` | `job_id`, `docking_result_id` | séquence protéique, SMILES et paramètres de `docking_jobs` |
| HSA | `hsa.run_persisted` | `job_id` | séquence protéique et facteurs environnementaux de `qsa_jobs` |
| Structure ARN | `rna_structure.predict_secondary` | `job_id` | séquence et état de `rna_structure_jobs` |

Les tâches Celery historiques (`analysis.run`, `docking.run_vina`, `hsa.run`) restent enregistrées pour les chemins/clients historiques et les tests de compatibilité. Les workflows intégrés utilisent les variantes `*_persisted` afin d'éviter de recopier des séquences ou structures SMILES dans les messages de broker.

Les données d'entrée restent dans leurs enregistrements métier ; le workflow conserve notamment les identifiants, le contexte et des empreintes SHA-256. Les fichiers binaires/volumineux ne sont pas copiés dans la colonne JSON de sortie.

## 3. Idempotence, leases et reprises

- L'outbox utilise une livraison at-least-once. Elle ne promet pas exactement une fois.
- Une étape revendiquée possède un jeton aléatoire de fencing, une date d'expiration et un propriétaire de lease.
- `begin_step_execution()` écrit un marqueur atomique. Une redélivrance du même message avec le même lease ne démarre pas une seconde exécution.
- Un heartbeat renouvelle le lease pendant le traitement.
- Un lease expiré invalide le jeton précédent et peut replacer l'étape en `retry_wait`, dans la limite de `max_attempts`.
- Les moteurs existants possèdent aussi des garde-fous de replay sur leurs enregistrements/fichiers métier terminés.
- Une étape en échec définitif fait passer le run à `failed` et les étapes encore non démarrées à `skipped`. Les autres étapes déjà en cours ne sont pas tuées arbitrairement ; elles peuvent terminer, mais ne peuvent pas rouvrir un run terminal.
- Un workflow ne peut pas être bloqué pendant qu'une étape détient encore un lease actif. Le blocage se fait à une frontière sûre entre les étapes.
- Annuler un workflow invalide ses leases, mais ne garantit pas l'interruption immédiate d'un calcul natif ou d'un appel externe déjà lancé.

## 4. Observabilité et compatibilité API

Endpoint de lecture owner-scoped :

`GET /api/v1/scientific-workflows/{workflow_run_id}`

Il retourne l'état HFSM et son parent, la définition snapshot, le manifeste d'entrée/sortie, les étapes, leurs dépendances, tentatives, baux, erreurs et les 250 événements les plus récents. Cet endpoint est en lecture seule : aucun nom de tâche Celery arbitraire n'est accepté par l'API.

Les soumissions des routes suivantes renvoient `workflow_run_id` et `dispatch: scientific_workflow` :

- `POST /api/v1/analysis/sequence` en mode asynchrone ;
- `POST /api/v1/docking/submit` ;
- `POST /api/v1/hsa/submit` et l'alias historique `/api/v1/qsa/submit` ;
- `POST /api/v1/rna-structure/submit`.

Les endpoints de statut métier restent disponibles et renvoient aussi `workflow_run_id` pour les jobs concernés.

## 5. Queue et services d'exécution

Le worker Celery consomme aussi la file `workflows` (en plus des files existantes). Le service `outbox-dispatcher` assure maintenant deux responsabilités successives et isolées :

1. planifier les étapes DAG et écrire leurs événements dans l'outbox ;
2. publier les événements de l'outbox vers Redis/Celery.

Une erreur de planification n'empêche pas le cycle de publication des tâches déjà en attente.

Pour déployer cette version, le service `outbox-dispatcher` et un worker configuré pour écouter `workflows` doivent être en fonctionnement.

## 6. Schéma PostgreSQL

La Phase 1C ajoute les FK `workflow_run_id` sur :

- `analyses` ;
- `docking_jobs` ;
- `qsa_jobs` ;
- `rna_structure_jobs`.

La table `rna_structure_jobs` est aussi créée par le schéma neuf et par la migration autonome si elle n'existe pas encore.

Pour une base existante :
1. sauvegarder la base ;
2. appliquer `infra/postgres/migrations/20261031_scientific_workflow_core.sql` ;
3. déployer l'API et les workers de cette branche ;
4. démarrer/vérifier `outbox-dispatcher` et la file `workflows`.

Pour une installation neuve, `infra/postgres/init.sql` contient le schéma complet.

La CI inclut un serveur PostgreSQL 16, applique d'abord le schéma frais, puis rejoue la migration avec `ON_ERROR_STOP` pour vérifier la syntaxe et l'idempotence du chemin PostgreSQL. Les tests SQLite ne sont pas utilisés comme substitut à cette validation.

## 7. Périmètre et limites

- Les adaptateurs intégrés exécutent des workflows monosteps à partir des jobs historiques. Le moteur DAG est capable de fan-out/fan-in, mais les chaînes scientifiques multi-étapes (ex. phytochimie LC-MS/MS → annotation → docking → assay) ne sont pas encore publiées comme workflows prêts à l'emploi.
- Les routes expérimentales 4PL conservent leur contrat actuel ; elles ne sont pas encore transformées en tâches asynchrones de workflow.
- Le module phytochimique reste un catalogue/couche de curation ; le pipeline spectral LC-MS/MS/GC-MS/RMN devra être développé séparément avec ses propres objets d'échantillon, extraction, lot analytique et niveau d'identification.
- AfriBio align/phylo et la résolution de structures restent des contrats de tâches enregistrés, mais leurs routes ne sont pas toutes converties en presets de workflow dans cette première intégration.
- Les calculs scientifiques restent les implémentations actuelles. Le noyau ne rend pas une analyse HSA prédictive cliniquement, ne transforme pas un score Vina en IC50 et ne remplace pas l'expérience.
- Le résultat de worker doit fournir explicitement `status=completed` ou `success=true`. Un dictionnaire sans preuve de succès explicite est rejeté.
- Les moteurs sont appelés dans le worker via l'application Celery locale (pas de `delay().get()` imbriqué), afin de ne pas bloquer un worker en attendant une seconde tâche placée dans la même file.

## 8. Validation

Les tests de Phase 1C couvrent :
- création des workflows depuis les endpoints existants ;
- conservation des relations `workflow_run_id` ;
- transport par ID plutôt que par séquence/SMILES ;
- enregistrement atomique du claim et de l'outbox ;
- marqueur anti-redélivrance et reprise après expiration ;
- exécution eager d'une vraie tâche HSA enregistrée et du nouvel adaptateur d'analyse de séquence ;
- sortie compacte, SHA-256 et manifeste de run ;
- contrôle d'accès owner-scoped ;
- échec terminal, skip des étapes non démarrées et blocage aux frontières sûres ;
- installation fraîche PostgreSQL puis exécution de la migration additive.
