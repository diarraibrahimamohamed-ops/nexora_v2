# N3XORA-Sahel BioEvidence — guide d'intégration et de vérification

Ce document décrit les modifications de la branche `feature/n3xora-sahel-bioevidence`. Elles sont proposées comme extension de Nexora v2 : aucune migration de base ni modification de code n'a été fusionnée dans `main`.

## 1. Ce qui a été ajouté

### Bibliothèque phytochimique africaine
- Modèle séparé `PhytochemicalCandidate` et table PostgreSQL `phytochem_candidates`.
- Imports CSV ANPDB, SDF AfroDb et CSV WANPDB fourni par l'utilisateur.
- Cache local de données ANPDB, de références PubChem et de correspondances taxonomiques GBIF.
- Seed des 50 entrées de l'annexe Sahel, noms/espèces/classes recopiés depuis la source et sans SMILES/CID/InChIKey ajoutés.
- Statuts de curation distincts pour identité chimique et occurrence botanique; conservation des hashes de source et licences déclarées.
- API et interface web de revue : `frontend/phytochemistry.html`.

Le registre `validated_ligands` existant n'est pas utilisé pour les entrées brutes de ces bibliothèques.

### Gate de curation avant criblage
Un candidat de catalogue n'est accepté par le parcours de Research Screening que si :
1. le SMILES est valide et canonique, avec InChIKey/CID comparés à la référence chimique;
2. le nom du candidat est cohérent avec les synonymes de la référence;
3. le taxon est résolu et un curateur a attesté l'occurrence du composé dans la plante en citant une référence;
4. une accession cible et un protocole nommé sont explicitement sélectionnés;
5. la structure cible est déjà dans un état opérationnel accepté par le workflow de recherche.

Une entrée qui correspond à la bibliothèque ne peut pas contourner la curation en collant son SMILES dans le chemin générique de docking/screening. Le score du docking ne prouve pas l'activité, l'efficacité ou l'innocuité.

### Profils, preuves, autonomie locale
- Registre versionné des profils `/api/v1/research/profiles` : *P. falciparum* Sahel en priorité, AMR bactérienne conditionnée à AMRFinderPlus, génotypage viral marqué comme préparatoire, screening de produits naturels en pilote.
- Registre `research_evidence` attaché aux études, avec état de preuve, référence, méthode, versions et hashes facultatifs.
- Manifeste de paquet de recherche hors ligne : chemins contenus dans le répertoire configuré, licences/références déclarées et SHA-256 des fichiers; endpoint de vérification d'intégrité.
- Assistant optionnel Ollama configuré pour `gemma2:2b`, avec récupération lexicale d'éléments du registre et de fichiers textuels locaux. Il ne modifie pas les résultats et ne génère pas de réponse scientifique lorsque le corpus local ne contient rien de pertinent.

### AMRFinderPlus
Le module Python d'analyse AMR existait déjà; le moteur n'était pas installé par le Dockerfile standard. La branche ajoute une installation optionnelle et versionnée, un script de mise à jour de base et un endpoint qui distingue l'exécutable disponible de la base de référence prête.

## 2. Fichiers importants

- `backend/app/models/phytochemistry.py` — modèle de candidats.
- `backend/app/services/phytochemistry_catalog.py` — imports, calculs et vérification/curation.
- `backend/app/services/anpdb_client.py` — source ANPDB et cache local.
- `backend/app/api/v1/phytochemistry.py` — endpoints de curation.
- `backend/app/fixtures/sahel_50_ligands.json` — seed source-faithful.
- `backend/app/services/research_protocols.py` — protocole nommé de screening.
- `backend/app/services/research_profiles.py` et `backend/app/fixtures/research_profiles.json` — profils versionnés.
- `backend/app/models/evidence.py` et migration 012 — registre de preuves.
- `backend/app/services/offline_packages.py` — manifestes et vérification SHA-256.
- `backend/app/services/local_research_assistant.py` — RAG lexical local et Ollama.
- `backend/app/services/tool_status.py` et `GET /api/v1/research/tools/status` — état réel des outils.
- `backend/tests/test_phytochemistry.py`, `test_research_bioevidence.py`, `test_amrfinder_readiness.py` — tests ajoutés.
- `.github/workflows/n3xora-bioevidence-tests.yml` — job CI ciblé.

