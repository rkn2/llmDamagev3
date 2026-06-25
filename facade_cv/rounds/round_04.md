# Round 4 — H6: Row-Occupancy Projection

**Node:** H6 (new child of H5)
**Date:** 2026-06-25

## Measure
- **H6:** 2/5 stories (worse than H5 3/5), fenestration MAE 9.2 pp (same as H5 — same blobs)
- Delta vs LLM: -1 (lost ground)

## Per-building results

| Building     | GT | H6 | H5 | Notes |
|--------------|----|----|-----|-------|
| 100 Main     | 3  | 3  | 3  | 3 runs [25, 38, 249] ✓ |
| 112 State    | 5  | 1  | 1  | 1 run [463] — entire facade one run ✗ |
| 27 Langdon   | 3  | 2  | 3  | photo(80)=1 run [332], photo(81)=3 runs [184,133,69]; median=2 ✗ |
| 40 Main      | 3  | 3  | 3  | photos give 4,2 → median=3 ✓ |
| 54 Elm       | 3  | 4  | 4  | 4 runs [27, 57, 80, 79] — cornice 27px passes 6% threshold ✗ |

## Confirmed failure modes

**112 State (facade_h=463px):** 32 blobs distributed floor-to-floor. `occ[y] > 0` for EVERY
row y from 0..462 — spandrel bands have scattered mortar/brick fragments that create nonzero
occupancy even between window rows. One continuous connected run covers the whole facade.
Root cause: `occ > 0` threshold is too permissive; spandrel bands have occ ~20-50px from small
artifacts vs window rows with occ ~200-600px.

**27 Langdon photo 80 (facade_h≈332px):** Same mechanism as 112 State — 15 blobs span the full
facade height with no clean zero-occupancy gap.

**54 Elm (facade_h≈450px):** Cornice blob at top forms a run of height 27px.
MIN_RUN_FRAC = 0.06 → minimum = 27px. The cornice run is EXACTLY at the boundary (27 ≥ 27).
With MIN_RUN_FRAC = 0.08 → minimum = 36px > 27px → cornice rejected.

## Fix identified: H6.1

**Width-gated occupancy:** change `occupied = (occ > 0)` to
`occupied = (occ >= facade_w * 0.10)`.

Rationale:
- Spandrel bands: scattered 1-2 blobs, total occ ~20-50px (3-8% of facade_w ~600px) → FAIL threshold
- Real window rows: 3-6 windows each 40-80px wide, total occ ~200-500px (33-80% of facade_w) → PASS
- Creates clean zero-valued gaps between floors → multiple connected runs

Also increase MIN_RUN_FRAC from 0.06 to 0.08 to reject 54 Elm cornice (27px < 36px).

## Status
H6 dead (2/5 < 3/5 = H5 best). H6.1 queued — implementing width-gated occupancy.
