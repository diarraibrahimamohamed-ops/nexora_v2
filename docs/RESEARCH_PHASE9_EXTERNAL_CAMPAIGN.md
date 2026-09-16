# N3XORA-Sahel — Phase 9 : campagne externe et décision ASP

Phase 9 prend la validation externe du stade « prêt » au stade « exécutable et statistiquement interprétable ».

Le protocole sépare les capacités de docking et de screening conformément à CASF-2016 et utilise DUD-E Diverse comme premier corpus court et reproductible. DUD-E indique 102 cibles au total et un sous-ensemble Diverse de 8 cibles. citeturn613875search1turn639782search0

Le nouvel évaluateur (`tools/benchmark_phase9_campaign.py`) réutilise le gate Phase 8 puis ajoute une inférence appariée au niveau des cibles. La décision principale compare Vina TOP1 à ASP clustered ; les deux autres comparaisons sont secondaires.

Les paramètres Vina sont verrouillés et le seed est explicite. Le manuel Vina indique que l'identité des résultats dépend du seed et de l'ensemble des entrées/paramètres. citeturn613875search2

DUD-E reste un benchmark de reconnaissance moléculaire : ses décoys sont construits et équilibrés sur des propriétés physico-chimiques, ils ne constituent pas des inactifs expérimentaux confirmés. citeturn613875search3turn613875search6

CASF-2016 sépare scoring, ranking, docking et screening ; N3XORA conserve donc ces tracks séparés. citeturn613875search0

## État de la campagne dans cette archive

La campagne réelle n'est **pas déclarée exécutée** si les moteurs et les packages externes n'ont pas été réellement lancés. Cette archive contient le protocole, les contrôles et le code d'inférence, pas des performances inventées.