## 3. Préparer l'environnement

1. Sauvegarder la base PostgreSQL et vérifier qu'une restauration est possible.
2. Créer un fichier `.env` local à partir de `.env.example`; générer un vrai `SECRET_KEY` et un mot de passe Flower unique. Ne pas committer le fichier `.env`.
3. Vérifier la configuration sans lancer de migration sur la base de production :

```bash
docker compose config
docker compose ps
```

4. Les répertoires persistants sont initialisés par `backend/entrypoint.sh`: `/storage/african_libs`, `/storage/offline_packages` et `/storage/reference/amrfinder`.

## 4. Migrations PostgreSQL

La création idempotente des nouvelles tables/colonnes est prévue dans `ensure_schema()`. Pour une mise à jour contrôlée en environnement institutionnel, appliquer les migrations versionnées sur une copie restaurée de la base, après les migrations historiques qui créent les tables Research :

1. `infra/postgres/migrations/009_phytochem_african_library.sql`
2. `infra/postgres/migrations/010_phytochem_docking_provenance.sql`
3. `infra/postgres/migrations/011_research_screening_phytochem_candidates.sql`
4. `infra/postgres/migrations/012_research_evidence_registry.sql`

Exemple de principe (adapter utilisateur, base et chemin à la configuration locale) :

```bash
docker compose exec -T db psql -U <DB_USER> -d <DB_NAME> < infra/postgres/migrations/009_phytochem_african_library.sql
```

Répéter en ordre séquentiel pour 010, 011 et 012. Ne pas exécuter directement sur la production sans sauvegarde, copie de test et plan de retour arrière.

## 5. AMRFinderPlus — installation conditionnelle

Le binaire est optionnel pour garder l'image standard plus légère. Pour le compiler dans les services API et worker :

```bash
INSTALL_AMRFINDERPLUS=true docker compose build api worker
docker compose up -d api worker
docker compose exec api sh /app/scripts/update_amrfinder_db.sh
docker compose restart api worker
```

Puis vérifier :
- `GET /api/v1/analysis/amr/status` pour l'état du moteur AMR;
- `GET /api/v1/research/tools/status` pour le statut consolidé des outils.

La première mise à jour de la base exige une connexion Internet. Il faut enregistrer la version et la date de mise à jour dans le journal du déploiement. `AMRFINDER_REQUIRED=true` fait échouer le démarrage de l'API si le binaire ou la base manquent. L'analyse AMR concerne les déterminants génotypiques bactériens; elle ne remplace pas un antibiogramme/AST.

## 6. Importer puis curer les bibliothèques

Les endpoints d'import exigent le rôle administrateur :
- `POST /api/v1/phytochem/import/sahel50`
- `POST /api/v1/phytochem/import/anpdb/download?format=csv`
- `POST /api/v1/phytochem/import/anpdb` avec CSV multipart
- `POST /api/v1/phytochem/import/afrodb` avec SDF multipart
- `POST /api/v1/phytochem/import/wanpdb` avec CSV multipart

Le download ANPDB met en cache le CSV/SDF officiel sous `AFRICAN_LIBS_DIR`. Les imports CSV/SDF fournis sont archivés avec leur SHA-256 pour la reproductibilité. WANPDB est actuellement un import de fichier fourni par l'utilisateur; ce dépôt ne prétend pas avoir une API de téléchargement stable pour cette source.

Étapes de curation pour chaque composé :
1. ouvrir `frontend/phytochemistry.html`;
2. comparer un CID PubChem, l'InChIKey et un SMILES canonique; le nom source doit correspondre aux synonymes PubChem;
3. vérifier le taxon accepté par GBIF et citer une publication/source justifiant spécifiquement l'occurrence du composé dans la plante;
4. documenter toute preuve biologique publiée séparément de l'identité chimique;
5. conserver les candidats qui restent ambigus comme non vérifiés — ne pas inventer de structure.

