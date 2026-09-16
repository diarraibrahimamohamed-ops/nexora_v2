# Audit ASP / Boltzmann — Phase 2

## Décision scientifique et logicielle

Le pipeline ne traite plus les poses issues de poches différentes comme un seul ensemble Boltzmann. Une poche de liaison candidate est une alternative de site; mélanger directement toutes les poches dans un pseudo-partition-fonction introduit une hypothèse thermodynamique non justifiée par le seul docking.

Le protocole courant est donc:

1. exécuter Vina sur chaque poche candidate;
2. regrouper les poses **dans chaque poche** par RMSD;
3. conserver la pose au meilleur score comme représentant de chaque cluster;
4. calculer les poids de Boltzmann dans l'ensemble propre à chaque poche;
5. calculer un score Boltzmann **normalisé par le nombre de clusters** (`log-mean-exp`) pour éviter qu'une poche paraisse artificiellement meilleure parce qu'elle possède simplement davantage de clusters;
6. comparer les poches entre elles et retenir la meilleure comme site candidat pour ce ligand.

## Deux quantités distinctes

`logsumexp_score` reste disponible comme quantité diagnostique. Elle dépend du nombre de membres et ne doit pas devenir le score canonique d'un classement entre ensembles de tailles différentes.

`boltzmann_logmean_score` est maintenant la quantité canonique d'agrégation de l'ASP:

`S_eff = -RT * ln( mean_i[ exp(-S_i / RT) ] )`

Elle conserve l'échelle numérique du score d'entrée mais n'est **pas** une énergie libre de liaison expérimentale.

## Tests ajoutés

- normalisation des poids;
- stabilité numérique;
- invariance à la duplication exacte d'un membre identique dans l'agrégation normalisée;
- séparation stricte des poches;
- sélection du meilleur site sur son score ASP propre;
- séparation de poses translatées dans le repère fixe ;
- exclusion des hydrogènes du RMSD de clustering ;
- rejet des scores non finis.

## Limitation de validation

La suite pytest globale reste bloquée dans cet environnement par l'absence du paquet `psycopg2`. Les tests ASP autonomes ont néanmoins été exécutés directement hors du fixture PostgreSQL et sont passés.

## Conséquence pour le benchmark futur

Le benchmark doit comparer les méthodes à **niveau de ligand et de site clairement défini**, sans réintroduire un mélange inter-poches non justifié. Les scénarios à tester incluront notamment Vina TOP1, Vina TOP-N, moyenne simple et ASP avec clustering.
