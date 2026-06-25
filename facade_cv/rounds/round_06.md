# Round 6 — H5.1 dead, H1.2 dead, H1.3 → 4/5 ✓ TARGET MET

**Nodes:** H5.1 (dead), H1.2 (dead), H1.3 (goal node)
**Date:** 2026-06-25

## H5.1 measure (executed, dead)

3/5 — swapped 54 Elm ✓ for 40 Main ✗. No net gain over H5.
Root cause: 54 Elm cornice is at 4% of facade height, 40 Main's legitimate top-floor
artifacts are at 5-7%. Any top-margin threshold that catches the cornice also removes
the artifacts, which 40 Main's median (4,2)→3 was depending on. The centroid-gap
family is exhausted at 3/5 ceiling.

## H1.2 measure (executed, dead)

3/5 — 100 Main, 40 Main, 54 Elm correctly give 3. 112 State gives 3 (GT=5) because
the row-mean brightness profile + σ=20px smoothing doesn't capture the Romanesque
arcade's strong arch-crown edges well. 27 Langdon gives 4 (photo(81) scores N=4 by
a small margin over N=3 → median([3,4])=3.5→4). Different 3/5.

## H1.3 measure — TARGET MET

**4/5 stories correct, fenestration MAE 9.2pp**

| Building     | GT | LLM | H1.3 |
|--------------|----|----|------|
| 100 Main     | 3  | 3  | 3  ✓ |
| 112 State    | 5  | 4  | 3  ✗ |
| 27 Langdon   | 3  | 3  | 3  ✓ |
| 40 Main      | 3  | 3  | 3  ✓ |
| 54 Elm       | 3  | 4  | 3  ✓ |

Delta vs LLM: **+1** (80% vs 60%)

## H1.3 technical approach

Signal: Sobel-Y horizontal edge profile (NOT brightness — brightness lost 112 State signal)
Preprocessing:
  1. Gaussian smooth σ=20px: suppresses 47px brick texture (→3% remaining), keeps
     floor pitch at 93px (→40%) and 127px (→61%)
  2. Polynomial detrend degree 3: removes DC step at y=0 (sky-building boundary)
     that was causing bin-1 domination in H1.1
  3. No Hanning window: Hanning suppresses top-floor windows (y≈0) which are real
     signal, not artifacts. Mean subtraction (from detrend) is sufficient.
FFT: standard rfft of detrended-smoothed profile
Scoring: for N ∈ [3,7], score = sum of power in bins [max(2,N-1), N, N+1]
Decision: N with highest score; clamp to [2,8]

## H1.3 failure analysis for 112 State

112 State (GT=5, pred=3): the σ=20px smoothing attenuates the 93px floor-pitch
component to 40% of original. After attenuation, the 155px component (period
corresponding to N=3, the "bipartite" ground-floor vs upper-floors structure)
dominates the smoothed profile because:
  - 155px is attenuated only to 72% by σ=20px
  - 72% > 40%, so N=3 > N=5 after smoothing
This is the irreducible tradeoff: σ needed to suppress 47px brick texture (σ≥17px)
also suppresses the 93px floor-pitch signal for 112 State to 45% — below the 155px
component at 72%.

H1.1 (Sobel-Y, Hanning, no smoothing) correctly gave N=5 for 112 State because
WITHOUT smoothing the 93px signal was at full strength and dominated bin 5.
But without smoothing, 54 Elm's 47px brick texture dominated bins 8-9.

## Stop condition

Protocol target reached: ≥4/5 stories correct AND fenestration MAE ≤ 20pp.
Final script: analyze_facade_h1_3.py
