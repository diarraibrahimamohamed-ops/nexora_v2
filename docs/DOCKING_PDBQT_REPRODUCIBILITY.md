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
Le ligand est explicitement rigide (`TORSDOF 0`), même si le nombre de liaisons
rotatables calculé en amont est supérieur à zéro. Le manifeste conserve le
nombre demandé et le nombre effectif, ainsi que la cause du fallback. Les deux
voies ne doivent jamais être présentées comme équivalentes.

Le champ de validation `pdbqt_fallback_used` et le bloc
`metadata.pdbqt_preparation` rendent cette différence visible.

## Ordre atomique et RMSD avec symétrie moléculaire

Lorsque RDKit prépare le ligand, Nexora conserve le MolBlock 3D exact et le
transmet à Open Babel comme source SDF. La conversion du ligand vers PDBQT
utilise explicitement l'option de sortie PDBQT `-xp`, qui préserve les indices
atomiques du fichier SDF. Cette option est distincte de `-p 7.4`, qui demande
l'ajout/protonation des hydrogènes au pH indiqué.

Cette distinction est essentielle : sans `-xp`, Open Babel peut renuméroter les
atomes en fonction de l'ordre de l'arbre de torsion écrit dans le PDBQT. Dans ce
cas, les numéros de série ne peuvent pas être interprétés comme les indices
atomiques du MolBlock RDKit. Nexora conserve donc ce contrat explicitement dans
la commande de conversion et trie les enregistrements PDBQT par leurs numéros
de série source pour reconstruire l'ordre RDKit. La suppression des hydrogènes
non polaires par le format PDBQT ne doit pas modifier l'ordre des atomes lourds.

Avant l'agrégation ASP, les poses Vina sont réordonnées par les numéros de série
source lorsque ceux-ci sont conservés, avec vérification des noms et éléments
atomiques. Si Vina réécrit les numéros de série, le repli par couples
nom-d'atome/élément n'est accepté que si ces clés sont uniques dans les deux
documents. Une pose qui ne peut pas être réconciliée sans ambiguïté est rejetée;
Nexora ne devine jamais une correspondance.

Lorsque le MolBlock RDKit est disponible et que la séquence des éléments lourds
correspond à l'ordre PDBQT restauré, RDKit calcule les automorphismes du graphe
moléculaire (maximum 256 correspondances, avec prise en compte de la chiralité).
Le clustering ASP utilise le RMSD minimal sur ces permutations, tout en
conservant les coordonnées dans le repère fixe du récepteur. Aucune
superposition de Kabsch n'est effectuée : une translation ou une rotation réelle
d'une pose reste mesurable. Sans MolBlock partagé ou sans ordre atomique
réconciliable, l'optimisation par symétrie est désactivée.

Le test d'intégration CI utilise le véritable exécutable Open Babel sur un
ligand symétrique et flexible. Il vérifie que les numéros de série du PDBQT
permettent de retrouver les indices des atomes lourds du MolBlock RDKit, puis
que la génération des automorphismes peut s'appliquer. Ce test contrôle le
contrat logiciel de correspondance atomique; il ne démontre ni la qualité
chimique de la protonation ni la précision prédictive du docking.

## Manifeste de calcul

Chaque docking réussi fournit `calculation_manifest` (schéma
`nexora.docking-manifest.v1`) dans le JSON complet du job et dans
`docking_results.pose_data`. Le manifeste conserve :
- SHA-256 de la séquence protéique, du SMILES brut, de la structure source,
  des PDBQT récepteur/ligand et des configurations Vina par poche;
- identifiant, source et validation de la structure;
- chemins, empreintes SHA-256 et versions disponibles de Vina, fpocket et
  Open Babel; version RDKit et Biopython;
- paramètres Vina effectifs (CPU, exhaustivité, modes, plage énergétique,
  graine, timeout), paramètres d'agrégation ASP et boîtes de chaque poche;
- état de chaque poche (terminée, timeout, erreur Vina ou aucune pose).

Quand une version ne peut pas être interrogée de manière fiable, elle reste
`null` ou explicitement non vérifiée; l'empreinte binaire est conservée si le
fichier est lisible. fpocket n'est pas interrogé avec `--version`, car sa
bannière peut être ambiguë suivant les distributions.

## Portée scientifique

Le manifeste permet de reconstruire et d'auditer les paramètres et fichiers
d'entrée d'un calcul. Il ne constitue pas une preuve de précision prédictive.
Le score Vina demeure empirique; ASP demeure une agrégation de scores après
clustering, pas une énergie libre de liaison expérimentale. Une validation
externe sur complexes de référence, avec protocole indépendant et mesures
prédéfinies, reste indispensable.
