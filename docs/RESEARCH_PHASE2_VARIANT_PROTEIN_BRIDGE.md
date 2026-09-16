# N3XORA — Phase 2 : pont variant → protéine

## Objectif

Cette phase transforme un variant protéique explicitement décrit (par exemple `K76T`) et une séquence WT vérifiée en une séquence protéique variante déterministe. Elle prépare la comparaison WT/variant sans prétendre déduire une causalité biologique.

## Ce qui est calculé

Pour une substitution missense `XnY` :

1. validation de la séquence WT ;
2. lecture de la position protéique 1-based ;
3. vérification que le résidu WT observé est bien `X` ;
4. substitution de `X` par `Y` ;
5. contrôle de conservation de longueur ;
6. enregistrement de la méthode et de la provenance.

## Ce qui n'est pas inféré

- causalité de la mutation ;
- résistance phénotypique ;
- changement de stabilité ;
- structure 3D du mutant ;
- efficacité d'un médicament.

Une mutation nucléotidique n'est pas convertie automatiquement en mutation protéique sans information de cadrage suffisante. La phase suivante pourra introduire un mapping CDS explicite (coordonnées, frame, strand et code génétique) lorsque les métadonnées nécessaires seront présentes.

## Alignement avec l'état des connaissances

Pour la résistance antipaludique, le cadre OMS distingue les marqueurs `validated`, `candidate` et `potential` en combinant les preuves de laboratoire, cliniques et de génétique des populations. N3XORA conserve cette distinction et ne transforme pas un variant simplement observé en marqueur validé.

## Limitation structurelle

La cible variante créée par cette phase porte `structure_status=not_generated`. Une structure expérimentale ou une modélisation validée doit être fournie séparément et conserver sa provenance avant tout docking comparatif WT/variant.

## Conséquence nucléotidique explicite

Une route dédiée accepte une CDS explicitement fournie dans son orientation codante 5'→3' et une coordonnée 1-based. N3XORA vérifie la base de référence, reconstruit le codon de référence et le codon mutant avec le code génétique standard (NCBI table 1), puis détermine une conséquence `synonymous`, `missense`, `nonsense` ou `stop_loss`.

Cette étape n'autorise pas une mutation génomique à devenir automatiquement une annotation codante : le contexte CDS et son système de coordonnées doivent être fournis. Les coordonnées génomiques, le strand et les régions UTR/introniques doivent être résolus par une source d'annotation appropriée avant d'utiliser cette route.
