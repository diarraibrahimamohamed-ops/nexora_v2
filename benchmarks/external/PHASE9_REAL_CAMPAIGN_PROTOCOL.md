# N3XORA-Sahel — Phase 9 : campagne externe réelle

## Objectif

Exécuter enfin une campagne externe reproductible qui compare **Vina TOP1**, **ASP sans clustering** et **ASP avec clustering** sur les mêmes cibles, mêmes ligands, mêmes récepteurs, même protocole et mêmes paramètres.

Cette phase ne suppose pas qu'ASP doit gagner. L'hypothèse nulle est explicitement autorisée à être vraie.

## Corpus initial

La première campagne recommandée utilise le sous-ensemble **DUD-E Diverse**, qui contient 8 cibles : AKT1, AMPC, CP3A4, CXCR4, GCR, HIVPR, HIVRT et KIF11. DUD-E publie ce sous-ensemble comme représentatif de l'ensemble de 102 cibles. Source : https://dude.docking.org/subsets/diverse

DUD-E contient des actifs et des décoys construits à partir de critères physico-chimiques et topologiques ; les décoys ne sont pas des inactifs expérimentaux confirmés. Source : https://dude.docking.org/faq

## Protocole verrouillé

- AutoDock Vina : **version 1.2.5** pour correspondre au protocole N3XORA ; une autre version ne doit pas être mélangée dans le même benchmark.
- `seed` fixe.
- `exhaustiveness` fixe.
- `num_modes` fixe.
- `energy_range` fixe.
- même récepteur pour les trois méthodes.
- même boîte de docking pour les trois méthodes.
- mêmes ligands et même préparation chimique.
- même sélection de poses Vina en entrée d'ASP.
- aucune optimisation des paramètres sur les 8 cibles finales.

Le manuel Vina confirme que la reproductibilité exacte exige le même seed ainsi que les mêmes entrées et paramètres ; `exhaustiveness` règle le nombre de runs indépendants et `num_modes` le nombre maximal de modes retournés. Source : https://vina.scripps.edu/manual/

## Tracks

### Virtual screening

Endpoint primaire : **AP / Average Precision** au niveau cible-macro.

Endpoints secondaires : ROC-AUC, EF1%, EF5%.

### Pose selection

Si les poses natives et les données CASF compatibles sont utilisées : RMSD lourd, succès à 2 Å et médiane RMSD.

CASF-2016 sépare explicitement scoring, ranking, docking et screening ; ces tâches ne doivent pas être fusionnées dans une métrique unique.

## Statistique Phase 9

L'unité d'inférence est la **cible**, pas la ligne ligand.

Pour chaque paire :

- ASP clustered vs Vina TOP1 ;
- ASP no-cluster vs Vina TOP1 ;
- ASP clustered vs ASP no-cluster.

On rapporte :

- différence moyenne macro par cible ;
- IC95 % bootstrap target-level ;
- test de permutation sign-flip apparié au niveau des cibles ;
- correction Holm-Bonferroni sur les comparaisons planifiées.

## Fuite

La campagne doit être accompagnée du manifest Phase 8 et de tous les hashes. Les ligands réutilisés entre cibles ne sont pas automatiquement invalides, mais doivent être rendus visibles. Lorsque les SMILES sont disponibles, le rapport Phase 9 ajoute un diagnostic des scaffolds Murcko partagés entre cibles.

Le résultat d'un benchmark n'est interprétable que si le jeu final n'a pas été ajusté à partir de ses propres performances.

## Ce qui serait une vraie conclusion

**Exemple acceptable :** « sur les 8 cibles DUD-E Diverse, la moyenne macro d'AP d'ASP clustered est X, celle de Vina TOP1 est Y, différence X−Y = Z, IC95 %, p ajustée = ... ».

**Exemple interdit :** « ASP augmente l'affinité », « ASP prédit la résistance », « le ligand est un médicament », ou « la mutation est causale ».

## Décision ASP

- IC/effet systématiquement favorable et cohérent : étude de suivi.
- absence de différence : module complémentaire sans avantage démontré.
- performance inférieure : ASP ne doit pas rester le ranking principal.

Aucune de ces décisions n'est pré-remplie : elle dépend des données réellement exécutées.
