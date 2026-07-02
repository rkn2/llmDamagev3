# NRHP Parser — Round Protocol

**Goal:** Deterministic, pythonic parser for the Montpelier Historic District NRHP 2017
amendment PDF (`montpelierContext/National Register of Historic Places- Downtown Montpelier
update (2017).pdf`, 246 pages, Section 7 inventory, resources #1–563). Deliverable:
`parse_nrhp.py` that reads ONLY the PDF and writes `nrhp_inventory.json` with one record
per numbered resource. No LLM calls anywhere in the parse path.

Modeled on `facade_cv/PROTOCOL.md` (the hypothesis-loop method that got facade CV to 4/5).

## Why this document matters to the pipeline

The NRHP inventory is an *authoritative, independent* source for attributes the pipeline
currently gets from LLM vision or leaves `un`:

| NRHP field | Fills pipeline attribute | Current source |
|---|---|---|
| year built (`c. 1870` etc.) | `year_built_u` | **un** (DATA_METHODS §8 gap) |
| stories (`three stories`) | `number_stories` ground truth | critic-corrected LLM |
| construction (`Brick`, `Wood frame`) | `construction_type_u` | LLM vision |
| cladding (`clapboarded`) | `wall_cladding_u` | LLM vision |
| roof shape/material | `roof_shape_u`, `roof_cover_u` | LLM vision / satellite |
| contributing status, historic name | `building_name_listing`, heritage fields | manual |
| resource number | `NRHP_ref_number` sub-resource id | **un** |

## The metric (recompute every round)

`python3 nrhp/evaluate_parser.py` → composite score, all parts reported separately:

1. **Structural** — segmentation quality:
   - entries found / 563 expected (numbering is continuous 1–563; #517–530 are the
     1989 East State St boundary-increase sub-inventory, same format)
   - duplicate resource numbers (must be 0), non-monotonic sequence breaks (must be 0)
2. **Spot-check fidelity** — 10 hand-verified entries (read from the PDF by a human/agent
   during recon, recorded in `evaluate_parser.py:GROUND_TRUTH`): address, status, year,
   stories, construction must match exactly. Score = fields correct / fields checked.
3. **Coverage** — % of entries with non-null status / year_built / stories / construction.
   (Not all entries have all fields — demolished entries, sites, structures — so coverage
   targets are <100%: status ≥ 0.95, year ≥ 0.90, stories ≥ 0.80, construction ≥ 0.80.)
4. **Plausibility** — 0 violations: years in [1780, 2017], stories in [1, 6.5],
   demolished entries carry `demolished=true`.
5. **Target recovery** — the 5 pipeline buildings resolve to the correct resource numbers
   via `match_buildings.py` (GT: 100 Main→184, 27 Langdon→188, 54 Elm→207,
   112 State→487, 40 Main→72 via former-range 32-50).

**Stop condition:** structural exact (563/563, 0 dupes, 0 breaks) AND spot-check = 100%
AND coverage targets met AND 0 plausibility violations AND 5/5 targets recovered.

## Hard safety rails (never violate)

1. **Never mutate source data.** READ-ONLY: `montpelierContext/`, `ref_photos/`, all
   pipeline JSONs. New artifacts ONLY under `nrhp/`.
2. **No leakage in the parser.** `parse_nrhp.py` reads ONLY the PDF. Ground truth lives
   in `evaluate_parser.py` (hand-verified constants) — evaluation is a separate script.
   `match_buildings.py` may read pipeline addresses (it is a consumer, not the parser).
3. **Evidence before claims.** Re-run `evaluate_parser.py` after every change; record
   score deltas in `rounds/round_NN.md`.
4. **No silent error sinks.** Every rejected header candidate and unparsed field is
   logged to `nrhp_parse_audit.json` — the audit file is the debugging surface.
5. **No LLM calls in the parse path.** This whole module is $0.

## Round steps

1. MEASURE — run `evaluate_parser.py`, record all five metric parts.
2. HYPOTHESIZE — from the audit file, name the top failure mode; propose fix mapped to a
   node in `HYPOTHESES.json`.
3. EXECUTE — implement in `parse_nrhp.py`. Keep stages separable (extract → segment →
   header parse → body parse) so a fix in one stage can't silently regress another.
4. VERIFY — re-run evaluation; show before → after on every metric part.
5. LOG — update `HYPOTHESES.json` + `rounds/round_NN.md` + `PROCESS_NOTES.md`.

## Stop conditions

- All five metric parts at target (see above), OR
- 3 consecutive rounds with no gain, OR
- A failure mode that requires OCR / layout analysis beyond the text layer (out of scope —
  document it and stop).
