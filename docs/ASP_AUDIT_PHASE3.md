# Audit ASP / Boltzmann — Phase 3: cohérence bout-en-bout

## Résultats observés sur les résultats historiques embarqués

Les fichiers `storage/results/job_822.json` à `job_858.json` ont été recalculés avec l'agrégateur ASP courant.

- `job_822`: 59 poses, 3 poches; une grappe par poche dans les coordonnées stockées; le score canonique retrouve le meilleur score de la poche gagnante.
- `job_823`: 39 poses, 2 poches; une grappe par poche.
- `job_824`: 39 poses, 2 poches; une grappe par poche.
- `job_858`: 40 poses, 2 poches; une grappe par poche; le score ASP courant retrouve `-4.797` sur la poche gagnante.

Ces sorties historiques montrent que, pour ces exemples, le clustering RMSD à 2 Å fusionne pratiquement toutes les poses d'une même poche. L'ASP devient alors numériquement très proche du TOP1 Vina par poche. Cela ne prouve pas une amélioration du classement: un benchmark externe est nécessaire.

## Correction de cohérence identifiée

`best_pocket` était mis à jour selon le TOP1 Vina pendant la boucle de docking alors que le score ASP canonique sélectionne indépendamment le meilleur agrégat par poche. Le résultat pouvait donc publier un site gagnant Vina différent du site gagnant ASP.

Le code courant resynchronise maintenant `best_pocket` avec `aggregation_metadata.best_pocket_id` après l'agrégation.

## Traçabilité des fallbacks

La validation distingue maintenant:

- `structure_fallback_used`
- `pdbqt_fallback_used`
- `asp_fallback_used`

`no_fallback` n'est vrai que si aucune de ces voies de repli n'a été utilisée et que le PDBQT vient d'Open Babel.

## Limitation critique toujours ouverte

Dans les résultats historiques, certains jobs ont utilisé ESMFold et le générateur PDBQT interne. Ces résultats sont des exécutions Vina réelles, mais ils ne doivent pas être présentés comme équivalents à une exécution fondée sur une structure expérimentale et un PDBQT ligand flexible Open Babel.

Le benchmark scientifique final doit donc stratifier les résultats par provenance de structure et de PDBQT.

## Validation exécutée

Les tests autonomes de l'agrégation ASP ont été exécutés directement; la suite pytest globale reste bloquée par l'absence de `psycopg2` dans l'environnement d'exécution.
