# Round 4 — P3 replaced-with semantics + status fix

**Node:** P3 (replaced-with-semantics)
**Action:** status = `startswith("non")`; records whose header contains
`(demolished ...) replaced with` are flagged `replaced=True` and parsed from the text
*after* "replaced with" (year, address); stories for replaced records take the *last*
match in the body (the present building is described after the predecessor); name
segments deduped preserving order.

## Measure (before → after) — `evaluate_parser.py`

| check group | before | after |
|---|---|---|
| structural (563/563, 0 dupes/missing) | PASS | PASS |
| spot-check (13 hand-verified entries, all fields) | 15 field failures | **PASS (0)** |
| coverage (status .97 / year .99 / stories .93 / construction .83) | PASS | PASS |
| plausibility (years 1780–2017, stories 1–6.5) | PASS | PASS |
| target recovery (5 pipeline buildings → correct resource #) | — | **PASS 5/5** |
| doc-totals reconciliation (566/571 C, 86/82 NC, 18 status-None) | FAIL | **PASS** |

Matcher results: 100 Main→#184, 112 State→#487, 27 Langdon→#188, 54 Elm→#207 all
`exact_current/high`; 40 Main→#72 via `range_former/medium` (French Block, former
range 32-50 Main, parity match).

## Stop condition reached

All six validation groups pass. Parser is done; frontier closed.
Cross-validation moved to `cross_validate.py` (see PROCESS_NOTES.md for findings).
