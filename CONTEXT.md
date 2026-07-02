# Context

## Current Task (2026-07-02)
Built the NRHP document parser (`nrhp/`): deterministic parse of the 246-page Montpelier
Historic District 2017 amendment into 670 validated per-resource records; matched all 5
pipeline buildings (184/487/188/72/207); cross-validated stories/construction/year
against LLM-vision and facade-CV paths. Wrote `PYTHONIC_ROADMAP.md` — the repo-wide plan
for converting remaining LLM/manual attributes to tuned+validated CV/lookups and
extending to tornado/hurricane/earthquake/wildfire.

## Previous Task
Audited/fixed llmDamagev3's flood-damage pipeline (Montpelier, VT) end to end — geocoding,
vision pass, LLM damage assessment, detail-page generation — closing staleness bugs.

## Key Decisions
- `generate_detail_pages.py` now reads `address_assessments.json`/`building_attributes_auto.json`/
  `visual_attributes.json` live, not hand-copied snapshots (root cause of several bugs).
- Added re-run-safety guards (`footprint_source`/`aerial_screenshot` markers) so producer
  scripts can't silently wipe manual corrections.
- Resolved 100 Main St/27 Langdon St OSM footprint collision via VT parcel/E911 data —
  27 Langdon St is a unit inside the 90 Main St building.
- `first_floor_elevation_m` = ground_elevation_m + step height estimated by Claude vision on
  ref_photos/before front photo (`analyze_entrance_step.py`). All 5 buildings populated;
  confidence=medium for all (partial occlusion of entrances).

## Key Decisions (continued)
- `flood_height_building` now derived from USGS HWM survey data (IDW from 3 nearest Good+/Fair
  HWMs in montpelierContext/USGS Highwater Data/table_JulyHWMs.csv) via `compute_flood_depth_hwm.py`.
  LLM estimates replaced for all 5 buildings. Flood depths above FFE: 100 Main=2.4ft, 112 State=2.2ft,
  27 Langdon=1.4ft, 40 Main=3.6ft. 54 Elm: front entrance not overtopped (WSE 526.23 ft < front FFE
  527.16 ft; Δ=−0.93 ft) BUT confirmed flooded via lower/rear access — business website
  (laundryonelm.com) states rebuilding after flooding; water was 0.84 ft above grade at perimeter.
  flood_depth_hwm.json carries confirmed_flooded=true + evidence string; _flood_height_note()
  in generate_detail_pages.py handles this case explicitly. Building is "Capitol City Laundromat /
  Laundry on Elm," confirmed reopened post-flood.

## Next Steps
- Wire `nrhp/nrhp_matches.json` into `generate_detail_pages.py` (`year_built_u`,
  `NRHP_ref_number`, `building_name_listing`, contributing status) — PYTHONIC_ROADMAP §4.3.
- Fix 112 State St `construction_type_u`: NRHP shows 1994 brick-veneer replacement, not
  URM; `wall_thickness=0.46 m` masonry assumption invalid (high-severity finding in
  `nrhp/nrhp_cross_validation.json`).
- Build order for pythonic conversions: PYTHONIC_ROADMAP.md §5.5.
- critic_findings.json: 54 Elm occupancy finding is now stale (use_after_flood corrected to mercantile).
- 27 Langdon and 40 Main back fenestration remain "un" — no usable Street View coverage.

## Pipeline Limitations / Known Issues (for v4 or paper)

### `flooded` boolean uses wrong predicate
`above_ffe > 0` (front-entrance FFE) is insufficient. Water enters through the lowest
accessible opening — rear doors, utility access, drain backflow — not just the front
entrance. Correct logic should use three states:
  - `above_ffe`: water reached first floor (above front FFE)
  - `above_grade_only`: street inundated but front entrance not overtopped (uncertain interior)
  - `dry`: above_grade <= 0
54 Elm was miscalled `dry` when it was `above_grade_only` with confirmed interior flooding.

### FFE from front entrance only
`first_floor_elevation_m` = ground_elev + step_height captures front entrance only.
For sloping terrain or buildings with rear access at lower elevation, this overstates
the effective flood threshold. A complete model needs the minimum FFE across all ingress
points (front, rear, side).

### IDW carries no uncertainty estimate
WSE is reported as a point estimate with no confidence interval. For 54 Elm, the
"not flooded" margin was only 0.93 ft — well within the noise of Fair-quality HWMs
(±0.20 ft) at 75–97 m distance. Results within ~1.0 ft of FFE should be flagged as
"uncertain" rather than resolved binary.

### Surprising results need automated sanity check
If `above_grade > 0` but `flooded = false`, flag for manual review. A block with
0.84 ft of street-level water calling a building "dry" should never pass silently.
External verification (business websites, news archives, social media) can resolve
these cases quickly.
