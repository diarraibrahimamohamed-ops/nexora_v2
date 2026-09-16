# Hypothèses (assumptions) — Nexora v2

Liste explicite des hypothèses **implicites dans le code**, pour le chapitre méthodologique du mémoire.

---

## Docking / Vina / ASP

1. Le score Vina \(s_i\) peut être injecté dans une formule de type Boltzmann **comme s’il s’agissait** d’une énergie \(E_i\) (hypothèse forte, **non garantie** par Trott & Olson 2010).  
2. Les poses retenues sont traitées comme un ensemble discret de micro-états **équipondérés a priori** (seul le facteur \(e^{-s_i/RT}\) pondère).  
3. Les poses issues de **poches différentes** sont sommées dans la **même** \(Z\) (hypothèse que ces états sont comparables / mutuellement exclusifs au même titre).  
4. Température fixe **298 K** pour ASP, indépendamment du paramètre température HSA.  
5. Le récepteur est **rigide** pendant Vina (hypothèse standard docking).  
6. Le PDB externe est suffisamment représentatif du récepteur pour l’analyse exploratoire; cette hypothèse doit être documentée. En mode ColabFold, le modèle est une prédiction et ses scores pLDDT/PAE doivent être contrôlés.
7. Filtrage des scores Vina hors fenêtre ≈ [−15 ; 0,5] kcal·mol⁻¹ (choix logiciel).

---

## Structure protéique

1. Le PDB fourni par l’administrateur est lisible et correctement préparé pour Vina.
2. Un modèle ColabFold est utilisé seulement avec sa version, ses MSA et ses scores de confiance archivés.
3. Les motifs regex de sites biologiques restent heuristiques et ne remplacent pas une annotation structurale validée.

---

## HSA (ex-QSA)

1. La composition GC d’une séquence (même longue) informe un « risque mutationnel » via formules linéaires.  
2. Un facteur `stress_factor` scalaire capture température/pH/radiations/chimie (extrêmement réducteur).  
3. La forme \(\Delta G=\Delta H-T\Delta S\) avec coefficients arbitraires reste interprétable (en réalité : **cosmétique**).  
4. Le profil en 5 régions + sinus est représentatif de « zones fragiles ».

---

## Données & système

1. Ligands pré-enregistrés (≈ 31) sont des exemples pharmacologiques, pas un screening exhaustif.  
2. L’utilisateur authentifié est la source de vérité pour la propriété des analyses (après correctifs auth).  
3. Au-delà d’un seuil de longueur, le navigateur délègue au serveur (hypothèse UX/perf).

---

## Hypothèses que le projet **ne** revendique **pas** (après clarification)

- Équivalence ASP ↔ Implicit Ligand Theory.  
- Équivalence HSA ↔ calcul quantique.  
- Équivalence entre un modèle ColabFold/PDB et une structure expérimentale locale.

6. Le clustering RMSD suppose une correspondance d'atomes stable entre poses d'un même ligand. Les atomes lourds sont comparés dans le repère du récepteur, sans superposition Kabsch.
