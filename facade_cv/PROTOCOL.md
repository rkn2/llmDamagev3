# Facade CV — Round Protocol

**Goal:** Replace the Claude vision pass in `analyze_visual_attributes.py` with a scriptable
CV pipeline that extracts `number_stories` and `wall_fenesteration_front_per` from
before-flood front-facade photos — without any LLM call. Deliverable: `analyze_facade_cv.py`
that reads images only and writes `facade_cv_output.json` with per-address predictions.
Budget: ~$2 (only the Opus gate costs money; all CV work is free local compute).

**Scope order:** (1) story count accuracy first — it has hard critic-validated ground truth.
(2) Fenestration % second — softer ground truth, compare to LLM baseline.

Every wakeup runs **exactly one round** following the steps below.

## The metric (recompute every round)

Story count exact-match rate across 5 buildings (higher = better).
Fenestration MAE vs. LLM baseline (lower = better; no better ground truth exists).

**Ground truth (story counts, critic-corrected):**
```
100 Main St:  3  (visual_attrs=3, no critic correction)
112 State St: 5  (visual_attrs=4, critic HIGH: photos show 5 stories + mansard)
27 Langdon St:3  (visual_attrs=3, critic notes stepped massing but 3 primary)
40 Main St:   3  (visual_attrs=3, critic notes 3-story main block)
54 Elm St:    3  (visual_attrs=4, critic HIGH: photos show 3 stories only)
```

**LLM baseline:** 3/5 exact-match on stories (errors: 112 State St got 4 not 5; 54 Elm got 4 not 3)
**Target:** ≥ 4/5 stories correct to call CV competitive with or better than LLM.

Metric recompute command (run from llmDamagev3/):
```
python facade_cv/evaluate_cv.py
```

## Baseline / known failure modes

- LLM Street View pass gets `number_stories` wrong on 2/5 buildings (112 State St, 54 Elm St)
  because Street View angle obscures the full height
- `wall_fenesteration_front_per` has no critic correction → use LLM values as reference baseline
- ref_photos/before/*/Front*.png are the correct inputs; aerial and side photos exist but are
  separate and should not be used for front-fenestration estimates

## Hard safety rails (never violate)

1. **Never mutate source data.** READ-ONLY: `ref_photos/`, `visual_attributes.json`,
   `critic_findings.json`, `address_assessments.json`, `building_attributes_auto.json`,
   `generate_detail_pages.py`. New artifacts ONLY under `facade_cv/`.
2. **No leakage.** `analyze_facade_cv.py` must NOT import or read any pipeline JSON
   (visual_attributes.json, critic_findings.json, address_assessments.json, building_attributes_auto.json).
   It reads ONLY image files. Evaluation is a separate script.
3. **Budget guard.** Check `STATE.budget` before any paid call. Cap: $2.
4. **Evidence before claims.** Re-run `evaluate_cv.py`; show the exact-match count delta.
5. **No silent error sinks.** Log tracebacks to round file. Never fabricate metrics.
6. **Subagent models are explicit.** Gate = `model: "opus"`. Workers = `"sonnet"`.

## Round steps

1. **MEASURE.** Read `STATE.json` + latest `facade_cv_output.json` (if exists). Recompute
   story exact-match and fenestration MAE. Write to round file.
2. **HYPOTHESIZE — Opus gate.** Launch `Agent(model="opus")` with pruned HYPOTHESES.json
   view (frontier + one-line dead/exhausted summaries). Ask for 3 structurally-diverse
   hypotheses, each mapped to a tree node, ≥1 opening a new family. Return ranked rec ≤250w.
3. **SELECT.** Pick from gate rec; sanity-check budget + rails. Record node id + rationale.
4. **EXECUTE.** Write/edit scripts under `facade_cv/`. Tag artifacts with node id.
5. **VERIFY.** Run `evaluate_cv.py`. Show exact-match count before → after.
6. **LOG + PERSIST.** Update `HYPOTHESES.json` node (attempt, posterior, status, frontier).
   Append to `LOG.md`. Update `STATE.json`. Snapshot `rounds/round_NN.md`.
7. **SCHEDULE.** Check stop conditions. Schedule next wakeup or write `FINAL_REPORT.md`.

## Stop conditions

- Story exact-match ≥ 4/5 AND fenestration MAE < 10 percentage points vs. LLM.
- `budget.spent_usd_est >= 2.00`.
- Frontier empty / all families exhausted or dead.
- 3 consecutive rounds with no gain and no new viable hypothesis.
