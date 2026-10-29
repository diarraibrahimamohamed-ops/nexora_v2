# AMRFinderPlus dans Nexora v2

Nexora intègre AMRFinderPlus de NCBI pour détecter des déterminants génétiques de résistance antimicrobienne dans des séquences nucléotidiques bactériennes assemblées. Les résultats sont génotypiques et ne remplacent pas un antibiogramme; ils ne constituent pas une probabilité clinique de résistance.

## Installation du logiciel

L'image backend installe le binaire NCBI AMRFinderPlus 4.2.7 par défaut. La version est épinglée dans le Dockerfile. Le workflow CI construit aussi cette image pour valider l'installation système.

## Initialiser la base de référence

La base de référence n'est pas incluse dans l'image. Elle doit être mise à jour séparément et persiste dans le volume local `./storage:/storage`.

Depuis un environnement connecté, crée le répertoire et assure-toi que l'utilisateur du conteneur (UID/GID 1000) peut y écrire, puis exécute :

```bash
mkdir -p storage/reference/amrfinder
sudo chown -R 1000:1000 storage/reference/amrfinder
docker compose build api worker
docker compose --profile amrfinder-db-init run --rm amrfinder-db-init
docker compose up -d api worker
```

La mise à jour est séparée du démarrage normal de l'API afin qu'un redémarrage n'effectue pas de téléchargement implicite.

## Vérification

Après connexion à Nexora, appeler `GET /api/v1/analysis/amr/status`. Le statut indique séparément la disponibilité du binaire et sa version, les fichiers requis de la base, la version de la base et les groupes taxonomiques supportés.

L'interface charge la liste des groupes taxonomiques fournie par le binaire. Sélectionne un groupe lorsque des appels de mutations spécifiques à l'organisme sont souhaités. Sans groupe explicite, certains appels spécifiques peuvent ne pas être effectués.

Si le binaire ou la base manque, Nexora signale le moteur indisponible et ne génère aucun score. Ne règle `AMRFINDER_REQUIRED=true` qu'après installation du binaire et initialisation de la base.

## Sources officielles

- AMRFinderPlus NCBI : https://github.com/ncbi/amr
- Tamarind Bio API : https://app.tamarind.bio/api-docs/overview
