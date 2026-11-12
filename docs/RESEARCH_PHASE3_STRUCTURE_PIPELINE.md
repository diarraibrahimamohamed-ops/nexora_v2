# N3XORA — Phase 3 : structures WT / variant et contrôle structural

## Objectif

Cette phase ajoute une couche de gestion structurale au workflow Research sans prétendre valider biologiquement une structure. Chaque cible conserve une provenance explicite, un statut de structure et un rapport de correspondance séquence/structure.

## Sources structurales

1. **RCSB expérimental** (première étape de `auto_rcsb`) : recherche par similarité limitée aux entrées expérimentales; Nexora télécharge et valide plusieurs candidats (jusqu'à 8 entrées), classe les chaînes par identité × couverture, puis privilégie le meilleur match qui franchit les seuils d'acceptation. Le mode PDB est préféré; mmCIF est utilisé si le PDB legacy n'est pas disponible, puis normalisé pour les parseurs courants. Une recherche permissive (0,5) ne réduit jamais le seuil local d'acceptation configuré.
2. **AlphaFold Protein Structure Database** (`alphafold_db`, modèle calculé explicite ou fallback de `auto_rcsb`) : RCSB Search API recherche les Computed Structure Models (`AF_`), puis le connecteur récupère les métadonnées du modèle via l'API AlphaFold et télécharge l'URL mmCIF versionnée renvoyée par `cifUrl`. Le mmCIF est normalisé en PDB pour les parseurs actuels, puis validé indépendamment par identité et couverture.
3. **ESMFold / ESM Atlas / Tamarind** (`esmfold`, `tamarind_esmfold`) : prédiction à partir de la séquence; ESM Atlas est limité à 400 aa et Tamarind à 1 024 aa dans l'adaptateur courant.
4. **ColabFold** (`colabfold`) : prédiction locale possible uniquement via un binaire fourni dans un worker séparé; l'image API standard bloque son installation tant que la dépendance Biopython n'est pas compatible avec la version corrigée.
5. **PDB fourni par l'utilisateur** : import contrôlé et validation de correspondance.

Le mode automatique suit l'ordre : RCSB expérimental (paramètres standards puis permissifs) → AlphaFold DB calculé → ESMFold/Atlas/Tamarind → ColabFold si un worker isolé l'expose. Les structures expérimentales et calculées restent des catégories de provenance différentes. Les modèles AlphaFold/ESMFold ne sont jamais marqués comme expérimentaux; pLDDT informe une confiance locale et n'établit pas une vérité biologique ni l'effet d'un variant. RCSB prend en charge les CSM de type AlphaFold (`AF_`) et ModelArchive (`MA_`); le connecteur AlphaFold DB actuel saute explicitement les identifiants `MA_` qui ne sont pas encore reliés à un fournisseur de coordonnées dans Nexora. citeturn423134search0turn423134search1

## Contrôles effectués

Pour chaque structure PDB exploitable :

- présence d'atomes protéiques et de coordonnées finies ;
- nombre de chaînes, résidus représentés par CA et atomes lourds ;
- correspondance de la séquence cible avec chaque chaîne par alignement global ;
- identité de séquence et couverture de la cible ;
- sélection de la meilleure chaîne selon une hiérarchie déterministe identité × couverture ;
- pour les modèles prédits, extraction du pLDDT-like depuis les B-factors lorsqu'il existe.

Les seuils par défaut de N3XORA (`identity >= 0.70`, `coverage >= 0.70`) sont **des critères opérationnels de pipeline**, pas des seuils universels de validité structurale.

## Statuts

- `not_generated` : aucune structure enregistrée.
- `sequence_mismatch` : la structure ne correspond pas suffisamment à la séquence cible selon les seuils configurés.
- `accepted_for_docking_review` : correspondance suffisante pour une revue humaine et la préparation d'une étape de docking; ce statut ne signifie pas « structure biologiquement validée ».
- `predicted_reviewable` : modèle prédit correspondant suffisamment à la séquence; doit être interprété selon ses métriques de confiance.

## Variant

Une cible variante provenant de la Phase 2 possède une nouvelle séquence protéique. Cette phase permet d'obtenir séparément une structure expérimentale correspondante si elle existe, ou un modèle prédit. Le simple fait de fournir une séquence variante ne constitue pas une validation de son effet structural. L'AlphaFold Protein Structure Database précise notamment que les prédictions de structure ne constituent pas une validation de l'effet des mutations; pLDDT mesure une confiance locale du modèle et non une certitude biologique. citeturn654781search0turn654781search1

## Architecture

```text
ResearchTarget
      │
      ├── sequence WT / variant
      │
      └── Structure job (Celery)
              │
       ┌──────┼─────────────────────┐
       ↓      ↓                     ↓
  RCSB exp.  RCSB CSM            PDB externe
       │     AlphaFold DB            │
       │         │                   │
       └────┬────┴─────────┬─────────┘
            ↓              ↓
       ESM Atlas         Tamarind
            │              │
            └──────┬───────┘
                   ↓
       ColabFold isolé (si fourni)
                   ↓
            PDB normalisé
                   ↓
          validation séquence/3D
                     ↓
             statut + provenance
                     ↓
               revue structurale
```

## Ce que cette phase ne fait pas

- aucune mutation structurelle artificielle par simple déplacement d'atomes;
- aucun score de stabilité inventé;
- aucune conclusion de résistance ou d'efficacité médicamenteuse;
- aucun docking automatique déclenché avant le contrôle de la structure;
- aucune assimilation de pLDDT à une probabilité de justesse biologique.

MODELLER reste disponible comme voie de modélisation comparative experte, mais n'est pas exécuté automatiquement dans cette phase sans choix explicite d'un template et d'un alignement. Sa documentation décrit justement le besoin d'un template et d'un alignement pour le comparative modeling. citeturn654781search2turn654781search3

## Prévention d'un mélange silencieux au docking

Une exécution Research ne peut être soumise que si une structure locale correspondant à la cible est présente et possède un statut `accepted_for_docking_review` ou `predicted_reviewable`. Le chemin exact contrôlé est transmis au worker Vina comme récepteur externe. Ainsi, une cible variante ne peut pas être discrètement remplacée par une autre structure retrouvée à partir de sa seule séquence.

Cette règle est une mesure de traçabilité du pipeline, pas une preuve que la structure est biologiquement correcte.
