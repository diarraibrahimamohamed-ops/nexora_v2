# Scientific Workflow Core — Phase 1D

## 1. Objectif

La Phase 1D étend le noyau HFSM/DAG livré en Phase 1B et raccordé à l'analyse de séquence et au docking en Phase 1C. Elle ne remplace ni les routes historiques ni les moteurs scientifiques existants.

Cette livraison ajoute des soumissions de workflows versionnées pour :

- HSA ;
- QSA, comme alias de compatibilité historique vers le même moteur HSA ;
- prédiction de structure secondaire ARN par RNA-FM/Tamarind ;
- résolution structurale des cibles d'une étude de recherche.

Chaque soumission crée l'enregistrement métier et le `ScientificWorkflowRun` avec ses étapes dans une seule transaction. Le dispatcher publie ensuite les étapes via l'outbox transactionnelle.

## 2. Endpoints

Tous les endpoints ci-dessous sont authentifiés.

| Endpoint | Objet métier | Tâche autorisée | File |
|---|---|---|---|
| `POST /api/v1/scientific-workflows/hsa` | `HSAJob` / `qsa_jobs` | `hsa.run` | `qsa` |
| `POST /api/v1/scientific-workflows/qsa` | `HSAJob` / `qsa_jobs` | `qsa.run` legacy-compatible workflow | `qsa` |
| `POST /api/v1/scientific-workflows/rna-secondary-structure` | `RnaStructureJob` | `rna_structure.predict_secondary` | `structure` |
| `POST /api/v1/scientific-workflows/research-structure/{study_id}/{target_id}` | `ResearchStructureJob` | `research.resolve_structure` | `structure` |

Exemple HSA :

```http
POST /api/v1/scientific-workflows/hsa
Authorization: Bearer <token>
Idempotency-Key: hsa-study-42-run-1
Content-Type: application/json
```

```json
{
  "protein_sequence": "ACGTGCGTACGTACGT",
  "env_factors": {
    "temperature": 37.0,
    "ph": 7.4,
    "stress_factor": 1.0
  }
}
```

Le champ `protein_sequence` est conservé pour compatibilité avec le modèle historique `HSAJob`. Le moteur HSA actuellement présent dans le dépôt valide en réalité une séquence nucléotidique ADN/ARN (A/C/G/U/T). Il s'agit d'un indice heuristique de stabilité, pas d'une probabilité de mutation ou d'un prédicteur clinique.

Pour le workflow RNA, `TAMARIND_API_KEY` doit être configurée. La soumission refuse l'appel avant création du job lorsque la clé n'est pas disponible.

La résolution structurale exige que l'étude appartienne à l'utilisateur courant et soit dans l'état métier `analysis`, comme la route de recherche existante. Le fournisseur est limité à `auto_rcsb`, `tamarind_esmfold`, `esmfold` ou `colabfold`.

## 3. Contrats des adaptateurs

### HSA et QSA

L'adaptateur charge la séquence et les facteurs environnementaux depuis `qsa_jobs`, appelle le worker historique puis relit le résultat persisté. Un job marqué `completed` dont le résultat contient `success=false` est rejeté par le workflow. Le digest SHA-256 du JSON de résultat est conservé dans la sortie de l'étape.

Le contrat `qsa.run` est conservé dans la liste blanche afin de prendre en charge l'alias historique, sans créer un deuxième moteur analytique.

### RNA-FM

L'adaptateur appelle la tâche existante avec le seul identifiant du job. Il exige :

- le statut métier `completed` ;
- un chemin d'artefact existant ;
- un chemin contenu dans `TEMP_DIR/rna_secondary/job_<id>` ;
- une empreinte SHA-256 calculée à partir des octets réellement stockés.

Le chemin, la taille et le digest sont retournés dans la sortie du step. Le contenu du fichier n'est pas recopié dans PostgreSQL ou dans un message Celery.

### Résolution structurale

