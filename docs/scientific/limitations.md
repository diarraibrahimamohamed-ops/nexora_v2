# Limites — Nexora v2

Document destiné au chapitre « Limites et validité » d’un mémoire.

---

## 1. Limites structurelles (docking)

| Limite | Impact |
|--------|--------|
| Récepteur externe ou prédit | Les scores Vina/ASP dépendent de la qualité du PDB fourni ou du modèle ColabFold; aucune structure heuristique n’est utilisée par le chemin actif |
| Pas de protonation / pH explicite du récepteur | États tautomères/pKa ignorés |
| Pas d’eau explicite / co-facteurs | Sites métallo / solvants absents sauf si encodés dans la géométrie |
| Rigidité du récepteur | Pas d’induced fit / ensemble MD |

---

## 2. Limites du score Vina

- Fonction **empirique** (pas électrostatique/solvatation type AD4).  
- Meilleure en **pose prediction** moyenne qu’en ranking d’affinité absolue (littérature docking / benchmarks CASF).  
- Unités kcal·mol⁻¹ **trompeuses** si lues comme ΔG ITC.

---

## 3. Limites ASP

- Applique \(-RT\ln\sum e^{-s_i/RT}\) à des scores empiriques.  
- Biais systématique : \(\Delta G_{\mathrm{eff}}\) plus favorable que le best score.  
- Pas d’état non lié, pas d’état standard; les poses sont regroupées par RMSD mais les populations thermodynamiques ne sont pas modélisées.
- Distinct de la moyenne BW et des cadres ILT / PopShift.  
- Libellés historiques (« méthode ASP », `ASP_scientific_validated`) **surestiment** la validité.

Détail : `RAPPORT_ASP.md`.

---

## 4. Limites HSA (ex-QSA)

- Aucune QM.  
- Tm ≠ Wallace fidèle ; ≠ SantaLucia.  
- `ph` est lu mais **peu/pas** utilisé dans les formules centrales de `stability_analyzer.py`.  
- Profil `sin` : artefact.  
- Inadapté à une prédiction de stabilité génomique ou de mutagénèse.

---

## 5. Limites génomiques

- Pas d’alignement optimal ni d’appel de variants standardisé.  
- Profils « résistance antibiotique » : à traiter comme **règles/heuristiques métier** tant qu’une validation clinique/microbio n’est pas publiée dans le dépôt.

---

## 6. Limites logicielles / reproductibilité

- Le mode léger dépend d’un PDB externe; le mode ColabFold dépend de JAX, des MSA, du modèle et des versions de GPU.
- Dépendance à la présence du binaire Vina / RDKit dans l’image Docker.  
- Les structures externes doivent être archivées avec leur source, identifiant, date, version et contrôles de qualité.

---

## Phrase type pour conclusion de chapitre

> Les résultats numériques de docking doivent être lus comme des **scores relatifs** obtenus sur une structure PDB externe ou un modèle ColabFold contrôlé via AutoDock Vina, éventuellement résumés par ASP ; ils ne remplacent pas une mesure d’affinité. Le module HSA est un **proxy pédagogique**, non un outil prédictif validé.
