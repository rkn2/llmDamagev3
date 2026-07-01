#!/usr/bin/env python3
"""
compute_lowest_ffe.py

Addresses the documented limitation that first_floor_elevation_m (FFE) is derived
from the front entrance only (analyze_entrance_step.py), which overstates the
effective flood threshold for buildings whose rear/lower access sits at a lower
grade (see 54 Elm: confirmed flooded via rear/lower access despite an un-overtopped
front entrance).

Method:
  - For each building, offset from its centroid opposite the front-facing direction
    by half the building depth (wall_length_side) plus a clearance margin, landing
    on open ground behind the building (alley/rear yard) rather than inside the
    footprint.
  - Query USGS 3DEP bare-earth elevation at that rear point.
  - Rear ingress elevation = rear ground elevation (conservative: assumes grade-level
    rear access, i.e. zero rear steps, absent photo evidence of a raised rear
    threshold — no systematic rear-entrance step survey exists across all 5
    buildings, unlike the front). This is a deliberate lower bound, not a measured
    rear FFE: a building with actual raised rear steps would have a higher true
    rear threshold than this script reports.
  - effective_ffe_m = min(front_ffe_m, rear_ground_elevation_m)

Caveat carried over from the earlier LiDAR-for-FFE test (analyze_entrance_step.py):
1m DEM resolution cannot resolve step-level detail. This script only uses the DEM
for a coarser comparison — relative grade between front and rear of the same
building — which is a different, safer use of the same data.

Output: lowest_ffe.json (loaded by compute_flood_depth_hwm.py in place of the
front-only first_floor_elevation_m).

Pass --force to overwrite existing output.
"""

from __future__ import annotations
import json, math, sys, urllib.parse, urllib.request
from pathlib import Path

REPO       = Path(__file__).parent
BA_PATH    = REPO / "building_attributes_auto.json"
UA_PATH    = REPO / "urban_attrs.json"
VA_PATH    = REPO / "visual_attributes.json"
OUT        = REPO / "lowest_ffe.json"

USER_AGENT = "llmDamagev3-research/1.0 (academic flood damage study)"

# 27 Langdon St shares its OSM polygon with 100 Main St (see compute_wall_lengths.py);
# use the same manual override for its building depth.
FOOTPRINT_OVERRIDE = {
    "27 Langdon St, Montpelier, VT 05602": {"ns_m": 20.0, "ew_m": 18.0},
}

# Distance past the rear wall to clear the footprint before sampling the DEM —
# same rationale as the LiDAR-for-FFE test: points inside/at the footprint edge
# interpolate through the building rather than reading open ground.
CLEARANCE_M = 3.0

DEG_LAT_M = 111_000.0

REAR_OF = {"n": "s", "s": "n", "e": "w", "w": "e"}

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]


def side_depth_m(addr: str, rec: dict, orientation: str) -> float:
    """Building depth perpendicular to the front wall (front-to-rear distance)."""
    if addr in FOOTPRINT_OVERRIDE:
        a_ns = FOOTPRINT_OVERRIDE[addr]["ns_m"]
        b_ew = FOOTPRINT_OVERRIDE[addr]["ew_m"]
    else:
        a_ns = rec["approx_wall_length_a_m"]
        b_ew = rec["approx_wall_length_b_m"]
    # Mirrors compute_wall_lengths.py's front_and_side(): front faces e/w -> front
    # wall runs N-S (=a) -> side/depth = b; front faces n/s -> side/depth = a.
    return b_ew if orientation in ("e", "w") else a_ns


def offset_point(lat: float, lon: float, direction: str, dist_m: float) -> tuple[float, float]:
    deg_lon_m = 111_000.0 * math.cos(math.radians(lat))
    if direction == "n":
        return lat + dist_m / DEG_LAT_M, lon
    if direction == "s":
        return lat - dist_m / DEG_LAT_M, lon
    if direction == "e":
        return lat, lon + dist_m / deg_lon_m
    if direction == "w":
        return lat, lon - dist_m / deg_lon_m
    raise ValueError(f"Unknown direction: {direction!r}")


def usgs_elevation(lat: float, lon: float) -> float:
    """Same endpoint/parsing as collect_building_attributes.py's usgs_elevation() —
    returns metres NAVD88 directly, no unit conversion (verified against the
    existing ground_elevation_m values, which are this API's raw output)."""
    url = (
        f"https://epqs.nationalmap.gov/v1/json"
        f"?x={lon}&y={lat}&wkid=4326&includeDate=false"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    return float(data["value"])


def main(force: bool = False) -> None:
    if OUT.exists() and not force:
        existing = json.loads(OUT.read_text())
        if all(a in existing for a in ADDRESSES):
            print("lowest_ffe.json already complete — pass --force to recompute")
            return

    ba = json.loads(BA_PATH.read_text())
    ua = json.loads(UA_PATH.read_text())
    va = json.loads(VA_PATH.read_text())

    results: dict[str, dict] = {}
    print(f"{'Building':<15} {'front_ffe':>10} {'rear_grd':>10} {'eff_ffe':>10} {'lowest':>8} {'Δ(ft)':>7}")
    print("─" * 68)

    for addr in ADDRESSES:
        rec  = ba[addr]
        ori  = ua.get(addr, {}).get("front_elevation_orientation")
        front_ffe_m = rec.get("first_floor_elevation_m")
        label = addr.split(",")[0]

        if ori is None or front_ffe_m is None:
            print(f"  SKIP {label}: missing orientation or first_floor_elevation_m")
            continue

        depth = side_depth_m(addr, rec, ori)
        rear_dir = REAR_OF[ori]
        offset_dist = depth / 2 + CLEARANCE_M
        rlat, rlon = offset_point(rec["latitude"], rec["longitude"], rear_dir, offset_dist)

        rear_ground_m = round(usgs_elevation(rlat, rlon), 3)
        effective_ffe_m = min(front_ffe_m, rear_ground_m)
        lowest = "rear" if rear_ground_m < front_ffe_m else "front"
        delta_ft = (front_ffe_m - rear_ground_m) * 3.28084

        results[addr] = {
            "front_ffe_m":         front_ffe_m,
            "rear_ground_elevation_m": rear_ground_m,
            "rear_offset_point":   {"lat": round(rlat, 7), "lon": round(rlon, 7),
                                     "direction": rear_dir, "dist_m": round(offset_dist, 1)},
            "effective_ffe_m":     round(effective_ffe_m, 3),
            "lowest_ingress":      lowest,
            "delta_front_minus_rear_ft": round(delta_ft, 2),
            "note": (
                "rear ingress elevation assumes grade-level access (0 rear steps) — "
                "no systematic rear-entrance step survey exists; this is a conservative "
                "lower bound, not a measured rear FFE"
            ),
        }

        print(f"{label:<15} {front_ffe_m:>10.3f} {rear_ground_m:>10.3f} "
              f"{effective_ffe_m:>10.3f} {lowest:>8} {delta_ft:>7.2f}")

    OUT.write_text(json.dumps(results, indent=2))
    print(f"\nWrote → {OUT.name}")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
