# Scientific Workflow Core — Phase 1B

**Statut :** proposition d'implémentation, non fusionnée  
**Base :** main après PR #10  
**Périmètre :** modèle d'exécution générique, politique HFSM, validation DAG, contrôle des baux et provenance événementielle.

## 1. Ce que cette phase ajoute

Phase 1A rend les soumissions asynchrones plus cohérentes en utilisant l'outbox transactionnelle. Phase 1B ajoute le noyau de modélisation des workflows eux-mêmes.

Les nouvelles tables sont :

- `scientific_workflow_runs` : une exécution versionnée, son propriétaire, son périmètre métier, son état HFSM, ses manifests et son contexte ;
- `scientific_workflow_steps` : les étapes, l'état, les tentatives, les erreurs et les baux ;
- `scientific_workflow_step_dependencies` : les arêtes du DAG ;
- `scientific_workflow_events` : le journal append-only des transitions et événements d'exécution.

Ces objets sont séparés de `ResearchStudy`, `DockingJob`, `Analysis`, `HSAJob` et des autres tables historiques. Ils ne les remplacent pas.

## 2. HFSM

Les états persistés sont les feuilles de la machine hiérarchique. Les parents sont dérivés :

- `running.preparing`, `running.executing`, `running.postprocessing` appartiennent au parent `running` ;
- `quality_review.pending` et `quality_review.accepted` appartiennent au parent `quality_review`.

Les transitions sont définies dans `scientific_workflow_policy.py`, sans mutation de base de données. Les transitions persistantes sont appliquées par le service transactionnel avec verrou de ligne, version d'état et événement d'audit.

Une `state_version` monotone permet au client ou au service appelant de rejeter une transition issue d'un état périmé. Les événements peuvent recevoir une clé d'idempotence.

## 3. DAG

Les définitions de workflow sont des objets versionnés, créés par du code revu. Chaque étape contient :

- une clé stable ;
- un nom de tâche issu d'une liste blanche ;
- une file qui doit correspondre au contrat de la tâche ;
- les clés des étapes parentes ;
- un nombre maximal de tentatives.

Le validateur rejette les clés dupliquées, les références inconnues, les dépendances en double, les auto-dépendances et les cycles. Il retourne un ordre topologique déterministe.

Au moment de créer un run, Nexora enregistre un snapshot de la définition et les arêtes du DAG. Les modifications futures d'une définition ne réécrivent donc pas les exécutions déjà créées.

Les entrées de tâche peuvent uniquement fournir `args` et `kwargs`. Elles ne peuvent pas sélectionner un autre nom de tâche ou une autre file. La liste blanche actuelle couvre des tâches déjà présentes : analyse de séquence, alignement et phylogénie AfriBio, docking Vina, HSA, prédiction de structure ARN et résolution de structure de recherche.

## 4. Baux et reprise

Une étape prête n'est revendiquée que lorsque toutes ses dépendances sont en état `succeeded`.

La revendication :

1. verrouille le run et l'étape candidate ;
2. vérifie les dépendances ;
3. incrémente le compteur de tentatives ;
4. attribue un propriétaire, un jeton de fencing et une expiration ;
5. journalise la revendication.

Le worker peut renouveler son bail avec le même jeton. À la complétion ou à l'échec, un jeton absent, différent ou expiré est rejeté. Un ancien worker ne peut donc pas valider une étape avec un bail qui n'est plus le sien.

Les baux expirés deviennent `retry_wait` avec backoff borné tant que le budget de tentative n'est pas épuisé. Après épuisement, l'étape et le run échouent. Les sorties des étapes sont conservées en JSON structuré ; les artefacts binaires devront rester dans le stockage d'artefacts, référencés par manifeste et SHA-256.

## 5. Pourquoi le moteur ne déclenche pas encore les tâches

Cette livraison établit le modèle et les opérations de contrôle, mais ne branche pas encore les workers scientifiques existants sur un exécuteur générique.

C'est intentionnel. Les workers actuels ont des contrats hétérogènes : certains persistent un résultat propre à un domaine, certains utilisent un identifiant de tâche temporaire, et d'autres écrivent des fichiers. Les lancer directement dans un wrapper générique sans adaptateurs dédiés risquerait de déclarer un step réussi alors que le résultat métier ou son artefact n'est pas validé.

La prochaine sous-phase devra créer un adaptateur par famille d'outils, qui :
- valide la présence et l'état du job métier ;
- transmet les références d'entrée plutôt que les gros fichiers en arguments Celery ;
- vérifie les artefacts de sortie ;
- met à jour le step avec son jeton de bail ;
- rend les reprises idempotentes ;
- enregistre les versions d'outil, les paramètres et les empreintes.

Pas d'endpoint HTTP public de lancement générique n'est exposé dans cette phase. Aucun client ne peut donc soumettre un nom de tâche arbitraire ou construire un pipeline non vérifié.

## 6. Migrations

La migration additive est :

`infra/postgres/migrations/20261031_scientific_workflow_core.sql`

Elle crée les quatre tables et leurs contraintes sans modifier les tables existantes.

Le schéma des nouvelles installations est également mis à jour dans `infra/postgres/init.sql`. Le bootstrap `ensure_schema()` ajoute les mêmes tables pour les installations PostgreSQL qui utilisent le mécanisme de patch au démarrage.

Il faut sauvegarder la base avant de déployer une migration, puis vérifier les contraintes, index et droits d'accès sur une copie représentative de la base réelle.

## 7. Limites connues

- Le moteur de contrôle n'est pas encore un ordonnanceur de production.
- La revendication de step avec baux réduit les doubles exécutions, mais la sémantique de livraison reste au moins une fois.
- Les effets externes d'un outil ne peuvent être annulés simplement par un état `cancelled`.
- Les steps AfriBio n'ont pas encore de modèles métier persistants dédiés.
- La liste blanche doit être étendue au rythme des adaptateurs validés, jamais à partir d'une entrée HTTP arbitraire.
- Le versionnement des définitions est immuable par convention et snapshot, mais un registre central de définitions publié/retiré et signé reste à construire.
- Le stockage des artefacts binaires et le manifeste de provenance commun seront renforcés dans les phases suivantes.

## 8. Tests attendus

La suite `test_scientific_workflow_core.py` couvre :

- ordre topologique déterministe ;
- rejet des cycles, dépendances inconnues, dupliquées ou invalides ;
- liste blanche des tâches et cohérence des files ;
- création d'un run et snapshot de définition ;
- idempotence de création ;
- attente des dépendances avant revendication ;
- progression des états HFSM ;
- revue qualité avant finalisation ;
- renouvellement de bail et rejet d'un jeton périmé ;
- récupération après expiration et épuisement des tentatives ;
- retry borné ;
- contrôle de version et idempotence des événements ;
- blocage/reprise ;
- absence de réouverture d'un run déjà échoué par la complétion tardive d'une tâche parallèle.

La réussite CI valide les comportements logiciels couverts ; elle ne valide pas à elle seule la justesse scientifique des moteurs raccordés ultérieurement.
