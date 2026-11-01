# Phase 1K — Protocole de validation expérimentale externe LC-MS/MS

## 1. Objet

Évaluer la chaîne Nexora 1G–1J sur des données instrumentales réelles sans modifier les moteurs pendant la campagne. Le protocole sépare l'inventaire documentaire, la qualité des fichiers, le traitement, le QC, l'alignement, la correction quantitative et la reproductibilité.

La campagne ne commence qu'après validation du Gate K0.

## 2. Dataset candidat

**MTBLS15740 — Untargeted LC-MS metabolomics of feces, intestinal contents and serum from mice.**

Sources consultées le 1 novembre 2026 :
- Fiche OmicsDI : https://www.omicsdi.org/dataset/metabolights_dataset/MTBLS15740
- Page MetaboLights : https://www.ebi.ac.uk/metabolights/MTBLS15740
- Guide de fichiers MetaboLights : https://ebi-metabolights.github.io/guides/Files/
- Guide de téléchargement : https://www.ebi.ac.uk/training/online/courses/metabolights-quick-tour/getting-data-from-metabolights/

Le résumé public indique LC-MS reverse-phase positif/négatif, matrices de souris, pooled-QC et blancs. Il ne prouve pas que chaque assay possède un mzML, que l'ordre d'injection est complet ni que tous les QC peuvent soutenir le QC-RLSC 1J.

### Licence

La documentation MetaboLights indique CC0 pour les études soumises depuis avril 2025 et les conditions EMBL-EBI/submitter pour les études antérieures. Ne pas déduire la licence spécifique à partir du seul résumé OmicsDI. Enregistrer la preuve de licence attachée à l'étude et les conditions additionnelles des déposants avant téléchargement ou publication de données dérivées.

## 3. K0 — Inventaire et éligibilité

1. Résoudre l'accession auprès de MetaboLights.
2. Récupérer la liste des métadonnées et les tables ISA-Tab : investigation, sample, assay, éventuellement MAF.
3. Parcourir l'index public des données et inventorier les formats exacts, tailles si disponibles, répertoires et références de fichiers.
4. Relier chaque ligne assay à son échantillon, fichier brut, fichier dérivé, méthode et mode d'ionisation.
5. Identifier les termes de pooled-QC, blancs solvant/extraction, standards et échantillons. Les suggestions automatiques du script restent des candidats ; le mapping final doit être validé par un humain.
6. Vérifier l'ordre réel d'injection. Ne jamais le déduire de l'ordre alphabétique des noms de fichiers.
7. Vérifier la licence et les restrictions, puis enregistrer l'avis d'un relecteur.
8. Vérifier qu'un mzML exploitable existe ou approuver un plan de conversion versionné.
9. Générer le manifeste et les SHA-256 des fichiers de métadonnées.
10. Ne déclarer eligible_for_processing que lorsque tous les contrôles requis sont approuvés.

### Revue K0