Exemple corrigé pour Cryptolepine : utiliser le CID et l'InChIKey de la référence PubChem vérifiée (le CID donné dans une note ou une liste ancienne n'est pas une autorité à lui seul). Le test du dépôt utilise le CID PubChem **82143**, pas le CID non confirmé 75092.

Certaines entrées du seed demandent une résolution manuelle avant tout criblage, en particulier *Erinacine A (Plante)* (ligne 36) et les associations *Mitraphylline / Isomitraphylline* (lignes 49–50). Les mécanismes allégués de l'annexe ne sont pas copiés dans le registre comme des faits validés.

## 7. Parcours de recherche

1. Obtenir un profil via `GET /api/v1/research/profiles`.
2. Créer une étude avec `profile_id=plasmodium_falciparum_sahel_v1`; l'étude conserve un snapshot et son hash.
3. Relier séquence/accession, variants, structure et références dans l'étude.
4. Ajouter les éléments de preuve via `POST /api/v1/research/studies/{study_id}/evidence`.
5. Pour les candidats africains, obtenir les candidats prêts via `GET /api/v1/phytochem/ready-for-docking`, charger les candidats dans la section Screening, sélectionner une cible structurale et le protocole `N3XORA_VINA_ADAPTIVE_V1`.
6. Préparer le screening; revalider les statuts et la provenance au moment du lancement; conserver les hashes, la version du protocole, la graine 42 et les paramètres effectivement rapportés par le moteur.
7. Pour le travail hors ligne, copier les fichiers référence dans `OFFLINE_PACKAGES_DIR`, créer un manifeste via `POST /api/v1/research/offline-packages/manifest`, puis vérifier avec `POST /api/v1/research/offline-packages/{package_id}/verify`.
8. L'assistant local est facultatif. En Docker, configurer `OLLAMA_BASE_URL=http://host.docker.internal:11434`; l'hôte doit autoriser uniquement l'accès local nécessaire. Ne pas exposer Ollama sans contrôle réseau. Le retrieval actuel est lexical, pas vectoriel.

## 8. Commandes de tests et vérification

Depuis la racine du dépôt, dans un environnement où les dépendances déclarées sont installées :

```bash
python -m compileall -q backend/app backend/tests
pytest -q backend/tests/test_phytochemistry.py backend/tests/test_research_bioevidence.py backend/tests/test_amrfinder_readiness.py backend/tests/test_heuristic_scientific_guards.py backend/tests/test_virtual_screening_phase7.py
node --check frontend/js/api.js
```

Le workflow GitHub exécute les mêmes tests ciblés et vérifie la syntaxe des scripts JavaScript intégrés aux pages. Une suite verte est requise avant fusion; les campagnes Vina/fpocket, le chargement réel des bibliothèques et les tests d'intégration PostgreSQL restent à exécuter dans un environnement de calcul approprié.

## 9. Limites et décisions à conserver

- Les 50 ligands sont des candidats, pas une bibliothèque biologiquement validée.
- La normalisation SMILES avec RDKit ne prouve pas que le nom botanique ou l'occurrence du composé est correct.
- GBIF valide/rapproche un taxon; il ne prouve pas que le composé existe dans cette plante. Cette partie requiert un humain et une référence spécifique.
- Un hash SHA-256 prouve l'intégrité relative d'un fichier comparé à un manifeste, pas sa qualité scientifique, son droit d'usage ni sa licence.
- QED, NP-likeness et PAINS sont des descripteurs/filtres; ils ne prédisent pas l'efficacité.
- AutoDock Vina est un score empirique de docking; l'ASP reste un post-traitement à comparer, pas une énergie libre expérimentale.
- Le statut « disponibilité outil » n'est pas une performance validée.
- AMRFinderPlus doit être installé et sa base versionnée dans le conteneur réellement déployé pour être opérationnel.
- L'assistant Gemma/Ollama est un assistant documentaire facultatif, jamais une source d'autorité scientifique.
- Résoudre avant publication la divergence de licence décrite dans la documentation historique du dépôt.
