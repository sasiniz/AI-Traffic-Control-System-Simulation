# Results Log

Append-only index of result artefacts. `DECISIONS.md` (the ADR log) records WHY a choice was made; `results/` records WHAT was measured to support it; this file links the two. Never rewrite an existing row, even to correct it - add a new row instead and let the correction be visible in the log.

| Date | Artefact | Script | Commit | Related ADR | One-line finding |
|---|---|---|---|---|---|
| 2026-08-15 | results/MODEL_SELECTION.md | model_selection.py | df4864f | ADR-020 | RandomForest_n100_depth10/diff selected on validation (MAE 4.087); rolling protocol beats baseline overall (4.87 vs 5.22 MAE) but static does not (6.69 vs 5.22 MAE). |
