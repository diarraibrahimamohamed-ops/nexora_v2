# N3XORA v2.0 — Release Validation Report

## Packaging scope
This archive is a clean release candidate of the supplied `nexora_v2` repository. Source files and project documentation are preserved; generated Python bytecode and the Git metadata directory are excluded from the distribution archive.

## Checks performed
- Repository archive successfully extracted and inspected.
- Frontend JavaScript syntax checked with Node.js for the principal modules inspected.
- Backend test suite was invoked with `pytest -q backend/tests --disable-warnings --maxfail=1`.

## Test environment limitation
The backend test collection stopped during import because the execution environment did not have `psycopg2` installed. The project requirements explicitly declare `psycopg2-binary==2.9.9`; therefore this is an environment dependency issue, not evidence that the test suite passes.

Observed collection error:
`ModuleNotFoundError: No module named 'psycopg2'`

## Important release status
This archive must be considered a **release candidate**, not a scientifically validated final publication release. Full backend tests, Docker integration tests, database-backed tests, docking benchmarks and scientific validation must be executed in an environment with the declared dependencies and the required external scientific executables/data.

## Integrity
No source-code modification was made solely to conceal the failed test collection. The supplied project content is preserved for reproducibility.


## Mise à jour — modularisation frontend

Le monolithe `frontend/js/nexora-core.js` a été séparé en modules fonctionnels classiques afin de réduire son périmètre et de limiter le couplage. `nexora-core.js` contient désormais principalement l'état partagé, les constantes et l'initialisation; AfriBio possède son propre fichier `afribio-core.js`. La compatibilité avec les handlers inline de `index.html` est conservée. Voir `docs/frontend-modularization.md`.

Vérification syntaxique Node.js : tous les nouveaux fichiers JavaScript sont syntaxiquement valides.

## Phase 2 — ASP / Boltzmann audit

La phase 2 a corrigé le traitement des poses provenant de poches distinctes. L'agrégation canonique est maintenant effectuée **indépendamment pour chaque poche**, puis les poches sont comparées comme sites alternatifs. Un score `log-mean-exp` normalisé est utilisé comme agrégat canonique afin de limiter la dépendance artificielle au nombre de clusters.

Des tests autonomes ont été exécutés avec succès pour la normalisation des poids, l'agrégation intra-poche, l'invariance à la duplication exacte et le RMSD après transformation rigide. La suite pytest complète reste bloquée par l'absence de `psycopg2` dans l'environnement d'exécution.


## ASP Phase 8 (14 septembre 2026)
- Correction du clustering : RMSD heavy-atom en repère fixe du récepteur, sans superposition Kabsch.
- Le parseur Vina expose `element` et `atom_name`, permettant l exclusion correcte des hydrogènes.
- Rejet explicite des scores non finis et des poses sans coordonnées lourdes finies.
- Smoke tests ASP exécutés indépendamment de PostgreSQL : PASS.
- `py_compile` ASP/Vina : PASS.
- `node --check frontend/js/docking.js` : PASS.
- Benchmark synthétique 2 000 cas rerun : aucun gain démontré d ASP+clustering sur Vina TOP1.
- Aucun benchmark externe réel exécuté dans cet environnement ; aucune métrique externe inventée.

## Phase 9 — hardening des heuristiques

- Suppression du calcul aléatoire de résistance dans le frontend.
- Nouveau endpoint `/api/v1/analysis/amr`; sortie fondée sur preuve génotypique et intégration optionnelle d’AMRFinderPlus.
- Virus + antibiotiques : `not_applicable`, sans score.
- Interprétation génomique : signatures reformulées comme compatibilités contextuelles.
- `confidence_score` heuristique supprimé; `evidence_score` descriptif introduit.
- Pathogénicité : aucune estimation; `pathogenicity_score=None`.
- HSA : interface nettoyée pour interdire les formulations assimilant l’indice à une probabilité de mutation ou une fiabilité clinique.
- Tests ajoutés : `backend/tests/test_heuristic_scientific_guards.py`.

