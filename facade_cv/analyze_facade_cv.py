#!/usr/bin/env python3
"""
H1: Horizontal edge-band projection for story counting + fenestration estimation.

LEAKAGE POLICY: This script reads ONLY image files from ref_photos/before/.
It MUST NOT import or read any pipeline JSON (visual_attributes.json,
critic_findings.json, address_assessments.json, building_attributes_auto.json,
generate_detail_pages.py). Evaluation against ground truth is in evaluate_cv.py.

Output: facade_cv/facade_cv_output.json
Debug:  facade_cv/debug/{address_slug}/{photo}_profile.png
"""
import cv2
import numpy as np
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from scipy.signal import find_peaks, savgol_filter

REPO      = Path(__file__).parent.parent        # llmDamagev3/
PHOTO_DIR = REPO / "ref_photos" / "before"      # READ-ONLY
OUT_DIR   = Path(__file__).parent               # facade_cv/
DEBUG_DIR = OUT_DIR / "debug"
DEBUG_DIR.mkdir(exist_ok=True)

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]


# ── Image loading ─────────────────────────────────────────────────────────────

def find_front_photos(address: str) -> list:
    addr_dir = PHOTO_DIR / address
    if not addr_dir.exists():
        return []
    return sorted([
        p for p in addr_dir.iterdir()
        if p.stem.lower().startswith("front")
        and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".avif")
    ])


def load_gray(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"cv2 could not load {path}")
    if img.ndim == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    elif img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


# ── Facade region detection ───────────────────────────────────────────────────

def estimate_facade_region(gray: np.ndarray) -> tuple:
    """
    Return (top_row, bottom_row) bounding the building facade in the image.

    Sky is nearly uniform → low per-row std.  Street/foreground is below.
    We scan from the top to find where meaningful facade texture begins,
    and cap the bottom at 82% of image height to exclude street clutter.
    """
    h, w = gray.shape
    mid_l, mid_r = w // 4, 3 * w // 4

    row_std = np.array([float(np.std(gray[r, mid_l:mid_r])) for r in range(h)])

    # First row with texture standard deviation > threshold = top of facade
    texture_threshold = 12.0
    facade_rows = np.where(row_std > texture_threshold)[0]
    top = int(facade_rows[0]) if len(facade_rows) > 0 else int(h * 0.10)
    top = max(top, int(h * 0.04))   # never clip less than 4% from top

    bottom = int(h * 0.82)          # exclude bottom 18% (street, vehicle, foreground)
    bottom = min(bottom, h - 1)

    return top, bottom


# ── Story count via horizontal edge-band projection ──────────────────────────

