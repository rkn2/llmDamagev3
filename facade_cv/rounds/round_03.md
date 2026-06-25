# Round 3 — H4 + H5: Autocorrelation + Window Blob Detection

**Node:** H4 (dead), H5 (promising)
**Date:** 2026-06-25

## Measure
- **H4:** 0/5 stories, fenestration N/A — ACF finds wrong period (38px brick texture, not 130px floor pitch)
- **H5 (12% gap, min_row=1):** 3/5 stories (=LLM baseline), fenestration MAE **9.2 pp** (vs 27.5 pp for H1)

## H4 failure evidence
- ACF argmax in [10%, 82%] range picks the HIGHEST peak, which is at lag=38px for 100 Main
  (some repetitive brick/trim texture), not the floor-pitch peak at ~130px
- The floor period IS visible in the ACF as a broader hump around lag~100-150px for 40 Main,
  but the sharper, taller initial peak at ~38px always wins argmax
- Root cause: single argmax is wrong when multiple periodicities exist; need to find the
  FIRST significant peak from the large-lag direction, not the global max

## H5 results
Best config: 12% gap, min_row=1 → 3/5 stories (100 Main ✓, 27 Langdon ✓, 40 Main ✓)

Per-building row sizes (12% gap):
- 100 Main (GT=3):    [3, 11, 4] over 1 photo → pred=3 ✓
- 112 State (GT=5):   [32] (single chain) → pred=1 ✗
- 27 Langdon (GT=3):  ~[1,7,4] + [7,7,8] (2 photos) → median=3 ✓
- 40 Main (GT=3):     ~[2,2,3,4,4,10] → pred=3 ✓
- 54 Elm (GT=3):      [1, 2, 2, 6] → pred=4 ✗

## Exact failure modes
**112 State St:** 32 blobs from 35th percentile threshold. All chain because:
- Romanesque arcade ground floor creates dense cluster of large arched openings
- Spandrel bands between upper floors are < 56px (the threshold at 12% of 464px facade)
- No gap in sorted centroid list exceeds 56px → single row

**54 Elm St:** [1, 2, 2, 6] → 4 rows. The topmost row has 1 blob (Italianate cornice artifact).
- min_row≥2 filter removes it → 3 stories ✓ for 54 Elm
- BUT this same filter breaks 27 Langdon (one photo has a real 1-blob row for a partially-
  obscured floor level) → 27 Langdon drops from 3 to 2

## Grid search result
No single (gap%, min_row) combination exceeds 3/5. Parameter space is exhausted at 3/5.

## Fenestration (best result so far)
H5 percentile=35%, 12% gap: MAE = 9.2 pp vs LLM baseline
- 100 Main: 18.7% (LLM: 25%)
- 112 State: 47.1% (LLM: 45%) ← very close
- 27 Langdon: 18.2% (LLM: 35%) ← biggest miss
- 40 Main: 12.4% (LLM: 30%) ← miss
- 54 Elm: 11.7% (LLM: 15%) ← close

## Status
H5 = promising, 3/5 stories (tied with LLM baseline), 9.2pp fen MAE.
Need gate guidance to reach 4/5: either fix 112 State (complex arcade/mansard building)
or fix the 27 Langdon ↔ 54 Elm singleton trade-off.
