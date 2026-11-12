# Exécuter le worker de docking avec les outils Ubuntu déjà installés

Nexora peut conserver PostgreSQL et Redis dans Docker tout en exécutant le worker Celery de docking directement sur l'hôte Ubuntu. Ce mode utilise fpocket, RDKit, Open Babel et AutoDock Vina déjà présents dans l'environnement Python/système de l'utilisateur. Il n'installe ni ne compile fpocket dans le Dockerfile.

## Pourquoi un worker natif ?

Le service `worker` de `docker-compose.yml` est isolé dans un conteneur. Les exécutables et modules Python installés sur Ubuntu (par exemple dans un venv) ne sont pas automatiquement visibles depuis ce conteneur.

Pour exploiter les outils de l'hôte sans les dupliquer, on arrête le worker Docker qui consomme la file `docking` puis on démarre ce worker sur Ubuntu. L'API, PostgreSQL et Redis peuvent rester dans Docker.

## Préparation

Installe les dépendances Python du backend dans l'environnement qui contient déjà RDKit et les bindings Open Babel. Depuis la racine du dépôt :

```bash
python3 -m pip install -r backend/requirements.txt
```

Ne réinstalle pas fpocket, RDKit ou Open Babel s'ils sont déjà disponibles. Vérifie séparément le binaire fpocket, le CLI Open Babel et les modules Python :

```bash
command -v fpocket
command -v obabel
obabel -V
python3 -c "import rdkit; print(rdkit.__version__)"
python3 -c "from openbabel import openbabel; print(openbabel.OBReleaseVersion())"
command -v vina
```

La sortie `fpocket --version` de certaines distributions affiche une bannière `fpocket 4.0` tout en exécutant aussi la validation d'argument « nom de PDB ». Cette bannière ne suffit pas à identifier le tag source exact. Le parseur Nexora ne déduit donc pas le schéma de numérotation de cette chaîne : il l'infère à partir des fichiers réellement générés pendant l'exécution.

Le pipeline de conversion appelle la commande `obabel`. Les bindings Python `from openbabel import openbabel` ne garantissent pas, à eux seuls, que l'exécutable CLI `obabel` se trouve dans le `PATH`.

Configure `VINA_PATH` dans le fichier `.env` avec le chemin affiché par `command -v vina`. Garde les URLs `DATABASE_URL` et Redis configurées pour Docker dans le fichier `.env`; le script les réécrit uniquement dans l'environnement du worker natif.

## Démarrage

Démarre PostgreSQL, Redis et l'API, puis arrête le worker Docker pour éviter deux consommateurs concurrents de la même file :

```bash
docker compose up -d db redis api
docker compose stop worker
```

Active l'environnement Python backend dans lequel les outils et dépendances sont disponibles, puis lance :

```bash
python3 scripts/run_docking_worker_native.py
```

Le script :
- charge le fichier `.env` sans le modifier ;
- redirige `db:5432` vers `127.0.0.1:5434` et `redis:6379` vers `127.0.0.1:6381` ;
- redirige les chemins `/storage` vers le dossier `storage/` du dépôt ;
- vérifie fpocket, Vina et RDKit ;
- affiche l'état du CLI Open Babel et lance Celery sur la file `docking` avec `--concurrency=1`.

Pour arrêter le worker natif, utilise `Ctrl+C`. Pour revenir au worker Docker :

```bash
docker compose up -d worker
```

## Politique scientifique des poches

Le détecteur fpocket est la voie primaire. Le fallback heuristique est désactivé par défaut. Il ne peut être activé explicitement qu'avec :

```dotenv
DOCKING_ALLOW_HEURISTIC_POCKET_FALLBACK=true
```

Les poches heuristiques restent des candidates exploratoires non calibrées : elles ne sont ni des probabilités de liaison ni des scores fpocket. Pour des résultats utilisés dans une analyse scientifique, conserve la valeur par défaut `false` et exige une détection fpocket réelle.

Le worker Docker standard n'embarque pas fpocket/Open Babel et ne voit pas les installations de l'hôte. Sans outils disponibles dans son propre environnement, le docking strict échouera explicitement au stade de détection de poche ; il n'inventera pas de poches pour faire passer le calcul.
