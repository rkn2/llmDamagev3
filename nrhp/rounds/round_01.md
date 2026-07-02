# Round 1 — P1 strict line regex (baseline)

**Node:** P1 (strict-line-regex)
**Action:** `^\s{0,4}(\d{1,3})\.\s{1,3}<number + street-typed name>` over the raw joined
pypdf text of all 246 pages.

## Measure

- entries found: **380 / 563** (67.5%)
- sequence breaks: 86
- no field extraction attempted

## Failure modes (from inspecting the 86 breaks)

1. Whitespace after the entry number varies wildly — `22.        18 Bailey Avenue` has
   8 spaces; the `\s{1,3}` cap silently drops these.
2. `(formerly ...)` clauses sit between house number and street name
   (`55. 58 (formerly 58 ½) State Street, ...`) and break the address pattern.
3. Headers exist without a street-typed address at all
   (`106. Railroad Roundtable building, Contributing (Demolished)`).
4. Nothing prevents numbered list items inside long descriptions from matching.

## Verdict

Baseline only. A single regex cannot express "this number continues the inventory" —
that is sequence state, not lexical shape. → P2.
