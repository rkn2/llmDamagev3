#!/usr/bin/env python3
"""
compute_urban_attrs.py

Derives two building attributes from OSM geometry, replacing LLM estimates:
  - front_elevation_orientation : N/S/E/W (direction building's front facade faces)
    Method: find the nearest segment of the building's address street in OSM,
    compute direction from building centroid to that segment.
  - building_urban_setting       : row_middle / row_end / isolated
    Method: count how many distinct sides of the building have an adjacent building
    polygon within ADJACENCY_THRESHOLD_M (3 m) in local metric coordinates.

LEAKAGE POLICY: reads only building_attributes_auto.json (lat/lon only — no
assessment outputs or ground truth) and OpenStreetMap public API.

Output: urban_attrs.json
"""

from __future__ import annotations
import json, math, time, urllib.request, urllib.parse
from pathlib import Path

REPO       = Path(__file__).parent
BA_PATH    = REPO / "building_attributes_auto.json"
OUTPUT     = REPO / "urban_attrs.json"

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]

USER_AGENT            = "llmDamagev3-research/1.0 (academic flood damage study)"
ADJACENCY_THRESHOLD_M = 3.0   # buildings sharing a wall are within 3 m
ROAD_SEARCH_M         = 200   # search radius for address street
BLDG_SEARCH_M         = 80    # search radius for adjacent buildings

R = 6_371_000.0


# ── Local metric coordinate helpers ──────────────────────────────────────────

def _scale(origin_lat: float):
    lat_m = R * math.pi / 180
    lon_m = R * math.pi / 180 * math.cos(math.radians(origin_lat))
    return lat_m, lon_m

def to_xy(lat, lon, origin_lat, origin_lon):
    lat_m, lon_m = _scale(origin_lat)
    return (lon - origin_lon) * lon_m, (lat - origin_lat) * lat_m

def closest_point_on_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return ax, ay
    t = max(0.0, min(1.0, ((px - ax)*dx + (py - ay)*dy) / (dx*dx + dy*dy)))
    return ax + t*dx, ay + t*dy

def angle_to_cardinal(dx, dy):
    """Unit bearing (0=north clockwise) → N/S/E/W quadrant."""
    bearing = (90 - math.degrees(math.atan2(dy, dx))) % 360
    if bearing < 45 or bearing >= 315:
        return "n"
    elif bearing < 135:
        return "e"
    elif bearing < 225:
        return "s"
    else:
        return "w"


# ── Overpass helper ──────────────────────────────────────────────────────────

def overpass(query: str) -> dict:
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=query.encode(),
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def _nodes(data):
    return {e["id"]: (e["lat"], e["lon"]) for e in data["elements"] if e["type"] == "node"}

def _ways(data):
    return [e for e in data["elements"] if e["type"] == "way"]


# ── Orientation ───────────────────────────────────────────────────────────────

def parse_street_keyword(address: str) -> str:
    """'27 Langdon St, Montpelier ...' → 'Langdon'"""
    tokens = address.split(",")[0].strip().split()
    return tokens[1] if len(tokens) >= 2 else tokens[0]

def compute_orientation(lat: float, lon: float, street_kw: str) -> tuple[str | None, str]:
    """
    Hypothesis: the front facade faces the address street.
    Evidence: find all OSM highway ways whose name contains street_kw within
    ROAD_SEARCH_M; find the closest segment; the direction from building centroid
    to that closest point is the front orientation.
    """
    q = f"""
[out:json][timeout:20];
(way["highway"]["name"~"{street_kw}",i](around:{ROAD_SEARCH_M},{lat},{lon}););
out body;>;out skel qt;
"""
    data = overpass(q)
    nodes = _nodes(data)
    ways  = _ways(data)

    if not ways:
        return None, f"no road ways matching '{street_kw}' within {ROAD_SEARCH_M}m"

    best_dist = float("inf")
    best_cx = best_cy = 0.0

    for way in ways:
        pts = [nodes[n] for n in way["nodes"] if n in nodes]
        for i in range(len(pts) - 1):
            ax, ay = to_xy(pts[i][0],   pts[i][1],   lat, lon)
            bx, by = to_xy(pts[i+1][0], pts[i+1][1], lat, lon)
            cx, cy = closest_point_on_segment(0, 0, ax, ay, bx, by)
            d = math.hypot(cx, cy)
            if d < best_dist:
                best_dist, best_cx, best_cy = d, cx, cy

    cardinal = angle_to_cardinal(best_cx, best_cy)
    note = (f"closest '{street_kw}' segment at ({best_cx:+.1f}m E, {best_cy:+.1f}m N), "
            f"dist={best_dist:.1f}m → facing {cardinal.upper()}")
    return cardinal, note


