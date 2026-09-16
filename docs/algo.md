# Inventaire des algorithmes — Nexora v2

Document de référence technique. Chaque entrée précise : **nom complet**, **rôle dans Nexora**, **fichier**, **origine** (open source / littérature / maison), **ce que le code fait réellement**, **limites**.

---

## Légende de statut

| Statut | Signification |
|--------|----------------|
| **Validé (outil)** | Logiciel / algorithme publié, utilisé tel quel |
| **Inspiré (littérature)** | Formule voisine d’un cadre publié, mais **adaptation maison** |
| **Heuristique maison** | Règle empirique du projet, **non validée** expérimentalement |
| **Étiquette trompeuse** | Nom ou commentaire dans le code qui **surestime** le statut scientifique |

---

## 1. AutoDock Vina — molecular docking (optimisation stochastique + scoring empirique)

| Champ | Contenu |
|-------|---------|
| **Nom complet** | AutoDock Vina |
| **Classe algorithmique** | Optimisation globale stochastique (Monte Carlo itéré + raffinement type BFGS) d’une **fonction de score empirique** |
| **Rôle dans Nexora** | **Seul moteur de docking** (pose + score) |
| **Fichier** | Binaire `/usr/local/bin/vina` appelé dans `backend/app/core/docking/vina_runner.py` |
| **Référence primaire** | Trott O., Olson A. J. *AutoDock Vina: improving the speed and accuracy of docking with a new scoring function, efficient optimization, and multithreading.* **J. Comput. Chem.** 31(2):455–461 (2010). DOI: [10.1002/jcc.21334](https://doi.org/10.1002/jcc.21334) PubMed: [19499576](https://pubmed.ncbi.nlm.nih.gov/19499576/) |
| **Docs / code** | https://vina.scripps.edu/ — https://github.com/ccsb-scripps/AutoDock-Vina |
| **Licence** | Apache 2.0 |
| **Statut** | Validé (outil) |

**Point critique (manuel Vina / article 2010)** : le « score » Vina est une **approximation empirique** exprimée en kcal·mol⁻¹ ; ce n’est **pas** une énergie libre de liaison expérimentale (ITC, SPR, etc.). Les fonctions de score de docking visent des potentiels chimiques approximés, pas un ΔG thermodynamique exact.

**Paramètres Nexora (code)** : multi-poches, `exhaustiveness` adaptative, `num_modes = 20`, `energy_range = 3`.

---

## 2. ASP — Agrégation Statistique des Poses (post-traitement maison)

| Champ | Contenu |
|-------|---------|
| **Nom complet retenu** | **Agrégation Statistique des Poses** (ASP) |
| **Ancien libellé code** | « Amarrage par Superposition Probabiliste » — **à abandonner** : suggère à tort une méthode d’amarrage |
| **Classe algorithmique** | Réduction d’ensemble de scores via **log-sum-exp** / fonction de partition discrète des poses |
| **Formule implémentée** | \(\displaystyle \Delta G_{\mathrm{eff}} = -RT \ln \sum_i \exp(-s_i / RT)\) avec \(s_i\) = score Vina de la pose \(i\), \(RT \approx 0{,}593\) kcal·mol⁻¹ (298 K) |
| **Fichier** | `vina_runner.py` lignes ~534–547 |
| **Statut** | Inspiré (littérature) + **heuristique maison** ; **n’est pas** une méthode de docking |

### Ce que la littérature fait vraiment (à ne pas confondre)

| Approche | Formule / idée | Exemple de source | Identique à Nexora ? |
|----------|----------------|-------------------|----------------------|
| **Moyenne pondérée de Boltzmann des scores** | \(\sum_i s_i e^{-\beta s_i}/\sum_i e^{-\beta s_i}\) | Paulsen & Anderson, *J. Chem. Inf. Model.* 2009 (ensemble de structures récepteur) ; scores BW type Sci. Rep. 2024 | **Non** — Nexora utilise \(-RT\ln Z\), pas la moyenne pondérée |
| **Fonction de partition / free energy from states** | \(F \sim -kT\ln\sum e^{-\beta E_i}\) (ou ratio de Z pour un vrai ΔG°) | Implicit Ligand Theory (Nguyen, Minh et al., *J. Chem. Phys.* 2012) ; exhaustive docking (JPCB 2012) ; cadre K\* / OSPREY | **Formellement voisin** du log-sum-exp, mais sur **énergies** et avec **références non liées / état standard** — absents chez Nexora |
| **Boltzmann docking (populations apo MSM)** | Pondération par populations d’états récepteur | PopShift / « Boltzmann docking » (JCTC 2024 et travaux associés) | **Non** — Nexora n’a pas de MSM ni de populations apo |

**Conclusion honnête** : ASP Nexora est un **post-score** sur des poses Vina multi-poches. Le calcul \(-RT\ln\sum e^{-s_i/RT}\) est mathématiquement celui d’une énergie libre d’un ensemble discret de micro-états **si** les \(s_i\) étaient des énergies thermodynamiques cohérentes — ce que les scores Vina **ne sont pas**. Voir `RAPPORT_ASP.md` et `docs/scientific/asp.md`.

---

## 3. Génération de structure protéique 3D — AlphaFold2 / ColabFold

| Champ | Contenu |
|-------|---------|
| **Nom complet** | Prédiction de structure par AlphaFold2 via ColabFold |
| **Pas** | Structure expérimentale PDB automatiquement, puis ColabFold si absente |
| **Fichier** | `backend/app/core/protein/colabfold_runner.py` |
| **Principe** | Recherche MSA + modèle AlphaFold2; sélection du modèle `rank_001`, contrôle du pLDDT moyen |
| **Statut** | Outil publié, dépendant de la version, des MSA, du modèle et des scores de confiance |

Conséquence : les scores Vina/ASP restent dépendants d’une structure prédite lorsque aucune structure PDB validée n’est disponible; ils ne constituent pas une affinité expérimentale.

---

## 4. Détection de poches / sites

| Nom | Fichier | Origine | Statut |
|-----|---------|---------|--------|
| Pipeline poches enrichi | `pocket_detector.py` → `detect_binding_pockets_enriched_fixed` | Maison (+ éventuelle dépendance fpocket si dispo) | Heuristique / outil selon build |
| Motifs catalytiques type CSA | `structure.py` (`BiologicalSiteDetector`) | Motifs consensus (sérine protéase, kinase, Walker A, etc.) — **pas** une API CSA live systématique | Heuristique maison |

**fpocket** (si installé) : détection géométrique de cavités (Le Guilloux et al., *BMC Bioinformatics* 2009) — à documenter dans le build réel du conteneur.

---

## 5. Préparation ligand

| Nom complet | Rôle | Origine |
|-------------|------|---------|
| **RDKit** (embed + MMFF + charges Gasteiger + `CalcNumRotatableBonds`) | SMILES → 3D | Open source — https://www.rdkit.org/ |
| **Open Babel** (MMFF94 + `NumRotors`) | Fallback | Open source — https://openbabel.org/ |

---

## 6. HSA — Heuristic Stability Analysis (ex-« QSA / Quantum »)

| Champ | Contenu |
|-------|---------|
| **Nom complet** | Heuristic Stability Analysis / Analyse Heuristique de Stabilité |
| **Ancien nom** | QSA, Quantum Stability Analysis — **incorrect** (aucune mécanique quantique : pas de Schrödinger, DFT, semiempirique QM) |
| **Fichier** | `backend/app/core/hsa/stability_analyzer.py` |
| **Statut** | Heuristique maison (proxy pédagogique) |

### Composantes et noms complets

| Composante code | Nom / référence réelle | Fidélité dans Nexora |
|-----------------|------------------------|----------------------|
| Comptage GC / AT | Composition en bases | Exacte sur caractères A/C/G/T/U |
| `h_bonds = 3·GC + 2·AT` | Règle empirique de paires de bases (G–C 3 H-bonds, A–T 2) — manuel biologie moléculaire | Comptage grossier, pas une énergie |
| Tm | `Bio.SeqUtils.MeltingTemp.Tm_NN` avec paramètres explicites | Ne s’applique qu’à des fenêtres locales; pas une thermodynamique complète du génome |
| Entropie / GC-skew | Shannon, \((G-C)/(G+C)\) | Descripteurs établis |
| Profil régional | Fenêtres locales GC/Tm avec score borné | Indice heuristique transparent, non validé |
| Score global 0–100 | Agrégation heuristique | Maison |

Pour la thermodynamique locale des oligonucléotides, HSA utilise le modèle
nearest-neighbor de Biopython basé sur SantaLucia; le score HSA global reste un
indice composite propre au projet et non une énergie libre expérimentale.

---

## 7. Analyse génomique / séquences

| Nom | Description | Statut |
|-----|-------------|--------|
| Comptage nucléotidique / %GC | Statistiques de composition | Standard |
| Traduction code génétique standard | Table codon → AA (code universel) | Standard |
| Comparaison positionnelle (mutations) | Alignement naïf 1:1 vs référence | Heuristique simple |
| Analyse chunkée sync/async | Découpage 20 kb ; Celery si > 100 kb | Ingénierie logicielle |

---

## 8. Non-algorithmes / artefacts à ne pas sur-vendre

| Élément | Verdict |
|---------|---------|
| `modeling_method: 'ASP_scientific_validated'` | **Étiquette trompeuse** — aucune validation expérimentale dans le dépôt |
| Cache Redis de résultats docking (`CacheEntry`) | Modèle présent, **non utilisé** |
| MODELLER dans l'enum historique | Non utilisé dans le chemin scientifique actif |
| « Architecture distribuée / micro-orchestrée » | Monolithe modulaire conteneurisé + Celery |

---

## Schéma de vérité

```
Séquence AA
  → structure 3D ALPHAFOLD2 / COLABFOLD (pLDDT + PAE contrôlés)
  → poches candidates (pocket_detector / motifs)
  → AutoDock VINA  (docking réel, scores empiriques)
  → ASP = log-sum-exp des scores Vina  (post-traitement, pas un docking)
```

```
Séquence ADN + paramètres env.
  → HSA (heuristique pédagogique)
  ≠ mécanique quantique
  ≠ SantaLucia / duplex thermodynamique validé
```

Documents détaillés : `docs/scientific/` et `RAPPORT_ASP.md`.
