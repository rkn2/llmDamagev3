#!/usr/bin/env python3
"""
H5: Window-blob detection via global Otsu threshold + minimum-dimension filter.

Key fix over H1: Otsu finds a GLOBAL dark/bright split, so mortar joints (globally
a medium tone, not the darkest 50%) are excluded; only large, uniformly-dark window
openings pass.  A minimum WIDTH and HEIGHT constraint (not just area) eliminates
mortar joints (thin lines), awning soffits (too wide), and street blobs (too tall).

Window centroids are clustered by y-coordinate to count story rows.

LEAKAGE POLICY: reads ONLY image files from ref_photos/before/.
Output: facade_cv/facade_cv_h5_output.json
Debug:  facade_cv/debug_h5/{slug}/{photo}_h5.png
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
DEBUG_DIR = OUT_DIR / "debug_h5"
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
        raise ValueError(f"Could not load {path}")
    if img.ndim == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    elif img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


# ── Facade region ─────────────────────────────────────────────────────────────

def estimate_facade_region(gray: np.ndarray) -> tuple:
    """Sky-texture detection + fixed bottom crop."""
    h, w = gray.shape
    mid_l, mid_r = w // 4, 3 * w // 4
    row_std = np.array([float(np.std(gray[r, mid_l:mid_r])) for r in range(h)])
    facade_rows = np.where(row_std > 12.0)[0]
    top = int(facade_rows[0]) if len(facade_rows) > 0 else int(h * 0.08)
    top = max(top, int(h * 0.04))
    bottom = min(int(h * 0.82), h - 1)
    return top, bottom


# ── Window detection: Otsu + minimum-dimension filter ─────────────────────────

def detect_window_blobs(gray: np.ndarray, top: int, bottom: int) -> tuple:
    """
    Find window-sized dark rectangular blobs in the facade region.

    Strategy:
    1. Extract facade ROI
    2. CLAHE for local contrast
    3. Otsu threshold (global): separates dark windows from lighter brick
       — mortar joints are a medium global tone, not the darkest ~50%
    4. Morphological clean-up
    5. Contour filter: reject blobs that are too thin (mortar), too wide
       (full facade spans), or wrong aspect ratio

    Returns:
        binary_mask: uint8 (facade ROI size), white = window candidate
        contour_list: list of (x, y, w, h) bounding boxes accepted as windows
    """
    roi = gray[top:bottom, :]
    h_roi, w_roi = roi.shape

    # CLAHE to normalise brightness across facade
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(roi)

    # Percentile threshold: bottom 35% of facade pixel values = dark (windows).
    # More robust than Otsu when facade is in shadow (shifts entire distribution dark)
    # or when sky/street pull Otsu to the wrong midpoint.
    thresh_val = int(np.percentile(enhanced, 35))
    _, binary  = cv2.threshold(enhanced, thresh_val, 255, cv2.THRESH_BINARY_INV)

    # Open only (remove mortar-joint noise). Do NOT close — closing merges
    # adjacent same-row windows into a single wide blob that fails max_w filter.
    k_open = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k_open, iterations=2)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Window dimension criteria (fraction of facade ROI)
    min_w_frac, max_w_frac = 0.03, 0.55   # window: 3–55% of facade width
    min_h_frac, max_h_frac = 0.04, 0.45   # window: 4–45% of facade height

    accepted = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 30:
            continue
        x, y, cw, ch = cv2.boundingRect(cnt)
        w_frac = cw / w_roi
        h_frac = ch / h_roi
        if not (min_w_frac <= w_frac <= max_w_frac):
            continue
        if not (min_h_frac <= h_frac <= max_h_frac):
            continue
        aspect = cw / max(ch, 1)
        if not (0.2 <= aspect <= 5.0):    # window-like aspect ratios
            continue
        accepted.append((x, y, cw, ch))

    return binary, accepted


# ── Story count from window-blob y-centroids ──────────────────────────────────

def count_stories_from_blobs(blobs: list, facade_h_px: int) -> tuple:
    """
    Cluster window blob y-centroids into horizontal rows.
    Gap > 12% of facade height = story boundary.
    Returns (stories, sorted_row_center_ys).
    """
    if not blobs:
        return 1, []

    centroids_y = sorted(int(y + ch / 2) for (x, y, cw, ch) in blobs)

    gap_threshold = max(int(facade_h_px * 0.12), 5)
    rows = [[centroids_y[0]]]
    for cy in centroids_y[1:]:
        if cy - rows[-1][-1] <= gap_threshold:
            rows[-1].append(cy)
        else:
            rows.append([cy])

    row_centers = [int(np.mean(r)) for r in rows]
    stories = max(1, min(8, len(rows)))
    return stories, row_centers


# ── Fenestration from accepted window blobs ───────────────────────────────────

def fenestration_from_blobs(blobs: list, roi_h: int, roi_w: int) -> float:
    """Sum accepted window area / facade area × 100."""
    facade_area = float(roi_h * roi_w)
    if facade_area == 0:
        return 0.0
    window_area = sum(cw * ch for (x, y, cw, ch) in blobs)
    return round(min(95.0, (window_area / facade_area) * 100.0), 1)


# ── Debug ─────────────────────────────────────────────────────────────────────

def save_debug_h5(
    address, photo_name, gray, binary, top, bottom, blobs, row_centers, stories, fen_pct
):
    slug = address.split(",")[0].replace(" ", "_")
    addr_debug = DEBUG_DIR / slug
    addr_debug.mkdir(exist_ok=True)

    # Draw accepted windows on a color copy
    roi_h = bottom - top
    roi_w = gray.shape[1]
    vis = cv2.cvtColor(gray[top:bottom, :], cv2.COLOR_GRAY2BGR)
    for (x, y, cw, ch) in blobs:
        cv2.rectangle(vis, (x, y), (x + cw, y + ch), (0, 255, 0), 2)
    for rc in row_centers:
        cv2.line(vis, (0, rc), (roi_w, rc), (0, 0, 255), 2)

    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    fig.suptitle(
        f"{address}\n{photo_name}  |  stories={stories}  fen={fen_pct}%  blobs={len(blobs)}",
        fontsize=9,
    )

    axes[0].imshow(gray, cmap="gray")
    axes[0].axhline(top,    color="lime",   linewidth=1.5)
    axes[0].axhline(bottom, color="orange", linewidth=1.5)
    axes[0].set_title("Original", fontsize=8)
    axes[0].axis("off")

    axes[1].imshow(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB))
    axes[1].set_title(f"Accepted blobs (green) + row lines (red)", fontsize=8)
    axes[1].axis("off")

    axes[2].imshow(binary, cmap="gray")
    axes[2].set_title("Otsu dark mask (pre-filter)", fontsize=8)
    axes[2].axis("off")

    stem = Path(photo_name).stem
    out  = addr_debug / f"{stem}_h5.png"
    plt.tight_layout()
    plt.savefig(str(out), dpi=100, bbox_inches="tight")
    plt.close(fig)


# ── Per-address processing ────────────────────────────────────────────────────

def process_address(address: str) -> dict:
    photos = find_front_photos(address)
    if not photos:
        return {"address": address, "error": "no front photos found", "method": "H5"}

    story_list, fen_list, per_photo = [], [], []

    for path in photos:
        gray = load_gray(path)
        top, bottom = estimate_facade_region(gray)
        facade_h = bottom - top
        roi_h    = facade_h
        roi_w    = gray.shape[1]

        if facade_h < 40:
            continue

        binary, blobs = detect_window_blobs(gray, top, bottom)
        stories, row_centers = count_stories_from_blobs(blobs, facade_h)
        fen_pct = fenestration_from_blobs(blobs, roi_h, roi_w)

        save_debug_h5(address, path.name, gray, binary, top, bottom,
                      blobs, row_centers, stories, fen_pct)

        story_list.append(stories)
        fen_list.append(fen_pct)
        per_photo.append({
            "photo":   path.name,
            "stories": stories,
            "fen_pct": fen_pct,
            "n_blobs": len(blobs),
            "n_rows":  len(row_centers),
        })

    if not story_list:
        return {"address": address, "error": "no processable photos", "method": "H5"}

    return {
        "address":                      address,
        "method":                       "H5",
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
                  f"  ({r['n_photos']} photo(s))")

    out_path = OUT_DIR / "facade_cv_h5_output.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
