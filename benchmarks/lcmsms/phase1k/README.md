# Phase 1K — Experimental Validation & Analytical Qualification

La Phase 1K qualifie expérimentalement la chaîne LC-MS/MS 1G–1J sur des données instrumentales externes. Elle commence par l'éligibilité documentaire K0 ; aucun fichier RAW ou mzML n'est traité avant PASS documenté.

## État au 1 novembre 2026

- Les phases 1G, 1H, 1I et 1J sont intégrées dans `main`.
- L'outillage K0 et ses garde-fous de provenance sont définis ; l'éligibilité réelle de MTBLS13040 reste PENDING jusqu'à l'inventaire live et à la revue humaine.
- La CI post-fusion de la première contribution K0 est verte sur le run [41340571258](https://github.com/diarraibrahamimamohamed-ops/nexora_v2/actions/runs/41340571258).
- Aucun jeu de données expérimental n'a encore été téléchargé ou retraité par Nexora ; aucune validation analytique instrumentale n'est revendiquée.

## Sélection des jeux de données

### Priorité 1 — MTBLS13040 : phytochimie de l'Acacia mearnsii

La publication [Solvent-dependent metabolomic profiling of Acacia mearnsii bark](https://doi.org/10.1007/s00216-026-06496-0) (29 avril 2026) décrit :
- huit conditions d'extraction de l'écorce d'Acacia mearnsii ;
- UHPLC-QTOF-MS en ESI positif et négatif ;
- pooled-QC, blancs de solvant et blancs de procédé ;
- ordre randomisé et injections pooled-QC répétées tous les cinq échantillons ;
- acquisition MS/MS DDA ;
- fichiers bruts Bruker `.d`, conversion mzML décrite avec Data Analysis 4.3 ;
- dépôt des données MS brutes dans [MetaboLights MTBLS13040](https://www.ebi.ac.uk/metabolights/MTBLS13040).

C'est le candidat initial le plus pertinent pour le volet phytochimique. **Ces caractéristiques décrites par l'article ne sont pas une validation K0 par Nexora.** L'accès actuel à l'archive, les fichiers `.d` référencés, les tables ISA-Tab, les correspondances assay, la licence propre à l'accession, le véritable ordre d'injection et les nombres de QC par polarité/lot restent à vérifier. La présence de mzML dans l'archive MetaboLights n'est pas présumée à partir de la conversion décrite dans l'article.

### Priorité 2 — MTBLS679 : profils métaboliques de plantes

Cette étude de treize espèces de prairie est un dataset botanique de soutien. La publication rapporte des données mzML, une acquisition positive uniquement, des blancs et 73 injections pooled-QC réparties dans douze lots. Les conditions d'accès/licence et la répartition des QC dans chaque lot restent à vérifier. Le nombre total de QC n'établit pas à lui seul l'éligibilité au QC-RLSC.

### Usage général — MTBLS15740

Cette étude LC-MS sur matrices de souris reste un candidat pour évaluer le pipeline LC-MS/MS général, mais **ce n'est pas un benchmark phytochimique végétal**.

Le registre de sélection se trouve dans [`benchmarks/lcmsms/phase1i/datasets.json`](../phase1i/datasets.json). Les trois candidats restent bloqués en attente de K0.

## Première opération — K0

Depuis la racine du dépôt, dans un environnement avec accès HTTPS :

~~~bash
python tools/lcmsms_dataset_inventory.py MTBLS13040
~~~

L'outil :
1. interroge le catalogue de fichiers MetaboLights ;
2. parcourt l'index public en lecture seule avec des limites explicites ;
3. inventorie les fichiers et les conteneurs de données fournisseur, notamment les répertoires Bruker `.d` ;
4. télécharge uniquement les métadonnées ISA-Tab ;
5. calcule leurs empreintes SHA-256 ;
6. relie les échantillons aux lignes Assay et aux références raw/derived ;
7. classe les rôles QC et polarités comme candidats à confirmer ;
8. bloque l'éligibilité si le parcours est incomplet, si des métadonnées requises n'ont pas été téléchargées, si les références ne sont pas résolues ou si la revue humaine manque ;
9. ne télécharge aucun fichier instrumental.

Le rapport est écrit dans `benchmarks/lcmsms/phase1k/reports/MTBLS13040_inventory.json` et les métadonnées sous `benchmarks/lcmsms/phase1k/reports/metadata/`. Ces données locales sont ignorées par Git ; conserver les rapports et métadonnées dans l'espace documentaire du laboratoire.

## Revue humaine obligatoire

Copier `ELIGIBILITY_REVIEW_TEMPLATE.json` dans l'espace de revue local, renseigner l'accession, puis vérifier à partir de la fiche d'étude, du protocole publié et des fichiers ISA-Tab :
- licence exacte, preuve de licence et conditions du déposant ;
- mappings Sample ↔ Assay ↔ fichier source/dérivé ;
- rôles pooled-QC, blancs de solvant, blancs de procédé et standards ;
- polarité positive/négative et strates d'acquisition ;
- ordre d'injection à partir d'une source traçable ;
- nombres de pooled-QC par méthode, polarité et lot ;
- présence d'un mzML relié à l'Assay ou plan de conversion approuvé, versionné et reproductible ;
- nom du relecteur, date UTC et justification.

Exemple d'utilisation après revue :

~~~bash
python tools/lcmsms_dataset_inventory.py MTBLS13040 \
  --review-record benchmarks/lcmsms/phase1k/review/MTBLS13040_review.json \
  --fail-if-ineligible
~~~

Une revue ne doit jamais être réutilisée pour une autre accession. Les dates de dépôt et les politiques générales ne remplacent pas la preuve de licence attachée à l'étude.

## Gates

| Gate | Condition de passage |
|---|---|
| K0 — Éligibilité | Licence, ISA-Tab, archive complète, références de fichiers, QC, polarité, ordre et conversion revus |
| K1 — Intégrité | Transfert autorisé, SHA-256 des sources, manifestes et liens parent-enfant |
| K2 — Réexécution | OpenMS/matchms avec versions, paramètres, logs et hashes figés |
| K3 — QC de lot | Mesures interprétables avec critères propres à la méthode pré-déclarés |
| K4 — Alignement | Alignement par méthode/polarité, contrôles des ancres, résidus et groupes |
| K5 — QC-RLSC | Évaluation brute/corrigée, couverture QC et diagnostic hors-échantillon |
| K6 — Annotation | Niveaux d'identification évalués avec les preuves réellement disponibles |
| K7 — Reproductibilité | Réexécution, analyse des écarts et rapport relu |

PASS, CONDITIONAL et FAIL sont des décisions documentées, pas des scores automatiques. Aucune porte ne transforme un résultat LC-MS/MS en preuve pharmacologique ou clinique.
