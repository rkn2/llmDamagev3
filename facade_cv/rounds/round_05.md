# Round 5 — H5.1 (top-margin cornice filter) + H1.1 decision

**Nodes:** H5.1 executed (dead), H1.1 selected for execution
**Date:** 2026-06-25

## H5.1 measure

**H5.1 result: 3/5 — no improvement over H5 baseline**

| Building     | GT | H5  | H5.1 | Notes |
|--------------|----|----|------|-------|
| 100 Main     | 3  | 3  | 3    | ✓ maintained |
| 112 State    | 5  | 1  | 1    | ✗ unchanged |
| 27 Langdon   | 3  | 3  | 3    | ✓ maintained |
| 40 Main      | 3  | 3  | 2    | ✗ REGRESSION |
| 54 Elm       | 3  | 4  | 3    | ✓ FIXED |

Different 3/5, not better. Swapped 40 Main ✗ for 54 Elm ✓.

## H5.1 failure analysis

**Why 54 Elm fixed:** cornice centroid at 4% of facade height filtered by 9% margin → 3 rows ✓

**Why 40 Main regressed:**
- H5 photo(65): 4 rows (includes artifact blobs at 5%/7% of facade_h); photo(67): 2 rows → median([4,2])=3 ✓
- H5.1 photo(65): removes 5%/7% blobs (same range as 54 Elm cornice) → 3 rows; photo(67): 2 rows → median([3,2])=2.5 → rounds to 2 ✗
- Root cause: can't distinguish 54 Elm's cornice blob (4%) from 40 Main's legitimate top blobs (5-7%). Any margin that catches one catches the other.
- The "accidentally correct" median for H5 was masking an underlying issue.

## H5.1 dead — centroid-gap family ceiling confirmed at 3/5

**Evidence:** Exhaustive diagnostic scan confirms no single (gap%, margin%) combination yields 4/5:
- Cornice at 4%; top-floor windows at 5-7% and 10% → ranges overlap
- 40 Main photo(67) gap of 51px is 2px below the 53px threshold → changes with every ±4% gap tweak
- Parameter space is fully probed: centroid-gap family is exhausted at 3/5

## Next: H1.1 (FFT floor-period detection)

**Hypothesis:** The horizontal edge profile of a facade has a dominant LOW-FREQUENCY component at
the floor pitch period. H4 (ACF argmax) failed because brick texture (38px) creates the TALLEST
ACF peak. But in the FFT power spectrum, the floor pitch (93px for 112 State 5 floors) appears
at LOWER FREQUENCY than brick texture (38px). By restricting FFT search to low-frequency bins
(periods spanning 2.5-8 stories), we find the floor pitch while ignoring brick/mortar texture.

**Why this could reach 4/5:**
- 112 State: 5 floors, floor pitch 93px. FFT of horizontal edge profile should show strong
  component at k≈5 (period 93px). Each Romanesque arcade arch creates a horizontal edge band
  at its crown level, reinforcing the floor-pitch frequency.
- Other buildings (100 Main, 27 Langdon, 40 Main): floor pitch clearly periodic, FFT should
  confirm the dominant period easily.
- 54 Elm: cornice creates a false-period component but is at the top edge of a short facade;
  the 3-story period at 127px should dominate.

**Evidence from round_03:** "The floor period IS visible in the ACF as a broader hump around
lag~100-150px for 40 Main, but the sharper, taller initial peak at ~38px always wins argmax."
This directly supports the low-frequency FFT approach: the floor signal exists, we just need
to look at the right frequency range.
