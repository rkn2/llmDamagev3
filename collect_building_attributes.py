#!/usr/bin/env python3
"""
collect_building_attributes.py

Auto-populates building attributes from free public APIs:
  - lat/lon               : OpenStreetMap Nominatim geocoding
  - ground_elevation_m    : USGS 3DEP Elevation Point Query (LiDAR bare-earth, NAVD88)
  - building_area_m2      : OpenStreetMap Overpass building footprint polygon
  - approx_wall_length_a_m: N-S bounding box extent of footprint (metres)
  - approx_wall_length_b_m: E-W bounding box extent of footprint (metres)
  - osm_* tags            : any building:levels, height, name OSM has

Writes:  building_attributes_auto.json
Prints:  manifest of attributes that still require manual / visual entry
"""

from __future__ import annotations
import json, math, time, urllib.request, urllib.parse
from pathlib import Path

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]

# Attributes that cannot be auto-filled and the suggested method for each
MANUAL_REQUIRED: dict[str, str] = {
    "buidling_height_m":              "Google Earth Pro ruler OR number_stories × 3.5 m",
    "number_stories":                 "Count floors from Street View photo",
    "first_floor_elevation_m":        "ground_elevation_m + step-height above grade (Street View)",
    "wall_length_front":              "Pick street-facing side from approx_wall_length_a/b",
    "wall_length_side":               "Pick perpendicular side from approx_wall_length_a/b",
    "front_elevation_orientation":    "Cardinal direction of street-facing facade (N/S/E/W)",
    "roof_shape_u":                   "Google Earth aerial + Street View",
    "roof_slope_u":                   "Street View side-elevation gable angle",
    "wall_fenesteration_front_per":   "Street View visual estimation",
    "wall_fenesteration_back_per":    "Street View or field inspection",
    "wall_fenesteration_right_per":   "Street View or field inspection",
    "wall_fenesteration_left_per":    "Street View or field inspection",
    "soffit_type_u":                  "Street View eave detail",
    "parapet_height_m":               "Street View + scale from estimated story height",
    "wall_thickness":                 "Sanborn fire insurance maps or historic drawings",
    "masonry_leaves":                 "Damage photos showing wall cross-section or drawings",
    "wall_substrate_u":               "Damage photos showing substrate behind cladding",
    "wall_cladding_u":                "Street View facade material",
    "foundation_type_u":              "Sanborn maps, historic records, or inspection",
    "year_built_u":                   "NRHP individual resource record or Sanborn maps",
}

USER_AGENT = "llmDamagev3-research/1.0 (academic flood damage study)"
OUTPUT = Path(__file__).parent / "building_attributes_auto.json"


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def geocode(address: str) -> tuple[float, float]:
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": address, "format": "json", "limit": 1}
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    if not data:
        raise ValueError(f"No geocoding result for: {address}")
    return float(data[0]["lat"]), float(data[0]["lon"])


