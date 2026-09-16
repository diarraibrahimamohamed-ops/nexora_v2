# N3XORA-Sahel — Phase 7 : virtual screening contrôlé

## Objet

Cette phase transforme la branche structurale en un petit protocole de virtual screening reproductible. Elle sépare trois niveaux :

1. **Contrôle chimique** : parsing RDKit, canonicalisation SMILES, suppression des doublons et calcul de descripteurs.
2. **Filtrage explicite** : seuils paramétrables et, si demandé, Rule of Five / PAINS.
3. **Docking** : AutoDock Vina comme moteur principal. Les descripteurs RDKit ne produisent aucun score d'activité.

## Choix méthodologiques

RDKit fournit notamment MW, MolLogP, HBD/HBA, TPSA, nombre de liaisons rotatables et QED. La Rule of Five est présentée comme règle de drug-likeness/oralité, pas comme preuve d'activité. Les filtres peuvent donc rester désactivés : des molécules valides peuvent volontairement être conservées.

PAINS est un drapeau d'interférence potentielle et non une preuve qu'un composé est inactif.

Le screening est limité à 250 entrées pour la préparation et à 50 dockings par lancement. Le protocole de lancement conserve le hash SHA-256 de la structure du récepteur, la version du protocole et les paramètres Vina configurés dans N3XORA.

Le mode actuel effectue un screening à **une poche** (`max_pockets=1`) pour contrôler le coût et la comparabilité du protocole. Pour une étude de publication, la sensibilité au choix de poche devra être évaluée séparément.

## Ce que le système n'affirme pas

- QED, Lipinski ou PAINS ne constituent pas une prédiction d'efficacité.
- Le score Vina reste un score de docking, pas une constante expérimentale d'affinité.
- Le classement obtenu sur une bibliothèque sans labels actifs/décoys ne constitue pas un benchmark de virtual screening.
- Pour le benchmark scientifique, les jeux externes doivent contenir des labels appropriés et être séparés des données de développement.

## Benchmark externe

DUD-E est conçu pour benchmarker les programmes de docking et fournit des actifs et des décoys physico-chimiquement similaires mais topologiquement différents; il contient 102 cibles et 50 décoys par actif. Il doit être utilisé comme jeu de benchmark externe et non comme preuve de succès biologique réel.

Le harnais N3XORA existant (`tools/benchmark_external.py`) reste l'outil de référence pour les métriques ROC-AUC, AP, EF1/EF5 et les analyses par cible.

## Références principales

- RDKit documentation — descriptors, Lipinski and QED.
- AutoDock Vina manual — search space, exhaustiveness, num_modes, seeds and score semantics.
- DUD-E — database and decoy construction.

