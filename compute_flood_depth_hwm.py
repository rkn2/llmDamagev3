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

# compute_lowest_ffe.py's effective (min front/rear) FFE — guarded, since pages
# should still render off the front-only FFE if it hasn't been run yet.
_LOWEST_FFE_PATH = REPO / "lowest_ffe.json"
_LOWEST_FFE: dict = (
    json.loads(_LOWEST_FFE_PATH.read_text()) if _LOWEST_FFE_PATH.exists() else {}
)

# Downtown Montpelier bounding box (covers all 5 buildings + surrounding HWMs)
LAT_MIN, LAT_MAX =  44.255,  44.270
LON_MIN, LON_MAX = -72.585, -72.565

# Exclude these quality tiers (too imprecise for engineering use)
EXCLUDE_QUALITY = {"Poor", "Unknown/Historical"}

# IDW: use 3 nearest qualifying HWMs
N_NEAREST = 3

# Results within this margin of FFE are within HWM survey noise (Fair quality is
# +/- 0.20 ft, but siting/IDW error compounds it) — flag rather than resolve binary.
UNCERTAIN_MARGIN_FT = 1.0

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


# Survey sigma by quality tier, from the "+/- X ft" in the quality strings
# (Excellent +/-0.05, Good +/-0.10, Fair +/-0.20). Fallback if unparseable: Fair.
_QUALITY_SIGMA_RE = None  # set lazily to avoid an import-order footgun with re


def quality_sigma_ft(quality: str) -> float:
    global _QUALITY_SIGMA_RE
    import re
    if _QUALITY_SIGMA_RE is None:
        _QUALITY_SIGMA_RE = re.compile(r"([\d.]+)\s*ft")
    m = _QUALITY_SIGMA_RE.search(quality)
    return float(m.group(1)) if m else 0.20


def idw_wse(blat: float, blon: float, hwms: list[dict]) -> tuple[float, float, list[dict]]:
    ranked = sorted(hwms, key=lambda h: dist_m(blat, blon, h["lat"], h["lon"]))
    top = ranked[:N_NEAREST]
    dists = [dist_m(blat, blon, h["lat"], h["lon"]) for h in top]
    weights = [1.0 / d for d in dists]
    wsum = sum(weights)
    wse = sum(w * h["elev_ft"] for w, h in zip(weights, top)) / wsum

    # Uncertainty = survey error + spatial disagreement, combined in quadrature:
    #  - measurement term: IDW-propagated survey sigma, sqrt(Σ(w_i σ_i)²)/Σw
    #    (independent per-mark errors through the weighted mean)
    #  - spread term: weighted stdev of the contributing marks around the IDW value
    #    (captures the real water-surface gradient / siting error the survey sigma
    #    can't see — this is usually the dominant term)
    meas = math.sqrt(sum((w * quality_sigma_ft(h["quality"])) ** 2
                         for w, h in zip(weights, top))) / wsum
    spread = math.sqrt(sum(w * (h["elev_ft"] - wse) ** 2
                           for w, h in zip(weights, top)) / wsum)
    sigma = math.sqrt(meas**2 + spread**2)

    sources = [
        {
            "label":    h["label"],
            "elev_ft":  h["elev_ft"],
            "dist_m":   round(d, 1),
            "quality":  h["quality"],
        }
        for h, d in zip(top, dists)
    ]
    return wse, sigma, sources


