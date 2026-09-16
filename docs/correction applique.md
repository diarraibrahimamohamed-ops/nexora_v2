# Journal des corrections appliquées — Nexora v2

> Fichier créé le 2026-09-04. Toute correction issue de l’audit Gespark IA (et suivantes) est journalisée ici.

## Décisions d’arbitrage (incohérences du brief)

| Point | Conflit | Décision retenue |
|-------|---------|------------------|
| QSA | « Effacer partout » **vs** « renommer / développer à part » | **Renommage formalisé** en **HSA** (*Heuristic Stability Analysis*). Suppression du terme « quantique ». Persistance A1 réparée. Routes legacy `/qsa` + redirect `qsa.html`. |
| ASP | Présenté comme méthode de docking | **Reformulé** : post-traitement statistique des poses **AutoDock Vina** (*Agrégation Statistique des Poses*), **pas** une méthode de docking autonome. |
| Statuts `done` / `completed` | Trois enums divergents | Unification sur **`completed`** (+ migration SQL). |
| Cache Redis | Mentionné mais `CacheEntry` jamais utilisé | Mentions README corrigées ; modèle mort laissé inerte. |
| C1 ligands | Audit « 31 » vs README « 30 » | **30** lignes `VALUES` dans `init.sql` — README « environ 30 » correct. |

---

## Session 2026-09-04 — Audit Partie 2

### A. Bugs bloquants

- [x] **A1** — Worker HSA persiste `status` / `progress` / `results` / `error` via `SessionLocal`.
- [x] **A2** — Worker docking synchronise `DockingResult` (`docking_result_id`).
- [x] **A3** — Statuts unifiés `pending|running|completed|failed` ; migration `infra/postgres/migrations/20260904_audit_fixes.sql`.

### B. Sécurité

- [x] **IDOR `/analysis/save`** — auth obligatoire ; `body.user_id` ignoré.
- [x] **LoginAttempt** — IP réelle + blocage 5 échecs / 15 min.
- [x] **Flower** — Basic Auth (`FLOWER_BASIC_AUTH`).
- [x] **OAuth2 Swagger** — `POST /api/v1/auth/token` + `tokenUrl` aligné.

### C. Documentation ↔ code

- [x] C1–C7 traités (README, ligands, Redis, architecture, Celery, cookies, `--reload` dev).

### D. Scientifique

- [x] QSA → HSA (`core/hsa/stability_analyzer.py`, `hsa.html`, API `/hsa`).
- [x] ASP formalisé post-Vina ; inventaire dans `algo.md`.

### E. Nettoyage / async

- [x] Modules docking morts retirés ; ancien package QSA retiré.
- [x] Séquences / FASTA > 100 000 nt → Celery `analysis` + poll status.

### Migration DB déjà existante

```bash
psql "$DATABASE_URL" -f infra/postgres/migrations/20260904_audit_fixes.sql
```

Puis redémarrer API + worker. Définir `FLOWER_BASIC_AUTH` dans `.env`.

### Hors code (mémoire)

- Vérifier références biblio `genomic_analysis.py`.
- Chapitre « Limites et validation expérimentale ».

---

## Historique (entrées suivantes)

*(Les corrections ultérieures s’ajoutent ci-dessous avec date.)*
