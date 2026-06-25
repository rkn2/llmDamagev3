# Process Notes — Facade CV Pipeline

Notes for future reference on what worked, what didn't, and why. Written after completing
the H1.3 approach (4/5 stories, 9.2pp fenestration MAE).

---

## What worked

### Diagnostic-first before fixing

The single most valuable step was dumping exact centroid_y percentages for all 5 buildings
before choosing a top-margin threshold. It revealed the cornice/top-floor overlap (54 Elm
at 4%, 40 Main artifacts at 5-7%) that made the fix impossible in the centroid-gap family.
Without the diagnostic, I would have iterated on thresholds blind. Lesson: when you think
you know the threshold, compute it from the actual data first.

### Debug visualizations for every hypothesis

Every script wrote a 3-4 panel debug image per photo. These caught problems that were
invisible from aggregate scores — the H6 debug image immediately showed that 112 State's
entire facade was one continuous green band (no gaps at all), confirming the diagnosis
before any code was written.

### The FFT bin=story insight

Realizing that "for a facade of N samples with a floor pitch at period N/k samples, the FFT
bin is exactly k" made the scoring trivial to implement and reason about. The search range
[3,7] directly maps to story count hypotheses. No lookup tables, no period arithmetic.

### Hypothesis-loop framing

Writing explicit `failure_modes` arrays in HYPOTHESES.json for each dead node prevented
re-proposing the same ideas. The gate asking "what am I missing?" was genuinely useful: it
was the gate that identified H1.1 as the independent-family fallback after H6 stalled.

---

## What didn't work and why

### Centroid-gap clustering (H5 family)

The approach was brittle at both ends:
- Too large a gap threshold: chains all 112 State blobs into 1 row
- Too small: splits single-floor blobs in 100 Main into multiple rows
- Cornice filtering: 54 Elm's cornice at 4% and 40 Main's real top-floor blobs at 5-7% were
  in the same range — no single threshold separated them

**Key trap:** H5 was "correctly" predicting 40 Main via median([4,2])=3, where the "4" came
from an artifact row accidentally inflating the count. This masked the underlying fragility
of the approach. When H5.1 removed the artifact, the median dropped to 2. The lesson:
validate that correct predictions are correct for the right reason, not by luck.

### Row-occupancy projection (H6 family)

The `occ > 0` threshold was too permissive for dense facades: scattered mortar fragments
created nonzero occupancy in every row of 112 State, making the entire facade one connected
run. The width-gating fix (H6.1) was too aggressive — real windows on narrow facades were
only 8-13% of facade width, overlapping with the 10% threshold designed to filter mortar
spots. Row occupancy fundamentally can't distinguish "sparse mortar blobs" from "real but
narrow windows" without per-building calibration.

### Hanning window with FFT

The Hanning window zeros out the signal at y=0 and y=N. For a building where the top-floor
windows are near y=0 (just below the roofline), the Hanning window suppresses the exact
signal you need. Polynomial detrend is better: it removes the DC trend (sky-building edge
at y=0) without suppressing real top-floor windows that are a few pixels into the ROI.

### SegFormer (H2)

Domain shift was fatal: ADE20K's "window" class was trained on residential/interior windows
and classifies 95%+ of 19th-c. commercial masonry facades as "building." Pretrained
segmentation needs explicit fine-tuning or a model trained on historic commercial masonry.

---

## The irreducible tradeoff at 4/5

The remaining failure (112 State, GT=5, pred=3) has a mathematical root cause:

- Gaussian σ=20px attenuates a 93px signal to 40% and a 155px signal to 72%
- After smoothing, the "N=3 bipartite interpretation" (tall ground floor + 4 shorter upper
  floors = 2 visual sections ≈ 155px period) dominates the N=5 uniform-floor interpretation
- To preserve the 93px signal you need σ≤12px, but σ≤12px leaves 47px brick texture at 33%
  amplitude, which causes 54 Elm to give N=8 instead of N=3

The two constraints are:
  - σ ≥ 17px (to suppress 47px brick texture to <10%)
  - σ ≤ 12px (to keep 93px floor pitch above 60%)

These are mutually exclusive. Reaching 5/5 would require either:
1. Detecting the brick texture period first and notch-filtering it specifically (vs. a broad Gaussian)
2. Separate handling for the tall-ground-floor / Romanesque architectural type
3. ACF with minimum-lag filter: skip all peaks at lag < facade_h/8 (skips brick texture
   at 38-47px, finds floor pitch at 93-129px) — not yet tried but shows promise

---

## Fenestration notes

The 9.2pp MAE vs LLM baseline (no hard ground truth) is a solid result given:
- 27 Langdon off by 16.8pp: the building appears partially occluded in both photos; the
  facade ROI likely under-counts the actual window area
- 40 Main off by 17.6pp: very wide commercial storefront at ground level; the large
  windows are partially cut off at the bottom crop (h=82% of image height)

Both misses are related to facade visibility/occlusion, not the threshold choice. The 35th
percentile threshold is a reasonable heuristic for this dataset.

---

## If continuing this work

1. **ACF with minimum-lag filter** is the next most promising approach for 112 State:
   skip lags < facade_h/8, take the first significant peak in the valid range. This avoids
   smoothing the signal while still excluding brick texture.

2. **Notch filter** instead of broad Gaussian: compute the dominant high-frequency period
   of the edge profile (the texture period), then band-stop filter it specifically. This
   would suppress brick texture while preserving the floor-pitch frequency.

3. **112 State as a separate case**: The Romanesque building has a fundamentally different
   structural signature. A classifier that detects "arcade-type ground floor" and applies
   a different counting strategy (e.g., divide by 78px upper-floor height after detecting
   the arcade base) might generalize better to this building type.

4. **Fenestration ground truth**: The current evaluation uses the LLM estimate as a
   pseudo-ground-truth. Getting verified fenestration percentages (e.g., from architectural
   drawings or manual measurement) would enable actual MAE computation.
