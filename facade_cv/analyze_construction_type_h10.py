#!/usr/bin/env python3
"""
H10: Construction type (wood frame vs. URM) from facade photos.

HYPOTHESIS REJECTED after three probes.

Three signals tested; none separates 100 Main (wood frame) from URM buildings:

  Signal A -- Fine-scale Sobel-Y FFT (3-8 px clapboard band):
    wood_frame=0.041  URM=0.040-0.064
    FAILS: dark maroon paint kills clapboard edge contrast. URM buildings with
    decorative string courses / keystones contribute comparable or more energy.

  Signal B -- Brick-red HSV pixel fraction:
    100 Main (wood)=0.226  112 State (URM)=0.133  others=0.35-0.39
    FAILS: dark maroon paint on 100 Main overlaps brick-red HSV range.
    Photo lighting variation makes 112 State read as grey (0.133 < wood 0.226).

  Signal C -- Horizontal/vertical Sobel ratio in non-window wall zone:
    wood_frame=1.32  URM=0.89-1.67  (no separating threshold)
    FAILS: horizontal elements common to all facades (window sills, cornices,
    string courses) push H/V > 1 for URM buildings too.

OSM building:material tags: queried for all 5 buildings -- NONE populated.

Root cause: the only non-URM building (100 Main, dark maroon clapboard) is the
hardest possible case -- paint-to-board contrast is near zero at Street View
distances, and the siding colour mimics brick. Individual clapboard boards at
~10-15 cm (~4-6 px) are detectable in lighter painted or natural wood, but
not here.

Decision: NO changes to construction_type_u in visual_attributes.json.
  LLM values are correct for all 5 buildings (4 URM, 1 wood frame).
  A reliable CV approach would require close-up texture patches (not available)
  or a trained classifier on many buildings.

LEAKAGE POLICY: reads only image files from ref_photos/before/.
Debug images remain in facade_cv/debug_h10/ for reference.
"""

from __future__ import annotations
import json, sys
import numpy as np, cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from scipy.ndimage import gaussian_filter1d

sys.path.insert(0, str(Path(__file__).parent))
from analyze_facade_h5 import (
    find_front_photos, load_gray,
    estimate_facade_region, detect_window_blobs,
)

REPO      = Path(__file__).parent.parent
OUT_DIR   = Path(__file__).parent
DEBUG_DIR = OUT_DIR / "debug_h10"
DEBUG_DIR.mkdir(exist_ok=True)

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]

KNOWN = {
    "100 Main St, Montpelier, VT 05602":   "wood_frame",
    "112 State St, Montpelier, VT 05602":  "URM",
    "27 Langdon St, Montpelier, VT 05602": "URM",
    "40 Main St, Montpelier, VT 05602":    "URM",
    "54 Elm St, Montpelier, VT 05602":     "URM",
}

# Calibration constants (set after probe run)
CLAPBOARD_ENERGY_THRESHOLD = None   # TBD from probe
BRICK_COLOR_THRESHOLD      = None   # TBD from probe


# ── Signal A: fine-scale horizontal texture ───────────────────────────────────

