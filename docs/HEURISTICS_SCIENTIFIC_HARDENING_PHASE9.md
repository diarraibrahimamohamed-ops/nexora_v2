# N3XORA — Hardening scientifique des modules heuristiques (Phase 9)

## Objectif

Renforcer les trois branches heuristiques qui sont cohérentes avec le positionnement microbiologie computationnelle de N3XORA : interprétation génomique, résistance antimicrobienne et HSA. Le but n’est pas de transformer les heuristiques en diagnostics ni en prédicteurs cliniques.

## 1. Interprétation génomique

Les règles GC/longueur/k-mers/GC-skew sont désormais formulées comme **signaux de compatibilité contextuelle**. Un GC faible ne permet plus d’afficher « virus/plasmide/Plasmodium » comme identification. La présence d’un motif court n’est plus appelée un gène : les motifs hérités de 5 nt sont explicitement insuffisants pour une annotation.

Le champ `confidence_score` n’est plus calculé artificiellement. Il est remplacé par `evidence_score` / `evidence_components`, qui décrivent la complétude descriptive du dossier d’analyse.

La détection de variations expose également un `impact_heuristic_score`, mais `pathogenicity_score` est forcé à `None`. Une variation de séquence ne suffit pas à conclure à la pathogénicité.

## 2. Résistance antimicrobienne

Le pourcentage aléatoire basé sur le nombre de mutations a été supprimé. Pour une bactérie, N3XORA cherche en priorité à déléguer la détection des déterminants AMR à **AMRFinderPlus** lorsqu’il est installé. NCBI décrit AMRFinderPlus comme un outil identifiant des gènes AMR et certaines mutations associées à la résistance dans des séquences assemblées, avec des bases et seuils curés. Les sorties comprennent notamment la méthode d’identification, l’identité et la couverture.

N3XORA ne convertit pas ces hits génétiques en probabilité de résistance, et ne remplace pas l’AST/antibiogramme. Pour un virus, le module antibiotiques retourne « non applicable » au lieu de fabriquer un score.

## 3. HSA

HSA reste un indice heuristique de séquence. L’interface ne parle plus de « mutations probables » ni de « séquence hautement fiable ». `mutation_prob` reste toujours `None`. Le pH est rapporté mais n’entre pas dans la formule actuelle, conformément au code existant qui ne dispose pas d’un modèle buffer/sels/pKa.

## 4. Ce que cette phase prouve / ne prouve pas

Elle prouve que les sorties heuristiques sont déterministes, traçables et mieux bornées scientifiquement. Elle ne prouve pas une performance biologique ou clinique. Pour une publication, il faut ensuite comparer les sorties à des jeux de données externes et à des annotations/phenotypes de référence.

## Références externes

- NCBI AMRFinderPlus: https://www.ncbi.nlm.nih.gov/pathogens/antimicrobial-resistance/AMRFinder/
- NCBI Pathogen Detection / AMR Resources: https://www.ncbi.nlm.nih.gov/pathogens/antimicrobial-resistance/resources/
- Feldgarden et al., validation/curation AMRFinderPlus: https://pmc.ncbi.nlm.nih.gov/articles/PMC8208984/ et https://pmc.ncbi.nlm.nih.gov/articles/PMC9455714/
- EUCAST Expert Rules: https://www.eucast.org/bacteria/important-additional-information/expert-rules/
- EUCAST Clinical Breakpoints v16.1 (24 juin–31 décembre 2026): https://www.eucast.org/bacteria/clinical-breakpoints-and-interpretation/clinical-breakpoint-tables/