# ── Urban setting ─────────────────────────────────────────────────────────────

def compute_urban_setting(lat: float, lon: float) -> tuple[str, str]:
    """
    Hypothesis: adjacent buildings share a party wall and appear within
    ADJACENCY_THRESHOLD_M in local metric coords.
    Evidence: query OSM building polygons; for each neighbor, compute minimum
    node-to-node distance from our polygon to theirs; group adjacent neighbors
    by cardinal direction; count distinct adjacent quadrants.
      0 quadrants → isolated
      1 quadrant  → row_end
      2+ quadrants→ row_middle
    """
    q = f"""
[out:json][timeout:20];
(way["building"](around:{BLDG_SEARCH_M},{lat},{lon}););
out body;>;out skel qt;
"""
    data = overpass(q)
    nodes = _nodes(data)
    bways = [w for w in _ways(data) if "building" in w.get("tags", {})]

    if not bways:
        return "isolated", "no OSM building polygons found"

    def centroid(w):
        pts = [nodes[n] for n in w["nodes"] if n in nodes]
        if not pts:
            return lat, lon
        return sum(p[0] for p in pts)/len(pts), sum(p[1] for p in pts)/len(pts)

    our_way   = min(bways, key=lambda w: math.dist(centroid(w), (lat, lon)))
    our_nodes = [(to_xy(nodes[n][0], nodes[n][1], lat, lon))
                 for n in our_way["nodes"] if n in nodes]

    adjacent_dirs = set()
    details       = []

    for nw in bways:
        if nw["id"] == our_way["id"]:
            continue
        n_nodes = [(to_xy(nodes[n][0], nodes[n][1], lat, lon))
                   for n in nw["nodes"] if n in nodes]
        if not n_nodes:
            continue

        min_dist = min(
            math.hypot(ox - nx, oy - ny)
            for ox, oy in our_nodes
            for nx, ny in n_nodes
        )
        if min_dist < ADJACENCY_THRESHOLD_M:
            nc      = centroid(nw)
            ncx, ncy = to_xy(nc[0], nc[1], lat, lon)
            direction = angle_to_cardinal(ncx, ncy)
            adjacent_dirs.add(direction)
            details.append(f"OSM way {nw['id']} at {direction.upper()}, min_dist={min_dist:.1f}m")

    n_adj = len(adjacent_dirs)
    if n_adj >= 2:
        setting = "row_middle"
    elif n_adj == 1:
        setting = "row_end"
    else:
        setting = "isolated"

    note = (f"adjacent directions: {sorted(adjacent_dirs) or 'none'}; "
            + ("; ".join(details[:4]) if details else "no neighbors within threshold"))
    return setting, note


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ba = json.loads(BA_PATH.read_text())
    results = {}

    for addr in ADDRESSES:
        label = addr.split(",")[0]
        print(f"\n{label}")
        rec   = ba.get(addr, {})
        lat   = rec.get("latitude")
        lon   = rec.get("longitude")

        if lat is None:
            print("  SKIP — no lat/lon")
            results[addr] = {}
            continue

        kw = parse_street_keyword(addr)
        entry = {}

        try:
            orient, note_o = compute_orientation(lat, lon, kw)
            entry["front_elevation_orientation"] = orient
            entry["_orient_note"] = note_o
            print(f"  orientation   : {orient}  | {note_o}")
        except Exception as exc:
            entry["_orient_note"] = f"ERROR: {exc}"
            print(f"  orientation   : FAILED — {exc}")
        time.sleep(1.5)

        try:
            setting, note_s = compute_urban_setting(lat, lon)
            entry["building_urban_setting"] = setting
            entry["_setting_note"] = note_s
            print(f"  urban_setting : {setting}  | {note_s}")
        except Exception as exc:
            entry["_setting_note"] = f"ERROR: {exc}"
            print(f"  urban_setting : FAILED — {exc}")
        time.sleep(1.5)

        results[addr] = entry

    OUTPUT.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {OUTPUT}")


if __name__ == "__main__":
    main()
