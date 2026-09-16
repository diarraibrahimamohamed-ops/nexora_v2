# N3XORA v2 — Modularisation du frontend

## Objectif

Le fichier historique `frontend/js/nexora-core.js` contenait l'essentiel du frontend dans un seul fichier d'environ 4 100 lignes. Il mélangeait état global, import FASTA, NCBI, analyse, résistance, IA, visualisation, historique, événements et AfriBio.

La stabilisation conserve volontairement des **scripts classiques** plutôt qu'une migration ES Modules complète. Cela évite de casser les appels inline historiques présents dans `index.html` (`onclick="..."`) et réduit le risque de modifier la logique applicative pendant la phase de stabilisation.

## Nouvelle répartition

- `nexora-core.js` — état partagé, constantes scientifiques, compatibilité et initialisation principale.
- `nexora-ui-core.js` — onglets et commandes UI de base.
- `nexora-fasta.js` — import, parsing, validation et prévisualisation FASTA.
- `nexora-ncbi.js` — recherche/récupération NCBI et fonctions associées.
- `nexora-ai.js` — interface d'interprétation IA.
- `nexora-analysis.js` — analyse de séquences, transcription/traduction et sauvegarde.
- `nexora-resistance.js` — analyse de résistance.
- `nexora-ui.js` — notifications, historique et utilitaires UI.
- `nexora-visualization.js` — visualisations, graphiques et fonctions 3D générales.
- `nexora-events.js` — listeners, fonctions de compatibilité et exports globaux.
- `afribio-core.js` — module frontend AfriBio.

## Point critique corrigé

Dans l'ancien fichier, une grande partie des fonctions était déclarée à l'intérieur d'un callback `DOMContentLoaded`. Certaines fonctions utilisées depuis le HTML ou depuis d'autres modules n'étaient donc pas réellement globales.

La modularisation transforme ces fonctions en déclarations globales de scripts classiques. Le HTML existant reste ainsi compatible sans introduire prématurément un système d'import/export ES Modules.

## Ordre de chargement

`index.html` charge d'abord les dépendances applicatives existantes (`config`, `state`, `api`, `docking`, `app`, `bridge`, `ligands`), puis le noyau N3XORA et ses modules, et enfin AfriBio.

Tous les scripts sont chargés avant `DOMContentLoaded`; l'initialisation principale du noyau est donc exécutée seulement lorsque le DOM est disponible.

## Vérifications

- Syntaxe Node.js vérifiée sur les nouveaux fichiers JavaScript.
- `index.html` restauré depuis la source originale avant modification du seul bloc de scripts.
- Les fonctions globales historiques nécessaires au HTML sont toujours exportées.

## Limites

Cette modularisation ne constitue pas une réécriture fonctionnelle complète. Elle vise à réduire le couplage du monolithe et à préparer les corrections suivantes sans changer volontairement les algorithmes métier.
