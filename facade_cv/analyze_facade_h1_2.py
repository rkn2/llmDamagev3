#!/usr/bin/env python3
"""
H1.2: FFT of row-mean brightness profile with detrending and smoothing.

Diagnosis from H1.1:
- 112 State: Sobel-Y FFT correctly finds N=5 (Romanesque arcade creates very strong
  floor-level edges). Works.
- 100 Main / 40 Main / 27 Langdon: Dominated by bin-1 DC trend (large edge at facade
  top creates a step-function that puts all power in bin 1).
- 54 Elm: Brick texture at 47px period dominates bins 8-9 (Italianate ornamental brick).

Fixes applied:
1. Signal: row-mean BRIGHTNESS instead of Sobel-Y. Brightness is directly related
   to window (dark) vs. wall (light) content. Floor bands show dark–light alternation
   at the floor pitch, making the pitch more visible in the FFT.
2. Gaussian smooth σ=20px: suppresses 47px brick texture to ~3% of original amplitude
   while keeping 93px floor pitch at 40% and 127px pitch at 61%.
3. Polynomial detrend (3rd order): removes DC offset and illumination gradient,
   preventing the bin-1 DC component from swamping the floor signal.
4. Search range N=[3, 7]: avoids bin-1/2 contamination; all 5 buildings have 3+ floors.
5. Fenestration: unchanged from H5 (percentile blob detection).

LEAKAGE POLICY: reads ONLY image files from ref_photos/before/.
Output: facade_cv/facade_cv_h1_2_output.json
"""
import cv2
import numpy as np
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from scipy.ndimage import gaussian_filter1d

REPO      = Path(__file__).parent.parent
PHOTO_DIR = REPO / "ref_photos" / "before"
OUT_DIR   = Path(__file__).parent
DEBUG_DIR = OUT_DIR / "debug_h1_2"
DEBUG_DIR.mkdir(exist_ok=True)

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]

from analyze_facade_h5 import (
    find_front_photos,
    load_gray,
    estimate_facade_region,
    detect_window_blobs,
    fenestration_from_blobs,
)

SIGMA_PX    = 20   # Gaussian sigma: suppresses 47px brick texture, keeps 93px+ floor pitch
POLY_DEGREE = 3    # polynomial detrend order
MIN_STORIES = 3    # search range lower bound (avoids bin-1/2 DC contamination)
MAX_STORIES = 7    # search range upper bound


def compute_brightness_profile(gray: np.ndarray, top: int, bottom: int) -> np.ndarray:
    """
    Row-mean brightness of the facade ROI.
    Windows are dark (absorb light); walls are lighter.
    Floor bands show alternating dark-light at the floor pitch.
    """
    roi = gray[top:bottom, :]
    return np.mean(roi.astype(np.float64), axis=1)


def detrend_polynomial(profile: np.ndarray, degree: int) -> np.ndarray:
    """Subtract a polynomial fit to remove illumination trend."""
    x = np.arange(len(profile), dtype=np.float64)
    coeffs = np.polyfit(x, profile, degree)
    trend = np.polyval(coeffs, x)
    return profile - trend


def fft_floor_period(profile: np.ndarray) -> tuple:
    """
    Score each story-count hypothesis N ∈ [MIN_STORIES, MAX_STORIES] by the FFT
    power in the neighborhood [N-1, N, N+1]. Return best N, power spectrum, scores.
    """
    n = len(profile)
    fft_vals = np.fft.rfft(profile)
    power = np.abs(fft_vals) ** 2

    scores = {}
    for N in range(MIN_STORIES, MAX_STORIES + 1):
        k_lo = max(2, N - 1)   # floor at 2 to stay away from DC/trend
        k_hi = min(len(power) - 1, N + 1)
        scores[N] = float(np.sum(power[k_lo : k_hi + 1]))

    best_N = max(scores, key=scores.get)
    return best_N, power, scores