Le reviewer doit vérifier :
- accession, titre et statut public ;
- licence et preuve source ;
- tables ISA-Tab complètes et cohérentes ;
- unicité des échantillons et des références de fichiers ;
- mapping raw/derived non ambigu ;
- modes positifs et négatifs séparables ;
- ordre d'acquisition / injection ;
- pooled-QC et blancs confirmés avec le protocole du déposant ;
- formats mzML ou conversion possible ;
- présence de suffisamment de pooled-QC par feature pour le QC-RLSC (par défaut 8 détectés et couverture d'ordre QC de 60 %).

Le nombre d'injections pooled-QC ne doit pas être confondu avec le nombre de pooled-QC détectés pour chaque feature.

## 4. K1 — Intégrité et chaîne de provenance

- Hacher chaque fichier téléchargé par SHA-256.
- Conserver l'identifiant d'origine, le nom exact, la taille, le chemin de dépôt et l'horodatage de récupération.
- Garder le fichier brut intact.
- Relier toute conversion en tant qu'artefact dérivé, avec logiciel/version, options, sortie et hash.
- Vérifier les fichiers mzML avec le validateur et le lecteur OpenMS déjà utilisés par Nexora.
- Documenter les fichiers illisibles, incomplets ou ambigus ; ne pas les exclure sans justification.

## 5. K2 — Réexécution OpenMS / matchms (Phase 1H)

Pour chaque injection sélectionnée :
- fixer l'image worker, versions de logiciels et paramètres ;
- conserver le manifeste d'entrée/sortie ;
- mesurer le nombre de features MS1, spectres MS2, associations, spectres non appariés et annotations candidates ;
- enregistrer temps CPU, mémoire, durée et erreurs ;
- vérifier la conservation des MS2 et leur statut après toute opération de suppression/curation.

Les nombres de features ou d'annotations ne constituent pas à eux seuls une métrique de performance analytique.

## 6. K3 — QC des lots (Phase 1I)

Appliquer les critères définis avant l'analyse par l'expert de méthode :
- répétabilité pooled-QC et standards internes ;
- RSD des signaux interprétables ;
- dérive selon l'ordre d'injection ;
- ratios blancs / pooled-QC ;
- exactitude de masse, dérive RT, suitability et carry-over lorsque les unités et limites sont documentées ;
- couverture, doublons et données insuffisantes.

Les valeurs par défaut du logiciel sont des paramètres de dépistage. Elles ne sont pas des critères universels d'acceptation.

## 7. K4 — Alignement inter-injections (Phase 1J)

Valider séparément les polarités positive et négative :
- nombre et distribution des repères m/z mutuellement uniques ;
- erreur m/z en ppm et résidus RT ;
- couverture RT source/référence ;
- monotonicité de la transformation RT ;
- features hors plage conservées comme singletons ;
- taux de groupes consensus alignés, partiels et singletons ;
- faux regroupements sur un sous-ensemble revu manuellement.

Préparer une vérité de référence indépendante à partir de standards, réplicats ou chromatogrammes/spectres examinés par un expert. Un outil concurrent peut servir de comparateur, jamais de vérité absolue.

## 8. K4 — QC-RLSC

Pour chaque feature éligible :
- comparer RSD pooled-QC avant/après ;
- documenter les facteurs de correction et leurs bornes ;
- compter les features améliorées, inchangées, refusées ou non éligibles ;
- vérifier les données manquantes, sans imputation à zéro ;
- vérifier l'absence d'extrapolation hors plage QC ;
- confirmer que les intensités brutes n'ont jamais été modifiées.

Ajouter une analyse de sensibilité hors-échantillon, par exemple leave-one-QC-out lorsque le nombre et la distribution des pooled-QC le permettent. La courbe qui corrige les mêmes points qui l'ont ajustée ne suffit pas, seule, à démontrer la qualité prédictive de la correction.

## 9. K5 — Reproductibilité et statistiques

Répéter le traitement avec les mêmes entrées et paramètres. Comparer les sorties scientifiques canoniques en neutralisant les identifiants de base de données et horodatages ; vérifier les hashes d'entrées, paramètres et versions.

Rapporter :
- résultats descriptifs par batch, injection, polarité et feature ;
- intervalles d'incertitude adaptés au nombre de lots/injections ;
- exclusions, données manquantes et échecs ;
- analyses de sensibilité ;
- limites du plan expérimental.

Un lot unique ne permet pas de conclure à une reproductibilité inter-instrument, inter-jour, inter-opérateur ou inter-laboratoire. Les unités statistiques doivent correspondre à la structure réelle de l'expérience ; des features d'un même lot ne sont pas autant de réplicats indépendants.

## 10. Rapport final

Le rapport inclut l'accession, la licence, les métadonnées ISA-Tab, les fichiers et hashes, le protocole instrumental, versions et paramètres, métriques 1H/1I/1J, contrôles manuels, incertitudes, exclusions, échecs, limites et décision par gate.

Les résultats LC-MS/MS peuvent soutenir une annotation chimique plus ou moins probable ; ils ne démontrent pas automatiquement une activité pharmacologique, une efficacité thérapeutique ou une utilité clinique.

## 11. Critères d'arrêt

Suspendre la campagne si :
- licence ou réutilisation non clarifiée ;
- mapping assay/sample/fichier ambigu ;
- polarité ou ordre d'injection non fiable ;
- mzML corrompu ou conversion non traçable ;
- pooled-QC insuffisants pour l'endpoint QC-RLSC ;
- paramètres ou critères QC définis après observation des résultats ;
- dérive d'algorithme ou d'environnement pendant la campagne sans versionnement.

Un gate peut être CONDITIONAL ou FAIL ; aucun score synthétique ne doit masquer une défaillance critique.
