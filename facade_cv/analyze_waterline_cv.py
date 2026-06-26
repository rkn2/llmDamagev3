#!/usr/bin/env python3
"""
analyze_waterline_cv.py

Estimates flood water depth at the building facade from during-flood street-level
photos using Sobel-Y coverage-weighted peak detection.

Algorithm:
  1. Load a manually-specified during-flood photo per building.
  2. Estimate facade extent with estimate_facade_region (applied to the after photo).
  3. In the bottom SEARCH_FRAC of the facade height, compute per-row:
       - mean Sobel-Y magnitude (horizontal edge strength)
       - coverage: fraction of columns with |Sobel-Y| > COVERAGE_THRESH
  4. Weighted score = mean_sobelY × coverage  → find the peak row.
     The coverage weight suppresses local obstructions (people, poles) and selects
     edges that span the full facade width — the water surface.
  5. Scale calibration: px_per_m from H7 story count + building height formula,
     estimated from the AFTER photo's own facade extent (not borrowed from before).
  6. waterline_height_m = (facade_bottom − waterline_row) / px_per_m
     (positive when waterline is above the facade base; negative when below).

Outputs:
  facade_cv/facade_cv_waterline_output.json
  facade_cv/debug_waterline/{slug}/{photo}_waterline.png

LEAKAGE POLICY: reads only image files and non-labelled CV outputs.
"""

from __future__ import annotations
import sys, json, cv2, re
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks

REPO      = Path(__file__).parent.parent
OUT_DIR   = Path(__file__).parent
DEBUG_DIR = OUT_DIR / "debug_waterline"
DEBUG_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(OUT_DIR))
from analyze_facade_h5 import estimate_facade_region, load_gray

H7_JSON   = OUT_DIR / "facade_cv_h7_output.json"

# ── parameters ────────────────────────────────────────────────────────────────
SEARCH_ABOVE_PX = 90    # search at most this many px above facade_bottom (~1.8 m at 50 px/m)
                        # keeps the zone out of awnings and signs; covers up to ~6 ft water depth
SIGMA          = 3.0    # Sobel-Y profile smoothing
COVERAGE_THRESH= 15.0   # Sobel-Y pixel magnitude to count as "edge present"
MIN_COVERAGE   = 0.30   # waterline must span ≥30% of image width

HEIGHT_BASE_M  = 4.0
HEIGHT_UPPER_M = 3.5
RISER_M        = 0.18

# ── manually curated photo assignments ───────────────────────────────────────
# Only during-flood street-level photos where the waterline is visible on
# the building facade.  Other buildings lack usable photos of this type.
PHOTO_MAP = {
    "100 Main St, Montpelier, VT 05602":
        REPO / "ref_photos/after/100 Main St, Montpelier, VT 05602"
              / "2023_7_13_front(13).webp",
    "40 Main St, Montpelier, VT 05602":
        REPO / "ref_photos/after/40 Main St, Montpelier, VT 05602"
              / "2023_7_12_front (2).png",
    "27 Langdon St, Montpelier, VT 05602":
        REPO / "ref_photos/after/27 Langdon St, Montpelier, VT 05602"
              / "2023_7_11_front (20).png",
}

NO_PHOTO = {
    "112 State St, Montpelier, VT 05602":
        "no during-flood street-level photo — October photos are post-flood",
    "54 Elm St, Montpelier, VT 05602":
        "no during-flood street-level photo — October photos are post-flood",
}

# LLM flood height for comparison (from generate_detail_pages.py BUILDINGS dict)
LLM_FLOOD = {
    "100 Main St, Montpelier, VT 05602":  "~3.5 ft above first floor",
    "112 State St, Montpelier, VT 05602": "~4.0 ft above first floor",
    "27 Langdon St, Montpelier, VT 05602":"~3.5 ft above first floor",
    "40 Main St, Montpelier, VT 05602":   "~2.5 ft above first floor",
    "54 Elm St, Montpelier, VT 05602":    "~3.0 ft above first floor",
}