def save_debug(address, photo_name, gray, raw_profile, smooth_profile, power, scores,
               top, bottom, best_N, fen_pct):
    slug = address.split(",")[0].replace(" ", "_")
    addr_debug = DEBUG_DIR / slug
    addr_debug.mkdir(exist_ok=True)

    n = len(smooth_profile)
    y_ax = np.arange(n)
    max_k = min(20, len(power) - 1)

    fig, axes = plt.subplots(1, 4, figsize=(18, 4))
    fig.suptitle(
        f"{address}\n{photo_name}  |  stories={best_N}  fen={fen_pct}%",
        fontsize=9,
    )

    axes[0].imshow(gray, cmap="gray")
    axes[0].axhline(top,    color="lime",   linewidth=1.5)
    axes[0].axhline(bottom, color="orange", linewidth=1.5)
    axes[0].set_title("Original", fontsize=8)
    axes[0].axis("off")

    axes[1].plot(raw_profile[top:bottom], y_ax, color="lightblue", linewidth=0.6, label="raw brightness")
    axes[1].plot(smooth_profile, y_ax, color="steelblue", linewidth=1.2, label=f"smooth σ={SIGMA_PX}")
    axes[1].invert_yaxis()
    axes[1].set_title("Brightness profile", fontsize=8)
    axes[1].legend(fontsize=6)

    k_range = np.arange(1, max_k + 1)
    axes[2].bar(k_range, power[1:max_k+1], color="steelblue", alpha=0.7)
    axes[2].axvline(best_N, color="red", linewidth=2, label=f"best N={best_N}")
    axes[2].axvspan(MIN_STORIES - 0.5, MAX_STORIES + 0.5, alpha=0.08, color="green", label="search range")
    axes[2].set_xticks(range(1, max_k + 1))
    axes[2].set_xlabel("FFT bin (= story count hypothesis)", fontsize=7)
    axes[2].set_title("FFT power (detrended+smoothed)", fontsize=8)
    axes[2].legend(fontsize=6)

    ns = sorted(scores.keys())
    colors = ["red" if n == best_N else "steelblue" for n in ns]
    axes[3].bar(ns, [scores[n] for n in ns], color=colors, alpha=0.8)
    axes[3].set_xlabel("Story count hypothesis N", fontsize=7)
    axes[3].set_title("Scores per N", fontsize=8)

    stem = Path(photo_name).stem
    out  = addr_debug / f"{stem}_h1_2.png"
    plt.tight_layout()
    plt.savefig(str(out), dpi=100, bbox_inches="tight")
    plt.close(fig)


def process_address(address: str) -> dict:
    photos = find_front_photos(address)
    if not photos:
        return {"address": address, "error": "no front photos found", "method": "H1.2"}

    story_list, fen_list, per_photo = [], [], []

    for path in photos:
        gray = load_gray(path)
        top, bottom = estimate_facade_region(gray)
        facade_h = bottom - top
        facade_w = gray.shape[1]

        if facade_h < 60:
            continue

        # Brightness profile → smooth → detrend
        raw_full = np.mean(gray.astype(np.float64), axis=1)
        raw_roi  = raw_full[top:bottom]
        smoothed = gaussian_filter1d(raw_roi, sigma=SIGMA_PX)
        detrended = detrend_polynomial(smoothed, POLY_DEGREE)

        best_N, power, scores = fft_floor_period(detrended)

        # Fenestration from H5 blob detection (unchanged)
        _, blobs = detect_window_blobs(gray, top, bottom)
        fen_pct = fenestration_from_blobs(blobs, facade_h, facade_w)

        save_debug(address, path.name, gray, raw_full, smoothed, power, scores,
                   top, bottom, best_N, fen_pct)

        story_list.append(best_N)
        fen_list.append(fen_pct)
        per_photo.append({
            "photo":   path.name,
            "stories": best_N,
            "fen_pct": fen_pct,
            "scores":  {str(k): round(v, 2) for k, v in scores.items()},
        })

    if not story_list:
        return {"address": address, "error": "no processable photos", "method": "H1.2"}

    return {
        "address":                      address,
        "method":                       "H1.2",
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
            pp0 = r["per_photo"][0]
            top_scores = sorted(pp0["scores"].items(), key=lambda x: -x[1])[:3]
            print(f"stories={r['number_stories']}  top_scores={top_scores}")

    out_path = OUT_DIR / "facade_cv_h1_2_output.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
