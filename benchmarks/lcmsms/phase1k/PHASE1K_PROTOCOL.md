# Phase 1K — Protocole de validation expérimentale externe LC-MS/MS

**Version :** 1.1.0  
**Statut :** protocole proposé ; K0 reste PENDING  
**Cible principale :** MTBLS13040 — phytochimie de l'écorce d'Acacia mearnsii

## 1. Objectif et périmètre

Évaluer la chaîne Nexora 1G–1J sur des données LC-MS/MS instrumentales issues d'une étude externe, sans ajuster les algorithmes de production sur le jeu d'évaluation.

La Phase 1K est une phase de qualification, pas une extension algorithmique. Elle doit séparer :
- la reproductibilité logicielle sur les mêmes entrées ;
- la répétabilité des injections pooled-QC ;
- la cohérence de l'alignement entre injections ;
- la qualité de la correction quantitative ;
- la reproductibilité entre lots, jours, instruments ou laboratoires, qui exige un plan expérimental adapté.

Un dataset ne devient pas un benchmark simplement parce qu'il est public ou techniquement téléchargeable.

## 2. Sélection des datasets

### 2.1 Candidat principal — MTBLS13040

**Étude :** *Solvent-dependent metabolomic profiling of Acacia mearnsii bark: an untargeted metabolomics approach*.  
**Publication :** 29 avril 2026, *Analytical and Bioanalytical Chemistry*.  
**Accession :** [MetaboLights MTBLS13040](https://www.ebi.ac.uk/metabolights/MTBLS13040).  
**Article :** [DOI 10.1007/s00216-026-06496-0](https://doi.org/10.1007/s00216-026-06496-0).  
**Catalogue :** [OmicsDI MTBLS13040](https://www.omicsdi.org/dataset/metabolights_dataset/MTBLS13040).

La publication décrit un cas réellement phytochimique :
- écorce d'**Acacia mearnsii**, riche en tanins condensés et proanthocyanidines ;
- huit conditions d'extraction, aqueuses et hydro-organiques ;
- cinq expériences d'extraction indépendantes rapportées ;
- profilage UHPLC-QTOF-MS avec ionisation ESI positive et négative ;
- pooled-QC constitué à partir d'aliquotes des extraits, blancs de solvant et blancs de procédé ;
- ordre d'injection randomisé dans l'étude et pooled-QC réinjectés tous les cinq échantillons après conditionnement du système ;
- acquisition MS/MS en mode DDA ;
- données brutes Bruker au format `.d`, conversion en mzML décrite avec Data Analysis 4.3 ;
- dépôt des données MS brutes dans MetaboLights sous MTBLS13040 ; dépôt des données MS/MS également rapporté dans GNPS MassIVE sous MSV000098533.

**Ces éléments sont des informations publiées sur l'étude, pas le résultat de l'inventaire exécuté par Nexora.** La publication indique que les données brutes sont déposées sous MTBLS13040 ; elle ne prouve pas à elle seule que les mzML dérivés se trouvent dans l'archive MetaboLights actuelle.

C'est le meilleur candidat initial pour la branche phytochimique, car il réunit une matrice végétale, des conditions d'extraction contrastées, les deux polarités, des pooled-QC, des blancs et de la fragmentation MS/MS.

### 2.2 Candidat botanique secondaire — MTBLS679

L'étude *LC-MS based plant metabolic profiles of thirteen grassland species grown in diverse neighbourhoods* (2021) est un candidat de soutien : profils végétaux, fichiers mzML rapportés, acquisition positive uniquement, pooled-QC et blancs. L'article rapporte 73 injections pooled-QC réparties entre douze lots.

Ce dataset est **positif uniquement** et plus ancien. La licence d'étude et les conditions du déposant doivent donc être vérifiées individuellement. Le nombre total de pooled-QC ne prouve pas que chaque lot ou chaque feature satisfait les exigences de QC-RLSC. MTBLS679 ne remplace pas le candidat principal pour une validation en deux polarités.

### 2.3 Candidat biomédical général — MTBLS15740

MTBLS15740 reste utile comme candidat général de pipeline LC-MS non ciblé sur des matrices de souris, en polarités positive et négative, avec pooled-QC et blancs selon son résumé public. Il ne s'agit pas d'un dataset phytochimique : il ne doit pas être présenté comme preuve de performance sur extraits végétaux.

## 3. K0 — Éligibilité, licence et provenance

Aucun fichier instrumental ne doit être téléchargé avant K0 PASS documenté.

Contrôles obligatoires :
1. Résoudre l'accession et récupérer le catalogue de fichiers, les métadonnées Investigation, Sample et toutes les tables Assay pertinentes.
2. Hacher les métadonnées récupérées avec SHA-256.
3. Parcourir l'archive publique jusqu'à complétude ; une erreur, une limite de parcours ou un répertoire non exploré bloque le passage.
4. Recenser les fichiers ordinaires et les conteneurs de données fournisseur, notamment les répertoires Bruker `.d`.
5. Relier chaque échantillon et chaque ligne Assay aux chemins raw/derived, à la méthode et à la polarité. Une correspondance ambiguë ou absente bloque la sélection.
6. Confirmer les rôles des pooled-QC, blancs de solvant, blancs de procédé, standards et échantillons dans le protocole du déposant. Les étiquettes automatiques sont des candidats, jamais une validation.
7. Documenter l'ordre d'injection à partir d'une source traçable. Ne jamais le déduire de l'ordre alphabétique des fichiers ou de l'ordre des lignes.
8. Vérifier la licence propre à l'accession et les restrictions additionnelles du déposant. La politique générale du dépôt ne remplace pas cette vérification.
9. Vérifier qu'un mzML est effectivement référencé par l'Assay et résolu dans l'archive, ou approuver une conversion versionnée et reproductible des fichiers source.
10. Identifier un lot homogène et le nombre de pooled-QC par polarité, méthode et lot ; confirmer que le plan permet les endpoints QC visés.
11. Faire compléter et signer `ELIGIBILITY_REVIEW_TEMPLATE.json` par un relecteur identifié.

### Règles d'arrêt K0

K0 reste PENDING ou FAIL si la licence est inconnue, si un fichier Assay n'est pas téléchargé, si l'archive est partielle, si un chemin raw/derived n'est pas résolu, si les polarités ou rôles QC restent ambigus, si l'ordre des injections n'est pas documenté, ou si aucune conversion mzML traçable n'est possible.

Les règles MetaboLights relatives aux études soumises depuis avril 2025 et aux études plus anciennes sont une indication de politique de dépôt, pas une autorisation attribuée automatiquement à MTBLS13040 ou MTBLS679. Il faut vérifier les conditions de chaque accession.

## 4. K1 — Intégrité et sous-ensemble

Après K0 PASS :
- sélectionner un sous-ensemble homogène par méthode, lot et polarité ;
- conserver les fichiers bruts originaux en lecture seule ;
- calculer SHA-256 après transfert contrôlé ;
- enregistrer nom, taille, chemin source, date de récupération, échantillon, Assay, polarité, ordre et rôle QC ;
- tracer les conversions parent-enfant avec logiciel, version, paramètres/SOP, sortie et hash ;
- vérifier les mzML avec les validateurs et lecteurs employés par Nexora ;
- ne jamais placer les données instrumentales volumineuses dans Git.

Un répertoire fournisseur `.d` est un conteneur de données, pas un fichier mzML. Son inventaire et sa conversion doivent être documentés comme tels.

## 5. K2 — Réexécution OpenMS / matchms (Phase 1H)

Geler avant exécution :
- commit Nexora et digest de l'image worker ;
- versions de Python, NumPy, OpenMS et matchms ;
- paramètres par polarité et méthode ;
- hashes des entrées.

Rapporter, par injection :
- validation du fichier et unités/plages RT ;
- spectres MS1 et MS2 ;
- features détectées ;
- associations MS2, spectres non appariés et annotations candidates ;
- durée, CPU, mémoire, erreurs et hashes de sortie.

Une feature est un signal analytique ; une correspondance de bibliothèque est une annotation candidate, pas une identification confirmée ni une preuve d'activité.

## 6. K3 — QC des lots (Phase 1I)

Définir les critères spécifiques à la méthode avant d'observer les résultats. Examiner :
- répétabilité pooled-QC et standards internes ;
- RSD/CV avec couverture et nombre de valeurs interprétables ;
- dérive du signal selon l'ordre réel d'injection ;
- ratios blancs / pooled-QC, si les signaux sont comparables ;
- exactitude de masse, dérive RT, suitability et carry-over si mesurés ;
- données manquantes, doublons, unités incohérentes et mesures insuffisantes.

Les seuils décrits dans l'article d'origine (par exemple QC/blank et CV) appartiennent à son protocole publié ; ils ne deviennent pas automatiquement les seuils d'acceptation universels de Nexora. Les valeurs par défaut du logiciel restent des paramètres de dépistage.

## 7. K4 — Alignement inter-injections (Phase 1J)

Traiter séparément les polarités et les strates de méthode :
- nombre, distribution et unicité des ancres m/z ;
- erreurs m/z en ppm et résidus de temps de rétention ;
- couverture RT et monotonie de la transformation ;
- absence d'extrapolation ;
- groupes consensus, groupes partiels et singletons ;
- faux regroupements sur un sous-ensemble revu manuellement.

Conserver les features sources et les intensités brutes. Une vérité de référence doit provenir de standards, de réplicats adaptés ou d'une adjudication experte indépendante.

## 8. K5 — QC-RLSC

Pour chaque feature éligible :
- comparer les RSD pooled-QC avant et après correction ;
- rapporter les facteurs de correction et leurs bornes ;
- compter les features améliorées, inchangées, rejetées ou non éligibles ;
- vérifier les données manquantes sans imputation à zéro ;
- vérifier l'absence d'extrapolation hors plage QC ;
- préserver séparément les intensités brutes et corrigées.

Ajouter un diagnostic hors-échantillon leave-one-pooled-QC-out dans un harness de validation séparé lorsque le nombre et la distribution des QC le permettent. Si le nombre minimal de QC n'est plus satisfait après exclusion, marquer l'analyse non estimable. Ce diagnostic ne modifie pas les sorties de production. Une réduction de RSD in-sample seule ne démontre pas la qualité prédictive de la correction.

## 9. K6 — Annotation chimique

Évaluer le niveau d'identification selon les éléments effectivement disponibles :
- masse exacte et formule candidate ;
- spectre MS/MS et similarité aux bibliothèques ;
- temps de rétention et cohérence de méthode ;
- comparaison avec standard authentique lorsque disponible.

Une identification MSI niveau 1 exige un standard authentique analysé dans les mêmes conditions avec au moins deux propriétés analytiques orthogonales. Une similarité de bibliothèque ou une annotation in silico ne suffit pas à établir ce niveau.

Une annotation phytochimique ne prouve ni activité antiplasmodiale, ni efficacité pharmacologique, ni bénéfice clinique. Ces questions relèvent d'essais biologiques et d'un workflow scientifique distinct.

## 10. K7 — Reproductibilité et rapport final

Répéter le traitement sur les mêmes entrées, image et configuration. Comparer les sorties scientifiques canoniques en neutralisant les identifiants techniques et horodatages ; expliquer toute non-détermination.

Le rapport doit inclure :
- accession, licence, preuve source et sélection des fichiers ;
- manifestes, checksums, parenté des artefacts et métadonnées ISA-Tab ;
- instrument, méthode, matrice, extraction, polarité, lot et ordre d'injection ;
- versions, paramètres, critères pré-déclarés et métriques ;
- résultats par lot, injection, polarité et feature ;
- incertitudes, données manquantes, exclusions, échecs et adjudications ;
- décision PASS, CONDITIONAL ou FAIL pour chaque gate ;
- limites du plan expérimental et portée exacte des conclusions.

Un lot unique ne démontre pas une reproductibilité inter-instrument, inter-jour, inter-opérateur ou inter-laboratoire. Les features d'un même lot ne sont pas des réplicats indépendants.

## 11. Non-revendications

Cette phase ne démontre pas automatiquement une identification chimique confirmée, une activité biologique, une efficacité thérapeutique, une validité clinique, une utilité de biomarqueur ou une qualification réglementaire. Toute conclusion doit être bornée aux fichiers, méthodes, lots et contrôles réellement examinés.
