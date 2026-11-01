# Nexora v2 — Phase 1H : moteur LC-MS/MS OpenMS + matchms

## Portée

La Phase 1H ajoute le premier moteur LC-MS/MS intégré de Nexora. Une exécution traite **une injection mzML à la fois**. Elle ne réalise pas encore l'alignement inter-injections, la normalisation quantitative, la déconvolution multi-échantillons ni la validation par étalon authentique.

Le parcours est piloté par le noyau HFSM/DAG : API authentifiée, run analytique persistant, dispatcher/outbox, worker dédié sur la file `lcmsms`, exécution, persistance transactionnelle et revue qualité.

## Traitement pris en charge

1. Validation sémantique du fichier mzML avec OpenMS.
2. Détection non ciblée des features MS1 centroidées avec `MassTraceDetection`, `ElutionPeakDetection` et `FeatureFindingMetabo`.
3. Extraction des métadonnées précurseur et d'un résumé borné des pics MS2.
4. Association MS2-feature selon polarité, tolérance de masse précurseur et fenêtre de temps de rétention.
5. Matching optionnel aux bibliothèques MSP archivées et revues, avec `matchms.CosineGreedy`.
6. Persistance des features, observations, spectres, annotations candidates, métriques QC et manifestes SHA-256.

## Centroiding MS2

Le type de chaque spectre MS2 est enregistré comme `centroided`, `profile` ou `unknown`.

Les spectres profilés ou de type inconnu restent consultables dans le dossier expérimental, mais **ne sont jamais transmis au matching MSP**. Nexora ne tente pas de les convertir implicitement en pics centroidés. Un prétraitement doit être réalisé en amont, documenté comme artefact dérivé, puis validé avant le matching.

Les données MS1 profilées ou de type inconnu sont également refusées par la détection intégrée des features.

## Garde-fous d'interprétation

- Une feature MS1 est un signal analytique, pas une structure moléculaire identifiée.
- Un score CosineGreedy est un score de similarité, pas une probabilité d'identité.
- Une correspondance de bibliothèque est enregistrée comme annotation candidate MSI niveau 2, pas comme identification de niveau 1.
- Une annotation candidate ne démontre ni activité biologique, ni efficacité pharmacologique, ni innocuité.
- Les associations MS2 ambiguës restent non attribuées ; Nexora ne choisit pas artificiellement le meilleur voisin.
- La bibliothèque utilisée est identifiée par version, licence, citation et empreinte.

## Idempotence

Une `Idempotency-Key` est liée à l'empreinte du manifeste d'entrée. Sa réutilisation avec un autre mzML, une autre bibliothèque ou d'autres paramètres retourne HTTP 409. Les clés ne dédupliquent donc pas des requêtes scientifiques différentes.

## Limites connues

- Une injection par run ; aucun alignement ou normalisation quantitative inter-injections.
- Les limites de taille d'entrée et de bibliothèque sont configurées.
- Les fichiers sources demeurent la référence complète ; les fragments JSON persistés sont des résumés bornés.
- La revue qualité post-traitement reste obligatoire.
- La validation scientifique de performance nécessite des jeux mzML représentatifs, des standards authentiques, des contrôles qualité et des métriques adaptées à l'instrument et au protocole.

## Tests

La CI doit couvrir les contrats API/HFSM, les empreintes et l'idempotence, la classification MS2, le rejet des spectres non centroidés pour le matching, le scoring sur bibliothèque synthétique et les régressions de la plateforme.
