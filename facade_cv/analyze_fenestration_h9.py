#!/usr/bin/env python3
"""
H9: Fenestration percentage + window sill height from facade photos.

Two primary outputs per building (front face; side photos not available):
  wall_fenesteration_front_per           — detected window area / total facade area (%)
  wall_fenesteration_front_lowerlevel_per — same restricted to ground-floor band (%)
  window_sill_height_m                   — lowest window sill above grade (m)
  window_sill_height_upper_m             — median sill height for upper-floor windows (m)

Building height (for pixel→metre scale) from H7 story counts (CV-derived):
  building_height_m = 4.0 + (n_stories - 1) * 3.5

Ground-floor band = bottom (1/n_stories) fraction of facade height.

Sill height in metres:
  m_per_px = building_height_m / facade_height_px
  sill_m   = (roi_h - (blob_y + blob_h)) * m_per_px

Window detection reuses H5: CLAHE + 35th-percentile threshold + contour filter.

LEAKAGE POLICY: reads only image files from ref_photos/before/ and
facade_cv/facade_cv_h7_output.json (CV-derived story counts — no ground truth).
Output: facade_cv/facade_cv_h9_output.json
Debug:  facade_cv/debug_h9/{slug}.png
"""

from __future__ import annotations
import json, sys
import numpy as np, cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analyze_facade_h5 import (
    find_front_photos, load_gray,
    estimate_facade_region, detect_window_blobs,
)

REPO      = Path(__file__).parent.parent
OUT_DIR   = Path(__file__).parent
DEBUG_DIR = OUT_DIR / "debug_h9"
DEBUG_DIR.mkdir(exist_ok=True)

H7_PATH = OUT_DIR / "facade_cv_h7_output.json"

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]

# Height formula matching generate_detail_pages.py
def building_height_m(n_stories: int) -> float:
    return 4.0 + (n_stories - 1) * 3.5


# ── Per-photo analysis ────────────────────────────────────────────────────────

def detect_gf_blobs(gray: np.ndarray, top: int, bottom: int,
                    gf_top_px: int) -> list:
    """
    Stricter window detection for the ground-floor band only.

    Two calibration changes vs. H5's detect_window_blobs:
      1. 28th-percentile threshold (vs. 35th) — rejects shallower shadows from
         masonry arch spandrels and awnings that are not actual glazing.
      2. max blob width = 25% of facade width (vs. 55%) — rejects merged
         multi-bay blobs and arch openings whose bounding boxes include solid
         masonry above/beside the glazed insert.
    """
    roi    = gray[top + gf_top_px:bottom, :]
    h_roi, w_roi = roi.shape
    if h_roi < 10:
        return []

    clahe    = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(roi)

    thresh_val = int(np.percentile(enhanced, 28))
    _, binary  = cv2.threshold(enhanced, thresh_val, 255, cv2.THRESH_BINARY_INV)

    k_open = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k_open, iterations=2)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    accepted = []
    for cnt in contours:
        if cv2.contourArea(cnt) < 30:
            continue
        x, y, cw, ch = cv2.boundingRect(cnt)
        w_frac = cw / w_roi
        h_frac = ch / h_roi
        if not (0.03 <= w_frac <= 0.25):   # tighter max width (was 0.55)
            continue
        if not (0.05 <= h_frac <= 0.90):
            continue
        aspect = cw / max(ch, 1)
        if not (0.15 <= aspect <= 5.0):
            continue
        # Return coords in full-ROI space (offset by gf_top_px)
        accepted.append((x, y + gf_top_px, cw, ch))

    return accepted


