#!/usr/bin/env python3
"""
analyze_steps_cv.py  (H-steps)

Estimates entrance step count from front facade photos using Sobel-Y peak
detection at the building base.  No LLM calls.

Algorithm:
  1. Estimate facade extent (reuse estimate_facade_region).
  2. Scale calibration: px_per_m from H7 story count + height formula.
  3. Step zone: bottom - SEARCH_FRAC*facade_h … bottom + EXTRA_PX
     (centred on the building base where steps would appear).
  4. Crop center 50% of width (entrance is typically centred).
  5. Sobel-Y profile averaged across columns → 1-D edge signal.
  6. Gaussian smooth (σ=1.5), find peaks with adaptive prominence.
  7. step_count = n_peaks; step_height_m = step_count × RISER_M.
  8. Patch building_attributes_auto.json and write debug images.

LEAKAGE POLICY: reads only image files and non-labelled JSON outputs.
Output: facade_cv/facade_cv_steps_output.json
Debug:  facade_cv/debug_steps/{slug}/{photo}_steps.png
"""

from __future__ import annotations
import sys
import json
import cv2
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks

# ── repo layout ───────────────────────────────────────────────────────────────
REPO      = Path(__file__).parent.parent
OUT_DIR   = Path(__file__).parent
DEBUG_DIR = OUT_DIR / "debug_steps"
DEBUG_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(OUT_DIR))
from analyze_facade_h5 import estimate_facade_region, load_gray, find_front_photos

AUTO_JSON = REPO / "building_attributes_auto.json"
H7_JSON   = OUT_DIR / "facade_cv_h7_output.json"

# ── parameters ────────────────────────────────────────────────────────────────
RISER_M       = 0.18   # standard step riser height (m)
MAX_STEPS     = 5      # never predict more than this
HEIGHT_BASE_M = 4.0    # ground-floor height (m)
HEIGHT_UPPER_M= 3.5    # upper-floor height (m)

SEARCH_FRAC   = 0.18   # look in bottom this fraction of facade height
EXTRA_PX      = 25     # pixels below facade bottom to include
CENTER_FRAC   = 0.50   # use centre this fraction of image width

SIGMA         = 1.5    # Sobel-Y profile smoothing
MIN_PEAK_SEP_FRAC = 0.4  # min distance between peaks as fraction of riser_px
PROMINENCE_FRAC   = 0.12 # prominence as fraction of profile range in zone

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]

# LLM baseline for comparison (step counts from Claude vision)
LLM_STEPS = {
    "100 Main St, Montpelier, VT 05602":  2,
    "112 State St, Montpelier, VT 05602": 0,
    "27 Langdon St, Montpelier, VT 05602":3,
    "40 Main St, Montpelier, VT 05602":   0,
    "54 Elm St, Montpelier, VT 05602":    3,
}


def step_zone_profile(gray: np.ndarray, top: int, bottom: int,
                      riser_px: float) -> tuple[np.ndarray, int, int, int, int]:
    """
    Return (sobel_y_profile, zone_top, zone_bottom, col_lo, col_hi)
    for the step detection region.
    """
    h, w = gray.shape
    search_px = max(int((bottom - top) * SEARCH_FRAC), int(riser_px * MAX_STEPS) + 5)
    zone_top  = max(0, bottom - search_px)
    zone_bot  = min(h - 1, bottom + EXTRA_PX)

    col_lo = int(w * (0.5 - CENTER_FRAC / 2))
    col_hi = int(w * (0.5 + CENTER_FRAC / 2))

    roi = gray[zone_top:zone_bot, col_lo:col_hi].astype(np.float64)
    gy  = cv2.Sobel(roi, cv2.CV_64F, 0, 1, ksize=3)
    profile = np.mean(np.abs(gy), axis=1)
    return profile, zone_top, zone_bot, col_lo, col_hi


def count_steps(profile: np.ndarray, riser_px: float) -> tuple[int, np.ndarray]:
    smoothed   = gaussian_filter1d(profile, sigma=SIGMA)
    p_range    = smoothed.max() - smoothed.min()
    prominence = max(PROMINENCE_FRAC * p_range, 1.0)
    min_dist   = max(int(riser_px * MIN_PEAK_SEP_FRAC), 2)

    peaks, _ = find_peaks(smoothed, prominence=prominence, distance=min_dist)
    n = min(len(peaks), MAX_STEPS)
    return n, peaks


