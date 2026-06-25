#!/usr/bin/env python3
"""
H4: Autocorrelation of horizontal edge profile → dominant floor period → story count.

Key insight: true floor-to-floor bands repeat periodically → autocorrelation
produces a sharp peak at lag T (floor pitch).  Non-repeating architectural details
(cornices, awnings, decorative banding) appear as one-off spikes in the raw
edge profile but do NOT create ACF peaks — only periodic signals do.

Fenestration: column-average brightness + minimum-dimension window contour filter
to eliminate mortar-joint slivers (too thin) and sky/street blobs (too tall/wide).

LEAKAGE POLICY: reads ONLY image files from ref_photos/before/.
Output: facade_cv/facade_cv_h4_output.json
Debug:  facade_cv/debug_h4/{address_slug}/{photo}_h4.png
"""
import cv2
import numpy as np
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from scipy.signal import savgol_filter, correlate

REPO      = Path(__file__).parent.parent
PHOTO_DIR = REPO / "ref_photos" / "before"   # READ-ONLY
OUT_DIR   = Path(__file__).parent
DEBUG_DIR = OUT_DIR / "debug_h4"
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


# ── Facade region ─────────────────────────────────────────────────────────────

def estimate_facade_region(gray: np.ndarray) -> tuple:
    """
    Detect top of facade via row texture (sky = uniform).
    Crop bottom 20% as street/foreground.
    """
    h, w = gray.shape
    mid_l, mid_r = w // 4, 3 * w // 4
    row_std = np.array([float(np.std(gray[r, mid_l:mid_r])) for r in range(h)])
    facade_rows = np.where(row_std > 12.0)[0]
    top = int(facade_rows[0]) if len(facade_rows) > 0 else int(h * 0.08)
    top = max(top, int(h * 0.04))
    bottom = int(h * 0.82)
    return top, min(bottom, h - 1)


# ── H4 core: autocorrelation-based floor period detection ────────────────────

