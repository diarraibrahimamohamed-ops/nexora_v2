NEXORA V2 - NOTE DE MODIFICATION SCIENTIFIQUE
Date : 2026-09-06

OBJECTIF
--------
Rendre le pipeline de docking plus defensible scientifiquement sans presenter
une heuristique comme une structure biologique valide, ni un score Vina comme
une energie libre experimentale.

ETAT AVANT
----------
1. backend/app/core/docking/vina_runner.py appelait
   create_realistic_protein_structure() pour fabriquer une geometrie proteique
   interne. Cette geometrie n'etait ni un modele homologique, ni une structure
   experimentale.
2. AutoDock Vina etait lance sur plusieurs poches, avec num_modes=20.
3. Toutes les poses etaient additionnees directement dans
   -RT*log(sum(exp(-score/RT))). Les poses correlees n'etaient pas dedoublonnees.
4. Le resultat etait expose sous le nom effective_dg, alors que les scores
   Vina sont des scores empiriques et que l'etat non lie, l'etat standard et
   les populations des etats n'etaient pas modelises.
5. La valeur MODELLER existait dans l'enum de base, mais aucun appel MODELLER
   reel n'etait implemente. MODELLER aurait aussi impose une matrice, un
   alignement et une configuration de licence.

MODIFICATIONS
-------------
1. Ajout de backend/app/core/protein/colabfold_runner.py.
   - Appel optionnel de ColabFold/AlphaFold2 via colabfold_batch.
   - Selection du modele rank_001 produit par ColabFold.
   - Lecture du pLDDT moyen depuis le PDB et refus sous 50.
   - Conversion du PDB selectionne en atomes utilises par le docking.
   - Aucun fallback geometrique silencieux n'est fourni.
2. Le pipeline Vina utilise maintenant ce chemin ColabFold.
   Sans l'executable ColabFold, le job echoue avec un message explicite.
   C'est volontaire : un docking quantitatif ne doit pas masquer l'absence
   de structure d'entree exploitable.
3. Ajout de backend/app/core/docking/asp/.
   - scoring.py : poids Boltzmann stables, moyenne ponderee et log-sum-exp.
   - aggregation.py : clustering RMSD par poche, un representant par cluster,
     puis aggregation des representants.
   - Le resultat s'appelle effective_score, pas effective_dg.
4. Ajout de diagnostics : temperature, seuil RMSD, nombre de poses brutes,
   nombre de clusters, clusters par poche, moyenne ponderee et methode.
5. Ajout de la colonne effective_score et de la migration
   infra/postgres/migrations/003_add_effective_score_to_docking_results.sql.
   effective_dg est conserve pour compatibilite des anciennes donnees mais
   n'est plus rempli par le nouveau worker.
6. Ajout de tests unitaires dans backend/tests/test_asp_scoring.py.
7. Ajout de backend/requirements-structure.txt et d'un profil Docker optionnel
   `INSTALL_COLABFOLD=true`. ColabFold n'est pas ajoute aux dependances de base
   car sa pile AlphaFold/JAX/OpenMM et ses bases MSA sont lourdes.

CONFIGURATION REQUISE
---------------------
Les variables suivantes doivent etre definies dans .env et rendues accessibles
au service API et au worker Celery :

STRUCTURE_PROVIDER=auto_rcsb
RECEPTOR_PDB_PATH=/storage/structures/receptor.pdb
COLABFOLD_BINARY=colabfold_batch
COLABFOLD_OUTPUT_DIR=/storage/temp/colabfold
COLABFOLD_NUM_MODELS=5
COLABFOLD_NUM_RECYCLE=3

Pour construire l'image scientifique :
Le mode 8 Go recommande `STRUCTURE_PROVIDER=auto_rcsb`: Nexora recherche
automatiquement un PDB expérimental par similarité de séquence via l'API RCSB.
`external_pdb` reste un mode administrateur explicite. Pour le profil ColabFold :
`docker build --build-arg INSTALL_COLABFOLD=true -f backend/Dockerfile .`

Cette image doit disposer des ressources CPU/GPU et des bases MSA necessaires.
Le profil standard Nexora reste installable sans GPU.