def horizontal_edge_profile(gray: np.ndarray, top: int, bottom: int) -> np.ndarray:
    """
    Compute 1-D row-wise horizontal-edge-energy profile over the facade region.

    Sobel Y emphasises horizontal edges (floor slabs, spandrel bands, window sills).
    A Savitzky-Golay smooth removes pixel-level noise while preserving sharp peaks.
    """
    roi = gray[top:bottom, :]
    sobel_y = cv2.Sobel(roi.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    profile = np.abs(sobel_y).sum(axis=1)

    n = len(profile)
    window = max(5, min(51, (n // 15) | 1))   # odd, ~1/15th of facade height
    if n > window:
        profile = savgol_filter(profile, window_length=window, polyorder=2)

    return profile


def count_stories_from_profile(profile: np.ndarray, facade_h_px: int) -> tuple:
    """
    Detect prominent horizontal-edge peaks (floor/spandrel lines) and convert
    to a story count.

    Returns (story_count, peak_indices_in_profile).

    Physical assumption: minimum floor height ≥ 2.5 m; for a 3–5 story building
    the facade occupies ~8–18 m → each story is ≥ ~15 % of facade pixels.
    peaks represent floor separators; stories = peaks + 1.
    """
    if len(profile) < 10:
        return 1, []

    norm = profile / (profile.max() + 1e-6)

    # Minimum spacing: each story ≥ 12 % of facade height
    min_dist = max(int(facade_h_px * 0.12), 5)

    # Prominent peaks: must stand out by at least 20 % of max above their base
    peaks, _ = find_peaks(norm, distance=min_dist, prominence=0.18)

    # Clamp to plausible range
    stories = max(1, min(8, len(peaks) + 1))
    return stories, list(peaks)


# ── Fenestration via window contour detection ─────────────────────────────────

def estimate_fenestration(gray: np.ndarray, top: int, bottom: int) -> float:
    """
    Estimate front-facade fenestration percentage.

    Windows are typically darker than surrounding masonry/brick (especially
    in pre-flood daylight photos) and form rectangular shapes.

    Method:
      1. CLAHE contrast enhancement
      2. Adaptive threshold → dark-region mask
      3. Morphological cleaning
      4. Filter contours by area + aspect ratio
      5. total_window_area / facade_area × correction factor
    """
    roi = gray[top:bottom, :]
    h_roi, w_roi = roi.shape
    facade_area = float(h_roi * w_roi)

    # CLAHE to equalise brightness variations across the facade
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(roi)

    # Adaptive threshold: inverted → windows become white blobs
    binary = cv2.adaptiveThreshold(
        enhanced, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=35,
        C=10,
    )

    # Morphological clean-up: close small gaps inside windows, remove tiny noise
    k3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, k3, iterations=2)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k3, iterations=1)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_area = facade_area * 0.003   # ignore specs < 0.3 % of facade
    max_area = facade_area * 0.25    # ignore whole-facade blobs

    window_px = 0.0
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if not (min_area <= area <= max_area):
            continue
        _, _, cw, ch = cv2.boundingRect(cnt)
        aspect = cw / max(ch, 1)
        if 0.25 <= aspect <= 5.0:    # window-like, not a thin horizontal line
            window_px += area

    fen_pct = min(95.0, (window_px / facade_area) * 100.0)
    return round(fen_pct, 1)


# ── Debug plots ───────────────────────────────────────────────────────────────

def save_debug(
    address: str,
    photo_name: str,
    gray: np.ndarray,
    profile: np.ndarray,
    top: int,
    bottom: int,
    peaks: list,
    stories: int,
    fen_pct: float,
):
    slug = address.split(",")[0].replace(" ", "_")
    addr_debug = DEBUG_DIR / slug
    addr_debug.mkdir(exist_ok=True)

    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    fig.suptitle(f"{address}\n{photo_name}  |  stories={stories}  fen={fen_pct}%", fontsize=9)

    # Panel 1: original with facade strip
    axes[0].imshow(gray, cmap="gray")
    axes[0].axhline(top,    color="lime",   linewidth=1.5, label=f"top={top}")
    axes[0].axhline(bottom, color="orange", linewidth=1.5, label=f"bot={bottom}")
    axes[0].set_title("Facade region", fontsize=8)
    axes[0].legend(fontsize=6)
    axes[0].axis("off")

    # Panel 2: edge profile + detected peaks
    y_ax = np.arange(len(profile))
    axes[1].plot(profile, y_ax, color="steelblue", linewidth=0.8)
    for pk in peaks:
        axes[1].axhline(pk, color="red", linewidth=1, alpha=0.7)
    axes[1].invert_yaxis()
    axes[1].set_title(f"Edge profile  peaks={len(peaks)}", fontsize=8)
    axes[1].set_xlabel("edge energy", fontsize=7)
    axes[1].set_ylabel("row (facade-relative)", fontsize=7)

    # Panel 3: adaptive threshold mask
    roi = gray[top:bottom, :]
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(roi)
    binary = cv2.adaptiveThreshold(
        enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 35, 10
    )
    axes[2].imshow(binary, cmap="gray")
    axes[2].set_title("Window mask (adaptive thresh)", fontsize=8)
    axes[2].axis("off")

    stem = Path(photo_name).stem
    out = addr_debug / f"{stem}_profile.png"
    plt.tight_layout()
    plt.savefig(str(out), dpi=100, bbox_inches="tight")
    plt.close(fig)
    return str(out)


# ── Per-address processing ────────────────────────────────────────────────────

def process_address(address: str) -> dict:
    photos = find_front_photos(address)
    if not photos:
        return {"address": address, "error": "no front photos found", "method": "H1"}

    story_list, fen_list, per_photo = [], [], []

    for path in photos:
        gray = load_gray(path)
        top, bottom = estimate_facade_region(gray)
        facade_h = bottom - top

        if facade_h < 40:
            continue

        profile = horizontal_edge_profile(gray, top, bottom)
        stories, peaks = count_stories_from_profile(profile, facade_h)
        fen_pct = estimate_fenestration(gray, top, bottom)

        save_debug(address, path.name, gray, profile, top, bottom, peaks, stories, fen_pct)

        story_list.append(stories)
        fen_list.append(fen_pct)
        per_photo.append({"photo": path.name, "stories": stories,
                          "fen_pct": fen_pct, "n_peaks": len(peaks)})

    if not story_list:
        return {"address": address, "error": "no processable photos", "method": "H1"}

    return {
        "address":                     address,
        "method":                      "H1",
        "number_stories":              int(round(float(np.median(story_list)))),
        "wall_fenesteration_front_per": round(float(np.median(fen_list)), 1),
        "n_photos":                    len(story_list),
        "per_photo":                   per_photo,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

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
                  f"  ({r['n_photos']} photo(s))")

    out_path = OUT_DIR / "facade_cv_output.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
