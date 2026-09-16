# N3XORA — Research Workflow Sahel, Phase 1

Date: 15 septembre 2026

## 1. Purpose

Cette phase ne remplace pas N3XORA v2 : elle ajoute une couche d'orchestration scientifique au-dessus des modules existants.

Objectif initial: permettre de documenter une étude centrée sur les variations de *Plasmodium falciparum* d'intérêt pour le Mali/Sahel et d'enchaîner, sans prétendre à une causalité automatique:

`source/isolate → variant → protéine cible → structure → poche → Vina → analyse de poses → interprétation 3D`.

## 2. Pourquoi ce scope

Les travaux sur des isolats maliens ont documenté une forte diversité génomique et des différences géographiques/temporelles de marqueurs associés à la résistance. Une étude portant sur 830 isolats maliens et 12 177 SNPs de haute qualité rapporte notamment des variations aux loci pfcrt, pfdhps, pfdhfr, pfmdr1, pfmdr2 et pfk13 et recommande une évaluation continue des interventions confrontées à ces variantes.

Une étude plus récente sur 2019–2020 à Ouélessébougou, replacée dans un ensemble malien et africain, rapporte une hausse de certains marqueurs de résistance sulfadoxine-pyriméthamine et luméfantrine et souligne l'intérêt de la surveillance génomique continue.

WHO maintient aujourd'hui la surveillance de la résistance aux antipaludiques comme une priorité et a publié en décembre 2025 un compendium de marqueurs moléculaires classés selon des niveaux de preuve (validés, candidats, potentiels). Les recommandations 2025–2026 insistent sur des protocoles standardisés et sur la collecte/communication rapide de données de qualité.

## 3. Ce que N3XORA ne doit pas prétendre

- Un variant observé n'est pas automatiquement causal.
- Un marqueur moléculaire ne remplace pas une étude d'efficacité thérapeutique ou un phénotype.
- Un résultat de docking n'est pas une preuve d'efficacité clinique.
- Une structure prédite doit rester séparée d'une structure expérimentale et garder sa provenance.
- ASP est une agrégation mathématique complémentaire tant qu'un benchmark externe n'a pas démontré une valeur prédictive/utilitaire distincte de Vina.

## 4. Rôle des modules existants

| Module | Rôle dans le workflow |
|---|---|
| NCBI / FASTA | acquisition et entrée |
| Alignement / variants | comparaison et détection |
| génomique | caractérisation descriptive |
| AMR | évidence génotypique contextualisée |
| HSA | heuristique secondaire, non clinique |
| structure | représentation WT/variant et provenance |
| fpocket | détection des cavités |
| Vina | baseline / moteur principal de docking |
| ASP | analyse/agrégation expérimentale |
| 3Dmol.js | interprétation géométrique interactive |
| PostgreSQL | provenance et état de l'étude |
| Celery/Redis | calcul asynchrone |

## 5. Extension volontairement non automatique

La génération d'une structure mutante n'est pas inventée par cette phase. Une cible `variant` exige une séquence protéique explicitement fournie et sa provenance. Cela évite de présenter une mutation comme une structure validée alors qu'aucune modélisation indépendante n'a été exécutée.

## 6. Benchmark à conduire

### Docking

Comparer au minimum:

- Vina standard / TOP1;
- Vina + agrégation ASP;
- ensembles avec actifs/decoys;
- plusieurs cibles et états WT/variant lorsque les données le permettent.

Rapporter ROC-AUC, average precision, enrichment factor, métriques de pose et bootstrap au niveau cible. Ne pas utiliser un unique score global comme preuve.

### Variants

Le benchmark doit séparer:

1. variant observé;
2. association publiée;
3. effet structural calculé;
4. validation expérimentale.

## 7. Concurrence scientifique à surveiller

BactProNET (Scientific Reports, 2026) montre déjà qu'une plateforme peut intégrer mutations de résistance, structures WT/mutantes, docking et contexte évolutif. Il faut donc différencier N3XORA par son axe microbiologie computationnelle régional et par un continuum d'étude configurable, pas par une simple juxtaposition de docking + mutations.

## 8. Décision ASP

ASP n'est pas promu en méthode principale dans cette phase. Il reste présent pour permettre la comparaison expérimentale avec Vina. Sa promotion ou son retrait doit être piloté par des résultats reproductibles.

## 9. Sources externes de travail

- WHO, *Guidelines for malaria*, mise à jour 10 septembre 2026.
- WHO, *Compendium of molecular markers for antimalarial drug resistance*, 10 décembre 2025.
- WHO, *Malaria surveillance, monitoring and evaluation: reference manual, 2nd ed.*, 10 juillet 2025.
- Genome-wide SNP analysis of *P. falciparum* in southern Mali, 2022.
- Genome-wide variation and molecular surveillance in Ouélessébougou, Mali, 2023.
- BactProNET, *Scientific Reports*, 20 mai 2026.
- MalariaGEN Pf8 study: Genomic surveillance of *P. falciparum* in Mali.

La présence de ces sources dans ce document sert de justification de conception. Elle ne constitue pas une validation des performances de N3XORA.
