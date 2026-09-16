# N3XORA-Sahel — Phase 10 : exécution réelle et capture de preuves

## But

Phase 10 ne modifie pas l'hypothèse scientifique. Elle rend la campagne de validation réellement exécutable et auditable sur une machine qui possède Vina/fpocket et les données externes.

## Règles verrouillées

- Vina 1.2.5 est la version de référence de cette campagne.
- Les paramètres Vina sont fixés dans le manifest.
- Le seed est fixé.
- Une seule boîte de docking est utilisée pour les trois sorties d'une même paire cible/ligand.
- La poche est choisie à partir du récepteur seulement et figée avant l'utilisation des labels actifs/décoys.
- Les sorties ASP sont dérivées des mêmes poses Vina.
- Aucun résultat d'activité n'est synthétisé à partir de QED, Lipinski, PAINS ou d'une autre heuristique.
- Les résultats bruts sont conservés et hachés.

## Déroulement

```text
DUD-E réel
   ↓
préparation indépendante des entrées
   ↓
manifest figé
   ↓
préflight
   ↓
fpocket / choix du pocket selon règle déclarée
   ↓
Vina
   ↓
PDBQT bruts
   ↓
TOP1 + ASP no-cluster + ASP clustered
   ↓
CSV immuable + hashes
   ↓
Phase 8 gate
   ↓
Phase 9 inference
```

## Pourquoi cette organisation

DUD-E est un benchmark de virtual screening constitué de cibles, actifs et décoys. Son sous-ensemble Diverse contient 8 cibles et sert de corpus court reproductible. Le benchmark ne doit toutefois pas être interprété comme une preuve clinique et les décoys ne sont pas des inactifs expérimentaux au sens d'un essai biologique.

Vina indique que les résultats exacts dépendent de l'ensemble des entrées/paramètres et du seed, et rappelle que le docking reste une approche approximative. La release 1.2.5 a notamment corrigé le tri des poses par énergie ; elle est donc explicitement figée ici plutôt que mélangée avec d'autres versions.

## Échec acceptable

Si ASP est moins performant que Vina, cette conclusion est conservée.

Si ASP est équivalent et redondant, il peut devenir secondaire.

Si ASP n'apporte aucune information utile, il doit pouvoir être retiré du produit principal.

Aucune phase ultérieure ne doit modifier le benchmark après inspection des résultats afin de rendre ASP « gagnant ».
