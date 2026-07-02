# Pythonic Roadmap — CV, Document Parsing, and Multi-Hazard Extension

Written 2026-07-02. This is the repo review the roadmap grows out of: what is already
deterministic Python, what is still LLM/manual, what can be converted (with the
hypothesis-loop method that has now worked twice), and how the whole thing extends to
tornado / hurricane / earthquake / wildfire.

Companion docs: `DATA_METHODS.md` (how each attribute was populated),
`LESSONS_LEARNED.md` (address/GIS pitfalls), `facade_cv/PROTOCOL.md` +
`facade_cv/FINAL_REPORT.md` (the CV hypothesis loop), `nrhp/PROTOCOL.md` +
`nrhp/PROCESS_NOTES.md` (the document-parser hypothesis loop).

---

## 1. The operating principle (proven twice in this repo)

Every attribute should be produced by the **cheapest source that can be validated**,
in this preference order:

1. **Authoritative document / GIS lookup** (deterministic parse or REST query — $0,
   reproducible, citable). Examples now in-repo: USGS HWM CSV → flood depth
   (`compute_flood_depth_hwm.py`), VTrans parcels → footprints, NHD → river distance,
   **NRHP inventory → year built / stories / construction / status (`nrhp/`)**.
2. **Classical CV, tuned on a validation set** (deterministic given the image — $0,
   fast, auditable failure modes). Example: `facade_cv/analyze_facade_h1_3.py`
   (stories 4/5 vs LLM's 3/5; fenestration MAE 9.2pp).
3. **LLM vision** — only where geometry/texture math can't reach semantics
   (e.g. "is that a laundromat sign?"), and always subject to a critic pass.
4. **Manual entry** — last resort, and must be guarded against overwrite
   (the `footprint_source`/`aerial_screenshot` marker pattern).

The **hypothesis-loop protocol** is how a source moves up this ladder: explicit metric
with hard ground truth, one structural change per round, tree of hypotheses with
recorded failure modes, separate leakage-safe evaluation script, stop conditions.
Both loops so far converged in ≤6 rounds for $0.30 and $0 respectively.

**Rule of thumb learned from both loops:** segmentation-level metrics only validate
segmentation; you need field-level (or building-level) assertions to catch semantic
bugs. And whenever a document states its own totals (NRHP resource counts, HWM station
counts), reconcile against them — it's a free oracle.

---

## 2. Current state: attribute × source audit

Where each attribute group comes from today (✓ = deterministic Python, ~ = LLM,
✗ = manual/un):

| Attribute group | Today | Pythonic potential | Route |
|---|---|---|---|
| lat/lon, ground elevation | ✓ Nominatim + 3DEP EPQS | done | — |
| footprint area, wall lengths | ✓ OSM/VTrans parcels | done (keep parcel cross-check) | — |
| river distances | ✓ NHD + shapely | done | — |
| flood depth / WSE | ✓ HWM IDW | done; add uncertainty (see §4.1) | lookup |
| `number_stories` | ~ LLM + critic; ✓ CV 4/5 | **high** — now 3-way validated | CV + NRHP |
| `wall_fenesteration_front_per` | ~ LLM; ✓ CV MAE 9.2pp | high | CV |
| `year_built_u` | ✗ un → **✓ NRHP parser** | done for district buildings | document |
| `construction_type_u` | ~ LLM (+1 wrong: 112 State) | **high** — NRHP labels + texture CV | document + CV |
| `wall_cladding_u` | ~ LLM | high (NRHP `clapboarded` etc. + texture CV) | document + CV |
| `roof_shape_u` | ~ LLM / satellite | medium (NRHP text + aerial CV) | document + CV |
| `first_floor_elevation_m` (step height) | ~ LLM vision | **medium-high** (see §3.3) | CV |
| NRHP status / historic name / resource # | ✗ manual → **✓ parser** | done | document |
| damage percentages | ~ LLM before/after | medium (change detection, §3.4) | CV assist |
| archetype | ✗ manual judgment | medium (decision rules over collected attrs) | rules |
| `flooded` predicate | ✓ but wrong shape | fix to 3-state + uncertainty (§4.1) | rules |

---

## 3. CV: what to build next (each = one hypothesis loop, each with real ground truth)

The blocker for tuned-and-validated CV has always been **ground truth supply**. The
NRHP parser just changed that: it yields labels for ~650 resources, not 5 — stories
(93% coverage), construction (83%), cladding, roof shape. Any building in the district
with a photo is now a labeled sample. That converts several "can't tune, n=5" ideas
into real loops:

### 3.1 Construction / cladding classifier (brick vs clapboard vs other)
- **Signal:** facade texture — LBP histograms, Gabor bank energies, or the H1.3-style
  gradient periodicity at brick-course scale (~47 px signature already characterized in
  `facade_cv/FINAL_REPORT.md`). Plain scikit-image + a linear model or k-NN.
- **Ground truth:** NRHP `construction`/`cladding` for every photographed building.
- **Metric:** accuracy on held-out buildings; target ≥ 90% binary brick/wood.
- **Why it matters:** construction drives wall thickness, archetype, and every
  fragility curve downstream; it's also exactly where the LLM made its one high-impact
  error (112 State, brick *veneer* 1994). Note the veneer lesson: texture says "brick",
  the *document* says "frame" — the classifier must defer to year-built rules.

### 3.2 Story counter, round 7 (multi-σ consensus)
- H1.3's known limitation: single σ=20 can't hold both the 47 px brick-texture
  suppression and 112 State's 93 px floor pitch. Frontier idea recorded in
  `facade_cv/FINAL_REPORT.md`: run σ ∈ {8, 12, 20}, score FFT bins per σ, take
  cross-σ consensus (or pick σ per image from the detected dominant texture period).
- **New ground truth:** NRHP stories for the whole district — the loop can finally be
  tuned on dozens of buildings and *held out* properly instead of 5-building leave-none-out.
- **Deliverable metric:** exact-match on ≥ 30 NRHP-labeled facades, not 5.

### 3.3 Entrance step height via in-image scale reference
- Replaces `analyze_entrance_step.py`'s LLM estimate (confidence=medium everywhere).
- **Signal:** detect door rectangle (dark blob with 1:2.2 aspect at grade, or Hough
  vertical pairs); standard US commercial door = 2.03–2.13 m → px/m scale; step height
  = pixels between sidewalk line and threshold × scale. All OpenCV.
- **Ground truth:** none absolute — but *bounded validation*: (a) agreement with the
  LLM estimate ±0.05 m across 5 buildings, (b) FFE − WSE consistency against USGS HWMs
  where a building is known flooded/not-flooded (54 Elm's 0.93 ft margin case is the
  motivating example), (c) field-measurable on any future site visit.
- This is hypothesis node H3 ("reference-geometry-calibration") in
  `facade_cv/HYPOTHESES.json` — still marked `untried`. It stays valuable across
  *all* hazards because FFE/opening heights matter for flood AND surge.

### 3.4 Before/after change detection (the damage-percentage assist)
- Register before/after photo pairs (ORB/SIFT + RANSAC homography — the pairs in
  `ref_photos/` are near-same-viewpoint), then per-region SSIM / color-delta to flag
  changed facade areas; report % of facade area changed below a height line.
- **Not** a damage classifier — a *attention director* that (a) quantifies extent for
  `damage_*_per` fields, (b) tells the LLM pass where to look, (c) generalizes to
  every photo-pair hazard (tornado roof loss, wildfire scorch, quake spalling).
- **Ground truth:** the 5 buildings' assessed damage levels + synthetic validation
  (inject known patches into before-photos, measure recovery precision/recall) —
  synthetic GT is legitimate for a *detector* even when real GT is scarce.
- Failure modes to expect (log in HYPOTHESES): lighting/season deltas, parked cars,
  signage churn. Mask to facade ROI; report per-story bands.

### 3.5 Waterline / high-water stain detector — keep, re-scoped
- `facade_cv/analyze_waterline_cv.py` exists and was superseded by HWM survey data
  *for this event*. Keep it as the fallback for events **without** a USGS HWM campaign
  (most tornado-adjacent flash floods, many hurricanes outside surge zones): horizontal
  edge + color-discontinuity line below known-dry line → depth via §3.3's px/m scale.
- Validation for Montpelier is retroactive: predicted stain height vs HWM-derived
  depth at the 4 confirmed-flooded buildings. That's a real, honest test set.

### 3.6 Roof condition from aerials (feeds hurricane/tornado, §5)
- `analyze_roof_satellite*.py` currently does LLM shape calls. Aerial *change*
  detection (before/after tiles, same registration approach as §3.4) yields
  roof-cover-loss %, the primary wind-damage indicator. Blocked on imagery access for
  the flood case (satellite refresh cadence), ready as a module for wind events where
  NOAA flies post-event aerials (see §5.2 — NOAA ERMA/EMERG imagery is free and
  post-event-tasked).

**Method note for all of the above:** one `PROTOCOL.md` per module, cloned from
`nrhp/PROTOCOL.md`; hard rails (read-only sources, leakage-safe eval, audit files,
$0 or explicit budget); stop conditions written *before* round 1.

---

## 4. Near-term pythonic fixes that need no new data

These close known correctness gaps listed in `CONTEXT.md` §Pipeline Limitations:

### 4.1 Three-state flooded predicate + uncertainty band
`above_ffe > 0` → replace with `{above_ffe | above_grade_only | dry}` computed from
lowest-ingress FFE (`compute_lowest_ffe.py` already exists) and WSE; any margin
|WSE − FFE| < 1.0 ft (the Fair-HWM noise floor) gets `uncertain=True` and a manual
review flag. IDW should propagate an uncertainty estimate: distance-weighted stdev of
contributing HWMs + per-quality sigma (Good ±0.05 ft, Fair ±0.20 ft). Pure code.

### 4.2 Automated surprise detector (the critic, but deterministic)
Rules like: `above_grade > 0 and flooded == dry → flag`; `construction == URM and
year_built > 1978 → flag` (would have caught 112 State automatically);
`two addresses share a footprint → flag` (LESSONS_LEARNED §1). These are cheap
invariants over existing JSONs — a `sanity_checks.py` run at the end of
`run_pipeline.py`, failing loudly into a findings file the way `critic.py` does.

### 4.3 NRHP → spreadsheet integration
Wire `nrhp/nrhp_matches.json` into `generate_detail_pages.py`: `year_built_u`,
`NRHP_ref_number` (resource #), `building_name_listing`, contributing status, and an
`nrhp_construction` cross-check column. (Not done in this session — the detail-page
generator reads live JSONs, so this is a small, contained edit, but it touches the
177-column schema mapping and deserves its own careful pass.)

---

## 5. Multi-hazard extension (tornado / hurricane / earthquake / wildfire)

### 5.1 What stays fixed (the hazard-agnostic core)

Everything in this repo that is *about the building* is hazard-independent and reusable
as-is:

- **Attribute collection:** geocode → parcel → footprint → elevation → NRHP/document
  parse → facade CV (stories, fenestration, construction, openings, step height).
- **Evidence layer:** before/after photo registry (`ref_photos/` conventions),
  detail-page generator, manifest.
- **Validation layer:** hypothesis-loop protocol, critic/sanity rules, three-path
  triangulation (document / LLM / CV).
- **The matcher** (`nrhp/match_buildings.py`) and the address-drift lessons.

What changes per hazard is only: (a) the **intensity measure** and its authoritative
source, (b) the **damage indicators** CV/LLM should look for, (c) the **fragility /
damage scale** vocabulary.

### 5.2 Per-hazard intensity lookup (all free REST/CSV, same shape as the HWM module)

`compute_flood_depth_hwm.py` is the template: *(building coords, event) → intensity at
building + uncertainty + provenance*. Per-hazard backends:

| Hazard | Intensity measure | Authoritative source (free) | Analog of HWM CSV |
|---|---|---|---|
| Flood | WSE / depth above FFE | USGS STN HWM database (`stn.wim.usgs.gov/STNServices` REST — same data as the July 2023 CSV) | done |
| Hurricane surge | WSE | **same USGS STN** service — surge HWMs use the identical schema; parser reuse is total | done by reuse |
| Hurricane wind | 3-s gust / sustained | NOAA HURDAT2 + H*Wind/OSCAT gridded fields; ASOS station peaks (`ncei.noaa.gov`) | IDW over stations, same code shape |
| Tornado | EF rating / path | NWS Damage Assessment Toolkit (DAT) ArcGIS REST (points + swaths per event) | point-in-swath + nearest DI point |
| Earthquake | PGA/PGV/MMI | USGS ShakeMap GeoJSON grid (`earthquake.usgs.gov/ws/...`) | bilinear sample of grid at building |
| Wildfire | burned / not + severity | NIFC/WFIGS perimeter REST; MTBS burn-severity rasters | point-in-polygon + raster sample |

Suggested layout: `hazards/{flood,wind,tornado,quake,fire}/intensity.py` exposing one
function signature; `compute_flood_depth_hwm.py` refactors into `hazards/flood/`.

### 5.3 Per-hazard damage indicators → which CV modules fire

| Hazard | Primary visual indicators | CV module (from §3) | Document/lookup assist |
|---|---|---|---|
| Flood | waterline stain, debris line, FFE vs WSE | §3.5 waterline, §3.3 step height | HWM, business-reopening evidence |
| Hurricane | roof cover loss %, opening breach, surge line | §3.6 roof diff, §3.4 change, §3.5 | HWM surge + wind field |
| Tornado | EF Degrees-of-Damage: roof deck, wall collapse, slab-clean | §3.6 + §3.4 (aerial primary) | NWS DAT DI/DoD points |
| Earthquake | parapet/chimney failure, corner spalling, soft-story lean | §3.4 change + parapet detector (`facade_cv/analyze_parapet_h8.py` exists) | ShakeMap + **URM flag** (already collected!) |
| Wildfire | destroyed/standing binary, scorch, vegetation loss | §3.4 on aerials; binary is easy | CAL FIRE DINS-style parcel status where published |

Two notes with leverage:
- **The attributes this pipeline already collects are precisely the wind/quake
  vulnerability set**: parapet presence/height (falling-hazard #1 in quakes),
  construction URM vs frame, roof shape, fenestration %, stories, year built.
  The flood pipeline accidentally built most of a multi-hazard exposure database.
- **EF-scale is DI/DoD-structured** (Damage Indicator = building type, Degree of
  Damage = ordinal states) — the same shape as `damage_scale.py`'s 0–4 flood scale.
  Generalize `damage_scale.py` to `DamageScale` instances per hazard; the LLM prompt
  builder (`scale_as_prompt_text()`) already doesn't care which scale it renders.

### 5.4 Document parsing across hazards

The NRHP parser generalizes as-is to any NPS 10-900 nomination (see
`nrhp/PROCESS_NOTES.md` §generalization). The same *pattern* (sequence-segmented
inventory + field regex + doc-totals reconciliation + spot-check oracle) applies to:
- **StEER VAST/Fulcrum exports** (CSV/GeoJSON — parse is trivial, validation pattern
  still applies),
- **NWS DAT event summaries** and Public Information Statements (semi-structured text),
- **FEMA Preliminary Damage Assessments**,
- assessor/parcel CAMA tables (year built, stories — cross-checks NRHP outside
  districts).

### 5.5 Suggested build order

1. `sanity_checks.py` + 3-state flooded predicate (§4.1–4.2) — hours, closes known bugs.
2. NRHP → detail pages (§4.3) — small, makes the parser's value visible.
3. Construction classifier loop (§3.1) — first NRHP-supervised CV loop; also
   produces the veneer-vs-URM rule.
4. Step-height CV (§3.3) — de-LLMs FFE, the highest-stakes flood number.
5. `hazards/` refactor + USGS STN client (§5.2) — unlocks hurricane surge for free.
6. Change detection (§3.4) — the workhorse for every other hazard.
7. Story counter round 7 on district-wide GT (§3.2) — when photo collection scales.

---

## 6. Ground rules carried forward (hard-won, do not relearn)

- Diff auto-collected values across neighbors; identical = collision, not coincidence.
- Never trust a synthesized API endpoint; `curl ?f=json` before coding against it.
- Screenshots are for *categorical* reads, parcel geometry is for *measurement*.
- Producer scripts must not overwrite manual corrections (source-marker guards).
- Every surprising binary near a threshold gets an `uncertain` flag, not a coin flip.
- Keep the parse path LLM-free; keep the eval path parser-free (leakage in either
  direction invalidates the loop).
