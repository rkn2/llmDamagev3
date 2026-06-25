#!/usr/bin/env python3
"""
H6: 2D row-occupancy projection + vertical-run counting.

Instead of clustering y-centroids (fragile 1D point cloud), project
window-blob bounding boxes onto a 1D row-occupancy profile:
  occupancy[y] = total horizontal pixel-width covered by accepted blobs at row y.

Find connected runs of "occupied" rows (rows where occupancy > 0).
Filter runs by minimum VERTICAL HEIGHT (fraction of facade height):
  - Removes cornice artifacts (short blobs → short runs)
  - Keeps real floors (window height spans ≥ 8% of facade height)
  - For 112 State St: each arcade arch and each upper-floor band forms its own run

This replaces the fragile 1D gap-threshold clustering with a structural prior:
a real story band is a HORIZONTAL EXTENT of windows spanning enough vertical height.

LEAKAGE POLICY: reads ONLY image files from ref_photos/before/.
Output: facade_cv/facade_cv_h6_output.json
"""
import cv2
import numpy as np
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from scipy.ndimage import label as ndlabel

REPO      = Path(__file__).parent.parent
PHOTO_DIR = REPO / "ref_photos" / "before"
OUT_DIR   = Path(__file__).parent
DEBUG_DIR = OUT_DIR / "debug_h6"
DEBUG_DIR.mkdir(exist_ok=True)

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]

# Reuse blob detection from H5 verbatim — no changes to the detection step
from analyze_facade_h5 import (
    find_front_photos,
    load_gray,
    estimate_facade_region,
    detect_window_blobs,
    fenestration_from_blobs,
)


# ── Row-occupancy projection ──────────────────────────────────────────────────

def build_row_occupancy(blobs: list, facade_h: int, facade_w: int) -> np.ndarray:
    """
    Build a 1-D array of length facade_h.
    occupancy[y] = number of blob pixels covering row y (blob bounding-box width).
    Each blob (x, y_blob, cw, ch) contributes `cw` to rows [y_blob, y_blob+ch).
    """
    occ = np.zeros(facade_h, dtype=np.float32)
    for (x, y_blob, cw, ch) in blobs:
        y_start = max(0, y_blob)
        y_end   = min(facade_h, y_blob + ch)
        occ[y_start:y_end] += cw
    return occ


def count_stories_from_occupancy(occ: np.ndarray, facade_h: int) -> tuple:
    """
    Find connected runs of 'occupied' rows and count those with sufficient height.

    A run is kept as a real story band if its vertical height ≥ MIN_RUN_FRAC × facade_h.
    This rejects:
      - Cornice artifacts: 1 small blob → run height = blob height = small fraction of facade
      - Sign/awning artifacts: thin horizontal bands
    And keeps:
      - Real windows: typical window height ≥ 10% of story height ≥ 8% of facade

    Returns (story_count, list_of_run_spans [(start, end), ...]).
    """
    MIN_RUN_FRAC = 0.06   # run must span ≥ 6% of facade height to count as a story band

    occupied = (occ > 0).astype(np.int32)

    # Label connected components in 1D
    labeled, n_labels = ndlabel(occupied)

    valid_runs = []
    for lbl in range(1, n_labels + 1):
        rows = np.where(labeled == lbl)[0]
        run_h = rows[-1] - rows[0] + 1
        if run_h >= facade_h * MIN_RUN_FRAC:
            valid_runs.append((int(rows[0]), int(rows[-1])))

    stories = max(1, min(8, len(valid_runs)))
    return stories, valid_runs


# ── Per-address processing ────────────────────────────────────────────────────

