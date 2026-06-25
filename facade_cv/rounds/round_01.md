# Round 1 — H1: Horizontal edge-band projection

**Node:** H1  
**Date:** 2026-06-25

## Measure
- CV stories: 1/5 (20%) vs LLM baseline 3/5 (60%) — **worse by -2**
- Fenestration MAE vs LLM: 27.5 pp (massively under-estimates: 0–6% vs truth 15–45%)

## Raw results
| Address         | GT | LLM | CV | CV✓ |
|-----------------|----|-----|----|-----|
| 100 Main St     | 3  | 3   | 3  | ✓   |
| 112 State St    | 5  | 4   | 6  | ✗   |
| 27 Langdon St   | 3  | 3   | 4  | ✗   |
| 40 Main St      | 3  | 3   | 6  | ✗   |
| 54 Elm St       | 3  | 4   | 5  | ✗   |

## Evidence of failure modes

### Story over-count
- Min peak spacing was 12% of facade height → too tight for 19th-c. commercial masonry
- Decorative cornices, window sills, awning bands, and building banding all create horizontal
  edge peaks comparable in magnitude to true floor separations
- 40 Main St: clearly 3 stories in photo, but algorithm finds 4 peaks → 5 stories
- 27 Langdon: photo Front(80) correctly finds 2 peaks → 3 stories, but Front(81) finds 4 peaks
  (different angle / closer shot shows more decorative detail)

### Fenestration under-count
- Adaptive threshold (inverted, THRESH_BINARY_INV) finds dark mortar joints + shadows + awnings
- These merge in morphological close → no clean window-shaped contours survive
- All five buildings return 0–6%; LLM baseline is 15–45%
- Root cause: brick mortar joints are dark but not windows; inverted threshold is wrong choice
  for brick facades (glass may actually be lighter than brick due to sky reflection)

## Hypothesis update
- H1 is **not dead** — the approach is sound but parameters and threshold direction are wrong
- H1.1 refinement needed: (1) increase min_dist to ~22% facade height; (2) flip threshold
  direction or use gradient-rectangle approach for fenestration
- H2 (pretrained segmentation) is still untried and may handle decorative facades better
