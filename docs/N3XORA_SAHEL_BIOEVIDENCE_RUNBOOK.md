# Runbook — N3XORA-Sahel BioEvidence

Ce runbook décrit l'installation et la validation de l'extension BioEvidence sur Nexora v2. Il s'agit d'un environnement de développement; le `docker-compose.yml` actuel ne constitue pas une configuration de production.

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

Puis, depuis la racine du dépôt, applique les migrations 009 à 013 :

```bash
for migration in \
  infra/postgres/migrations/009_phytochem_african_library.sql \
  infra/postgres/migrations/010_phytochem_docking_provenance.sql \
  infra/postgres/migrations/011_research_screening_phytochem_candidates.sql \
  infra/postgres/migrations/012_phytochem_source_file_archive.sql \
  infra/postgres/migrations/013_phytochem_identity_taxonomy_provenance.sql
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
3. Résoudre le taxon accepté via GBIF et enregistrer une référence primaire (DOI, PMID ou URL) ainsi que l'attestation du curateur sur l'occurrence du composé dans la plante.
4. Enregistrer la note de revue et, pour les affirmations d'activité, un essai, une mesure, une unité et une référence appropriés.
5. Depuis une étude de recherche, sélectionner une cible structurale admissible, son accession et un protocole versionné, puis préparer le screening.

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
