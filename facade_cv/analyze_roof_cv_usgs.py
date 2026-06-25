#!/usr/bin/env python3
"""
analyze_roof_cv_usgs.py

Token-free roof shape classifier using ESRI World Imagery aerial tiles.
No browser, no LLM — pure Python (urllib + OpenCV).
Tile source: server.arcgisonline.com World_Imagery (free, no API key).

Pipeline:
  1. Fetch a 3×3 grid of USGS ImageryOnly tiles at zoom 19 (~0.3 m/px)
  2. Stitch into a composite image
  3. Mask to approximate OSM bounding-box footprint of the target building
  4. Two CV signals applied within the footprint:

     Signal A — Radial intensity ratio (outer/inner):
       Hypothesis: mansard roofs have dark sloped perimeters and a lighter flat
       centre, producing outer/inner < MANSARD_THRESHOLD. Flat roofs are uniform
       (ratio ≈ 1.0). Gable/hip may be intermediate.

     Signal B — Hough dominant lines:
       Hypothesis: gable/hip roofs show a strong ridgeline (diagonal in top-down
       view); flat/mansard do not.

  Decision:
    outer/inner < MANSARD_THRESHOLD               → "mansard"
    Hough finds strong non-perimeter diagonal lines→ "gable" or "hip"
    otherwise                                      → "flat"

LEAKAGE POLICY: reads only building_attributes_auto.json (footprint dims, lat/lon)
and public USGS imagery tiles. No pipeline assessment JSONs.

Output: facade_cv/facade_cv_roof_usgs_output.json
Debug:  facade_cv/debug_roof_usgs/{slug}.png

Run vs. LLM baseline:
  python3 facade_cv/analyze_roof_cv_usgs.py
"""

from __future__ import annotations
import json, math, time, urllib.request
import numpy as np, cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

REPO       = Path(__file__).parent.parent
OUT_DIR    = Path(__file__).parent
DEBUG_DIR  = OUT_DIR / "debug_roof_usgs"
DEBUG_DIR.mkdir(exist_ok=True)

BA_PATH    = REPO / "building_attributes_auto.json"

# 27 Langdon shares an OSM polygon with 100 Main (combined block polygon ≈43×38m).
# Override with estimated single-building dims (~20×18m) so the footprint mask
# covers only the actual roof rather than street/adjacent buildings.
FOOTPRINT_OVERRIDE = {
    "27 Langdon St, Montpelier, VT 05602": {"ns_m": 20.0, "ew_m": 18.0},
}

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]

USER_AGENT = "llmDamagev3-research/1.0 (academic flood damage study)"
ZOOM       = 19
TILE_SIZE  = 256
TILE_GRID  = 2    # fetch (2*TILE_GRID+1)² tiles centred on building

# Signal A — Radial ratio (outer/inner intensity).
# Post-critique calibration: bright flat membrane roofs have ratio < 0.92 (bright centre,
# darker surroundings). Dark roofs and mansard both have ratio ≥ 0.92. So:
#   ratio < FLAT_BRIGHT_THRESHOLD → "flat" (bright membrane roof, not mansard)
#   ratio ≥ FLAT_BRIGHT_THRESHOLD → ambiguous; use Signal C to resolve.
FLAT_BRIGHT_THRESHOLD = 0.92

# Signal B — Hough (after mask erosion to kill boundary edges).
# After erosion, diagonal lines within the footprint indicate true ridgelines.
HOUGH_VOTES      = 40
HOUGH_RHO        = 1
HOUGH_THETA      = np.pi / 180
MASK_ERODE_PX    = 20   # erode mask before Canny to remove footprint-boundary edges

# Signal C — Inner-core Canny edge density.
# Hypothesis: mansard has visible slope-transition shadow lines within the inner roof area;
# flat roofs (even dark ones) have minimal internal edges.
# If inner_edge_density > MANSARD_EDGE_THRESHOLD → "mansard"
MANSARD_EDGE_THRESHOLD = 0.08  # fraction of inner-core pixels that are Canny edges


# ── Tile helpers ──────────────────────────────────────────────────────────────