def usgs_elevation(lat: float, lon: float) -> float:
    """Ground elevation in metres (NAVD88) from USGS 3DEP bare-earth LiDAR."""
    url = (
        f"https://epqs.nationalmap.gov/v1/json"
        f"?x={lon}&y={lat}&wkid=4326&includeDate=false"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    return float(data["value"])


def osm_footprint(lat: float, lon: float) -> dict | None:
    """
    Query OSM Overpass for the nearest building polygon within 30 m.
    Returns area (m²), bounding-box wall lengths (m), and any OSM tags found.
    Returns None if no building polygon found.
    """
    query = f"""
[out:json][timeout:20];
(
  way["building"](around:30,{lat},{lon});
);
out body;
>;
out skel qt;
"""
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=query.encode(),
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.loads(r.read())

    nodes: dict[int, tuple[float, float]] = {
        e["id"]: (e["lat"], e["lon"])
        for e in data["elements"]
        if e["type"] == "node"
    }
    ways = [
        e for e in data["elements"]
        if e["type"] == "way" and "building" in e.get("tags", {})
    ]
    if not ways:
        return None

    # Pick the way whose centroid is closest to the query point
    def centroid(way: dict) -> tuple[float, float]:
        pts = [nodes[n] for n in way["nodes"] if n in nodes]
        if not pts:
            return lat, lon
        return (sum(p[0] for p in pts) / len(pts),
                sum(p[1] for p in pts) / len(pts))

    best = min(ways, key=lambda w: math.dist(centroid(w), (lat, lon)))
    pts = [nodes[n] for n in best["nodes"] if n in nodes]
    tags = best.get("tags", {})

    if len(pts) < 3:
        return None

    # Convert to local metric coords (equirectangular around centroid)
    c_lat = sum(p[0] for p in pts) / len(pts)
    R = 6_371_000.0
    lat_m = R * math.pi / 180
    lon_m = R * math.pi / 180 * math.cos(math.radians(c_lat))

    origin_lat, origin_lon = pts[0]
    xs = [(p[1] - origin_lon) * lon_m for p in pts]
    ys = [(p[0] - origin_lat) * lat_m for p in pts]

    # Shoelace formula for polygon area
    n = len(xs)
    area = abs(
        sum(xs[i] * ys[(i + 1) % n] - xs[(i + 1) % n] * ys[i] for i in range(n))
    ) / 2

    # Bounding-box extents
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    dim_ns = (max(lats) - min(lats)) * lat_m   # N-S extent
    dim_ew = (max(lons) - min(lons)) * lon_m   # E-W extent

    result: dict = {
        "building_area_m2":        round(area, 1),
        "approx_wall_length_a_m":  round(dim_ns, 1),   # N-S
        "approx_wall_length_b_m":  round(dim_ew, 1),   # E-W
    }
    for osm_key, attr in [
        ("building",        "osm_building_type"),
        ("name",            "osm_name"),
        ("building:levels", "osm_levels"),
        ("height",          "osm_height"),
    ]:
        if osm_key in tags:
            result[attr] = tags[osm_key]

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    results: dict[str, dict] = {}

    for addr in ADDRESSES:
        print(f"\n{addr}")
        rec: dict = {"address": addr}

        # 1. Geocode
        try:
            lat, lon = geocode(addr)
            rec["latitude"] = round(lat, 7)
            rec["longitude"] = round(lon, 7)
            print(f"  lat/lon          : {lat:.6f}, {lon:.6f}")
            time.sleep(1.1)   # Nominatim: max 1 req/s
        except Exception as exc:
            print(f"  geocode FAILED   : {exc}")
            results[addr] = rec
            continue

        # 2. Ground elevation (USGS 3DEP)
        try:
            elev = usgs_elevation(lat, lon)
            rec["ground_elevation_m"] = round(elev, 3)
            print(f"  ground elev      : {elev:.2f} m (NAVD88, bare-earth LiDAR)")
            time.sleep(0.3)
        except Exception as exc:
            print(f"  elevation FAILED : {exc}")

        # 3. OSM building footprint
        try:
            fp = osm_footprint(lat, lon)
            if fp:
                rec.update(fp)
                print(
                    f"  footprint area   : {fp['building_area_m2']} m²  "
                    f"(~{fp['approx_wall_length_a_m']} m N-S  ×  "
                    f"{fp['approx_wall_length_b_m']} m E-W)"
                )
                if fp.get("osm_levels"):
                    print(f"  OSM levels       : {fp['osm_levels']}")
                if fp.get("osm_height"):
                    print(f"  OSM height       : {fp['osm_height']}")
            else:
                print("  footprint        : no OSM building polygon within 30 m")
            time.sleep(1.5)   # Overpass courtesy delay
        except Exception as exc:
            print(f"  footprint FAILED : {exc}")

        results[addr] = rec

    # Write output
    OUTPUT.write_text(json.dumps(results, indent=2))
    print(f"\n\nWrote → {OUTPUT.name}")

    # Manual-entry manifest
    print("\n" + "=" * 60)
    print("ATTRIBUTES STILL REQUIRING MANUAL / VISUAL ENTRY")
    print("=" * 60)
    for attr, method in MANUAL_REQUIRED.items():
        print(f"  {attr:<45s}  {method}")


if __name__ == "__main__":
    main()