def analyse_photo(gray: np.ndarray, n_stories: int) -> dict | None:
    top, bottom = estimate_facade_region(gray)
    roi_h = bottom - top
    roi_w = gray.shape[1]
    if roi_h < 50:
        return None

    _, all_blobs = detect_window_blobs(gray, top, bottom)

    bldg_h_m = building_height_m(n_stories)
    m_per_px  = bldg_h_m / roi_h

    # Ground-floor band: bottom 1/n_stories of ROI height
    gf_top_px = int(roi_h * (n_stories - 1) / n_stories)

    # Upper-floor blobs from standard H5 detector (those above GF boundary)
    upper_blobs = [(x, y, cw, ch) for (x, y, cw, ch) in all_blobs
                   if (y + ch) < gf_top_px]

    # Ground-floor blobs with stricter detector
    gf_blobs = detect_gf_blobs(gray, top, bottom, gf_top_px)

    blobs = upper_blobs + gf_blobs

    if not blobs:
        return {"fen_front": 0.0, "fen_lower": 0.0,
                "sill_min_m": None, "sill_upper_m": None,
                "blobs": [], "top": top, "bottom": bottom, "roi_w": roi_w,
                "gf_top_px": gf_top_px, "m_per_px": round(m_per_px, 4)}

    total_window_area = 0
    gf_window_area    = 0
    sill_heights_m    = []
    upper_sills       = []

    for (x, y, cw, ch) in blobs:
        area = cw * ch
        total_window_area += area

        sill_y_in_roi = y + ch
        sill_m = max(0.0, (roi_h - sill_y_in_roi) * m_per_px)
        sill_heights_m.append(sill_m)

        if (x, y, cw, ch) in gf_blobs:
            gf_window_area += area
        else:
            upper_sills.append(sill_m)

    facade_area  = roi_h * roi_w
    gf_band_area = (roi_h - gf_top_px) * roi_w

    fen_front = round(min(95.0, total_window_area / facade_area * 100), 1)
    fen_lower = round(min(95.0, gf_window_area / gf_band_area * 100), 1) if gf_band_area > 0 else 0.0

    sill_min_m   = round(float(min(sill_heights_m)), 2) if sill_heights_m else None
    sill_upper_m = round(float(np.median(upper_sills)), 2) if upper_sills else None

    return {
        "fen_front":    fen_front,
        "fen_lower":    fen_lower,
        "sill_min_m":   sill_min_m,
        "sill_upper_m": sill_upper_m,
        "blobs":        blobs,
        "gf_blobs":     gf_blobs,
        "top": top, "bottom": bottom, "roi_w": roi_w,
        "gf_top_px": gf_top_px,
        "m_per_px": round(m_per_px, 4),
    }


# ── Aggregate across photos ───────────────────────────────────────────────────

def aggregate(results: list[dict]) -> dict:
    valid = [r for r in results if r is not None]
    if not valid:
        return {}

    def _median(vals):
        vals = [v for v in vals if v is not None]
        return round(float(np.median(vals)), 2) if vals else None

    return {
        "wall_fenesteration_front_per":           _median([r["fen_front"] for r in valid]),
        "wall_fenesteration_front_lowerlevel_per": _median([r["fen_lower"] for r in valid]),
        "window_sill_height_m":                   _median([r["sill_min_m"] for r in valid]),
        "window_sill_height_upper_m":             _median([r["sill_upper_m"] for r in valid]),
    }


# ── Debug image ───────────────────────────────────────────────────────────────

