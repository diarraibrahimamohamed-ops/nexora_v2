# Scientific Workflow Core — Phase 1E

## 1. Objectif

La Phase 1E raccorde l'analyse expérimentale dose-réponse 4PL avancée au noyau HFSM/DAG livré lors des phases 1A–1D.

Le calcul statistique reste dans `assay_statistics.py`. La couche `assay_analytics.py` orchestre l'accès aux dossiers expérimentaux, la vérification curatoriale, l'empreinte des observations, la persistance immuable du résultat et son enregistrement dans `ResearchEvidence`.

La route synchrone du domaine `research_assays.py` et le nouvel adaptateur asynchrone appellent désormais ce même service.

## 2. Nouveau workflow

Endpoint authentifié :

```http
POST /api/v1/research/studies/{study_id}/assays/{assay_id}/dose-response-workflow
Authorization: Bearer <token>
Idempotency-Key: <optional-key>
Content-Type: application/json
```

Exemple :

```json
{
  "response_name": "growth_inhibition",
  "response_unit": "% inhibition",
  "concentration_unit": "uM",
  "response_direction": "increasing",
  "endpoint_name": "IC50",
  "sample_label": "Compound A",
  "timepoint_value": 48,
  "timepoint_unit": "h",
  "bootstrap_iterations": 1000,
  "random_seed": 42
}
```

La soumission exige que le dossier expérimental soit `reviewed` avec `review_decision=accepted_for_analysis`. Elle accepte les types de dossier explicitement autorisés par le chemin 4PL avancé.

La réponse `202` contient le `workflow_run_id`. Le dispatcher claim ensuite l'étape unique `dose_response_4pl` et publie l'exécution via l'outbox. La file est `workflow`, déjà consommée par le worker générique.

Le step ne transporte que `study_id`, `assay_id`, `user_id` et les paramètres statistiques. Les observations expérimentales restent dans PostgreSQL.

## 3. Service partagé et compatibilité

`execute_dose_response_analysis` dans `assay_analytics.py` est maintenant le service d'exécution partagé. Il :

1. vérifie que l'étude appartient à l'utilisateur ;
2. recharge et verrouille le dossier expérimental ;
3. revalide l'acceptation curatoriale et le type d'essai ;
4. recharge les observations dans l'ordre déterministe ;
5. appelle sans modification l'algorithme `fit_dose_response_4pl` ;
6. calcule une empreinte d'entrée depuis le manifeste du dossier, les paramètres et les seules mesures sélectionnées par l'algorithme ;
7. calcule l'empreinte SHA-256 de la sortie JSON canonique ;
8. persiste `ResearchAssayAnalysis`, l'événement `ResearchAssayReviewEvent` et `ResearchEvidence`.

La route synchrone `/dose-response-analysis` est conservée, mais elle appelle maintenant ce même service. Si une exécution workflow a déjà persisté la même analyse, la route synchrone renvoie le résultat existant avec `idempotent_replay=true` au lieu de recalculer et créer un doublon.

L'idempotence du workflow est namespacée par étude et dossier. Quand aucun `Idempotency-Key` n'est fourni, une clé déterministe est construite à partir de l'empreinte du snapshot des mesures et des paramètres. Une nouvelle version des données ou des paramètres produit donc un nouveau run.

## 4. Séparation entre les deux chemins historiques de 4PL

Le dépôt contient encore deux parcours API/statistiques :

- Le chemin avancé `research_assays.py` / `assay_statistics.py`, qui applique les règles de réplicats biologiques, le cluster bootstrap, les contrôles d'unités et les gardes de qualité. C'est ce chemin qui est raccordé au nouveau workflow Phase 1E.
- Le chemin historique `research_analytics.py` / `experimental_analytics.py`, exposé sous `/analyses/dose-response`, avec son propre contrat de requête et de réponse, sa version d'algorithme et ses contrôles historiques.

La Phase 1E ne redirige pas silencieusement le chemin historique vers un autre algorithme. Il reste fonctionnel et distinct pour cette livraison. Une convergence de cette route exigera une migration explicite du contrat, une vérification des usages frontend et des tests de compatibilité des réponses.

## 5. Provenance

Pour le chemin avancé, l'enregistrement d'analyse conserve :

- `algorithm` et `algorithm_version` ;
- paramètres statistiques ;
- `analysis_key` idempotente ;
- `input_sha256` et `output_sha256` ;
- résultat complet, qualité et avertissements ;
- l'événement de revue de l'analyse ;
- un `ResearchEvidence` de grade D avec limitations et mêmes empreintes.

Le `ScientificWorkflowStep.output` conserve le résumé d'exécution, l'identifiant de l'analyse, la version de l'algorithme, l'empreinte d'entrée/sortie, la qualité et l'estimation. Le dossier et l'analyse restent les sources canoniques des mesures et du résultat complet.

Les empreintes servent à vérifier l'intégrité et la stabilité des entrées/sorties ; elles ne prouvent pas l'exactitude du modèle, la reproductibilité expérimentale indépendante ou la validité clinique.

## 6. Limites scientifiques

Le 4PL avancé reste une analyse computationnelle des mesures déclarées et soumises à revue. Le midpoint estimé est relatif aux asymptotes ajustées ; il ne correspond pas nécessairement à une concentration produisant exactement 50 % de réponse absolue. Le bootstrap rééchantillonne les profils biologiques complets. Les réplicats techniques ne deviennent pas des unités biologiques indépendantes.

Une analyse terminée et enregistrée dans `ResearchEvidence` reste classée comme `computational_hypothesis`, grade D. Elle ne devient pas automatiquement une preuve expérimentale validée, une démonstration d'efficacité ou une validation clinique.

## 7. Tests

`backend/tests/test_scientific_workflow_phase1e.py` vérifie :

- allowlist et file du contrat `experimental.run_4pl` ;
- soumission HFSM d'un dossier accepté ;
- idempotence déterministe sur les mêmes mesures et paramètres ;
- dispatch et exécution effective par le worker générique ;
- persistance de l'analyse, du hash, de l'événement et de `ResearchEvidence` ;
- réussite du workflow ;
- rejeu par la route synchrone avec la même analyse ;
- refus de soumission avant acceptation curatoriale.

Aucune migration de schéma ni table supplémentaire n'est introduite par cette phase.

## 8. Suite de l'évolution

La Phase 1F a été réalisée séparément. Elle standardise la provenance, ajoute l'idempotence et l'enregistrement `ResearchEvidence` au parcours 4PL historique, et scelle les manifestes d'entrée/sortie du noyau HFSM/DAG.

Le moteur historique `experimental_analytics.py` et le moteur avancé `assay_statistics.py` restent volontairement distincts. Les règles et écarts sont documentés dans `docs/SCIENTIFIC_PROVENANCE_CORE_PHASE1F.md`. Aucune migration de résultat historique ni conversion automatique de modèle n'est effectuée.