## Validation notes

Standalone scientific smoke tests for AMR, genomic interpretation and HSA pass. Full pytest was not executable in this container because the project's runtime dependencies are not fully installed (notably Celery/DB stack). No claim of full integration-test success is made.

## Phase 10 — Local Nginx/Auth hardening

- Local Nginx configuration syntax validated with nginx 1.26.3 test binary against equivalent local paths/upstream.
- Frontend smoke routes `/`, `/index.html`, `/nexora.html`, `/admin.html`, `/hsa.html` returned 200 in a local Nginx test.
- Unknown SPA paths returned `index.html` rather than a Nginx 403.
- No `deny all`, `auth_basic`, or `return 403` directives exist in the supplied Nginx configuration.
- `/api/` explicitly proxies to FastAPI with `proxy_intercept_errors off`, so FastAPI JSON errors are not replaced by Nginx error pages.
- JWT persistence was corrected so `remember=false` stays in sessionStorage and is no longer promoted to localStorage by the compatibility bridge.
- Remaining legitimate HTTP 403 locations are application-level: administrator authorization, ownership protection, and external NVIDIA ESMFold HTTP handling.
- Docker runtime could not be executed in this environment because the Docker CLI/daemon is unavailable. Therefore no claim of an end-to-end live container login test is made.

---

## Research Workflow / Sahel Phase 1 — 2026-09-15

Cette phase ajoute une couche métier `research` sans remplacer le socle FastAPI/PostgreSQL/Celery/Redis/Vina existant.

### Implémenté
- `research_studies`: étude scientifique avec pathogène, maladie, périmètre géographique, objectif et hypothèse.
- `research_variants`: variants avec provenance d'accession/analyse, classe de preuve et sources.
- `research_targets`: état WT/variant, séquence protéique, provenance structurelle et identifiant de structure.
- `research_docking_runs`: lien étude → cible → variant → job Vina, paramètres et résumé des résultats.
- API `/api/v1/research/*` avec contrôle de propriété utilisateur.
- Import de variants depuis une analyse N3XORA existante.
- Import d'une séquence protéique depuis une analyse existante comme cible WT.
- Page `research.html` pour piloter l'étude et lancer le même moteur Vina/Celery que le docking existant.

### Garde-fous scientifiques
- Aucune causalité de variant n'est inférée automatiquement.
- Une cible variant doit référencer explicitement le variant correspondant.
- Un docking WT et un docking variant sont distingués.
- Vina reste le moteur principal; ASP est présenté comme analyse complémentaire en attente de validation comparative.
- Aucune conversion score Vina/ASP → efficacité clinique ou énergie libre expérimentale.
- La provenance de la structure est conservée.

### Vérifications effectuées
- `python -m compileall -q backend/app` : PASS.
- `node --check frontend/js/api.js` : PASS.
- Import complet de l'application non exécuté dans l'environnement de contrôle car les dépendances/runtime Celery et la configuration des services Docker ne sont pas disponibles ici; ceci n'est pas présenté comme un test PASS.
- Docker live et benchmark Vina réel restent à exécuter dans l'environnement de déploiement/compute réel.

### Sources de conception vérifiées le 2026-09-15
- WHO, Guidelines for malaria, mise à jour 10 septembre 2026.
- WHO, Compendium of molecular markers for antimalarial drug resistance, 10 décembre 2025.
- WHO, Malaria surveillance, monitoring and evaluation: reference manual, 2e éd., 10 juillet 2025.
- Genome-wide SNP analysis of P. falciparum in southern Mali, 2022.
- Genome-wide variation and molecular surveillance of P. falciparum in Ouélessébougou, Mali, 2023.
- MalariaGEN Pf8, Genomic surveillance of P. falciparum in Mali.
- BactProNET, Scientific Reports, 2026.
