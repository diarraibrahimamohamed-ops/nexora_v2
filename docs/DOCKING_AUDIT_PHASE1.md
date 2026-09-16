# N3XORA v2 — Audit scientifique du pipeline Docking — Phase 1

Date: 2026-09-13

## Périmètre

Audit du flux réel `structure → fpocket → box Vina → AutoDock Vina → poses → clustering RMSD → ASP/Boltzmann → API/frontend`.

## Corrections appliquées

1. **fpocket**
   - Le dépôt cherchait auparavant des fichiers `pocket*.pdb` directement dans le dossier de sortie et utilisait `pockets_info.txt` avec un format simplifié.
   - Le parseur utilise maintenant le répertoire `protein_out/pockets/` et les fichiers `pocket*_vert.pqr` / `pocket*_atm.pdb` produits par fpocket.
   - Les alpha-sphères sont utilisées pour calculer centre et dimensions de la boîte.
   - Les sorties métriques sont associées aux poches sans dupliquer les mêmes poches.
   - Une pseudo-confiance `score/100` n'est plus fabriquée.
   - Les heuristiques internes sont désormais un **fallback explicite** et ne sont plus mélangées silencieusement aux poches fpocket.

2. **Boîte Vina**
   - Suppression de la taille fixe `20 Å` comme représentation implicite de la poche.
   - Lorsque les alpha-sphères fpocket sont disponibles, la box est dérivée de leur étendue avec une marge explicite de 4 Å par côté, puis bornée par la taille minimale adaptée au ligand.

3. **Scores Vina**
   - Suppression du filtrage arbitraire `-15 ≤ score ≤ 0.5` lors du parsing.
   - Les scores finis sont conservés; une valeur extrême doit être inspectée, pas supprimée silencieusement.
   - Suppression de l'assimilation du score Vina à `binding_energy` dans la persistance worker.

4. **Contrat de sortie**
   - Nouveau contrat canonique:
     - `vina_best_score`
     - `boltzmann_effective_score`
     - `boltzmann_mean_score`
     - `docking_engine`
     - `aggregation_method`
     - `aggregation_metadata`
   - Les anciens champs sont normalisés à la lecture des anciens résultats.
   - Le frontend privilégie désormais les champs canoniques.

5. **ASP**
   - Le résultat reste explicitement un **score effectif d'agrégation**, pas une ΔG expérimentale.
   - `no_fallback` n'est plus déclaré vrai lorsque le pipeline a réellement utilisé un fallback.

6. **Score normalisé historique**
   - Suppression du calcul `best_score * 100 / longueur_protéine`, qui n'avait pas de justification scientifique comme mesure comparable d'affinité.

7. **Analyse des poses**
   - Le clustering RMSD et les tests mathématiques de base ont été exécutés indépendamment.

## Vérifications exécutées

- Compilation Python de `backend/app` : OK.
- Vérification syntaxique de tous les fichiers JavaScript frontend : OK.
- Smoke tests indépendants ASP/Boltzmann : OK.
- Test RMSD d'une transformation rigide : OK.
- Test du parsing alpha-sphère PQR : OK.
- Intégrité de l'archive : à vérifier après packaging final.

## Limitation restante

La suite Pytest complète ne peut toujours pas être exécutée dans l'environnement courant car `psycopg2` n'est pas installé et l'environnement d'exécution n'a pas permis son installation réseau. Cela ne doit pas être présenté comme un succès de la suite complète.

## Point scientifique restant à auditer

La formule ASP utilise les scores Vina comme une énergie empirique dans une agrégation de type Boltzmann. Le code documente correctement cette limitation. Il reste à démontrer expérimentalement, sur un benchmark externe, si cette agrégation améliore réellement le classement par rapport à Vina TOP1/TOP-N et à des baselines simples.