L'adaptateur appelle `research.resolve_structure`, recharge la cible et exige :

- le job métier en statut `completed` ;
- un fichier présent sous `TEMP_DIR/research_structures/study_<study_id>/target_<target_id>` ;
- un rapport de validation de structure persisté ;
- `eligible_for_docking_review=true` dans ce rapport.

Un fichier PDB présent mais rejeté par la correspondance de séquence/couverture ne rend donc pas le step réussi. Le digest SHA-256, la source, le statut et les métriques de correspondance sont conservés dans la sortie de l'étape.

## 4. Idempotence et sécurité

Les quatre endpoints acceptent `Idempotency-Key`. La clé est hachée et namespace par domaine afin d'éviter qu'une même chaîne fournie par le client ne collisionne entre deux types de workflows.

Les lectures et actions restent limitées au propriétaire du `WorkflowRun`. La résolution structurale vérifie également le propriétaire de l'étude et l'appartenance de la cible à cette étude.

Les workers historiques sont réutilisés. Les messages du dispatcher contiennent l'identifiant de l'étape et son jeton de bail ; ils ne contiennent ni séquence longue, ni structure binaire, ni résultat scientifique complet.

La livraison reste au moins une fois. L'idempotence et les contrôles de sortie réduisent les effets de redélivrance, mais ne constituent pas une garantie exactly-once des effets externes.

## 5. Périmètre volontairement différé

### Analyse 4PL

Le 4PL est déjà livré dans le domaine expérimental : les analyses sont versionnées, liées à un dossier d'essai accepté, protégées par une clé d'analyse, et enregistrées dans `ResearchAssayAnalysis` avec des références dans `ResearchEvidence`.

Cette phase ne crée pas un second moteur 4PL. Une prochaine intégration devra isoler un service de calcul pur et un worker réutilisable sans contourner les règles d'acceptation curatoriale, de réplication biologique, d'unités et de provenance actuellement appliquées par l'API.

### Phytochimie

Le catalogue `PhytochemicalCandidate` reste un registre de candidats curés, pas un stockage de spectres ou de lots analytiques. La curation, les vérifications d'identité/occurrence et la revue sont des opérations contrôlées par rôle ; elles ne deviennent pas des tâches Celery génériques.

Le pipeline expérimental LC-MS/MS reste une phase distincte. Il exige d'abord les entités spécimen botanique, échantillon, lot d'extraction, fraction, lot analytique, artefact brut, feature spectrale et annotation. Il ne faut pas placer ces informations dans `PhytochemicalCandidate`.

## 6. Tests et limites scientifiques

La suite `test_scientific_workflow_phase1d.py` couvre :

- allowlist des tâches HSA, QSA, RNA-FM et structure ;
- création des jobs et des runs ;
- idempotence des soumissions ;
- contrôle du propriétaire ;
- contrôle de l'état `analysis` d'une étude ;
- rejet HSA si le résultat persisté ne porte pas `success=true` ;
- contrôle de confinement et SHA-256 des artefacts RNA/PDB ;
- rejet d'une structure qui n'a pas satisfait la porte de qualité.

Ces tests valident l'intégration logicielle et le contrat de persistance. Ils ne valident ni la pertinence biologique d'un score HSA, ni la justesse d'une prédiction RNA-FM sur toutes les séquences, ni la qualité d'un modèle structural sur une cible réelle. Les appels externes et les calculs lourds doivent être évalués sur des jeux de référence scientifiques séparés.

## 7. Déploiement

Aucune migration de schéma n'est ajoutée dans cette sous-phase : les tables métier et le noyau WorkflowRun/Step sont déjà présents. La Phase 1D doit être déployée après sauvegarde et validation de la migration scientifique de Phase 1B sur une copie représentative de la base.

Les routes HSA/QSA, RNA et Research historiques sont conservées. Le nouveau parcours est opt-in sous `/scientific-workflows` ; il ne remplace pas silencieusement les routes utilisées par le frontend existant.