def _tile_xy(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Fractional tile coordinates (Web Mercator)."""
    n = 2.0 ** zoom
    x = n * (lon + 180.0) / 360.0
    lr = math.radians(lat)
    y = n * (1 - math.log(math.tan(lr) + 1 / math.cos(lr)) / math.pi) / 2
    return x, y

def _fetch_tile(tx: int, ty: int, zoom: int) -> np.ndarray:
    # ESRI World Imagery — free public tiles, no API key required
    url = (f"https://server.arcgisonline.com/ArcGIS/rest/services"
           f"/World_Imagery/MapServer/tile/{zoom}/{ty}/{tx}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return np.zeros((TILE_SIZE, TILE_SIZE, 3), dtype=np.uint8)
    return img

def fetch_composite(lat: float, lon: float,
                    zoom: int, grid: int) -> tuple[np.ndarray, int, int]:
    """
    Fetch a (2g+1)×(2g+1) tile grid centred on (lat, lon).
    Returns (composite_BGR, origin_tile_x, origin_tile_y).
    """
    cx, cy = _tile_xy(lat, lon, zoom)
    tx0 = int(cx) - grid
    ty0 = int(cy) - grid
    side = 2 * grid + 1
    composite = np.zeros((side * TILE_SIZE, side * TILE_SIZE, 3), dtype=np.uint8)

    for di in range(side):       # row = y direction
        for dj in range(side):   # col = x direction
            tile = _fetch_tile(tx0 + dj, ty0 + di, zoom)
            y0 = di * TILE_SIZE
            x0 = dj * TILE_SIZE
            composite[y0:y0 + TILE_SIZE, x0:x0 + TILE_SIZE] = tile
            time.sleep(0.15)     # USGS courtesy delay

    return composite, tx0, ty0

def latlon_to_composite_px(lat, lon, zoom, origin_tx, origin_ty):
    tx, ty = _tile_xy(lat, lon, zoom)
    px = (tx - origin_tx) * TILE_SIZE
    py = (ty - origin_ty) * TILE_SIZE
    return px, py


# ── Footprint mask ────────────────────────────────────────────────────────────

def footprint_mask(lat, lon, ns_m, ew_m, zoom, origin_tx, origin_ty,
                   img_shape) -> tuple[np.ndarray, list]:
    """
    Approximate rectangular footprint mask from OSM bounding-box dimensions.
    Uses equirectangular projection (sufficient for ~50 m buildings).
    """
    lat_deg_per_m = 1 / 111_320.0
    lon_deg_per_m = 1 / (111_320.0 * math.cos(math.radians(lat)))
    h_ns = (ns_m / 2) * lat_deg_per_m
    h_ew = (ew_m / 2) * lon_deg_per_m

    corners_latlon = [
        (lat + h_ns, lon - h_ew),   # NW
        (lat + h_ns, lon + h_ew),   # NE
        (lat - h_ns, lon + h_ew),   # SE
        (lat - h_ns, lon - h_ew),   # SW
    ]
    corners_px = [
        latlon_to_composite_px(la, lo, zoom, origin_tx, origin_ty)
        for la, lo in corners_latlon
    ]
    pts = np.array([[int(x), int(y)] for x, y in corners_px], dtype=np.int32)
    mask = np.zeros(img_shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    return mask, corners_px


# ── CV classifiers ────────────────────────────────────────────────────────────

def radial_ratio(gray: np.ndarray, mask: np.ndarray) -> tuple[float, str]:
    """
    Compute outer-ring / inner-core mean intensity ratio within mask.
    Outer ring: pixels with dist > 0.75 × max_dist from centroid.
    Inner core: pixels with dist < 0.40 × max_dist from centroid.
    """
    ys, xs = np.where(mask > 0)
    if len(ys) == 0:
        return 1.0, "empty mask"

    cy, cx = ys.mean(), xs.mean()
    yg, xg = np.mgrid[0:gray.shape[0], 0:gray.shape[1]]
    dist = np.sqrt((xg - cx) ** 2 + (yg - cy) ** 2)

    in_mask      = mask > 0
    max_dist     = dist[in_mask].max()
    if max_dist == 0:
        return 1.0, "zero extent"

    inner        = in_mask & (dist < max_dist * 0.40)
    outer        = in_mask & (dist > max_dist * 0.75)
    inner_mean   = gray[inner].mean() if inner.any() else 128.0
    outer_mean   = gray[outer].mean() if outer.any() else 128.0
    ratio        = outer_mean / inner_mean if inner_mean > 0 else 1.0
    return ratio, f"inner={inner_mean:.0f} outer={outer_mean:.0f} ratio={ratio:.3f}"


def inner_edge_density(gray: np.ndarray, mask: np.ndarray) -> tuple[float, str]:
    """
    Fraction of inner-core (dist < 0.45 × max_dist) pixels that are Canny edges.
    Erode mask first to avoid footprint-boundary edges contaminating the result.
    """
    kernel   = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (MASK_ERODE_PX*2+1,)*2)
    inner_m  = cv2.erode(mask, kernel)
    if not inner_m.any():
        return 0.0, "mask too small after erosion"

    masked   = cv2.bitwise_and(gray, gray, mask=inner_m)
    edges    = cv2.Canny(masked, 40, 120)
    n_edge   = int((edges[inner_m > 0] > 0).sum())
    n_total  = int((inner_m > 0).sum())
    density  = n_edge / n_total if n_total > 0 else 0.0
    return density, f"inner edges={n_edge}/{n_total} density={density:.4f}"


def hough_ridgelines(gray: np.ndarray, mask: np.ndarray) -> tuple[int, str]:
    """Count diagonal Hough lines inside eroded footprint (erosion removes boundary edges)."""
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (MASK_ERODE_PX*2+1,)*2)
    eroded = cv2.erode(mask, kernel)
    if not eroded.any():
        return 0, "mask too small after erosion"
    masked = cv2.bitwise_and(gray, gray, mask=eroded)
    edges  = cv2.Canny(masked, 50, 150)
    lines  = cv2.HoughLines(edges, HOUGH_RHO, HOUGH_THETA, HOUGH_VOTES)
    if lines is None:
        return 0, "no lines"

    diagonal = 0
    for line in lines:
        rho, theta = line[0]
        angle_deg = math.degrees(theta)
        if 20 < angle_deg < 70 or 110 < angle_deg < 160:
            diagonal += 1

    return diagonal, f"{len(lines)} total lines, {diagonal} diagonal"


def classify_roof(ratio: float, edge_density: float, n_diagonal: int) -> tuple[str, str]:
    # Bright flat membrane roof — ratio clearly below 1 (bright centre, dark surroundings)
    if ratio < FLAT_BRIGHT_THRESHOLD:
        return "flat", f"bright-centre ratio={ratio:.3f} < {FLAT_BRIGHT_THRESHOLD}"
    # Dark or ambiguous.
    # Require n_diagonal ≥ 2 before calling mansard: mansard slope-to-flat transition
    # creates some aligned Canny edges that survive as Hough lines; a flat roof with
    # scattered HVAC equipment may have high edge_density but very few actual line features.
    if edge_density > MANSARD_EDGE_THRESHOLD and n_diagonal >= 2:
        return "mansard", (f"inner edge density={edge_density:.4f} > {MANSARD_EDGE_THRESHOLD}"
                           f" with {n_diagonal} diagonal lines")
    # Strong ridgeline signal (gable or hip)
    if n_diagonal >= 5:
        return "gable_or_hip", f"{n_diagonal} diagonal Hough lines"
    return "flat", (f"ratio={ratio:.3f} edge_density={edge_density:.4f} "
                    f"{n_diagonal} diag lines → default flat")


# ── Debug plot ────────────────────────────────────────────────────────────────

def save_debug(address, composite, mask, gray, corners_px,
               ratio, n_diag, shape, ratio_note, hough_note):
    slug = address.split(",")[0].replace(" ", "_")

    # Build an overlay showing the footprint polygon + classification
    vis = cv2.cvtColor(composite, cv2.COLOR_BGR2RGB)
    pts = np.array([[int(x), int(y)] for x, y in corners_px], dtype=np.int32)
    cv2.polylines(vis, [pts], True, (255, 80, 80), 2)

    # Grey out everything outside the mask
    grey_bg = (vis * 0.35).astype(np.uint8)
    vis_out = grey_bg.copy()
    vis_out[mask > 0] = vis[mask > 0]

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    fig.suptitle(
        f"{address}\n→ {shape} | {ratio_note} | {hough_note}",
        fontsize=9,
    )

    axes[0].imshow(vis_out)
    axes[0].set_title("composite (footprint highlighted)", fontsize=8)
    axes[0].axis("off")

    # Radial profile
    ys, xs = np.where(mask > 0)
    cy, cx = ys.mean(), xs.mean()
    yg, xg = np.mgrid[0:gray.shape[0], 0:gray.shape[1]]
    dist    = np.sqrt((xg - cx) ** 2 + (yg - cy) ** 2)
    max_d   = dist[mask > 0].max()
    bins    = np.linspace(0, max_d, 30)
    means   = []
    for i in range(len(bins) - 1):
        ring = (mask > 0) & (dist >= bins[i]) & (dist < bins[i + 1])
        means.append(gray[ring].mean() if ring.any() else np.nan)

    axes[1].plot(bins[:-1] / max_d, means, "o-", color="steelblue", linewidth=1.5)
    axes[1].axvline(0.40, color="green",  linestyle="--", linewidth=1, label="inner edge 40%")
    axes[1].axvline(0.75, color="orange", linestyle="--", linewidth=1, label="outer start 75%")
    axes[1].set_xlabel("Fractional distance from centroid")
    axes[1].set_ylabel("Mean pixel intensity")
    axes[1].set_title(f"Radial intensity profile (ratio={ratio:.3f})", fontsize=8)
    axes[1].legend(fontsize=7)

    plt.tight_layout()
    plt.savefig(str(DEBUG_DIR / f"{slug}_roof.png"), dpi=100, bbox_inches="tight")
    plt.close(fig)


# ── Main ─────────────────────────────────────────────────────────────────────

def process(address: str, ba: dict) -> dict:
    rec  = ba.get(address, {})
    lat  = rec.get("latitude")
    lon  = rec.get("longitude")
    ovr  = FOOTPRINT_OVERRIDE.get(address, {})
    ns_m = ovr.get("ns_m") or rec.get("approx_wall_length_a_m")
    ew_m = ovr.get("ew_m") or rec.get("approx_wall_length_b_m")

    if lat is None or ns_m is None:
        return {"address": address, "error": "missing lat/lon or footprint dims"}

    print(f"  fetching tiles …", end=" ", flush=True)
    composite, origin_tx, origin_ty = fetch_composite(lat, lon, ZOOM, TILE_GRID)
    print(f"done ({composite.shape[1]}×{composite.shape[0]} px)")

    gray = cv2.cvtColor(composite, cv2.COLOR_BGR2GRAY)
    mask, corners_px = footprint_mask(lat, lon, ns_m, ew_m,
                                      ZOOM, origin_tx, origin_ty, composite.shape)

    ratio, ratio_note        = radial_ratio(gray, mask)
    edge_density, edge_note  = inner_edge_density(gray, mask)
    n_diag, hough_note       = hough_ridgelines(gray, mask)
    shape, reason            = classify_roof(ratio, edge_density, n_diag)

    print(f"  ratio={ratio:.3f}  edge_density={edge_density:.4f}  "
          f"diag_lines={n_diag}  → {shape}")
    save_debug(address, composite, mask, gray, corners_px,
               ratio, n_diag, shape, ratio_note, hough_note)

    return {
        "address":         address,
        "roof_shape_cv":   shape,
        "radial_ratio":    round(ratio, 4),
        "inner_edge_density": round(edge_density, 5),
        "n_diagonal_hough": n_diag,
        "reason":          reason,
        "ratio_note":      ratio_note,
        "edge_note":       edge_note,
        "hough_note":      hough_note,
    }


def main():
    ba = json.loads(BA_PATH.read_text())

    # LLM baseline for comparison (from analyze_roof_satellite_all.py run)
    llm_baseline = {
        "100 Main St, Montpelier, VT 05602":   "flat",
        "112 State St, Montpelier, VT 05602":  "mansard",
        "27 Langdon St, Montpelier, VT 05602": "flat",
        "40 Main St, Montpelier, VT 05602":    "flat",
        "54 Elm St, Montpelier, VT 05602":     "flat",
    }

    results = {}
    for addr in ADDRESSES:
        label = addr.split(",")[0]
        print(f"\n{label}")
        r = process(addr, ba)
        results[addr] = r
        time.sleep(1)

    out_path = OUT_DIR / "facade_cv_roof_usgs_output.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")

    print("\n── Comparison vs. LLM baseline ─────────────────────────────")
    print(f"{'building':<15}  {'CV':>10}  {'LLM':>8}  {'match':>5}  ratio    edge_d   diag")
    for addr in ADDRESSES:
        r = results[addr]
        cv_shape  = r.get("roof_shape_cv", "error")
        llm_shape = llm_baseline.get(addr, "?")
        match     = "✓" if cv_shape == llm_shape else "✗"
        ratio_str = f"{r.get('radial_ratio', 0):.4f}" if "radial_ratio" in r else "?"
        edge_str  = f"{r.get('inner_edge_density', 0):.4f}" if "inner_edge_density" in r else "?"
        diag      = r.get("n_diagonal_hough", "?")
        label     = addr.split(",")[0]
        print(f"{label:<15}  {cv_shape:>10}  {llm_shape:>8}  {match:>5}  {ratio_str}  {edge_str}  {diag}")

    print(f"\nDebug images → {DEBUG_DIR}/")


if __name__ == "__main__":
    main()
