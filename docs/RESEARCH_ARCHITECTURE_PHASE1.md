# N3XORA — Architecture Research Workflow, Phase 1

## Décision d'architecture

Le socle N3XORA v2 est conservé. Le nouveau niveau métier est un sous-système `research` qui orchestre les briques existantes au lieu de les remplacer.

```text
Browser
  │
  ├── Existing N3XORA tabs
  │
  └── research.html
          │
        Nginx
          │
        FastAPI
          │
   /api/v1/research
          │
   ┌──────┼─────────────┐
   ↓      ↓             ↓
PostgreSQL  Celery     Existing services
   │         │          │
Studies     Vina       genomics / AMR / HSA
Variants    fpocket
Targets     ASP
Runs
```

## Graphe scientifique

```text
Study
  ├── Variant (observed/candidate/published evidence)
  │       └── Target variant state (optional)
  │
  ├── Target WT / Variant
  │       └── structure provenance
  │
  └── DockingRun
          ├── target
          ├── ligand
          ├── Vina job
          └── result summary
```

## Invariants

1. Une étude appartient toujours à l'utilisateur connecté.
2. Un variant, une cible et un docking run doivent appartenir à la même étude.
3. Une cible WT ne référence pas de variant.
4. Une cible variant référence explicitement le variant correspondant.
5. Un docking `reference_state` doit correspondre à l'état de la cible.
6. L'exécution réutilise le moteur Vina/Celery existant ; aucune seconde implémentation de docking n'est créée.
7. ASP reste marqué comme analyse complémentaire jusqu'à validation comparative externe.
8. La provenance structurale est conservée ; aucune structure mutante n'est fabriquée implicitement.

## Justification scientifique externe

La conception est alignée sur :

- WHO, Guidelines for malaria, mise à jour 10 septembre 2026.
- WHO, Compendium of molecular markers for antimalarial drug resistance, 10 décembre 2025.
- WHO, Malaria surveillance, monitoring and evaluation: reference manual, 2e éd., 10 juillet 2025.
- Genome-wide SNP analysis of *P. falciparum* in southern Mali (830 isolats; 12 177 SNPs de haute qualité; variation géographique/temporelle de marqueurs de résistance).
- Genome-wide variation and molecular surveillance of *P. falciparum* in Ouélessébougou, Mali (2019–2020; mise en contexte avec des isolats maliens et africains).
- BactProNET, Scientific Reports, 2026, qui montre la valeur et aussi les limites d'une intégration mutation–structure–docking–évolution.

Ces références justifient le périmètre et les garde-fous ; elles ne constituent pas une validation de performance de N3XORA.
