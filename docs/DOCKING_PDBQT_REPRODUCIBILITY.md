# PDBQT, traçabilité et reproductibilité du docking

## Validation PDBQT avant AutoDock Vina

Nexora valide le document préparé du récepteur et du ligand avant d'appeler
Vina. Le contrôle porte sur la syntaxe et la cohérence structurelle :
- au moins un atome;
- coordonnées et charges numériques finies;
- type AutoDock/Vina reconnu;
- récepteur rigide sans arbre ROOT/BRANCH/TORSDOF;
- ligand avec ROOT/ENDROOT, TORSDOF unique, branches appariées et nombre de
  branches cohérent avec TORSDOF.

Cette validation ne démontre pas la justesse chimique des états de protonation,
des charges partielles, des tautomères, de la stéréochimie ou des paramètres de
force field. Ces points exigent une validation chimique distincte.

## Deux chemins de préparation à ne pas confondre

**Open Babel** prépare le ligand 3D/PDBQT avec protonation demandée à pH 7.4,
charges Gasteiger et arbre de torsion. Le récepteur est préparé en rigide.

**Fallback interne Nexora** produit un PDBQT minimal avec types AutoDock estimés.
Le ligand est explicitement rigide (\`TORSDOF 0\`), même si le nombre de liaisons
rotatables calculé en amont est supérieur à zéro. Le manifeste conserve le
nombre demandé et le nombre effectif, ainsi que la cause du fallback. Les deux
voies ne doivent jamais être présentées comme équivalentes.

Le champ de validation \`pdbqt_fallback_used\` et le bloc
\`metadata.pdbqt_preparation\` rendent cette différence visible.

## Manifeste de calcul

Chaque docking réussi fournit \`calculation_manifest\` (schéma
\`nexora.docking-manifest.v1\`) dans le JSON complet du job et dans
\`docking_results.pose_data\`. Le manifeste conserve :
- SHA-256 de la séquence protéique, du SMILES brut, de la structure source,
  des PDBQT récepteur/ligand et des configurations Vina par poche;
- identifiant, source et validation de la structure;
- chemins, empreintes SHA-256 et versions disponibles de Vina, fpocket et
  Open Babel; version RDKit et Biopython;
- paramètres Vina effectifs (CPU, exhaustivité, modes, plage énergétique,
  graine, timeout), paramètres d'agrégation ASP et boîtes de chaque poche;
- état de chaque poche (terminée, timeout, erreur Vina ou aucune pose).

Quand une version ne peut pas être interrogée de manière fiable, elle reste
\`null\` ou explicitement non vérifiée; l'empreinte binaire est conservée si le
fichier est lisible. fpocket n'est pas interrogé avec \`--version\`, car sa
bannière peut être ambiguë suivant les distributions.

## Correspondance atomique et RMSD avec symétrie

Lorsque RDKit prépare le ligand, Nexora conserve le MolBlock 3D exact et le
transmet à Open Babel comme source SDF. Open Babel convertit cette structure
vers PDBQT sans régénérer une nouvelle géométrie 3D. Le manifeste conserve
l'empreinte SHA-256 de ce MolBlock.

Les numéros atomiques PDBQT source sont ensuite utilisés pour restaurer l'ordre
des atomes dans chaque pose Vina. Une pose dont le nombre ou l'ensemble des
numéros de série diffère du PDBQT source est rejetée pour l'agrégation ASP :
Nexora ne tente pas de deviner une correspondance d'atomes.

Quand le MolBlock RDKit est disponible et que le nombre ainsi que la séquence
des éléments lourds concordent avec le PDBQT source, RDKit calcule les
automorphismes du graphe moléculaire (jusqu'à 256 correspondances). L'ASP
calcule alors le RMSD minimal sur ces permutations, toujours dans le repère du
récepteur. Aucune superposition de Kabsch n'est effectuée : les translations et
rotations entre deux sites de liaison restent donc visibles.

Si le MolBlock n'est pas disponible, si l'ordre atomique ne peut pas être
réconcilié ou si la préparation Open Babel utilise le chemin SMILES historique,
l'optimisation par symétrie est désactivée. Le clustering reste alors un RMSD
fixe par indices après restauration vérifiée de l'ordre PDBQT, et les
métadonnées expliquent pourquoi aucune permutation n'a été appliquée.

La vérification du nombre et de la séquence des éléments lourds est un contrôle
de cohérence, pas une preuve chimique indépendante de la correspondance. Le
raccordement repose sur le même MolBlock/SDF fourni à Open Babel et sur les
numéros de série produits dans le PDBQT. Une validation réelle de bout en bout
avec plusieurs ligands symétriques préparés par les versions Open Babel ciblées
reste nécessaire avant de revendiquer une validation scientifique complète.

## Portée scientifique

Le manifeste permet de reconstruire et d'auditer les paramètres et fichiers
d'entrée d'un calcul. Il ne constitue pas une preuve de précision prédictive.
Le score Vina demeure empirique; ASP demeure une agrégation de scores après
clustering, pas une énergie libre de liaison expérimentale. Une validation
externe sur complexes de référence, avec protocole indépendant et mesures
prédéfinies, reste indispensable.
