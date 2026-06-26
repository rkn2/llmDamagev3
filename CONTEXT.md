# Context

## Current Task
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

## Next Steps
- Ask Becca: should 54 Elm St's "closed/vacant" status update? Oct 2023 photo shows reopened.
- critic_findings.json: flood depth estimates, occupancy classification (medium severity) still open.
- 27 Langdon and 40 Main back fenestration remain "un" — no usable Street View coverage behind either building (40 Main: "no imagery", 27 Langdon: camera landed on wrong street).