def horizontal_edge_profile(gray: np.ndarray, top: int, bottom: int) -> np.ndarray:
    """1-D row-wise sum of |Sobel_Y| over the facade region."""
    roi = gray[top:bottom, :]
    sobel_y = cv2.Sobel(roi.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    profile = np.abs(sobel_y).sum(axis=1)
    n = len(profile)
    window = max(5, min(31, (n // 15) | 1))
    if n > window:
        profile = savgol_filter(profile, window_length=window, polyorder=2)
    return profile


def autocorr_floor_period(profile: np.ndarray, facade_h_px: int) -> tuple:
    """
    Compute the 1-D autocorrelation of the horizontal edge profile and
    find the dominant positive lag (= floor-to-floor pitch T).

    Returns (T_in_pixels, acf_positive_half).

    Search range: lag in [10% of facade_h, 80% of facade_h].
    A 3-story building has T ≈ facade_h/3; a 5-story has T ≈ facade_h/5.
    We find the peak ACF lag in the plausible range.
    """
    # Mean-centre the profile so ACF decays away from 0
    p = profile - profile.mean()

    acf = correlate(p, p, mode="full")
    n = len(profile)
    acf_pos = acf[n:]          # positive lags starting at lag=1

    min_lag = max(int(facade_h_px * 0.10), 3)
    max_lag = min(int(facade_h_px * 0.82), len(acf_pos) - 1)

    if max_lag <= min_lag:
        return None, acf_pos

    search = acf_pos[min_lag:max_lag]
    dominant_offset = int(np.argmax(search))
    T = min_lag + dominant_offset

    return T, acf_pos


def stories_from_period(T: int, facade_h_px: int) -> int:
    """stories = facade_height / floor_period, clamped to [1, 8]."""
    if T is None or T <= 0:
        return 1
    raw = facade_h_px / T
    return max(1, min(8, int(round(raw))))


# ── Fenestration: column-profile + minimum-dimension contour filter ──────────

def estimate_fenestration_h4(gray: np.ndarray, top: int, bottom: int) -> float:
    """
    Improved fenestration estimate.

    1. CLAHE enhance
    2. Adaptive threshold (inverted — windows typically darker than brick in daylight)
       with a LARGE block size (71px) so mortar joints (1-2px) are below the local avg
    3. Morphological cleanup
    4. Filter contours by MINIMUM WIDTH AND HEIGHT (not just area) to reject:
       - Mortar joints: thin horizontal lines (height too small)
       - Sky reflections: too tall
       - Sign/awning spans: too wide
    5. window_px / facade_px × 100
    """
    roi = gray[top:bottom, :]
    h_roi, w_roi = roi.shape
    facade_px = float(h_roi * w_roi)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(roi)

    # Large block size: ~15% of facade height (avoids picking up mortar joints)
    block = max(71, ((h_roi // 7) | 1))
    binary = cv2.adaptiveThreshold(
        enhanced, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=block,
        C=8,
    )

    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, k, iterations=2)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN,  k, iterations=1)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Minimum dimensions: window must be at least 3% of facade in BOTH width and height
    min_w = w_roi * 0.03
    min_h = h_roi * 0.03
    max_w = w_roi * 0.70   # reject near-full-width blobs (whole floor slabs)
    max_h = h_roi * 0.60   # reject near-full-height blobs

    window_px = 0.0
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 10:
            continue
        x, y, cw, ch = cv2.boundingRect(cnt)
        if cw < min_w or ch < min_h:
            continue
        if cw > max_w or ch > max_h:
            continue
        aspect = cw / max(ch, 1)
        if 0.2 <= aspect <= 6.0:   # windows can be wide or tall, not extreme slivers
            window_px += area

    return round(min(95.0, (window_px / facade_px) * 100.0), 1)


# ── Debug ─────────────────────────────────────────────────────────────────────

def save_debug_h4(
    address, photo_name, gray, profile, acf_pos, T, top, bottom, stories, fen_pct
):
    slug = address.split(",")[0].replace(" ", "_")
    addr_debug = DEBUG_DIR / slug
    addr_debug.mkdir(exist_ok=True)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    fig.suptitle(
        f"{address}\n{photo_name}  |  stories={stories}  T={T}px  fen={fen_pct}%",
        fontsize=9
    )

    # Panel 1: original + facade strip
    axes[0].imshow(gray, cmap="gray")
    axes[0].axhline(top,    color="lime",   linewidth=1.5)
    axes[0].axhline(bottom, color="orange", linewidth=1.5)
    axes[0].set_title("Facade region", fontsize=8)
    axes[0].axis("off")

    # Panel 2: edge profile
    y_ax = np.arange(len(profile))
    axes[1].plot(profile, y_ax, color="steelblue", linewidth=0.8)
    axes[1].invert_yaxis()
    axes[1].set_title("Horizontal edge profile", fontsize=8)
    axes[1].set_xlabel("edge energy", fontsize=7)
    axes[1].set_ylabel("row (facade-relative)", fontsize=7)

    # Panel 3: autocorrelation (positive lags only)
    facade_h = bottom - top
    search_end = min(int(facade_h * 0.85), len(acf_pos))
    lags = np.arange(search_end)
    axes[2].plot(lags, acf_pos[:search_end], color="darkorange", linewidth=0.8)
    if T is not None:
        axes[2].axvline(T, color="red", linewidth=1.5, linestyle="--",
                        label=f"T={T}px → {stories} stories")
        axes[2].legend(fontsize=7)
    axes[2].set_title("ACF positive lags", fontsize=8)
    axes[2].set_xlabel("lag (px)", fontsize=7)
    axes[2].set_ylabel("ACF", fontsize=7)

    stem = Path(photo_name).stem
    out  = addr_debug / f"{stem}_h4.png"
    plt.tight_layout()
    plt.savefig(str(out), dpi=100, bbox_inches="tight")
    plt.close(fig)


# ── Per-address ───────────────────────────────────────────────────────────────

def process_address(address: str) -> dict:
    photos = find_front_photos(address)
    if not photos:
        return {"address": address, "error": "no front photos found", "method": "H4"}

    story_list, fen_list, per_photo = [], [], []

    for path in photos:
        gray = load_gray(path)
        top, bottom = estimate_facade_region(gray)
        facade_h = bottom - top
        if facade_h < 40:
            continue

        profile = horizontal_edge_profile(gray, top, bottom)
        T, acf_pos = autocorr_floor_period(profile, facade_h)
        stories = stories_from_period(T, facade_h)
        fen_pct = estimate_fenestration_h4(gray, top, bottom)

        save_debug_h4(address, path.name, gray, profile, acf_pos, T,
                      top, bottom, stories, fen_pct)

        story_list.append(stories)
        fen_list.append(fen_pct)
        per_photo.append({
            "photo":   path.name,
            "stories": stories,
            "fen_pct": fen_pct,
            "T_px":    T,
            "facade_h_px": facade_h,
        })

    if not story_list:
        return {"address": address, "error": "no processable photos", "method": "H4"}

    return {
        "address":                     address,
        "method":                      "H4",
        "number_stories":              int(round(float(np.median(story_list)))),
        "wall_fenesteration_front_per": round(float(np.median(fen_list)), 1),
        "n_photos":                    len(story_list),
        "per_photo":                   per_photo,
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
                  f"  T={r['per_photo'][0].get('T_px','?')}px"
                  f"  ({r['n_photos']} photo(s))")

    out_path = OUT_DIR / "facade_cv_h4_output.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
