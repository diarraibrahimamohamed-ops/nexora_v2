# Modèles mathématiques utilisés dans Nexora

Ce document formalise les équations **telles qu’implémentées**, sans leur attribuer plus de sens physique que le code n’en justifie.

---

## 1. Constantes

\[
R \approx 1{,}987\times 10^{-3}\ \mathrm{kcal\,mol^{-1}\,K^{-1}},\quad
T=298\,\mathrm{K},\quad
RT \approx 0{,}592\ \mathrm{kcal\,mol^{-1}}
\]

Code : `RT_KCAL = 0.593`.

---

## 2. Score AutoDock Vina (boîte noire empirique)

Noté \(s(\mathbf{x})\) pour une pose \(\mathbf{x}\). Forme générale (article 2010) : somme pondérée de termes d’interaction atomique (stérique, hydrophobe, H-bond) + pénalité torsionnelle.  
Nexora **n’implémente pas** la scoring function : il lit les `REMARK VINA RESULT`.

---

## 3. ASP — log-sum-exp des scores

Soit \(\{s_i\}_{i=1}^{N}\) les scores Vina retenus (multi-poches × modes).

\[
w_i = \exp\left(-\frac{s_i}{RT}\right),\quad
Z = \sum_{i=1}^{N} w_i,\quad
\Delta G_{\mathrm{eff}} = -RT \ln Z
\]

**Identité utile** :

\[
\Delta G_{\mathrm{eff}}
= s_{\min} - RT \ln \sum_{i=1}^{N} \exp\left(-\frac{s_i-s_{\min}}{RT}\right)
\]

(forme numériquement stable ; le code actuel n’utilise pas explicitement cette réduction).

**Propriété** : \(\Delta G_{\mathrm{eff}} \le s_{\min}\).

### Formule voisine **non** utilisée (moyenne BW)

\[
s_{\mathrm{BW}} = \frac{\sum_i s_i w_i}{\sum_i w_i}
\]

---

## 4. Score « normalisé » Nexora

\[
s_{\mathrm{norm}} = s_{\min} \times \frac{100}{L_{\mathrm{protéine}}}
\]

Sans dérivation thermodynamique ; facteur \(100/L\) arbitraire.

---

## 5. HSA (ex-QSA)

Soient \(n_{GC}\), \(n_{AT}\), \(L=\max(|seq|,1)\), \(T_K=t_{\circ\mathrm{C}}+273{,}15\), \(\sigma=\) `stress_factor`.

\[
\%GC =
\begin{cases}
100\cdot n_{GC}/(n_{GC}+n_{AT}) & n_{GC}+n_{AT}>0\\
50 & \text{sinon}
\end{cases}
\]

\[
\widehat{T_m} = \frac{4 n_{GC}+2 n_{AT}}{L} - 20\sigma
\]

\[
\Delta H = -10\cdot\frac{\%GC}{50},\quad
\Delta S = -0{,}03\cdot\frac{L}{1000},\quad
\Delta G = \Delta H - T_K\,\Delta S
\]

Profil région \(i\in\{0..4\}\) :

\[
\mathrm{stab}_i = \mathrm{clip}_{[0,100]}\Big(50 + 0{,}5(\%GC-50) + 5\sin(1{,}2 i) - 10\cdot\#\mathrm{mut}\Big)
\]

Score global : règles conditionnelles empiriques (pénalités zones fragiles, Tm, `mutation_prob`).

---

## 6. Structure du récepteur

Le chemin léger utilise un PDB externe fourni par l’administrateur. Le chemin
optionnel ColabFold produit une structure prédite à partir de la séquence; le
pLDDT, le PAE et la provenance du modèle doivent être archivés. Aucune
géométrie protéique synthétique n’est utilisée par le pipeline scientifique actif.

Pas d’énergie potentielle globale minimisée (pas de force field protéine type AMBER/CHARMM sur le modèle complet). Placement géométrique par résidu (φ/ψ types hélice/feuillet + bruit aléatoire sur boucles). Les « charges AMBER ff99SB » mentionnées en commentaire sont des **valeurs partielles assignées**, pas une simulation AMBER.

---

## 7. Lien avec un vrai \(\Delta G^\circ\) de liaison (rappel)

En thermodynamique statistique (forme schématique) :

\[
\Delta G^\circ = -kT\ln\left(\frac{Z_{RL}}{Z_R Z_L}\cdot\mathrm{facteur\ état\ standard}\right)
\]

Nexora calcule seulement une fonction de \(Z_{\mathrm{poses}}=\sum e^{-s_i/RT}\) **sans** \(Z_R\), \(Z_L\) ni facteur standard → **pas** un \(\Delta G^\circ\).
