# Docking moléculaire dans Nexora — documentation scientifique

## Définition

Le **molecular docking** cherche des poses ligand–récepteur et leur attribue un score. Dans Nexora, le **seul moteur de docking** est **AutoDock Vina**.

---

## AutoDock Vina — identité complète

| Élément | Détail |
|---------|--------|
| **Nom** | AutoDock Vina |
| **Auteurs / lab** | Oleg Trott, Arthur J. Olson — Molecular Graphics Lab / CCSB, Scripps |
| **Référence** | Trott & Olson, *Journal of Computational Chemistry* 31(2):455–461 (2010). DOI: [10.1002/jcc.21334](https://doi.org/10.1002/jcc.21334) |
| **Suite** | Vina 1.2.0 : Eberhardt et al., *J. Chem. Inf. Model.* (2021). DOI: [10.1021/acs.jcim.1c00203](https://doi.org/10.1021/acs.jcim.1c00203) |
| **Site** | https://vina.scripps.edu/ |
| **Licence** | Apache 2.0 |

### Nature algorithmique

1. **Fonction de score empirique** (united-atom) : contributions stériques (gaussiennes + répulsion), hydrophobes, liaisons H, pénalité de torsions actives.  
2. **Recherche** : optimisation stochastique globale + raffinement local (BFGS dans la lignée Vina).  
3. Sortie : poses classées + score en **kcal·mol⁻¹** (échelle empirique).

### Limite essentielle (d’après la littérature Vina elle-même)

Le score **approxime** un potentiel / une affinité ; ce n’est **pas** un \(\Delta G^\circ\) mesuré. Les scoring functions de docking sont inexactes ; Vina peut être meilleur *en moyenne* qu’AutoDock4 sur certains benchmarks, sans garantie sur une cible donnée (manuel Vina).

---

## Pipeline Nexora (ordre réel)

1. Nettoyage séquence AA  
2. **Structure 3D automatique** (`structure_resolver.py`) — recherche RCSB par séquence; ColabFold en fallback optionnel
3. Détection de poches candidates  
4. Ligand : RDKit (MMFF, Gasteiger, TORSDOF) ou Open Babel  
5. PDBQT récepteur/ligand  
6. **Vina** sur chaque poche (`exhaustiveness` adaptative, `num_modes=20`)  
7. Post-traitement **ASP** (voir `asp.md` / `RAPPORT_ASP.md`)

Fichier central : `backend/app/core/docking/vina_runner.py`.

---

## Préparation chimique (noms complets)

| Étape | Outil | Rôle |
|-------|-------|------|
| Embed 3D + minimisation | **RDKit** + **MMFF** (Merck Molecular Force Field) | Conformation ligand |
| Charges partielles | **Gasteiger–Marsili** (via RDKit) | Typage PDBQT |
| Fallback | **Open Babel** + **MMFF94** | Idem |
| Degrés de liberté torsionnels | `CalcNumRotatableBonds` / `NumRotors` → TORSDOF | Entrée Vina |

---

## Ce qui n’est pas du docking dans Nexora

- **ASP** : agrégation de scores **après** Vina (pas un moteur).  
- Structure protéique interne : génération géométrique.  
- Modules `docking_from_db*`, `create_enhanced_docking`, etc. : non utilisés par le worker Celery principal (à traiter comme code mort / legacy).

---

## Formulation mémoire

> Le docking est réalisé avec AutoDock Vina (Trott & Olson, 2010). Les scores sont empiriques. La structure du récepteur est prédite par AlphaFold2 via ColabFold lorsqu’aucune structure PDB validée n’est disponible ; le pLDDT, le PAE et la géométrie de la poche doivent être contrôlés avant toute interprétation quantitative.
