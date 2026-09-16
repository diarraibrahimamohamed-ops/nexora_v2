# N3XORA-Sahel — Phase 11 : gel, exécution isolée et adjudication

## Objectif

Phase 11 transforme la préparation Phase 10 en procédure d'exécution et d'adjudication figée. Elle ne choisit pas les paramètres après observation des scores et ne retire pas de molécules du test set pour améliorer un résultat.

## Trois étapes

1. `phase11_prepare_locked_manifest.py` fusionne le template Phase 10 avec le manifeste des poches choisi à partir du récepteur seul. Il refuse les boîtes placeholders et enregistre les hashes des récepteurs et des tables de ligands.
2. `phase11_execute_isolated.py` construit/lance l'environnement Docker Phase 10 avec `--network none`, montage en lecture seule des données, et sortie séparée. Si Docker manque, il s'arrête explicitement avec `BLOCKED_ENVIRONMENT`.
3. `phase11_adjudicate.py` exécute le gate Phase 8 puis l'inférence Phase 9 sur le CSV de résultats. Les scores observés ne servent pas à modifier le test set.

## Sorties minimales

- manifeste verrouillé ;
- logs Vina et PDBQT bruts ;
- CSV des scores ;
- rapport Phase 8 ;
- rapport Phase 9 ;
- index d'évidence Phase 11 ;
- hashes SHA-256.

## Règles scientifiques

- Vina TOP1 reste le baseline principal.
- ASP reste une comparaison expérimentale tant que son utilité n'est pas démontrée.
- Les décoys DUD-E ne sont pas assimilés à des inactifs expérimentaux.
- Aucun résultat de docking ne devient une mesure clinique ou une affinité expérimentale.
- Les choix de poche sont faits sans utiliser les labels actif/décoy et sans inspection du ligand natif.
- Les données test ne sont pas retunées après lecture des résultats.

## État dans l'environnement de contrôle

La campagne biologique réelle ne peut pas être déclarée exécutée si Vina, fpocket, Docker et le corpus externe ne sont pas disponibles dans l'environnement courant. Dans ce cas, Phase 11 laisse un état explicite `BLOCKED_ENVIRONMENT` au lieu de fabriquer des métriques.

## Why the data volume is mounted at the same absolute path

The locked manifest stores the canonical absolute dataset path to make the input
hash/provenance record unambiguous. The isolated executor therefore mounts the
same host path at the same path inside the container; it does not rewrite the
scientific manifest at execution time.
