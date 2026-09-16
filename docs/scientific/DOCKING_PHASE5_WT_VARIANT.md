# N3XORA Phase 5 — Docking comparatif WT ↔ Variant

## Objectif
Exécuter un même ligand sous un protocole déclaré sur une cible WT et sa cible variant, puis comparer les sorties de docking sans convertir l'écart de score en différence expérimentale d'affinité.

## Contrat
- même ligand (SMILES conservé et canonique lorsque RDKit est disponible) ;
- même nombre maximal de poches déclaré ;
- structure WT et structure variant déjà validées ;
- deux exécutions Vina séparées ;
- Vina reste le résultat primaire ;
- ASP reste une agrégation post-Vina complémentaire ;
- Δscore = contraste du protocole, pas ΔG expérimentale.

## Interprétation
Un changement de score peut résulter de la modification de la structure, de la poche, de la recherche ou de la fonction de score. Il ne prouve ni une modification expérimentale d'affinité, ni une résistance, ni une efficacité thérapeutique. La comparaison structurale Phase 4 doit être interprétée conjointement.

## Validation recommandée
Pour une étude scientifique, comparer plusieurs ligands et répétitions, documenter la version exacte de Vina, la géométrie des boîtes, les structures sources, et utiliser des contrôles/benchmarks adaptés. Les challenges D3R évaluent séparément la reproduction de poses par RMSD et le classement d'affinité, illustrant qu'une méthode de docking peut être correcte sur les poses tout en restant limitée pour le ranking.
