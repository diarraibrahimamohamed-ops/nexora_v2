# Audit technique Nexora v2 — Phase 0

Date: 24 octobre 2026  
Périmètre: revue statique du dépôt et corrections proposées sur la branche `audit/nexora-hardening-phase0`.

## Limites

Cet audit est basé sur les fichiers versionnés. Il ne prouve pas que les conteneurs ont démarré, que les tests passent, ni que le déploiement public utilise cette configuration. Aucune base de données utilisateur n'a été consultée ou modifiée. Les changements ne doivent pas être déployés avant revue et tests dans un environnement de sauvegarde.

## Corrections préparées sur cette branche

1. Le worker Celery écoute désormais aussi la file `structure`, utilisée par le workflow de résolution des structures.
2. Les ports d'API, Flower, PostgreSQL et Redis sont liés à `127.0.0.1` sur l'hôte dans le Compose de développement. Nginx reste le point d'entrée HTTP.
3. Flower n'a plus de valeur par défaut `nexora:changeme`; `FLOWER_BASIC_AUTH` doit être défini explicitement.
4. Nginx remplace l'en-tête `X-Forwarded-For` reçu par la véritable adresse du pair connecté, au lieu d'ajouter une chaîne potentiellement fournie par le client.
5. L'initialisation ne promeut plus automatiquement un utilisateur simplement parce que son nom est `admin`. Seule l'adresse `ADMIN_EMAIL` configurée peut être promue si un compte correspondant existe. Le compte de test avec hash fictif n'est plus inséré dans les nouvelles bases.
6. Les correctifs de schéma sont exécutés dans des transactions séparées et leurs erreurs sont journalisées. En PostgreSQL non-debug, une erreur d'initialisation interrompt le démarrage au lieu de rester silencieuse. Les environnements SQLite/test gardent leur comportement tolérant.
7. L'inscription rejette les mots de passe dépassant 72 octets UTF-8, limite bcrypt, au lieu de laisser une partie du mot de passe ignorée par bcrypt.

## Actions de validation obligatoires avant fusion

- [ ] Exécuter `docker compose config` avec un fichier `.env` de test complet.
- [ ] Vérifier que le compte administrateur prévu existe, que son email est correct et que les droits sont attendus avant de redémarrer une instance existante.
- [ ] Lancer les tests automatisés dans le conteneur backend.
- [ ] Tester une tâche de résolution de structure de bout en bout et confirmer qu'elle passe de `pending` à `completed` ou à une erreur explicite.
- [ ] Tester les connexions externes aux ports hôte 5434, 6381, 5555 et 8000; ils doivent rester inaccessibles depuis le réseau si le Compose de développement est utilisé.
- [ ] Examiner les logs de migration sur une copie de la base réelle; ne pas tester les migrations initiales directement sur la seule base de production.
- [ ] Vérifier que le proxy est le seul chemin d'accès à l'API. Si d'autres proxies sont ajoutés, configurer explicitement les proxies de confiance.
- [ ] Ajouter une procédure documentée de création du premier administrateur. `ADMIN_EMAIL` ne crée pas le compte; il ne fait que promouvoir un compte existant.

## Points non corrigés automatiquement

### Licence

`LICENSE` indique Apache 2.0 tandis que `docs/README_GITHUB.md` annonce GPL. Le choix de licence doit être décidé par le titulaire des droits et vérifié avec les contributions et dépendances. Aucune licence n'a été modifiée dans cette branche.

### Dépendances scientifiques

La disponibilité réelle d'AMRFinderPlus, ColabFold et des fournisseurs de structures dépend de l'image et de la configuration exécutées. Le code source ne suffit pas à confirmer leur fonctionnement sur le serveur. Il faut contrôler les versions, les binaires et les journaux des tâches.

### Données scientifiques

Un score de docking n'est pas une preuve d'activité, de résistance ou d'efficacité clinique. Les résultats doivent enregistrer les versions d'outils, paramètres, identifiants des structures et molécules, sources et statut de preuve. Les affirmations de performance de l'ASP doivent être étayées par des benchmarks comparatifs indépendants.

## Risques résiduels

- Le changement de l'initialisation de schéma peut faire échouer le démarrage si la base PostgreSQL existante est incomplète; c'est intentionnel pour éviter un état incohérent silencieux, mais cela exige une répétition sur une copie de sauvegarde.
- L'adresse `ADMIN_EMAIL` ne constitue pas à elle seule une vérification de propriété de l'email. L'accès à la configuration serveur doit rester restreint et le compte doit être créé/validé par un processus contrôlé.
- La limitation des tentatives de connexion en base devrait être complétée par des tests de concurrence, de nettoyage des anciennes tentatives et de comportement derrière le proxy.
- Le Compose est étiqueté développement et utilise Uvicorn `--reload`; il ne doit pas être traité comme un déploiement de production.

## Commandes de validation locale

À exécuter depuis une copie de travail avec un `.env` non partagé:

```bash
docker compose config
docker compose ps
docker compose logs --tail=150 api worker
docker compose exec worker celery -A app.workers.celery_app inspect active_queues
docker compose exec api pytest -q
```

Ne pas publier le fichier `.env`, les mots de passe, les clés d'API ni des données biologiques confidentielles.
