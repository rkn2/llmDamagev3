# NRHP Parser — Process Notes

Detailed working notes for the National Register document parser. Read together with
`PROTOCOL.md` (method), `HYPOTHESES.json` + `rounds/` (round history), and the repo-root
`PYTHONIC_ROADMAP.md` (where this module fits in the larger plan).

## What the document is

`montpelierContext/National Register of Historic Places- Downtown Montpelier update
(2017).pdf` — NPS Form 10-900-a continuation sheets, Section 7 (Description), 246 pages.
It is the 2017 amendment to the Montpelier Historic District (listed 1978, amended 1989),
containing a complete re-inventory of the district: **numbered resources #1–563**, plus
lettered secondary buildings (garages/barns/carriage houses) attached to primary numbers
(`5a.`, `21a.`, `297a`/`297b`). The document's own stated total: **653 resources**
(571 contributing / 82 non-contributing). Our parse: 670 records — the extra ~17 are
demolished/replaced inventory entries that the official count excludes, plus
status-less site entries. Parsed status counts (excluding demolished): 566 C / 86 NC /
18 None — within tolerance of the stated totals, and the deltas are explainable
(demolished-but-listed entries, sites/structures with unusual headers).

## Entry format (learned, round by round)

```
N[letter][ (formerly M)]. <address>[ & <address>][ (formerly <old-range> <street>)],
    [<historic name>,] [c. ]<year>[/<year2>...][.] <Contributing|Non-contributing|
    (demolished [in] [c. ]<year>)[ replaced with <address>, <year>. <status>]>
<blank line>
<Construction>, [<cladding>,] <N[ 1/2]> stories, <shape> roof [sheathed in <material>].
<free description... style keywords, alterations, Sanborn map references>
```

Key quirks that cost rounds (details in `rounds/`):

1. **Whitespace after `N.` is 1–8 spaces.** pypdf preserves the print layout's column
   alignment. Never cap trailing whitespace in the number token.
2. **`(formerly ...)` appears in two grammatically different places** — inside the
   *number* (`517 (formerly 1).` = 1989 boundary-increase renumbering) and inside the
   *address* (`54 (formerly 52-54) Elm Street` = street renumbering). Both matter:
   the first for segmentation, the second for address matching (pipeline addresses can
   fall in *former* ranges — 40 Main St resolves only via #72's former range 32-50).
3. **Sequence state beats lexical shape.** Numbered list items inside descriptions and
   cross-references (`# 184 (100 Main Street)`) never advance the running number, so a
   state machine with an evidence gate rejects them without any address grammar at all.
   The cost: one missed *format* becomes a cascade (round 2 lost 47 entries at the 517
   stall). Mitigation: jumps are logged loudly, and `evaluate_parser.py` counts
   563/563 — a stall cannot pass validation silently.
4. **"Replaced with" entries describe two buildings.** The record must represent the
   standing building (the replacement), not the demolished predecessor whose dates come
   first. Body text likewise describes the predecessor first — for these records the
   *last* stories mention wins, not the first.
5. **The first body sentence is a structured field in disguise** —
   `Brick, three stories, flat roof.` — consistent across ~85% of entries. It reads
   like the author worked from a survey form. This is why construction/stories/roof
   coverage is high with plain regex; no NLP needed.
6. **Unicode from the 2017 word processor:** curly quotes, en/em dashes, `½`, and the
   Hungarian-double-low quote `‟` (an OCR-ish artifact in some pages) all appear;
   normalize before any pattern work.

## Validation design (what made the bugs visible)

- The **spot-check oracle** (13 entries, hand-read from the extracted text during recon,
  frozen as constants in `evaluate_parser.py`) is what caught the status-classifier bug
  and the #487 semantics — structural counts alone were already perfect in round 3.
  Lesson: *segmentation metrics validate segmentation; only field-level assertions
  validate fields.*
- The **doc-totals reconciliation** is a free consistency check most documents offer
  (NRHP nominations always state resource counts in Section 5/7). Use it.
- **Target recovery** ties the parser to its downstream consumer: it must produce
  records the address matcher can resolve for the 5 pipeline buildings, including the
  range-match case.

## Cross-validation findings (`cross_validate.py` → `nrhp_cross_validation.json`)

Three independent measurement paths now exist for the same attributes: **A** document
(this parser), **B** LLM vision (Street View), **C** classical CV (facade_cv H1.3).

| building | resource | year built | stories A/B/C | construction A vs pipeline |
|---|---|---|---|---|
| 100 Main St | #184 Theriault Building | c. 1870 | 3/3/3 ✓ | wood frame = wood frame ✓ |
| 112 State St | #487 Chittenden Trust Co | **1994** | 5/5/**3** | — vs URM ✗ (see below) |
| 27 Langdon St | #188 Langdon Block #1 | 1900 | 3/3/3 ✓ | brick→URM = URM ✓ |
| 40 Main St | #72 French Block | 1875 | 3/3/3 ✓ | brick→URM = URM ✓ |
| 54 Elm St | #207 ("Columbian") | 1893 | 3/3/3 ✓ | brick→URM = URM ✓ |

1. **[high] 112 State St is not URM.** NRHP: the present building is *brick-clad new
   construction, 1994, non-contributing due to age* — i.e. brick veneer over a modern
   frame. The pipeline's `construction_type_u=URM` and `wall_thickness=0.46 m`
   load-bearing-masonry assumptions do not apply. This affects archetype/fragility use.
2. **[medium] 112 State stories:** document confirms 5 — independent confirmation of
   facade_cv's known H1.3 limitation (predicted 3), and of the critic's correction of
   the original LLM value.
3. **NRHP independently confirms the 100 Main wood-frame surprise** (two Street View
   passes had flagged it against the block's masonry pattern — LESSONS_LEARNED.md).
4. **`year_built_u` is now filled for all 5 buildings** — previously `un`
   (DATA_METHODS.md §8 listed "NRHP individual resource record; Sanborn maps" as the
   needed source; this is that source).
5. **40 Main St caveat:** it matches the *whole French Block* (#72, 18 window bays,
   multiple storefronts). Block-level attributes (year, construction, stories) apply;
   anything storefront-specific does not. Match confidence recorded as `medium`.

## What generalizes beyond Montpelier

NPS Form 10-900/10-900-a is a **national standard**. Every NRHP district nomination has
a Section 7 inventory; most post-1980 ones follow the same
`N. address, name, date. status` + first-sentence-structural-summary convention.
The stage split (extract → clean → segment → header → body) and the
sequence-state-machine segmentation carry over directly; only `HEADER_EVIDENCE`
street names and the field regexes may need locale tuning. The validation harness
carries over wholesale — swap the 13 GT entries and doc totals.

Coverage numbers to expect (this document): status .97, year .99, stories .93,
construction .83, roof shape .92 — the misses are sites/structures (cemeteries, dams,
parks, bridges) where the fields genuinely don't exist.

## Costs

$0. No LLM calls anywhere in the module (parse, match, evaluate, cross-validate are all
deterministic). Runtime ~15 s, dominated by pypdf extraction.
