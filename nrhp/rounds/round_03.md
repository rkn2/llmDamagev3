# Round 3 — P2.1 number-suffix grammar

**Node:** P2.1 (number-suffix-grammar)
**Action:** candidate grammar `(\d{1,3})([a-z])?\s*(?:\(formerly\s+#?\s*(\d+)\))?\.` ;
state machine additionally accepts same-number suffix succession (297a → 297b);
`former_number` captured for the renumbered 1989 entries.

## Measure (before → after)

- entries found: 515 → **670 records covering 563/563 distinct numbers**
- missing: 48 → **0**; duplicates 0; rejected candidates 33 → **0**; jumps 1 → **0**
- 108 lettered records — inspected: all are secondary buildings (garage, barn,
  carriage house, guest house) sharing the primary's address. This reconciles with the
  document's own stated total of 653 resources (563 primary + ~90 counted secondary).

## New failure modes (spot-checking fields against hand-read entries)

1. **Status classifier bug:** `"on" in "Con"[:3].lower()` → every entry classified
   non-contributing. (Substring test on the wrong operand — caught only because the
   spot check asserts exact values.)
2. **#487 replaced-with semantics:** header
   `112 State Street, Chittenden Trust Co., c. 1960 (demolished in 1994) replaced with
   112 State Street, Chittenden Trust Co, 1994. Non-contributing` parsed as
   year=1960 / stories=1.0 — the *demolished predecessor*, not the standing 1994
   five-story building. Body's first paragraph also describes the predecessor.
3. Historic name duplicated (`Chittenden Trust Co, Chittenden Trust Co`) when the
   name appears on both sides of `replaced with`.

## Verdict

Segmentation solved; field semantics next. → P3.
