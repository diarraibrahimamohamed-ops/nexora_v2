# Phase 1K — Experimental Validation & Analytical Qualification

La Phase 1K qualifie expérimentalement la chaîne LC-MS/MS 1G–1J sur des acquisitions publiques autorisées. Elle commence par un inventaire reproductible, pas par un traitement automatique des fichiers.

## État au 1 novembre 2026

- Le socle logiciel 1G, 1H, 1I et 1J est intégré dans la branche principale.
- La CI post-fusion 1J a réussi sur le merge commit 3081360754b3b235211afa7dcd7c91f8afb5ad0e (run 41329144891).
- MTBLS15740 est un candidat pertinent selon son résumé public : LC-MS en phase inverse, ionisation positive et négative, matrices de souris, pooled-QC et blancs.
- L'inventaire des fichiers, les formats exacts, les mappings ISA-Tab et la licence spécifique de l'étude ne sont pas encore confirmés ici.
- Aucun fichier brut n'a été téléchargé ni traité par Nexora ; aucune validation analytique réelle n'est revendiquée.

## Première opération — K0

Depuis la racine du dépôt, dans un environnement avec accès réseau :

~~~bash
python tools/lcmsms_dataset_inventory.py MTBLS15740
~~~

Le script :
1. interroge le catalogue de fichiers MetaboLights ;
2. parcourt en lecture seule l'index public FTP/HTTPS de l'étude ;
3. télécharge uniquement les métadonnées ISA-Tab ;
4. calcule SHA-256 des métadonnées téléchargées ;
5. extrait les références raw/derived des tables assay ;
6. présente les rôles QC et polarités comme des candidats à confirmer ;
7. écrit un rapport JSON dans benchmarks/lcmsms/phase1k/reports/ ;
8. ne télécharge jamais les données instrumentales brutes.

Les fichiers générés localement sous metadata/ et reports/ sont ignorés par Git. Ils doivent être archivés dans l'espace de stockage de travail du laboratoire, pas committés au dépôt.

## Revue humaine obligatoire

Compléter une copie de ELIGIBILITY_REVIEW_TEMPLATE.json après examen du dossier MetaboLights et des tables ISA-Tab. Le rapport ne peut retourner eligible_for_processing=true qu'après validation explicite :
- de la licence et des conditions de réutilisation ;
- du mapping échantillon ↔ assay ↔ fichiers ;
- des rôles pooled-QC / blancs / standards ;
- de la disponibilité de mzML ou d'un plan de conversion versionné et approuvé ;
- de l'identité du relecteur et de la date.

Pour appliquer une revue enregistrée :

~~~bash
python tools/lcmsms_dataset_inventory.py MTBLS15740 \
  --review-record benchmarks/lcmsms/phase1k/review/MTBLS15740_review.json \
  --fail-if-ineligible
~~~

Le modèle de revue est un gabarit à remplir ; son exemple n'est pas une décision de licence réelle.

## Sorties

- reports/MTBLS15740_inventory.json : résultat d'inventaire et blocages K0.
- metadata/ : fichiers ISA-Tab téléchargés et hachés.
- review/ : décision humaine versionnée séparément.

Les fichiers bruts et mzML volumineux restent hors Git ; les téléchargements doivent être faits explicitement après K0, par FTP/Aspera/Globus et dans le respect de la licence.

## Gates

| Gate | Condition de passage |
|---|---|
| K0 — Dataset eligibility | Accès public, ISA-Tab, licence, formats, mapping et rôles QC revus |
| K1 — Data integrity | SHA-256 des fichiers bruts/dérivés, chaîne parent-enfant et manifestes validés |
| K2 — Reprocessing | Exécution OpenMS/matchms reproductible, paramètres figés, logs et erreurs archivés |
| K3 — Batch QC | Rapports 1I interprétables, critères spécifiques à la méthode pré-déclarés |
| K4 — Alignment / QC-RLSC | Consensus 1J vérifiés et correction évaluée sans altérer les intensités brutes |
| K5 — Reproducibility | Réexécution indépendante et rapport scientifique relu |

PASS, CONDITIONAL et FAIL sont des décisions de qualification documentées, pas des scores automatiques. Aucune porte ne transforme un résultat exploratoire en preuve pharmacologique ou clinique.

Voir PHASE1K_PROTOCOL.md et DATASET_MANIFEST_TEMPLATE.json.