def main(force: bool = False) -> None:
    if OUT.exists() and not force:
        existing = json.loads(OUT.read_text())
        if all(existing.get(a, {}).get("wse_ft") for a in ADDRESSES):
            print(f"flood_depth_hwm.json already complete — pass --force to recompute")
            return

    # Manual research overrides (e.g. 54 Elm's confirmed-flooded-via-rear-access finding)
    # live only in the output file — carry them forward across recomputes instead of
    # silently dropping them.
    manual_overrides: dict[str, dict] = {}
    if OUT.exists():
        existing = json.loads(OUT.read_text())
        for addr, rec in existing.items():
            keep = {k: v for k, v in rec.items() if k in ("confirmed_flooded", "flood_evidence")}
            if keep:
                manual_overrides[addr] = keep

    hwms = load_hwms()
    print(f"Loaded {len(hwms)} qualifying HWMs in downtown bounding box\n")

    attrs = json.loads(ATTRS.read_text())
    results: dict[str, dict] = {}
    review_flags: list[str] = []

    print(f"{'Building':<20} {'WSE(ft)':>8} {'▲grade':>8} {'▲FFE':>8} {'LLM▲FFE':>9} {'status':>17}")
    print("─" * 80)

    for addr in ADDRESSES:
        rec = attrs[addr]
        blat = rec["latitude"]
        blon = rec["longitude"]
        gnd_ft  = rec["ground_elevation_m"]      * M_TO_FT
        lowest  = _LOWEST_FFE.get(addr)
        if lowest:
            ffe_m     = lowest["effective_ffe_m"]
            ffe_source = f"effective (lowest-ingress: {lowest['lowest_ingress']}) — see compute_lowest_ffe.py"
        else:
            ffe_m     = rec["first_floor_elevation_m"]
            ffe_source = "front entrance only — run compute_lowest_ffe.py for rear-ingress check"
        ffe_ft  = ffe_m * M_TO_FT

        wse, wse_sigma, sources = idw_wse(blat, blon, hwms)
        above_grade = wse - gnd_ft
        above_ffe   = wse - ffe_ft

        # Three-state model: water enters through the lowest accessible opening, not
        # just the front entrance, so `above_ffe <= 0` alone does NOT mean "dry" — it
        # only means the front entrance wasn't overtopped. above_grade_only means the
        # street/perimeter was inundated with the interior status unresolved by HWM
        # data alone (see 54 Elm: confirmed flooded via rear access despite front FFE
        # not being overtopped).
        if above_ffe > 0.0:
            status = "above_ffe"
        elif above_grade > 0.0:
            status = "above_grade_only"
        else:
            status = "dry"
        flooded   = status != "dry"
        # Uncertain when the FFE margin is inside either the fixed noise floor or
        # the 2-sigma band of the interpolated WSE (whichever is wider) — a margin
        # of 0.9 ft means nothing when the WSE itself is only known to +/-0.5 ft.
        uncertain = abs(above_ffe) < max(UNCERTAIN_MARGIN_FT, 2 * wse_sigma)

        if status == "above_grade_only":
            review_flags.append(
                f"{addr.split(',')[0]}: above_grade={above_grade:.2f}ft but front FFE not "
                f"overtopped (above_ffe={above_ffe:.2f}ft) — interior flooding unresolved by "
                f"HWM alone, verify via external sources (news, business records, site visit)"
            )

        results[addr] = {
            "wse_ft":          round(wse, 3),
            "wse_sigma_ft":    round(wse_sigma, 3),
            "above_ffe_z":     round(above_ffe / wse_sigma, 2) if wse_sigma > 0 else None,
            "ground_elev_ft":  round(gnd_ft, 3),
            "ffe_ft":          round(ffe_ft, 3),
            "above_grade_ft":  round(above_grade, 3),
            "above_ffe_ft":    round(above_ffe, 3),
            "ffe_source":      ffe_source,
            "status":          status,
            "flooded":         flooded,
            "uncertain":       uncertain,
            "hwm_sources":     sources,
            **manual_overrides.get(addr, {}),
        }

        name = addr.split(",")[0]
        llm  = LLM_ABOVE_FFE[addr]
        tag  = status + (" (uncertain)" if uncertain else "")
        print(f"{name:<20} {wse:>8.2f} {above_grade:>8.2f} {above_ffe:>8.2f} {llm:>9.1f} {tag:>17}")

    print()
    if review_flags:
        print("NEEDS MANUAL REVIEW:")
        for msg in review_flags:
            print(f"  - {msg}")
        print()

    OUT.write_text(json.dumps(results, indent=2))
    print(f"Wrote → {OUT.name}")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