def save_debug_h6(address, photo_name, gray, occ, top, bottom, blobs, valid_runs, stories, fen_pct):
    slug = address.split(",")[0].replace(" ", "_")
    addr_debug = DEBUG_DIR / slug
    addr_debug.mkdir(exist_ok=True)

    facade_h = bottom - top
    vis = cv2.cvtColor(gray[top:bottom, :], cv2.COLOR_GRAY2BGR)
    for (x, y, cw, ch) in blobs:
        cv2.rectangle(vis, (x, y), (x + cw, y + ch), (0, 255, 0), 1)
    for (rs, re) in valid_runs:
        cv2.line(vis, (0, rs), (vis.shape[1], rs), (0, 0, 255), 2)
        cv2.line(vis, (0, re), (vis.shape[1], re), (255, 0, 0), 1)

    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    fig.suptitle(
        f"{address}\n{photo_name}  |  stories={stories}  runs={len(valid_runs)}  fen={fen_pct}%",
        fontsize=9,
    )

    axes[0].imshow(gray, cmap="gray")
    axes[0].axhline(top,    color="lime",   linewidth=1.5)
    axes[0].axhline(bottom, color="orange", linewidth=1.5)
    axes[0].set_title("Original", fontsize=8)
    axes[0].axis("off")

    axes[1].imshow(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB))
    axes[1].set_title("Blobs (green) + run bounds (red/blue)", fontsize=8)
    axes[1].axis("off")

    y_ax = np.arange(len(occ))
    axes[2].plot(occ, y_ax, color="steelblue", linewidth=0.8)
    for (rs, re) in valid_runs:
        axes[2].axhspan(rs, re, alpha=0.2, color="green")
    axes[2].invert_yaxis()
    axes[2].set_title(f"Row occupancy  {len(valid_runs)} bands", fontsize=8)
    axes[2].set_xlabel("horiz. blob px", fontsize=7)

    stem = Path(photo_name).stem
    out  = addr_debug / f"{stem}_h6.png"
    plt.tight_layout()
    plt.savefig(str(out), dpi=100, bbox_inches="tight")
    plt.close(fig)


def process_address(address: str) -> dict:
    photos = find_front_photos(address)
    if not photos:
        return {"address": address, "error": "no front photos found", "method": "H6"}

    story_list, fen_list, per_photo = [], [], []

    for path in photos:
        gray = load_gray(path)
        top, bottom = estimate_facade_region(gray)
        facade_h = bottom - top
        facade_w = gray.shape[1]

        if facade_h < 40:
            continue

        _, blobs = detect_window_blobs(gray, top, bottom)
        occ = build_row_occupancy(blobs, facade_h, facade_w)
        stories, valid_runs = count_stories_from_occupancy(occ, facade_h)
        fen_pct = fenestration_from_blobs(blobs, facade_h, facade_w)

        save_debug_h6(address, path.name, gray, occ, top, bottom,
                      blobs, valid_runs, stories, fen_pct)

        story_list.append(stories)
        fen_list.append(fen_pct)
        per_photo.append({
            "photo":   path.name,
            "stories": stories,
            "fen_pct": fen_pct,
            "n_blobs": len(blobs),
            "n_runs":  len(valid_runs),
            "run_heights_px": [re - rs for (rs, re) in valid_runs],
        })

    if not story_list:
        return {"address": address, "error": "no processable photos", "method": "H6"}

    return {
        "address":                      address,
        "method":                       "H6",
        "number_stories":               int(round(float(np.median(story_list)))),
        "wall_fenesteration_front_per":  round(float(np.median(fen_list)), 1),
        "n_photos":                     len(story_list),
        "per_photo":                    per_photo,
    }


def main():
    results = {}
    for addr in ADDRESSES:
        print(f"  {addr} ...", end=" ", flush=True)
        r = process_address(addr)
        results[addr] = r
        if "error" in r:
            print(f"ERROR: {r['error']}")
        else:
            print(f"stories={r['number_stories']}  fen={r['wall_fenesteration_front_per']}%"
                  f"  blobs={sum(p['n_blobs'] for p in r['per_photo'])}"
                  f"  runs={r['per_photo'][0].get('n_runs','?')}"
                  f"  ({r['n_photos']} photo(s))")

    out_path = OUT_DIR / "facade_cv_h6_output.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