REFERENCES SCIENTIFIQUES UTILISEES
----------------------------------
1. Jumper J et al. Highly accurate protein structure prediction with AlphaFold.
   Nature. 2021;596:583-589. DOI: 10.1038/s41586-021-03819-2
2. Mirdita M et al. ColabFold: Making protein folding accessible to all.
   Nat Methods. 2022;19:679-682. DOI: 10.1038/s41592-022-01488-1
3. ColabFold repository and installation: https://github.com/sokrypton/ColabFold
4. AlphaFold Protein Structure Database: https://alphafold.ebi.ac.uk/
5. Trott O, Olson AJ. AutoDock Vina. J Comput Chem. 2010;31:455-461.
   DOI: 10.1002/jcc.21334
5. Atkovska K, Samsonov SA, Paszkowski-Rogacz M, Pisabarro MT. Multipose
   binding in molecular docking. Int J Mol Sci. 2014;15:2622-2645.
   DOI: 10.3390/ijms15022622
6. Paulsen JL, Anderson AC. Scoring ensembles of docked protein:ligand
   interactions for virtual lead optimization. J Chem Inf Model. 2009;49:2813-2819.
   DOI: 10.1021/ci9003078
7. Purisima EO, Hogues H. Protein-ligand binding free energies from exhaustive
   docking. J Phys Chem B. 2012;116:6872-6879.
   DOI: 10.1021/jp212646s
8. Smith LG et al. PopShift: A Thermodynamically Sound Approach... J Chem
   Theory Comput. 2024;20:1036-1050. DOI: 10.1021/acs.jctc.3c00870

LIMITES NON RESOLUES
--------------------
1. L'agregation de scores Vina reste une heuristique de post-traitement.
   Elle ne devient pas une energie libre standard par la seule presence de RT.
2. Les poches candidates restent dependantes de fpocket et des heuristiques
   de detection existantes. Leur commensurabilite thermodynamique n'est pas
   demontree.
3. Le clustering RMSD reduit le double comptage de poses correlees, mais ne
   fournit pas de populations d'etats du recepteur comme un MSM.
4. AlphaFold2/ColabFold ne garantit pas la qualite de la poche : il faut
   documenter la version, le modele, le pLDDT, le PAE, la couverture MSA et
   verifier la geometrie de la poche avant le docking.
5. Une publication exige une validation externe sur structures experimentales
   et affinites mesurees, par exemple PDBbind/CASF, BindingDB ou ChEMBL selon
   la question, avec ablation TOP1/TOP5/multi-pose/multi-poche et statistiques
   predefinies.

ETAT APRES
----------
Le code distingue maintenant :
 - ColabFold : prediction AlphaFold2 a partir de la sequence, avec confiance ;
 - Vina : moteur de docking ;
 - ASP : aggregation independante de scores empiriques apres clustering.

Cette modification ameliore la tracabilite et la defensibilite du protocole,
mais ne constitue pas une preuve que Nexora predit correctement les affinites.

MISE A JOUR AUTOMATISATION ET HSA
---------------------------------
Le resolver de structure `structure_resolver.py` interroge automatiquement
l'API RCSB Search par sequence (MMseqs2), avec seuil d'identite et E-value
configurables, puis telecharge le meilleur PDB experimental et archive son
identifiant, son score, son URL et son contexte de correspondance.
Le provider par defaut est `auto_rcsb`; `external_pdb` reste disponible pour
un administrateur, et `colabfold` reste un fallback optionnel lourd.

HSA-2.0 remplace les anciennes formules arbitraires :
 - Tm nearest-neighbor Biopython sur fenetres locales;
 - entropie de Shannon normalisee;
 - GC-skew;
 - heterogeneite locale GC/Tm;
 - indice borne explicite et documente dans docs/scientific/qsa.md.

HSA ne retourne plus de pseudo-probabilite de mutation (`mutation_prob=null`)
et n'utilise plus le pH faute de modele de tampon, force ionique et pKa. Les
references sont SantaLucia 1998, Vinga 2014 et Biopython MeltingTemp. Cela
ameliore la rigueur et la tracabilite, mais ne constitue pas une decouverte
biologique nouvelle ni une validation clinique.