def detect_waterline(gray: np.ndarray, facade_top: int, facade_bottom: int,
                     px_per_m: float) -> tuple[int, np.ndarray, np.ndarray]:
    """
    Return (waterline_row_abs, weighted_profile, sobelY_raw) where
    waterline_row_abs is the y-coordinate in the full image.
    Profile is defined over [search_top .. facade_bottom].
    """
    h, w = gray.shape
    search_top = max(0, facade_bottom - SEARCH_ABOVE_PX)

    roi = gray[search_top:h, :]   # search to image bottom — water may be in sidewalk zone
    gy  = cv2.Sobel(roi, cv2.CV_64F, 0, 1, ksize=3)
    abs_gy = np.abs(gy)

    # Per-row mean edge strength
    mean_sobel = np.mean(abs_gy, axis=1)

    # Per-row coverage: fraction of columns with strong edge
    coverage = np.mean(abs_gy > COVERAGE_THRESH, axis=1)

    # Weighted score: strong AND wide
    score = mean_sobel * np.clip(coverage, 0, 1)
    smooth_score = gaussian_filter1d(score, sigma=SIGMA)

    # Find all peaks that span the required fraction of image width.
    # Take the FIRST peak (smallest rel_y = closest to search_top = closest to
    # facade base) — that's the building waterline.  The global argmax finds the
    # strongest edge, which is often the frame bottom or street surface.
    p_range    = smooth_score.max() - smooth_score.min()
    prominence = max(0.08 * p_range, 0.5)
    min_dist   = max(8, int(0.18 * px_per_m))   # ≥1 riser apart

    all_peaks, _ = find_peaks(smooth_score, prominence=prominence, distance=min_dist)
    # Filter to coverage-passing peaks only
    cov_peaks = all_peaks[coverage[all_peaks] >= MIN_COVERAGE]

    if len(cov_peaks) > 0:
        peak_rel = int(cov_peaks[0])          # first from top = building waterline
    elif len(all_peaks) > 0:
        peak_rel = int(all_peaks[0])          # fall back: first peak, no coverage filter
    else:
        peak_rel = int(np.argmax(smooth_score))

    waterline_abs = search_top + peak_rel
    return waterline_abs, smooth_score, mean_sobel, coverage, search_top


