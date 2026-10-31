# Scientific Workflow Core — Phase 1C : moteurs et adaptateurs réels

**Statut :** implémentation candidate, non fusionnée  
**Base technique :** Phase 1B (PR #13)  
**Périmètre :** exécution d'adaptateurs pour l'analyse ADN existante et AutoDock Vina.

## 1. Objectif

Cette phase raccorde le noyau HFSM/DAG aux moteurs scientifiques existants sans réécrire leurs calculs. Elle introduit une couche d'adaptation explicite, un dispatcher de DAG et un worker Celery générique pour exécuter les étapes revendiquées.

Les moteurs gardent leur logique et leurs tables métier. Le noyau scientifique contrôle les dépendances, les états, les baux, les reprises et la validation des sorties.

## 2. Parcours disponibles

### 2.1 Analyse de séquence ADN

Endpoint authentifié :

`POST /api/v1/scientific-workflows/sequence-analysis`

Entrée : séquence ADN et nom facultatif.

Étapes :
1. `analysis.run` sur la file `analysis` : exécute le worker existant d'analyse chunkée.
2. `workflow.validate_sequence_result` sur `workflow` : relit le résultat enregistré dans `analyses.data.async_sequence_result`, vérifie sa structure et calcule une empreinte SHA-256.

La séquence est conservée dans la table métier `sequences`. Le message Celery transporte uniquement l'identifiant de l'analyse, pas le FASTA complet.

### 2.2 Docking protéine-ligand

Endpoint authentifié :

`POST /api/v1/scientific-workflows/docking`

Entrées : séquence protéique, SMILES, nombre maximal de poches.

Étapes :
1. `workflow.validate_docking_input` : vérifie l'existence du job, la séquence protéique et la structure RDKit du ligand.
2. `docking.run_vina` sur `docking` : appelle le worker Vina existant, qui charge les entrées persistées depuis `docking_jobs`.
3. `workflow.validate_docking_result` : vérifie le statut terminé, la présence de l'artefact dans le répertoire de résultats configuré, le JSON de sortie et calcule son SHA-256.

Le garde-fou phytochimique du catalogue est conservé : un candidat déjà présent dans la bibliothèque doit passer par le parcours Research Screening après curation.

## 3. Exécution

### 3.1 Dispatcher de workflows

Le service `scientific-workflow-dispatcher` :
- recherche les workflows contenant des étapes prêtes ou des baux expirés ;
- revendique une étape dont les dépendances sont réussies ;
- crée le bail et le jeton de fencing ;
- écrit le message `scientific_workflow.execute_step` dans l'outbox dans la même transaction PostgreSQL.

Le dispatcher d'outbox déjà présent publie ensuite le message vers la file prévue. La file est prise dans le contrat versionné de l'étape (`analysis`, `docking` ou `workflow`).

### 3.2 Worker d'exécution

`scientific_workflow.execute_step` :
- confirme le jeton de bail et la validité de l'expiration ;
- utilise `execution_started_at` pour ignorer une redélivrance concurrente du même message ;
- démarre un heartbeat dans une session SQLAlchemy indépendante ;
- appelle exclusivement un adaptateur présent dans la liste blanche ;
- enregistre la sortie structurée ou l'erreur par le moteur HFSM.

Les tâches Celery scientifiques existantes sont exécutées en mode local `Task.apply()` dans le processus qui a reçu l'étape. Cela évite qu'un worker appelle `AsyncResult.get()` sur une autre file et bloque cette file en attendant un sous-task. La file logique de l'étape reste celle du moteur concerné, et la concurrence des workers reste maîtrisée par la configuration Celery.

### 3.3 Reprise et fencing

- Le dispatcher de workflow repère les baux expirés et revendique de nouveau les étapes lorsque le budget de tentatives le permet.
- Chaque revendication crée un nouveau jeton.
- Un ancien worker ne peut pas valider ou faire échouer une étape avec un jeton périmé.
- Les étapes terminées ne sont pas recalculées si le résultat métier existant et son artefact sont déjà persistés et valides.
- En cas d'échec terminal, les étapes en attente sont ignorées et les baux parallèles sont invalidés.

La livraison reste **at-least-once**. L'exécution applicative est protégée par les marqueurs et les jetons, mais aucune infrastructure distribuée ne peut garantir que des effets externes arbitraires sont exactement-once. Les moteurs doivent continuer à persister des résultats idempotents.

## 4. Stockage et provenance

Les grands objets ne sont pas placés dans les arguments Celery :
- l'ADN est référencé via `Analysis` et `Sequence` ;
- la protéine et le ligand sont référencés via `DockingJob` ;
- les sorties volumineuses restent dans le répertoire d'artefacts configuré ;
- le workflow ne stocke que des résumés JSON, des métriques et des empreintes SHA-256.

L'état et les événements du workflow sont distincts des états des jobs métier historiques.

## 5. API de suivi et actions

- `GET /api/v1/scientific-workflows/{run_id}` : état, étapes, sorties résumées et événements.
- `POST /api/v1/scientific-workflows/{run_id}/actions` : actions permises selon l'état (annulation, blocage, reprise ou revue).

Tous les endpoints sont authentifiés et limités au propriétaire du workflow.

L'annulation invalide les baux et interdit l'acceptation d'une sortie tardive. Elle ne peut pas interrompre instantanément un appel natif Vina déjà en cours ; l'adaptateur laisse l'opération se terminer, mais sa sortie ne pourra plus être enregistrée dans un workflow annulé.

## 6. Déploiement local

Le fichier Compose de développement ajoute :
- la file `workflow` au worker Celery ;
- le service `scientific-workflow-dispatcher` ;
- le dispatcher d'outbox, toujours requis pour publier les messages persistés.

Après mise à jour du dépôt :
1. sauvegarder PostgreSQL ;
2. appliquer `infra/postgres/migrations/20261031_scientific_workflow_core.sql` si la base ne possède pas déjà les tables du noyau ;
3. reconstruire/redémarrer API, worker, `outbox-dispatcher` et `scientific-workflow-dispatcher` ;
4. vérifier que les deux dispatchers et le worker sont actifs ;
5. lancer d'abord une analyse ADN de test, puis un docking avec une cible et un ligand contrôlés.

L'installation neuve possède déjà le schéma dans `infra/postgres/init.sql`. Le bootstrap `ensure_schema()` ajoute également la colonne de marqueur d'exécution si les tables du noyau ont été créées par une version de développement précédente.

## 7. Tests

`backend/tests/test_scientific_workflow_execution.py` :
- crée et exécute une analyse de séquence avec le worker d'analyse réel ;
- vérifie la dépendance séquence → validation du résultat ;
- vérifie la progression du dispatcher et les événements ;
- exécute le parcours docking complet avec un adaptateur Vina simulé en CI ;
- valide la lecture de l'artefact et son empreinte.

Le docking réel dépend toujours de l'environnement (binaire Vina, structure ou fournisseur de structure, ressources et paramètres). La CI valide l'orchestration et le contrat du résultat, pas la performance scientifique de Vina sur une cible réelle.

## 8. Limites et étapes suivantes

- Les adaptateurs de HSA, RNA structure, recherche de structure, 4PL et phytochimie ne sont pas encore raccordés.
- Le pilote ne fournit pas encore d'interface visuelle dédiée au suivi ; l'API et les événements constituent le contrat initial.
- La distribution sur plusieurs catégories de ressources (CPU, mémoire, GPU) reste à configurer par file et worker.
- Les règles de revue qualité automatique sont limitées aux contrôles explicites des adaptateurs ; une revue humaine reste nécessaire pour interpréter les résultats scientifiques.
- La validation du workflow n'est pas une validation biologique ou pharmacologique.

La prochaine étape devrait être l'adaptateur de structure protéique/recherche, puis l'analyse 4PL. La phytochimie doit ensuite adopter le même contrat, avec des niveaux de confiance d'annotation séparés de la preuve biologique.
