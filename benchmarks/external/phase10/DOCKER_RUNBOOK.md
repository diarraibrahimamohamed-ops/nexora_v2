# Phase 10 — environnement isolé recommandé

L'exécution réelle doit se faire dans un environnement possédant au minimum :

- AutoDock Vina 1.2.5 ;
- fpocket 4.2.3 ;
- Python/NumPy/RDKit compatibles avec la version du projet ;
- les données DUD-E ;
- suffisamment d'espace disque pour conserver les poses brutes.

Le dépôt upstream de fpocket fournit une image Docker officielle `fpocket/fpocket`, et le dépôt AutoDock Vina fournit les binaires de release. Pour la campagne, les versions doivent être enregistrées dans le manifest final plutôt que d'utiliser `latest`.

Le démon Docker n'est pas requis dans cet environnement de développement pour inspecter le code ; il est requis sur la machine d'exécution pour lancer réellement le benchmark isolé.