def save_debug(address: str, photo_name: str, gray: np.ndarray,
               top: int, bottom: int, zone_top: int, zone_bot: int,
               col_lo: int, col_hi: int,
               profile: np.ndarray, peaks: np.ndarray,
               riser_px: float, cv_steps: int, llm_steps: int) -> None:

    slug      = address.split(",")[0].replace(" ", "_")
    addr_dir  = DEBUG_DIR / slug
    addr_dir.mkdir(exist_ok=True)

    smoothed = gaussian_filter1d(profile, sigma=SIGMA)
    y_ax     = np.arange(len(profile))

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    fig.suptitle(
        f"{address}  |  {photo_name}\n"
        f"CV steps={cv_steps}   LLM steps={llm_steps}   riser={riser_px:.1f}px",
        fontsize=9,
    )

    # Panel 1 — image with step zone and column band annotated
    vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    cv2.rectangle(vis, (col_lo, zone_top), (col_hi, zone_bot), (0, 255, 0), 1)
    cv2.line(vis, (0, bottom), (vis.shape[1], bottom), (0, 165, 255), 1)
    for pk in peaks:
        y_img = zone_top + int(pk)
        cv2.line(vis, (col_lo, y_img), (col_hi, y_img), (0, 0, 255), 2)
    axes[0].imshow(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB))
    axes[0].set_title("green=search zone  orange=facade bottom  red=peaks", fontsize=7)
    axes[0].axis("off")

    # Panel 2 — zoomed crop of step zone
    crop = gray[zone_top:zone_bot, col_lo:col_hi]
    axes[1].imshow(crop, cmap="gray", aspect="auto")
    for pk in peaks:
        axes[1].axhline(pk, color="red", linewidth=1.5)
    axes[1].axhline(bottom - zone_top, color="orange", linewidth=1, linestyle="--",
                    label="facade bottom")
    axes[1].set_title(f"Step zone crop (centre {int(CENTER_FRAC*100)}% width)", fontsize=7)
    axes[1].legend(fontsize=6)
    axes[1].axis("off")

    # Panel 3 — Sobel-Y profile
    axes[2].plot(profile,  y_ax, color="lightblue", linewidth=0.7, label="raw Sobel-Y")
    axes[2].plot(smoothed, y_ax, color="steelblue", linewidth=1.5, label=f"smooth σ={SIGMA}")
    for pk in peaks:
        axes[2].axhline(pk, color="red", linewidth=1.2, label=f"step @y={pk}")
    axes[2].axhline(bottom - zone_top, color="orange", linewidth=1, linestyle="--")
    for i in range(1, MAX_STEPS + 1):
        axes[2].axhline(bottom - zone_top + i * riser_px,
                        color="gray", linewidth=0.5, linestyle=":", alpha=0.5)
    axes[2].invert_yaxis()
    axes[2].set_xlabel("Sobel-Y magnitude", fontsize=7)
    axes[2].set_ylabel("Y in step zone (px from zone top)", fontsize=7)
    axes[2].set_title("Sobel-Y profile", fontsize=8)
    axes[2].legend(fontsize=5)

    stem = Path(photo_name).stem
    plt.tight_layout()
    plt.savefig(str(addr_dir / f"{stem}_steps.png"), dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(f"    debug → {addr_dir / (stem + '_steps.png')}")


def process_address(address: str, h7_results: dict) -> dict:
    photos = find_front_photos(address)
    if not photos:
        return {"address": address, "error": "no front photos"}

    h7_rec  = h7_results.get(address, {})
    stories = h7_rec.get("number_stories", 3)
    height_m = HEIGHT_BASE_M + (stories - 1) * HEIGHT_UPPER_M

    per_photo, step_counts = [], []

    for path in photos:
        gray = load_gray(path)
        top, bottom = estimate_facade_region(gray)
        facade_h = bottom - top
        if facade_h < 60:
            continue

        px_per_m  = facade_h / height_m
        riser_px  = RISER_M * px_per_m

        profile, zone_top, zone_bot, col_lo, col_hi = step_zone_profile(
            gray, top, bottom, riser_px
        )
        cv_steps, peaks = count_steps(profile, riser_px)
        llm_steps = LLM_STEPS.get(address, -1)

        save_debug(address, path.name, gray, top, bottom, zone_top, zone_bot,
                   col_lo, col_hi, profile, peaks, riser_px, cv_steps, llm_steps)

        step_counts.append(cv_steps)
        per_photo.append({
            "photo":       path.name,
            "cv_steps":    cv_steps,
            "llm_steps":   llm_steps,
            "peaks_y_px":  [int(p) for p in peaks],
            "riser_px":    round(riser_px, 1),
            "px_per_m":    round(px_per_m, 1),
        })

    if not step_counts:
        return {"address": address, "error": "no processable photos"}

    final_steps = int(round(float(np.median(step_counts))))
    return {
        "address":          address,
        "cv_step_count":    final_steps,
        "llm_step_count":   LLM_STEPS.get(address, -1),
        "step_height_m":    round(final_steps * RISER_M, 3),
        "n_photos":         len(per_photo),
        "per_photo":        per_photo,
    }


def main() -> None:
    h7_results = json.loads(H7_JSON.read_text())
    attrs = json.loads(AUTO_JSON.read_text())

    print("H-steps: CV entrance step detection\n")
    results = {}
    for addr in ADDRESSES:
        print(f"  {addr.split(',')[0]} ...")
        r = process_address(addr, h7_results)
        results[addr] = r
        if "error" in r:
            print(f"    ERROR: {r['error']}")
        else:
            match = "✓" if r["cv_step_count"] == r["llm_step_count"] else "✗"
            print(f"    CV={r['cv_step_count']}  LLM={r['llm_step_count']}  {match}")

    out_path = OUT_DIR / "facade_cv_steps_output.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")

    # Summary table
    print("\n" + "="*60)
    print(f"{'Address':<20} {'CV':>4} {'LLM':>4} {'match':>6}")
    print("-"*60)
    for addr, r in results.items():
        name = addr.split(",")[0]
        if "error" in r:
            print(f"{name:<20} {'ERR':>4}")
        else:
            match = "yes" if r["cv_step_count"] == r["llm_step_count"] else "NO"
            print(f"{name:<20} {r['cv_step_count']:>4} {r['llm_step_count']:>4} {match:>6}")


if __name__ == "__main__":
    main()
