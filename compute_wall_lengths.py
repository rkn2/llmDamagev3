#!/usr/bin/env python3
"""
compute_wall_lengths.py

Derives wall_length_front and wall_length_side from OSM bounding-box dimensions
(building_attributes_auto.json) + front elevation orientation (urban_attrs.json).

Mapping rule:
  a = N-S bounding-box extent  (per collect_building_attributes.py docstring)
  b = E-W bounding-box extent

  Front faces E or W  →  front wall runs N-S  →  front = a,  side = b
  Front faces N or S  →  front wall runs E-W  →  front = b,  side = a

Known data-quality flag:
  27 Langdon St shares an OSM polygon with 100 Main St. The OSM dimensions
  (43.0 × 38.5 m) are the combined block polygon, not a single building.
  The computed wall lengths are marked unreliable and excluded from
  visual_attributes.json; manual measurement or Google Earth disambiguation
  is needed.

LEAKAGE POLICY: reads only building_attributes_auto.json and urban_attrs.json
(both auto-collected from public APIs, no ground truth).
"""

from __future__ import annotations
import json
from pathlib import Path

REPO = Path(__file__).parent

BA_PATH  = REPO / "building_attributes_auto.json"
UA_PATH  = REPO / "urban_attrs.json"
VA_PATH  = REPO / "visual_attributes.json"

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]

# Buildings whose OSM polygon is known to be wrong (shared / block-level polygon).
# Wall lengths will be computed but flagged; visual_attributes.json NOT updated.
UNRELIABLE = {
    "27 Langdon St, Montpelier, VT 05602":
        "shares combined OSM block polygon with 100 Main St; dims are 43×38.5m "
        "(actual building ≈20×18m per FOOTPRINT_OVERRIDE in analyze_roof_cv_usgs.py)",
}


def front_and_side(a_ns: float, b_ew: float,
                   orientation: str) -> tuple[float, float]:
    """Return (front_wall_m, side_wall_m) given OSM bounding-box dims and orientation."""
    ori = orientation.strip().lower()
    if ori in ("e", "w"):
        return a_ns, b_ew   # front wall runs N-S
    elif ori in ("n", "s"):
        return b_ew, a_ns   # front wall runs E-W
    else:
        raise ValueError(f"Unknown orientation: {orientation!r}")


def main() -> None:
    ba = json.loads(BA_PATH.read_text())
    ua = json.loads(UA_PATH.read_text())
    va = json.loads(VA_PATH.read_text())

    results = {}
    for addr in ADDRESSES:
        rec  = ba.get(addr, {})
        a_ns = rec.get("approx_wall_length_a_m")
        b_ew = rec.get("approx_wall_length_b_m")
        ori  = ua.get(addr, {}).get("front_elevation_orientation")
        label = addr.split(",")[0]

        if a_ns is None or b_ew is None or ori is None:
            print(f"  SKIP {label}: missing a/b/orientation")
            continue

        front_m, side_m = front_and_side(a_ns, b_ew, ori)
        unreliable = addr in UNRELIABLE

        results[addr] = {
            "wall_length_front": round(front_m, 1),
            "wall_length_side":  round(side_m, 1),
            "orientation_used":  ori,
            "unreliable":        unreliable,
            "note":              UNRELIABLE.get(addr, ""),
        }

        flag = " ⚠ UNRELIABLE" if unreliable else ""
        print(f"{label:<15}  orient={ori}  "
              f"front={front_m:.1f}m  side={side_m:.1f}m{flag}")
        if unreliable:
            print(f"  → {UNRELIABLE[addr]}")

    # Write reliable values only
    changed = 0
    for addr, r in results.items():
        if r["unreliable"]:
            continue
        entry = va.setdefault(addr, {})
        entry["wall_length_front"] = r["wall_length_front"]
        entry["wall_length_side"]  = r["wall_length_side"]
        changed += 1

    VA_PATH.write_text(json.dumps(va, indent=2))
    print(f"\nUpdated wall_length_front/side for {changed} buildings in visual_attributes.json")
    print("27 Langdon St EXCLUDED — OSM polygon unreliable (see UNRELIABLE dict)")


if __name__ == "__main__":
    main()