def clapboard_energy(gray: np.ndarray, top: int, bottom: int,
                     blobs: list) -> tuple[float, np.ndarray, np.ndarray]:
    """
    Energy of fine-scale horizontal-edge periodicity in the 3-8 px band.

    Steps:
      1. Extract facade ROI, mask out detected window blobs
      2. Fine Sobel-Y (ksize=3), σ=2 smoothing
      3. Row-mean projection
      4. FFT → find energy fraction in 3-8 px period band
    """
    roi_h = bottom - top
    roi   = gray[top:bottom, :]

    # Mask out windows so glass reflections don't pollute the texture signal
    mask = np.ones((roi_h, roi.shape[1]), dtype=bool)
    for (x, y, cw, ch) in blobs:
        y0, y1 = max(0, y), min(roi_h, y + ch)
        x0, x1 = max(0, x), min(roi.shape[1], x + cw)
        mask[y0:y1, x0:x1] = False

    gy   = cv2.Sobel(roi.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    gy   = np.abs(gy) * mask.astype(np.float32)
    gy_s = gaussian_filter1d(gy, sigma=2.0, axis=0)

    profile = gy_s.mean(axis=1)   # row-mean, length = roi_h

    # FFT — convert period to index: period P → freq index = roi_h / P
    fft_mag = np.abs(np.fft.rfft(profile))
    freqs   = np.fft.rfftfreq(roi_h)   # cycles / pixel

    # Period band: 3–8 px → freq band: 1/8 to 1/3
    band = (freqs >= 1/8) & (freqs <= 1/3)
    band_energy = float(fft_mag[band].sum())
    total_energy = float(fft_mag[1:].sum()) + 1e-6   # skip DC

    clap_frac = band_energy / total_energy
    return clap_frac, profile, fft_mag


# ── Signal B: brick colour fraction ──────────────────────────────────────────

def brick_color_fraction(bgr: np.ndarray, top: int, bottom: int,
                         blobs: list) -> tuple[float, np.ndarray]:
    """
    Fraction of non-window facade pixels in the brick-red HSV range.

    Brick hue: H in [0, 12] or [165, 179] (red-orange in OpenCV 0-179 range)
    Saturation: S > 40
    Value:      V > 40  (not shadow-black)
    """
    roi_bgr = bgr[top:bottom, :].copy()
    roi_h, roi_w = roi_bgr.shape[:2]

    # Mask out windows
    win_mask = np.zeros((roi_h, roi_w), dtype=np.uint8)
    for (x, y, cw, ch) in blobs:
        y0, y1 = max(0, y), min(roi_h, y + ch)
        x0, x1 = max(0, x), min(roi_w, x + cw)
        win_mask[y0:y1, x0:x1] = 255

    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    brick_hue = (H <= 12) | (H >= 165)
    brick_sat = S > 40
    brick_val = V > 40
    not_window = win_mask == 0

    brick_px   = (brick_hue & brick_sat & brick_val & not_window).sum()
    total_px   = not_window.sum()
    fraction   = float(brick_px) / max(total_px, 1)

    # Hue histogram for debug
    hue_hist, _ = np.histogram(H[not_window], bins=36, range=(0, 180))
    return fraction, hue_hist


# ── Signal C: horizontal/vertical Sobel ratio in inter-window wall zones ──────

def hv_ratio(gray: np.ndarray, top: int, bottom: int,
             blobs: list) -> tuple[float, np.ndarray, np.ndarray]:
    """
    Ratio of mean(|Sobel-Y|) / mean(|Sobel-X|) in non-window facade pixels.

    Hypothesis:
      Wood clapboard → strong horizontal lines between windows (Sobel-Y >> Sobel-X)
      Brick          → isotropic mortar pattern (sub-pixel at distance) → ratio ≈ 1
      String courses / cornices are also horizontal, but they're few compared to
      wall-to-wall clapboard runs.

    The key: evaluate ONLY in wall pixels (exclude windows + ≥8px border from
    window edges to avoid frame contamination).
    """
    roi = gray[top:bottom, :]
    roi_h, roi_w = roi.shape

    # Build wall mask: ones everywhere, zero within windows + border
    wall_mask = np.ones((roi_h, roi_w), dtype=np.uint8) * 255
    border = 8
    for (x, y, cw, ch) in blobs:
        y0 = max(0, y - border)
        y1 = min(roi_h, y + ch + border)
        x0 = max(0, x - border)
        x1 = min(roi_w, x + cw + border)
        wall_mask[y0:y1, x0:x1] = 0

    gy = np.abs(cv2.Sobel(roi.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3))
    gx = np.abs(cv2.Sobel(roi.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3))

    wall = wall_mask > 0
    mean_gy = float(gy[wall].mean()) if wall.any() else 0.0
    mean_gx = float(gx[wall].mean()) + 1e-6

    ratio = mean_gy / mean_gx

    return round(ratio, 4), gy, gx


# ── Per-photo analysis ────────────────────────────────────────────────────────

def analyse_photo(path: Path) -> dict:
    gray = load_gray(path)
    bgr  = cv2.imread(str(path))
    if bgr is None:
        bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    top, bottom  = estimate_facade_region(gray)
    _, blobs     = detect_window_blobs(gray, top, bottom)

    clap_frac, profile, fft_mag = clapboard_energy(gray, top, bottom, blobs)
    brick_frac, hue_hist        = brick_color_fraction(bgr, top, bottom, blobs)
    hv, gy, gx                 = hv_ratio(gray, top, bottom, blobs)

    return {
        "clap_energy_frac": round(clap_frac, 5),
        "brick_color_frac": round(brick_frac, 4),
        "hv_ratio":         hv,
        "profile":  profile,
        "fft_mag":  fft_mag,
        "hue_hist": hue_hist,
        "top": top, "bottom": bottom,
        "gray": gray, "gy": gy, "gx": gx,
    }


# ── Debug image ───────────────────────────────────────────────────────────────

def save_debug(address: str, photo_name: str, results: list[dict],
               known: str) -> None:
    slug  = address.split(",")[0].replace(" ", "_")
    label = address.split(",")[0]

    fig, axes = plt.subplots(len(results), 4,
                             figsize=(18, 4 * len(results)),
                             squeeze=False)
    fig.suptitle(f"{label}  |  known={known}", fontsize=10)

    for i, r in enumerate(results):
        top, bottom = r["top"], r["bottom"]
        roi_h = bottom - top
        freqs = np.fft.rfftfreq(roi_h)

        # Panel 0: Sobel-Y vs Sobel-X (wall zone)
        gy_c = np.clip(r["gy"], 0, np.percentile(r["gy"], 98))
        gx_c = np.clip(r["gx"], 0, np.percentile(r["gx"], 98))
        side_by_side = np.hstack([gy_c, gx_c])
        axes[i][0].imshow(side_by_side, cmap="hot", aspect="auto")
        axes[i][0].set_title(
            f"Sobel-Y | Sobel-X  (H/V ratio={r['hv_ratio']:.3f})", fontsize=7)
        axes[i][0].axis("off")
        axes[i][0].axvline(r["gy"].shape[1], color="cyan", linewidth=1)

        # Panel 1: FFT of row profile
        axes[i][1].plot(freqs[1:], r["fft_mag"][1:], linewidth=0.8, color="steelblue")
        axes[i][1].axvspan(1/8, 1/3, color="orange", alpha=0.3,
                           label="clapboard band (3-8px)")
        axes[i][1].set_xlabel("Freq (cyc/px)")
        axes[i][1].set_ylabel("|FFT|")
        axes[i][1].set_title(f"FFT clap_frac={r['clap_energy_frac']:.4f}", fontsize=7)
        axes[i][1].legend(fontsize=6)
        axes[i][1].set_xlim(0, 0.5)

        # Panel 2: Hue histogram
        bin_edges = np.linspace(0, 180, 37)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
        axes[i][2].bar(bin_centers, r["hue_hist"], width=5, color="tomato", alpha=0.7)
        axes[i][2].axvspan(0, 12,  color="red",   alpha=0.2, label="brick red")
        axes[i][2].axvspan(165, 180, color="red", alpha=0.2)
        axes[i][2].set_xlabel("Hue (OpenCV 0-179)")
        axes[i][2].set_title(f"Hue  brick_frac={r['brick_color_frac']:.3f}", fontsize=7)
        axes[i][2].legend(fontsize=6)

        # Panel 3: Row-mean of Sobel-Y vs Sobel-X in wall zone
        gy_prof = r["gy"].mean(axis=1)
        gx_prof = r["gx"].mean(axis=1)
        axes[i][3].plot(gy_prof, np.arange(roi_h), color="red",  linewidth=0.8,
                        label=f"Sobel-Y (horiz)")
        axes[i][3].plot(gx_prof, np.arange(roi_h), color="blue", linewidth=0.8,
                        label=f"Sobel-X (vert)")
        axes[i][3].invert_yaxis()
        axes[i][3].set_xlabel("Mean gradient magnitude")
        axes[i][3].set_title("Row profiles", fontsize=7)
        axes[i][3].legend(fontsize=6)

    plt.tight_layout()
    out_path = DEBUG_DIR / f"{slug}_h10.png"
    plt.savefig(str(out_path), dpi=100, bbox_inches="tight")
    plt.close(fig)
    return str(out_path)


# ── Main ─────────────────────────────────────────────────────────────────────

def process(address: str) -> dict:
    photos = find_front_photos(address)
    if not photos:
        return {"address": address, "error": "no front photos"}

    per_photo = []
    for p in photos:
        r = analyse_photo(p)
        per_photo.append(r)

    clap_median   = float(np.median([r["clap_energy_frac"] for r in per_photo]))
    brick_median  = float(np.median([r["brick_color_frac"] for r in per_photo]))
    hv_median     = float(np.median([r["hv_ratio"]         for r in per_photo]))

    debug_path = save_debug(address, "", per_photo, KNOWN.get(address, "?"))

    known = KNOWN.get(address, "?")
    return {
        "address":          address,
        "known":            known,
        "clap_energy_frac": round(clap_median, 5),
        "brick_color_frac": round(brick_median, 4),
        "hv_ratio":         round(hv_median, 4),
        "n_photos":         len(photos),
        "debug":            debug_path,
    }


def main():
    results = {}
    for addr in ADDRESSES:
        label = addr.split(",")[0]
        print(f"\n{label}  (known={KNOWN[addr]})")
        r = process(addr)
        results[addr] = r
        print(f"  clap_energy_frac={r.get('clap_energy_frac','?')}  "
              f"brick_color_frac={r.get('brick_color_frac','?')}")

    out_path = OUT_DIR / "facade_cv_h10_probe.json"
    out_path.write_text(json.dumps(
        {k: {kk: vv for kk, vv in v.items()
             if kk not in ("profile", "fft_mag", "hue_hist", "gray")}
         for k, v in results.items()},
        indent=2
    ))
    print(f"\nWrote probe data to {out_path}")

    print("\n── Probe results (BEFORE threshold calibration) ─────────────────")
    print(f"{'building':<15}  {'known':>10}  {'clap_e':>8}  {'brick_c':>8}  {'H/V':>6}")
    for addr in ADDRESSES:
        r = results[addr]
        print(f"{addr.split(',')[0]:<15}  {r.get('known','?'):>10}  "
              f"{r.get('clap_energy_frac','?'):>8}  "
              f"{r.get('brick_color_frac','?'):>8}  "
              f"{r.get('hv_ratio','?'):>6}")

    print(f"\nDebug images → {DEBUG_DIR}/")
    print("\nNEXT: inspect debug images, check if clap_energy_frac separates")
    print("wood_frame (100 Main) from URM (others), then set CLAPBOARD_ENERGY_THRESHOLD.")


if __name__ == "__main__":
    main()
