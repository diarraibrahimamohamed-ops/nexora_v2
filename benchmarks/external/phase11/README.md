# Phase 11 — Real Campaign Evidence Pack

Cette répertoire contient les outils d'exécution/adjudication. Le corpus DUD-E réel n'est volontairement pas copié dans le dépôt.

Ordre recommandé :

```bash
python tools/phase10_prepare_pockets.py <dude-root> --fpocket fpocket --out benchmarks/external/phase10/pockets_frozen.json
python tools/phase11_prepare_locked_manifest.py \
  --template benchmarks/external/phase10/PHASE10_INPUT_MANIFEST_DUDE_DIVERSE_TEMPLATE.json \
  --pockets benchmarks/external/phase10/pockets_frozen.json \
  --dataset-root <dude-root> \
  --out benchmarks/external/phase11/MANIFEST_LOCKED_PHASE11.json

python tools/phase11_execute_isolated.py \
  --manifest benchmarks/external/phase11/MANIFEST_LOCKED_PHASE11.json \
  --dataset-root <dude-root> \
  --run-root benchmarks/external/phase11/runs \
  --build

python tools/phase11_adjudicate.py \
  --csv <run-dir>/vs_results.csv \
  --manifest benchmarks/external/phase11/MANIFEST_LOCKED_PHASE11.json \
  --out <run-dir>/PHASE11_EVIDENCE.json
```

Aucune étape ne doit être modifiée après observation des scores pour rechercher un résultat favorable à ASP.
