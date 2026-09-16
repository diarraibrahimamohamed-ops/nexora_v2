# Rapport scientifique — ASP dans Nexora v2

**Titre** : Agrégation Statistique des Poses (ASP) : audit du code, confrontation à la littérature Boltzmann / docking, et limites pour un mémoire.

**Date** : 2026-09-06
**Périmètre** : code réel `backend/app/core/docking/vina_runner.py` (étape 7) + module `backend/app/core/docking/asp/` + pipeline amont (structure, poches, Vina).
**Méthode** : lecture du code ligne à ligne + sources primaires / secondaires vérifiées (pas de reformulation marketing).

---

## 1. Verdict en une phrase

**ASP n’est pas une méthode de docking.** Dans Nexora, le docking est **AutoDock Vina** ; ASP est un **post-traitement numérique** qui combine plusieurs scores Vina via une formule de type **fonction de partition discrète** (\(-RT\ln\sum e^{-s_i/RT}\)). Cette formule est **inspirée** de la mécanique statistique, mais **ne produit pas** une énergie libre de liaison thermodynamique validée tant que les \(s_i\) sont des scores empiriques Vina et que l’état non lié / l’état standard ne sont pas définis.

---

## 2. Ce que le code fait exactement

### 2.1 Agrégation actuelle

Le module ASP utilise une température de 298,15 K et une constante \(R\) codée
en kcal·mol⁻¹·K⁻¹. Avant l’agrégation, les poses sont regroupées par RMSD
à l’intérieur de chaque poche; un représentant par cluster est conservé.

### 2.2 Étape ASP (après clustering des poses Vina multi-poches)

Pour chaque pose avec score Vina `score` (= \(s_i\)) :

1. calcul de poids Boltzmann-like stables numériquement;
2. conservation des représentants de clusters RMSD;
3. calcul du `effective_score` par log-sum-exp;
4. calcul séparé de la moyenne pondérée pour comparaison.

Soit :

\[
S_{\mathrm{eff}}^{\mathrm{(Nexora)}}
= -RT \ln \sum_i \exp\left(-\frac{s_i}{RT}\right)
\quad\text{avec } s_i = \text{score AutoDock Vina}.
\]

### 2.3 Propriété mathématique inévitable

Si \(s_{\min}=\min_i s_i\) (meilleure pose, la plus négative), alors :

\[
\sum_i e^{-s_i/RT} \ge e^{-s_{\min}/RT}
\quad\Rightarrow\quad
\Delta G_{\mathrm{eff}} \le s_{\min}.
\]

Donc **\(\Delta G_{\mathrm{eff}}\) est toujours plus favorable (ou égal) que le meilleur score Vina**. Ce n’est pas une « preuve » d’affinité supérieure : c’est un effet du **log-sum-exp** quand on additionne plusieurs facteurs de Boltzmann.

Exemple numérique (scores Vina typiques −9,2 … −6,8 kcal·mol⁻¹, RT=0,593) :

| Grandeur | Valeur |
|----------|--------|
| Meilleur score Vina | −9,20 |
| \(\Delta G_{\mathrm{eff}}\) (formule Nexora) | ≈ −9,67 |
| Moyenne de Boltzmann des scores \(\sum s_i w_i/\sum w_i\) | ≈ −8,77 |

La **moyenne pondérée de Boltzmann** et le **\(-RT\ln Z\)** ne sont **pas** la même statistique. Nexora calcule la seconde.

### 2.4 Autres sorties du même pipeline

- `docking_score` / `binding_energy` = meilleur score Vina (`best_score`)  
- `effective_score` = score log-sum-exp après clustering RMSD
- `normalized_score = best_score * (100 / longueur_protéine)` → **règle ad hoc**, sans référence bibliographique identifiée dans le code  
- Le champ de méthode décrit Vina + ASP; aucune validation expérimentale n’est fournie dans le dépôt
- Ancien texte UI/doc : « Amarrage par Superposition Probabiliste » → **sur-vente** ; le docking est Vina, la « superposition » = enchaînement multi-poches

---


### 2.1 Correction méthodologique du clustering (Phase 8)
Une révision du clustering a été appliquée après audit : le RMSD utilisé pour regrouper les poses est désormais calculé sur les atomes lourds **dans le repère fixe du récepteur**, sans superposition de Kabsch. Une superposition Kabsch peut supprimer une différence de placement du ligand et fusionner artificiellement des modes de liaison distincts. La nouvelle métrique est donc appropriée pour distinguer des placements dans un même site, avec la limite que la correspondance atomique suit l'ordre des atomes et ne traite pas explicitement toutes les symétries de ligand.