def save_debug(address: str, photo_path: Path, gray: np.ndarray,
               facade_top: int, facade_bottom: int, waterline_abs: int,
               smooth_score: np.ndarray, raw_sobel: np.ndarray,
               coverage: np.ndarray, search_top: int,
               waterline_m: float, px_per_m: float) -> None:

    slug     = address.split(",")[0].replace(" ", "_")
    addr_dir = DEBUG_DIR / slug
    addr_dir.mkdir(exist_ok=True)

    h, w = gray.shape
    y_ax = np.arange(len(smooth_score)) + search_top   # absolute image rows

    fig, axes = plt.subplots(1, 3, figsize=(17, 6))
    llm = LLM_FLOOD.get(address, "?")
    cv_ft = waterline_m / 0.3048
    fig.suptitle(
        f"{address}  |  {photo_path.name}\n"
        f"CV waterline height above facade base: {waterline_m:.2f} m "
        f"({waterline_m/0.3048:.1f} ft)   LLM: {llm}",
        fontsize=9,
    )

    # Panel 1 — image with waterline
    vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    cv2.line(vis, (0, facade_top),    (w, facade_top),    (0, 255, 0),  1)
    cv2.line(vis, (0, facade_bottom), (w, facade_bottom), (0, 165, 255), 1)
    cv2.line(vis, (0, search_top),    (w, search_top),    (200, 200, 0), 1)
    cv2.line(vis, (0, waterline_abs), (w, waterline_abs), (0, 0, 255),  2)
    axes[0].imshow(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB))
    axes[0].set_title("green=facade top  orange=facade base\nyellow=search top  red=waterline", fontsize=7)
    axes[0].axis("off")

    # Panel 2 — zoomed crop of search zone
    z_top = max(0, search_top - 20)
    z_bot = min(h - 1, facade_bottom + 50)
    crop = gray[z_top:z_bot, :]
    axes[1].imshow(crop, cmap="gray", aspect="auto")
    axes[1].axhline(waterline_abs - z_top, color="red",    linewidth=2,  label="waterline")
    axes[1].axhline(facade_bottom - z_top, color="orange", linewidth=1,  linestyle="--", label="facade base")
    axes[1].axhline(search_top - z_top,    color="yellow", linewidth=1,  linestyle=":", label="search top")
    axes[1].set_title(f"Search zone crop  ({waterline_m:.2f} m above base)", fontsize=7)
    axes[1].legend(fontsize=6)
    axes[1].axis("off")

    # Panel 3 — profiles
    ax3 = axes[2]
    ax3_r = ax3.twiny()

    ax3.plot(smooth_score, y_ax, color="steelblue", linewidth=1.5, label="score (mean×cov)")
    ax3.plot(raw_sobel,    y_ax, color="lightblue", linewidth=0.6, label="raw Sobel-Y", alpha=0.6)
    ax3_r.plot(coverage,  y_ax, color="green",     linewidth=1,   linestyle="--", alpha=0.7)
    ax3_r.axvline(MIN_COVERAGE, color="green", linewidth=0.5, linestyle=":")
    ax3_r.set_xlabel("coverage fraction", fontsize=7, color="green")
    ax3.axhline(waterline_abs,  color="red",    linewidth=2,  label=f"waterline y={waterline_abs}")
    ax3.axhline(facade_bottom,  color="orange", linewidth=1,  linestyle="--")
    ax3.axhline(search_top,     color="gold",   linewidth=1,  linestyle=":")
    ax3.invert_yaxis()
    ax3.set_xlabel("Sobel-Y score", fontsize=7)
    ax3.set_ylabel("Image row (px)", fontsize=7)
    ax3.set_title("Waterline detection profile", fontsize=8)
    ax3.legend(fontsize=6)

    stem = re.sub(r"[^\w\-]", "_", photo_path.stem)
    plt.tight_layout()
    out = addr_dir / f"{stem}_waterline.png"
    plt.savefig(str(out), dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(f"    debug → {out}")


def process_address(address: str, h7_results: dict) -> dict:
    photo_path = PHOTO_MAP.get(address)
    if photo_path is None:
        reason = NO_PHOTO.get(address, "not in PHOTO_MAP")
        return {"address": address, "skipped": reason}

    if not photo_path.exists():
        return {"address": address, "error": f"photo not found: {photo_path}"}

    gray = load_gray(photo_path)
    h, w = gray.shape

    facade_top, facade_bottom = estimate_facade_region(gray)
    facade_h = facade_bottom - facade_top

    h7_rec   = h7_results.get(address, {})
    stories  = h7_rec.get("number_stories", 3)
    height_m = HEIGHT_BASE_M + (stories - 1) * HEIGHT_UPPER_M
    px_per_m = facade_h / height_m

    waterline_abs, smooth_score, raw_sobel, coverage, search_top = detect_waterline(
        gray, facade_top, facade_bottom, px_per_m
    )

    # Height above facade base (positive = above grade, negative = water below base)
    waterline_m = (facade_bottom - waterline_abs) / px_per_m

    save_debug(address, photo_path, gray, facade_top, facade_bottom,
               waterline_abs, smooth_score, raw_sobel, coverage, search_top,
               waterline_m, px_per_m)

    llm_raw = LLM_FLOOD.get(address, "")
    llm_m   = None
    m = re.search(r"([\d.]+)\s*ft", llm_raw)
    if m:
        llm_m = float(m.group(1)) * 0.3048

    return {
        "address":              address,
        "photo":                photo_path.name,
        "waterline_m_above_base": round(waterline_m, 3),
        "waterline_ft_above_base": round(waterline_m / 0.3048, 2),
        "llm_flood_height":     llm_raw,
        "llm_flood_m":          round(llm_m, 3) if llm_m else None,
        "delta_m":              round(waterline_m - llm_m, 3) if llm_m else None,
        "px_per_m":             round(px_per_m, 1),
        "facade_h_px":          facade_h,
        "stories_used":         stories,
    }


def main() -> None:
    h7 = json.loads((H7_JSON).read_text())

    ADDRESSES = [
        "100 Main St, Montpelier, VT 05602",
        "112 State St, Montpelier, VT 05602",
        "27 Langdon St, Montpelier, VT 05602",
        "40 Main St, Montpelier, VT 05602",
        "54 Elm St, Montpelier, VT 05602",
    ]

    print("Waterline CV detection\n")
    results = {}
    for addr in ADDRESSES:
        print(f"  {addr.split(',')[0]} ...")
        r = process_address(addr, h7)
        results[addr] = r
        if "skipped" in r:
            print(f"    SKIP: {r['skipped']}")
        elif "error" in r:
            print(f"    ERROR: {r['error']}")
        else:
            delta = f"  Δ={r['delta_m']:+.2f}m" if r["delta_m"] is not None else ""
            print(f"    CV={r['waterline_ft_above_base']:.1f}ft  LLM={r['llm_flood_height']}{delta}")

    out = OUT_DIR / "facade_cv_waterline_output.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out}")

    print("\n" + "="*65)
    print(f"{'Address':<20} {'CV(ft)':>8} {'LLM':>20}  {'Δ(m)':>7}")
    print("-"*65)
    for addr, r in results.items():
        name = addr.split(",")[0]
        if "skipped" in r:
            print(f"{name:<20} {'—':>8}  SKIP: {r['skipped'][:30]}")
        elif "error" in r:
            print(f"{name:<20} {'ERR':>8}")
        else:
            delta = f"{r['delta_m']:+.2f}" if r["delta_m"] is not None else "—"
            print(f"{name:<20} {r['waterline_ft_above_base']:>8.1f}  "
                  f"{r['llm_flood_height']:>20}  {delta:>7}")


if __name__ == "__main__":
    main()
