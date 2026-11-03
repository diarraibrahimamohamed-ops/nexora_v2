# Runbook — N3XORA-Sahel BioEvidence

Ce runbook décrit l'installation et la validation de l'extension BioEvidence sur Nexora v2. Il s'agit d'un environnement de développement; le `docker-compose.yml` actuel ne constitue pas une configuration de production.

**État du code documenté — 3 novembre 2026 :** les phases PhytoEvidence 1L-B à 1L-E sont intégrées dans `main` (commit `2f33d06d80f06e34cbc8c30aa48a831865ca6b94`, [PR #30](https://github.com/diarraibrahamimohamed-ops/nexora_v2/pull/30)). La CI post-fusion est verte ([run 41719640849](https://github.com/diarraibrahamimohamed-ops/nexora_v2/actions/runs/41719640849)). La qualification externe Phase 1K reste conditionnelle et n'est pas validée par cette CI.

## 1. Configuration initiale

Depuis la racine du dépôt :

```bash
cp .env.example .env
mkdir -p storage/uploads storage/results storage/temp storage/african_libs storage/offline_packages storage/reference/amrfinder
```

Édite ensuite `.env` avec des valeurs propres à ta machine. Remplace au minimum `DB_PASSWORD`, le mot de passe inclus dans `DATABASE_URL` (les deux doivent être identiques), `SECRET_KEY` et `FLOWER_BASIC_AUTH`. Ne publie jamais le fichier `.env`. Génère une clé JWT avec :

```bash
python -c 'import secrets; print(secrets.token_hex(32))'
```

Les ports PostgreSQL, Redis, Flower et API sont liés à `127.0.0.1` dans cette configuration de développement. Le service Nginx publie le port 80. Ne déploie pas ce Compose directement sur Internet sans une configuration de production, des secrets gérés et des contrôles réseau adaptés.

## 2. Migrations PostgreSQL

La base doit recevoir les migrations versionnées dans l'ordre. Démarre seulement PostgreSQL et Redis :

```bash
docker compose up -d db redis
```

Puis, depuis la racine du dépôt, applique les migrations phytochimiques 009 à 016 :

```bash
for migration in \
  infra/postgres/migrations/009_phytochem_african_library.sql \
  infra/postgres/migrations/010_phytochem_docking_provenance.sql \
  infra/postgres/migrations/011_research_screening_phytochem_candidates.sql \
  infra/postgres/migrations/012_phytochem_source_file_archive.sql \
  infra/postgres/migrations/013_phytochem_identity_taxonomy_provenance.sql \
  infra/postgres/migrations/014_phytochem_wcvp_taxon_resolutions.sql \
  infra/postgres/migrations/015_phytochem_occurrence_evidence.sql \
  infra/postgres/migrations/016_phytochem_taxon_resolution_curation_audit.sql
do
  docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$migration" || exit 1
done
```

Les scripts sont conçus pour utiliser autant que possible `IF NOT EXISTS`, mais inspecte toujours la sortie des migrations dans une base existante. Le mécanisme `ensure_schema` du backend est un filet de compatibilité, pas un remplacement d'un processus de migration revu et enregistré pour la production.

Ensuite, lance les services :

```bash
docker compose up --build -d
curl -fsS http://127.0.0.1/api/v1/health
```

## 3. Premier compte administrateur

Le backend ne crée plus d'administrateur avec un mot de passe de test. Crée d'abord un compte normal via l'écran d'inscription ou `POST /api/v1/auth/register`. Après avoir confirmé que l'adresse t'appartient, renseigne cette adresse dans `ADMIN_EMAIL` dans `.env`, puis redémarre l'API :

```bash
docker compose restart api
```

Au démarrage, le compte existant correspondant à `ADMIN_EMAIL` reçoit le rôle administrateur. Cette variable ne crée aucun compte et ne doit pas pointer vers une adresse non contrôlée. Les opérations d'import et de revue phytochimique sont réservées aux administrateurs.

## 4. Bibliothèque phytochimique africaine

Les 50 entrées de l'Annexe A sont des **candidats à curer**, pas des molécules validées. Elles sont importées avec `CANDIDAT_IMPORTÉ`, sans CID, InChIKey ou SMILES inventés. Les valeurs source ambiguës (#23, #36, #38, #49 et #50 notamment) sont préservées et signalées pour curation.

Avec un jeton administrateur :

```bash
export BASE_URL=http://127.0.0.1
export TOKEN='<JETON_ADMIN>'
curl -fsS -X POST "$BASE_URL/api/v1/phytochem/import/sahel50" \
  -H "Authorization: Bearer $TOKEN"
```

### ANPDB

Le premier import connecté télécharge le CSV officiel puis archive une copie adressée par SHA-256. L'import est limité par `PHYTOCHEM_MAX_IMPORT` (500 par défaut) ; les données mises en cache peuvent être réutilisées hors ligne.

```bash
curl -fsS -X POST "$BASE_URL/api/v1/phytochem/import/anpdb/download?format=csv" \
  -H "Authorization: Bearer $TOKEN"
curl -fsS "$BASE_URL/api/v1/phytochem/anpdb/search?q=cryptolepine" \
  -H "Authorization: Bearer $TOKEN"
```

Site officiel et licence : [ANPDB](https://phabidb.vm.uni-freiburg.de/anpdb/download). Vérifie les conditions de réutilisation avant redistribution d'un jeu de données ou d'une sélection. Le dépôt conserve la provenance et la licence lorsqu'elles sont fournies; la présence d'un composé dans un catalogue ne démontre ni une activité biologique ni une innocuité.

### AfroDb et WANPDB

Charge le fichier reçu d'une source que tu as vérifiée. L'API attend un champ multipart nommé `file` :

```bash
curl -fsS -X POST "$BASE_URL/api/v1/phytochem/import/afrodb" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/chemin/vers/AfroDb.sdf"

curl -fsS -X POST "$BASE_URL/api/v1/phytochem/import/wanpdb" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/chemin/vers/WANPDB.csv"
```

Le backend ne présume pas qu'une URL publique de WANPDB ou une API JSON d'ANPDB existe. L'import conserve le hash du fichier réel. Les fichiers chargés et les datasets téléchargés sont archivés sous `storage/african_libs/imports/<source>/<sha256>.<extension>`. Les copies temporaires reçues par l'API sont supprimées après import; l'archive adressée par hash reste disponible.

## 5. Curation manuelle et gate de docking

Le parcours recommandé pour chaque candidat est :

1. Importer l'entrée au statut `CANDIDAT_IMPORTÉ`.
2. Résoudre l'identité chimique avec CID PubChem, InChIKey et SMILES vérifiés. RDKit canonicalise la structure et calcule les descripteurs; cela ne confirme pas à lui seul que le nom source décrit la structure fournie.
3. Résoudre le nom scientifique avec le service WCVP ciblé par l'API GBIF ChecklistBank v2 : `POST /api/v1/phytochem/<ID>/taxonomy/resolve`. Pour un spécimen, utiliser `POST /api/v1/lcmsms/specimens/<ID>/taxonomy/resolve`.
4. Examiner la file des propositions (`GET /api/v1/phytochem/taxonomy-resolutions?review_status=pending_review`) et ses synonymes, puis accepter ou rejeter via `POST /api/v1/phytochem/taxonomy-resolutions/<RESOLUTION_ID>/review`. La revue taxonomique doit être réalisée par un compte administrateur distinct du créateur ; l'API et PostgreSQL bloquent l'auto-revue. Une résolution acceptée normalise le nom et l'identifiant WCVP mais ne modifie pas `occurrence_status` et ne valide pas l'identification physique d'un spécimen.
5. Enregistrer une preuve d'occurrence (`POST /api/v1/phytochem/<ID>/occurrence-evidence`) en liant la résolution WCVP acceptée, la partie végétale, une source primaire et, si disponible, le spécimen, l'échantillon, l'extraction, la fraction ou l'annotation LC-MS/MS. L'ancien endpoint `verify-occurrence` reste compatible mais crée désormais une preuve bibliographique en attente, sans valider immédiatement le statut.
6. Pour un spécimen, résoudre puis accepter le nom via WCVP en visant ce spécimen (`POST /api/v1/lcmsms/specimens/<SPECIMEN_ID>/taxonomy/resolve`, puis revue taxonomique par un administrateur différent du créateur de la proposition). Faire ensuite examiner l'identification physique par un administrateur distinct du créateur (`POST /api/v1/lcmsms/specimens/<SPECIMEN_ID>/review`). Le statut `verified` exige la correspondance exacte avec la résolution WCVP acceptée liée au spécimen, plus méthode d'identification, identificateur nommé, code voucher et herbier. L'auto-vérification est refusée.
7. Faire examiner la preuve d'occurrence par un second acte de curation (`POST /api/v1/phytochem/occurrence-evidence/<EVIDENCE_ID>/review`). Seule une décision acceptée met le candidat au statut occurrence vérifiée ; elle ne modifie jamais le statut d'identité chimique.
8. Pour documenter une activité biologique, créer un dossier dans une étude autorisée (`POST /api/v1/research/studies/<STUDY_ID>/assays`). L'étude doit être dans l'un des états `analysis`, `results_review` ou `completed` ; une étude `draft` ou non encore approuvée est refusée. Renseigner le test article, la nature de l'essai, la méthode/version, le système biologique (organisme/souche ou lignée cellulaire), les conditions et la source. Pour rattacher l'essai à un candidat phytochimique, utiliser le composé de screening déjà relié au candidat : `screening_compound_id` doit pointer vers le bon composé, dont `phytochem_candidate_id` ou `phytochem_candidate_ids` contient l'identifiant exact du candidat. Le nom du composé, un InChIKey ou une similarité SMILES ne créent pas ce lien.
9. Ajouter les observations (`POST /api/v1/research/studies/<STUDY_ID>/assays/<ASSAY_ID>/measurements`) avec concentration strictement positive pour les mesures de test dose-réponse/susceptibilité, unité, réponse, rôle d'échantillon, type et identifiant de réplicat. Enregistrer séparément les endpoints (`.../endpoints`) et, si nécessaire, les fichiers de protocole/rapport/données (`.../attachments`). Soumettre le dossier (`.../submit`). Un administrateur actif différent du créateur doit ensuite l'examiner (`POST /api/v1/research/studies/<STUDY_ID>/assays/<ASSAY_ID>/review`) ; l'auto-approbation est refusée. Une décision `accepted_for_analysis` valide la complétude/provenance administrative du dossier, pas l'activité biologique elle-même.
10. Après curation, exécuter l'analyse dose-réponse avec le service 4PL existant (`POST /api/v1/research/studies/<STUDY_ID>/assays/<ASSAY_ID>/dose-response-analysis`), selon le schéma OpenAPI et les contrôles de qualité. La projection PhytoEvidence expose les analyses déjà stockées ; elle ne relance pas l'ajustement et ne convertit pas les scores de docking en IC50/EC50.
11. Consulter la vue intégrée du candidat (`GET /api/v1/phytochem/<ID>/evidence-integration?annotation_limit=50&quantification_cell_limit=250&assay_limit=50`). Elle restitue les essais explicitement reliés, les mesures sélectionnées, les réplicats biologiques, les endpoints, les résultats 4PL, leurs intervalles bootstrap, les résumés dose-réponse, les courbes ajustées et la provenance. Les compteurs complets et les indicateurs de troncature sont indépendants des limites de page. Un utilisateur non administrateur ne voit que les analyses des études dont il est propriétaire ; les coordonnées géographiques fines et chemins de stockage privés ne sont pas exposés. Les intensités QC-RLSC restent des signaux instrumentaux, jamais des concentrations absolues. Aucune efficacité, innocuité ou pertinence clinique n'est déduite.

Exemples de requêtes de curation (remplace les valeurs par celles vérifiées indépendamment) :

```bash
curl -fsS -X POST "$BASE_URL/api/v1/phytochem/<ID>/verify-identity" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"pubchem_cid":<CID_VERIFIE>,"inchikey":"<INCHIKEY_VERIFIE>","smiles":"<SMILES_VERIFIE>"}'

curl -fsS -X POST "$BASE_URL/api/v1/phytochem/<ID>/verify-occurrence" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"taxon":"<TAXON_ACCEPTE>","pmid_doi_or_url":"<DOI_PMID_OU_URL>","curator":"<CURATEUR>","review_note":"<JUSTIFICATION_DE_REVUE>","attest_compound_occurrence":true}'
```

Le statut d'identité vérifiée n'implique pas une occurrence botanique vérifiée. Le taxon GBIF valide le nom taxonomique, mais ne prouve pas que le composé a été identifié dans cette plante. Le gate de docking exige les deux validations, un InChIKey et un SMILES canonique valides. Le screening de recherche revérifie le statut juste avant de créer les jobs; un candidat qui a perdu son éligibilité est bloqué.

La route générique de docking refuse également le contournement par un SMILES qui correspond à un candidat phytochimique non curé. Ne contourne pas ce garde-fou en copiant le SMILES dans le mode générique.

Vérifie les statistiques et la liste des candidats admissibles :

```bash
curl -fsS "$BASE_URL/api/v1/phytochem/stats" -H "Authorization: Bearer $TOKEN"
curl -fsS "$BASE_URL/api/v1/phytochem/ready-for-docking?limit=200" -H "Authorization: Bearer $TOKEN"
```

Le téléchargement du fichier source est réservé aux administrateurs et vérifie SHA-256 avant de servir l'archive :

```bash
curl -fL "$BASE_URL/api/v1/phytochem/<ID>/source-file" \
  -H "Authorization: Bearer $TOKEN" -o source-archive.bin
sha256sum source-archive.bin
```

Compare le hash obtenu au champ `source_file_sha256` du registre.

## 6. Screening de produits naturels

Le mode produits naturels permet des limites plus souples (MW jusqu'à 600 Da, LogP jusqu'à 7, rotors jusqu'à 15 par défaut dans le mode spécifique) et calcule NP-likeness via RDKit lorsque le modèle NP_Score est présent. Les filtres PAINS restent disponibles comme indicateur ou critère d'exclusion explicite. Ces heuristiques sont des filtres de préparation, pas des prédicteurs d'efficacité; les métadonnées et les seuils exacts sont enregistrés dans le protocole du screening.

La page Recherche permet de sélectionner les candidats déjà curés ou un jeu de SMILES génériques, mais interdit de mélanger les deux dans la même préparation. Un screening lancé est limité à 50 jobs de docking Vina; sélectionne une cible structurale validée et le protocole versionné affiché dans l'interface. Vina et l'ASP ne remplacent pas des essais expérimentaux; tout score doit être interprété comme un résultat computationnel exploratoire.

## 7. Ollama local — Gemma 2B

Le modèle local est **facultatif**. Les analyses bioinformatiques déterministes fonctionnent même si Ollama n'est pas disponible. Le Compose configure par défaut :

- `OLLAMA_BASE_URL=http://host.docker.internal:11434`
- `OLLAMA_MODEL=gemma2:2b`

Vérifie d'abord que le modèle existe sur l'hôte :

```bash
ollama list
curl -fsS http://127.0.0.1:11434/api/tags
```

Puis vérifie la connectivité depuis le conteneur API :

```bash
docker compose exec api python -c 'import os,httpx; u=os.environ["OLLAMA_BASE_URL"].rstrip("/")+"/api/tags"; r=httpx.get(u,timeout=5); print(r.status_code); print(r.text[:500])'
```

Sur Linux, `host.docker.internal` résout l'adresse de passerelle Docker, mais Ollama peut n'écouter que sur la boucle locale. Si la requête depuis le conteneur échoue alors qu'elle fonctionne sur l'hôte, configure Ollama pour écouter sur une interface joignable depuis le bridge Docker et restreins l'accès au port 11434 par un pare-feu; ne publie pas ce port sur Internet. Pour exécuter l'API hors Docker, configure `OLLAMA_BASE_URL=http://127.0.0.1:11434`.

Le RAG local retrouve les études et preuves enregistrées, les documents locaux et les entrées du catalogue phytochimique. Les candidats non vérifiés restent explicitement des candidats dans le contexte, et l'assistant ne doit pas transformer un score de docking en preuve thérapeutique.

## 8. AMRFinderPlus — optionnel

Le code AMR existe, mais le moteur n'est opérationnel que si l'exécutable et les fichiers de référence sont présents. Le conteneur standard ne télécharge pas automatiquement la base de données NCBI.

Pour construire l'image API et worker avec l'exécutable épinglé à la version configurée :

```bash
# Dans .env
INSTALL_AMRFINDERPLUS=true
AMRFINDERPLUS_VERSION=4.2.7

docker compose build api worker
docker compose up -d api worker
docker compose exec api sh /app/scripts/update_amrfinder_db.sh
curl -fsS "$BASE_URL/api/v1/analysis/amr/status" -H "Authorization: Bearer $TOKEN"
```

La mise à jour de la base nécessite Internet et écrit les références sous `/storage/reference/amrfinder`, volume partagé via `./storage`. Le statut de l'API doit indiquer `ready: true` avant l'utilisation. Si le binaire ou les fichiers requis sont absents, l'analyse doit afficher l'indisponibilité et ne doit pas générer un score synthétique de résistance. Un hit génotypique AMRFinderPlus ne remplace pas un antibiogramme (AST).

## 9. Tests et vérifications avant évaluation

Depuis la racine :

```bash
cd backend
pytest tests/test_phytochemistry.py tests/test_amrfinder_readiness.py tests/test_research_bioevidence.py -v
pytest tests/ -k "ligand or phytochem or screening or amr" -v
```

Avant de déclarer le déploiement validé, effectue aussi ces contrôles **dans l'environnement réel** :

- `docker compose config` puis construction des images et démarrage des services;
- migrations PostgreSQL appliquées dans l'ordre et sauvegarde vérifiée;
- import réel d'un subset ANPDB avec connexion, puis répétition sans Internet pour prouver le cache local;
- vérification réelle PubChem/GBIF et téléchargement d'une archive source dont le hash est contrôlé;
- AMRFinderPlus exécutable + base officielle, version/date enregistrées et test sur séquences de référence connues;
- test de la connexion Docker vers Ollama/Gemma 2B sans exposer le port 11434 au réseau non autorisé;
- export d'un rapport de recherche et reproduction de ses résultats à partir des mêmes versions, paramètres et empreintes.

La CI couvre les tests automatisés et la syntaxe, mais ces vérifications réseau, Docker et bases externes doivent être faites dans un environnement connecté avant un déploiement institutionnel.


## 10. Points de conformité à résoudre avant une diffusion institutionnelle

- **Licence du code Nexora :** le fichier `LICENSE` et les métadonnées GitHub indiquent Apache-2.0, alors que `docs/README_GITHUB.md` indique GPL. Cette divergence n'est pas corrigée automatiquement dans cette PR : le propriétaire doit confirmer le choix de licence et harmoniser le fichier légal, les métadonnées et la documentation avant diffusion.
- **Licence des données :** la licence d'une base (par exemple ANPDB) et la licence du code du projet sont deux sujets distincts. Conserve la licence et la provenance propres à chaque dataset; ne redistribue pas un fichier sans avoir vérifié ses conditions.
- **Validation externe :** les tests CI ne remplacent pas un import réel connecté, une comparaison aux références biologiques, la vérification des versions des bases AMRFinderPlus ou un essai sur les vrais volumes de données.