## 3. Ce que la littérature Boltzmann / docking dit réellement

### 3.1 AutoDock Vina (moteur)

- Trott O., Olson A. J., *J. Comput. Chem.* 31:455–461 (2010).  
- Manuel : https://vina.scripps.edu/manual/  
- Le score est une **fonction empirique** (termes stériques gaussien/répulsion, hydrophobe, H-bond, pénalité torsionnelle). Vina **n’inclut pas** électrostatique / solvatation explicites comme AutoDock4 (cf. aussi Eberhardt et al., Vina 1.2.0, *J. Chem. Inf. Model.* 2021).

**Implication** : appliquer une formule d’énergie libre à \(s_i^{\mathrm{Vina}}\) **ne transforme pas** automatiquement ce score en \(\Delta G^\circ\) expérimental.

### 3.2 Cadres où \(-kT\ln\sum e^{-\beta E}\) a un sens thermodynamique

| Source | Contenu pertinent | Lien avec Nexora |
|--------|-------------------|------------------|
| Nguyen, Minh et al. — **Implicit Ligand Theory**, *J. Chem. Phys.* 137 (2012) ; arXiv:1208.4885 | \(\Delta G^\circ\) lié à un **ratio de fonctions de partition** et à des moyennes \(\langle e^{-\beta B}\rangle\) sur configurations ; le docking usuel ≈ approximation « état dominant » | Nexora **n’estime pas** \(Z_{\mathrm{non\-lié}}\), ni l’état standard \(C^\circ\), ni un échantillonnage de récepteur Boltzmann |
| Chen, Roux et al. / exhaustive docking — *J. Phys. Chem. B* (2012) DOI [10.1021/jp212646s](https://doi.org/10.1021/jp212646s) | Construction d’une Z par somme de facteurs de Boltzmann sur poses **exhaustives**, avec volumes translationnels/rotationnels | Nexora somme des poses Vina **déjà filtrées** (`energy_range`, modes limités), **sans** facteurs de volume de phase |
| OSPREY / K\* | \(Q \approx \sum e^{-E/RT}\) pour approximer des constantes de liaison sous hypothèses de force field | Cadre force-field / états définis — **pas** scores Vina empiriques |

### 3.3 « Boltzmann-weighted docking scores » (autre formule)

Beaucoup de travaux de *virtual screening* / ensembles utilisent plutôt :

\[
s_{\mathrm{BW}} = \frac{\sum_i s_i\, e^{-\beta s_i}}{\sum_i e^{-\beta s_i}}
\]

Exemples d’usage / discussion :

- Paulsen J. L., Anderson A. C., *J. Chem. Inf. Model.* 49:281–291? / DOI associé au travail 2009 sur **ensembles de structures** (MD) — pondération pour le **récepteur**, pas inventée pour « inventer un docking »  
- Scores Boltzmann-weighted pour multi-conformères / multi-structures (ex. Sci. Rep. 2024, docking multi-états kinases)

**Nexora n’implémente pas \(s_{\mathrm{BW}}\)** ; il implémente \(-RT\ln Z\).

### 3.4 PopShift / « Boltzmann docking » (encore autre chose)

PopShift (JCTC 2024, PubMed [38291966](https://pubmed.ncbi.nlm.nih.gov/38291966/)) discute la pondération par **populations d’états apo (MSM)** et montre qu’une moyenne naïve de scores de docking sur l’ensemble peut être **catastrophique** si on ignore les shifts de population.  
Nexora n’a **pas** de Markov State Model ni de populations apo.

---

## 4. Pipeline amont : ce qui conditionne (et limite) ASP

ASP ne peut pas être plus crédible que ses entrées.

| Étape | Implémentation Nexora | Limite scientifique |
|-------|----------------------|---------------------|
| Structure 3D | PDB externe par défaut; ColabFold optionnel avec pLDDT | La qualité et la provenance du récepteur doivent être archivées |
| Poches | `detect_binding_pockets_enriched_fixed` (+ motifs éventuels) | Candidats internes, pas forcément sites expérimentaux |
| Docking | AutoDock Vina multi-poches | Outil validé **en soi**, mais sur récepteur artificiel |
| ASP | log-sum-exp des scores | Post-traitement ; pas une nouvelle physique |

Sans structure expérimentale (ou modèle de confiance documenté), **les kcal·mol⁻¹ rapportés ne sont pas interprétables comme affinités réelles** du système biologique d’intérêt.

---

## 5. Ce qu’on peut affirmer honnêtement dans un mémoire

### Autorisé

1. Nexora utilise **AutoDock Vina** comme moteur de docking.  
2. Plusieurs poches sont explorées ; les poses retenues sont agrégées par une formule  
   \(\Delta G_{\mathrm{eff}}=-RT\ln\sum_i e^{-s_i/RT}\).  
3. Cette formule est **mathématiquement** celle d’une énergie libre d’un ensemble discret de micro-états **sous l’hypothèse** que les \(s_i\) sont des énergies cohérentes.  
4. ASP est un **indicateur composite** utile pour résumer un nuage de poses (toujours ≤ meilleur score).  
5. Des cadres publiés (Implicit Ligand Theory, exhaustive docking, moyennes BW) motivent l’idée d’**aller au-delà du seul meilleur score** — inspiration légitime.

### Interdit / à éviter (bluff)

1. Présenter ASP comme **méthode d’amarrage** concurrente de Vina, Glide, GOLD, etc.  
2. Affirmer que \(\Delta G_{\mathrm{eff}}\) est un **ΔG° expérimental** ou « thermodynamiquement exact ».  
3. Utiliser « **scientifically validated** » / « grandeur thermodynamique rigoureuse » sans campagne de validation (PDBBind, CASF, corrélation ITC, etc.).  
4. Assimiler ASP à Implicit Ligand Theory ou à PopShift sans en avoir les hypothèses.  
5. Confondre avec la **moyenne de Boltzmann des scores**.

---

## 6. Proposition de formulation pour le manuscrit

> Après docking multi-poches avec AutoDock Vina (Trott & Olson, 2010), Nexora calcule un score agrégé  
> \(\Delta G_{\mathrm{eff}}=-RT\ln\sum_i\exp(-s_i/RT)\)  
> où \(s_i\) désigne le score empirique Vina de la pose \(i\).  
> Cette étape, appelée **Agrégation Statistique des Poses (ASP)**, est un **post-traitement** inspiré de la forme d’une fonction de partition discrète ; elle **ne constitue pas** une méthode de docking autonome et **ne doit pas** être interprétée comme une énergie libre de liaison expérimentale. Le pipeline utilise un PDB externe par défaut ou un modèle ColabFold optionnel; la provenance et la qualité du récepteur limitent la portée quantitative des scores.

---

## 7. Recommandations techniques (si évolution du code)

1. Renommer dans les sorties JSON : `method: "vina_plus_pose_logsumexp"` ; retirer `ASP_scientific_validated`.  
2. Exposer **aussi** la moyenne BW \(s_{\mathrm{BW}}\) pour comparaison transparente avec la littérature VS.  
3. Documenter RT, température, et le fait que \(\Delta G_{\mathrm{eff}}\le s_{\min}\).  
4. Utiliser un PDB expérimental lorsque disponible; sinon archiver le modèle ColabFold, son pLDDT/PAE et les contrôles de la poche.
5. Validation : jeu public (CASF / PDBBind) — corrélation Spearman score vs pK — **avant** toute publication quantitative.

---

## 8. Références vérifiées (minimales)

1. Trott O., Olson A. J. *J. Comput. Chem.* 31, 455–461 (2010). DOI: 10.1002/jcc.21334  
2. AutoDock Vina Manual — https://vina.scripps.edu/manual/  
3. Eberhardt J. et al. AutoDock Vina 1.2.0. *J. Chem. Inf. Model.* (2021). DOI: 10.1021/acs.jcim.1c00203  
4. Nguyen T. H., Minh D. D. L. et al. Implicit ligand theory. *J. Chem. Phys.* 137 (2012). DOI: 10.1063/1.4751284 ; arXiv:1208.4885  
5. Protein–ligand binding free energies from exhaustive docking. *J. Phys. Chem. B* (2012). DOI: 10.1021/jp212646s  
6. Paulsen J. L., Anderson A. C. Scoring ensembles… *J. Chem. Inf. Model.* (2009) — DOI: 10.1021/ci9003078  
7. PopShift. *J. Chem. Theory Comput.* (2024). PubMed: 38291966  

---

## 9. Traçabilité code

| Élément | Emplacement |
|---------|-------------|
| `RT_KCAL` | `vina_runner.py` ≈ L100–101 |
| Poids / score effectif | `vina_runner.py` ≈ L534–547 + `core/docking/asp/` |
| Boucle Vina multi-poches | `vina_runner.py` ≈ L431–527 |
| Structure du récepteur | `core/protein/colabfold_runner.py` ou PDB externe configuré |

*Fin du rapport.*
