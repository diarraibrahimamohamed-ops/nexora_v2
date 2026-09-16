# Validation — état des lieux et protocole proposé

## État actuel dans le dépôt

| Module | Validation expérimentale / benchmark public | Commentaire |
|--------|-----------------------------------------------|-------------|
| AutoDock Vina (outil) | Validé **dans la littérature** (Trott 2010 ; suites CASF / comparatifs) | Validation de **l’outil**, pas du pipeline Nexora entier |
| Structure PDB externe / ColabFold | **Absente dans ce dépôt** | La structure doit être documentée et contrôlée séparément |
| ASP (log-sum-exp) | **Absente** | Pas de corrélation vs pK / ΔG exp. |
| HSA / ex-QSA | **Absente** | Aucun jeu de Tm / stabilité mesurée |
| Détection mutations / résistance | **Non documentée** ici | À fournir si revendiquée |

Le champ `ASP_scientific_validated` dans les sorties JSON **ne constitue pas** une validation.

---

## Ce qui compterait comme validation (proposition mémoire)

### A. Docking + ASP (minimal crédible)

1. Jeu public : **PDBBind** et/ou **CASF** (poses natives + affinités).  
2. Pour chaque complexe :  
   - docking sur **récepteur cristallographique** ou modèle externe correctement documenté ;
   - reporter : best Vina, \(\Delta G_{\mathrm{eff}}\) ASP, \(s_{\mathrm{BW}}\).  
3. Métriques :  
   - pose : RMSD ≤ 2 Å au ligand natif (taux de succès) ;  
   - ranking : Spearman / Pearson score vs pK ou ΔG exp. ; RMSE.  
4. Ablations : best-only vs ASP vs BW — pour montrer **si** ASP apporte quelque chose.

### B. Structure du récepteur

- Comparer le modèle externe à une structure expérimentale lorsque cela est possible.
- Rapporter pLDDT/PAE pour ColabFold et les métriques de qualité du PDB utilisé.
- Ne jamais mélanger dans une même analyse les structures externes sans stratifier le protocole.

### C. HSA

- Soit retiré des claims prédictifs,  
- soit confronté à Tm oligos (règle Wallace / SantaLucia) sur jeux courts — en admettant l’échec hors domaine.

---

## Critères de publication

Sans A (même partiel), toute affirmation quantitative d’affinité via Nexora doit rester **qualifiée** (« démonstration logicielle / relative »).  
Avec A positif, on peut discuter ASP comme **post-score exploratoire** — toujours sans le vendre comme nouvelle méthode de docking.

---

## Traçabilité

Protocoles détaillés ASP : `RAPPORT_ASP.md` §7.  
Hypothèses : `assumptions.md`. Limites : `limitations.md`.