def save_debug(address: str, photo_path: Path, gray: np.ndarray,
               result: dict, n_stories: int) -> None:
    slug  = address.split(",")[0].replace(" ", "_")
    photo = photo_path.stem

    top, bottom   = result["top"], result["bottom"]
    roi_h         = bottom - top
    roi_w         = result["roi_w"]
    gf_top_px     = result.get("gf_top_px", int(roi_h * (n_stories - 1) / n_stories))
    m_per_px      = result.get("m_per_px", 0)
    blobs         = result["blobs"]

    gf_blobs_set = set(result.get("gf_blobs", []))
    roi_bgr = cv2.cvtColor(gray[top:bottom, :], cv2.COLOR_GRAY2BGR)
    # Green = upper floor windows, orange = ground floor windows
    for (x, y, cw, ch) in blobs:
        in_gf  = (x, y, cw, ch) in gf_blobs_set
        color  = (0, 140, 255) if in_gf else (0, 200, 0)  # orange / green
        cv2.rectangle(roi_bgr, (x, y), (x + cw, y + ch), color, 2)
        sill_m = round(max(0.0, (roi_h - (y + ch)) * m_per_px), 2)
        cv2.putText(roi_bgr, f"{sill_m}m", (x, y + ch - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)

    # Ground-floor divider
    cv2.line(roi_bgr, (0, gf_top_px), (roi_w, gf_top_px), (255, 80, 80), 1)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    title = (f"{address.split(',')[0]}  |  {photo}\n"
             f"front={result['fen_front']}%  lower={result['fen_lower']}%  "
             f"sill_min={result['sill_min_m']}m  sill_upper={result['sill_upper_m']}m  "
             f"n_blobs={len(blobs)}")
    fig.suptitle(title, fontsize=8)

    axes[0].imshow(cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2RGB))
    axes[0].set_title("detected windows (green=upper, orange=GF)", fontsize=7)
    axes[0].axis("off")

    # Radial intensity profile → vertical intensity bar chart showing floor bands
    row_mean = np.mean(gray[top:bottom, :], axis=1)
    axes[1].barh(np.arange(roi_h), row_mean, color="steelblue", height=1, linewidth=0)
    axes[1].axhline(gf_top_px, color="red", linestyle="--", linewidth=1,
                    label=f"GF boundary (y={gf_top_px})")
    axes[1].invert_yaxis()
    axes[1].set_xlabel("Mean pixel intensity")
    axes[1].set_ylabel("Row (px from facade top)")
    axes[1].set_title("Facade row-mean intensity", fontsize=7)
    axes[1].legend(fontsize=6)

    plt.tight_layout()
    out_path = DEBUG_DIR / f"{slug}_{photo}_h9.png"
    plt.savefig(str(out_path), dpi=100, bbox_inches="tight")
    plt.close(fig)
    return str(out_path)


# ── Main ─────────────────────────────────────────────────────────────────────

def process(address: str, n_stories: int) -> dict:
    photos = find_front_photos(address)
    if not photos:
        print(f"  no front photos")
        return {"address": address, "error": "no front photos"}

    per_photo_results = []
    for p in photos:
        gray = load_gray(p)
        r    = analyse_photo(gray, n_stories)
        per_photo_results.append(r)
        if r:
            debug_path = save_debug(address, p, gray, r, n_stories)
            print(f"  {p.name}: front={r['fen_front']}%  lower={r['fen_lower']}%  "
                  f"sill_min={r['sill_min_m']}m  sill_upper={r['sill_upper_m']}m  "
                  f"blobs={len(r['blobs'])}")
            print(f"    → {debug_path}")
        else:
            print(f"  {p.name}: no blobs detected")

    agg = aggregate(per_photo_results)
    return {"address": address, "n_stories": n_stories, **agg,
            "n_photos": len(photos)}


def main():
    h7 = json.loads(H7_PATH.read_text())
    story_counts = {addr: rec["number_stories"] for addr, rec in h7.items()}

    results = {}
    for addr in ADDRESSES:
        n = story_counts.get(addr, 3)
        label = addr.split(",")[0]
        print(f"\n{label}  (n_stories={n}, bldg_h={building_height_m(n)}m)")
        results[addr] = process(addr, n)

    out_path = OUT_DIR / "facade_cv_h9_output.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")

    print("\n── Summary ──────────────────────────────────────────────────────")
    print(f"{'building':<15}  {'front%':>7}  {'lower%':>7}  {'sill_min':>9}  {'sill_up':>8}")
    for addr in ADDRESSES:
        r = results[addr]
        label = addr.split(",")[0]
        print(f"{label:<15}  "
              f"{str(r.get('wall_fenesteration_front_per','?')):>7}  "
              f"{str(r.get('wall_fenesteration_front_lowerlevel_per','?')):>7}  "
              f"{str(r.get('window_sill_height_m','?')):>9}  "
              f"{str(r.get('window_sill_height_upper_m','?')):>8}")

    print(f"\nDebug images → {DEBUG_DIR}/")


if __name__ == "__main__":
    main()
