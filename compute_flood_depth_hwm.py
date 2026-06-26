#!/usr/bin/env python3
"""
compute_flood_depth_hwm.py

Derives flood Water Surface Elevation (WSE) and depth at each building from the
USGS High Water Mark survey conducted after the July 2023 Montpelier flood.

Method:
  - Read table_JulyHWMs.csv (already in repo).
  - Keep Excellent / Good / Fair quality HWMs within the downtown bounding box.
  - For each building, IDW-interpolate WSE from the 3 nearest qualifying HWMs.
  - Compute flood depth above grade and above first-floor elevation (FFE).

Output: flood_depth_hwm.json  (loaded by generate_detail_pages.py at render time)

Pass --force to overwrite existing output.
"""

from __future__ import annotations
import csv, json, math, sys
from pathlib import Path

REPO     = Path(__file__).parent
CSV_PATH = REPO / "montpelierContext" / "USGS Highwater Data" / "table_JulyHWMs.csv"
ATTRS    = REPO / "building_attributes_auto.json"
OUT      = REPO / "flood_depth_hwm.json"

# Downtown Montpelier bounding box (covers all 5 buildings + surrounding HWMs)
LAT_MIN, LAT_MAX =  44.255,  44.270
LON_MIN, LON_MAX = -72.585, -72.565

# Exclude these quality tiers (too imprecise for engineering use)
EXCLUDE_QUALITY = {"Poor", "Unknown/Historical"}

# IDW: use 3 nearest qualifying HWMs
N_NEAREST = 3

M_TO_FT = 3.28084
DEG_LAT_M = 111_000.0
DEG_LON_M = 111_000.0 * math.cos(math.radians(44.26))

# Current LLM estimates for comparison printout only
LLM_ABOVE_FFE = {
    "100 Main St, Montpelier, VT 05602":  3.5,
    "112 State St, Montpelier, VT 05602": 4.0,
    "27 Langdon St, Montpelier, VT 05602":3.5,
    "40 Main St, Montpelier, VT 05602":   2.5,
    "54 Elm St, Montpelier, VT 05602":    3.0,
}

ADDRESSES = list(LLM_ABOVE_FFE.keys())


def load_hwms() -> list[dict]:
    hwms = []
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            lat_s = row.get("HWM Latitude", "").strip()
            lon_s = row.get("HWM Longitude", "").strip()
            elev_s = row.get("Surveyed HWM Elevation (ft)", "").strip()
            if not (lat_s and lon_s and elev_s):
                continue
            lat, lon, elev = float(lat_s), float(lon_s), float(elev_s)
            if not (LAT_MIN < lat < LAT_MAX and LON_MIN < lon < LON_MAX):
                continue
            quality = row.get("HWM Quality", "").strip()
            if any(x in quality for x in EXCLUDE_QUALITY):
                continue
            hwms.append({
                "label":   row.get("HWM Label", "").strip(),
                "lat":     lat,
                "lon":     lon,
                "elev_ft": elev,
                "quality": quality,
            })
    return hwms


def dist_m(lat1, lon1, lat2, lon2) -> float:
    dlat = (lat2 - lat1) * DEG_LAT_M
    dlon = (lon2 - lon1) * DEG_LON_M
    return math.sqrt(dlat**2 + dlon**2)


def idw_wse(blat: float, blon: float, hwms: list[dict]) -> tuple[float, list[dict]]:
    ranked = sorted(hwms, key=lambda h: dist_m(blat, blon, h["lat"], h["lon"]))
    top = ranked[:N_NEAREST]
    dists = [dist_m(blat, blon, h["lat"], h["lon"]) for h in top]
    weights = [1.0 / d for d in dists]
    wse = sum(w * h["elev_ft"] for w, h in zip(weights, top)) / sum(weights)
    sources = [
        {
            "label":    h["label"],
            "elev_ft":  h["elev_ft"],
            "dist_m":   round(d, 1),
            "quality":  h["quality"],
        }
        for h, d in zip(top, dists)
    ]
    return wse, sources


def main(force: bool = False) -> None:
    if OUT.exists() and not force:
        existing = json.loads(OUT.read_text())
        if all(existing.get(a, {}).get("wse_ft") for a in ADDRESSES):
            print(f"flood_depth_hwm.json already complete — pass --force to recompute")
            return

    hwms = load_hwms()
    print(f"Loaded {len(hwms)} qualifying HWMs in downtown bounding box\n")

    attrs = json.loads(ATTRS.read_text())
    results: dict[str, dict] = {}

    print(f"{'Building':<20} {'WSE(ft)':>8} {'▲grade':>8} {'▲FFE':>8} {'LLM▲FFE':>9} {'flooded':>8}")
    print("─" * 70)

    for addr in ADDRESSES:
        rec = attrs[addr]
        blat = rec["latitude"]
        blon = rec["longitude"]
        gnd_ft  = rec["ground_elevation_m"]      * M_TO_FT
        ffe_ft  = rec["first_floor_elevation_m"] * M_TO_FT

        wse, sources = idw_wse(blat, blon, hwms)
        above_grade = wse - gnd_ft
        above_ffe   = wse - ffe_ft
        flooded     = above_ffe > 0.0

        results[addr] = {
            "wse_ft":          round(wse, 3),
            "ground_elev_ft":  round(gnd_ft, 3),
            "ffe_ft":          round(ffe_ft, 3),
            "above_grade_ft":  round(above_grade, 3),
            "above_ffe_ft":    round(above_ffe, 3),
            "flooded":         flooded,
            "hwm_sources":     sources,
        }

        name = addr.split(",")[0]
        llm  = LLM_ABOVE_FFE[addr]
        flag = "YES" if flooded else "no"
        print(f"{name:<20} {wse:>8.2f} {above_grade:>8.2f} {above_ffe:>8.2f} {llm:>9.1f} {flag:>8}")

    print()
    OUT.write_text(json.dumps(results, indent=2))
    print(f"Wrote → {OUT.name}")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
