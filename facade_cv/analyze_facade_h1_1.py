#!/usr/bin/env python3
"""
H1.1: FFT-based floor-period detection from horizontal edge profile.

H4 (autocorrelation) failed because brick texture (38px period) created the tallest
ACF peak; global argmax always picked brick texture over floor pitch.

Key insight: floor pitch (93px for 5-story 112 State) is a LOWER FREQUENCY component
than brick texture (38px). By restricting the FFT power spectrum search to low-frequency
bins (periods corresponding to 2.5-8 stories), we find the floor pitch and ignore texture.

Algorithm:
1. Compute Sobel-Y gradient row profile (horizontal edge strength per row)
2. Smooth with Gaussian (sigma=3px) to suppress sub-floor noise
3. FFT power spectrum of the profile
4. For each candidate story count N in [2, 3, 4, 5, 6, 7, 8]:
   - Compute expected floor-pitch frequency bin k = facade_h / (facade_h/N) = N
   - Score = power at FFT bin N (plus ±1 bin for robustness)
5. stories = N with highest score
6. Fenestration: blob detection from H5 (percentile threshold)

LEAKAGE POLICY: reads ONLY image files from ref_photos/before/.
Output: facade_cv/facade_cv_h1_1_output.json
"""
import cv2
import numpy as np
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

REPO      = Path(__file__).parent.parent
PHOTO_DIR = REPO / "ref_photos" / "before"
OUT_DIR   = Path(__file__).parent
DEBUG_DIR = OUT_DIR / "debug_h1_1"
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


def compute_edge_profile(gray: np.ndarray, top: int, bottom: int) -> np.ndarray:
    """
    Horizontal Sobel-Y gradient magnitude averaged across each row of the facade ROI.
    Returns a 1D array of length (bottom - top).
    """
    roi = gray[top:bottom, :]
    sobel_y = cv2.Sobel(roi, cv2.CV_64F, 0, 1, ksize=3)
    profile = np.mean(np.abs(sobel_y), axis=1)
    return profile


def fft_floor_period(profile: np.ndarray, min_stories: int = 2, max_stories: int = 8) -> tuple:
    """
    Find the dominant floor-pitch frequency in the horizontal edge profile.

    The floor pitch for N stories = facade_h / N samples (period).
    In the FFT of an N-sample signal, this corresponds to frequency bin N.

    Approach: score each candidate story count by the FFT power in the bin
    neighborhood around that frequency. Return (stories, power_spectrum, scores).

    min_stories / max_stories: search range for story count.
    """
    n = len(profile)

    # Detrend and window before FFT (reduces spectral leakage from the facade edges)
    profile_demeaned = profile - np.mean(profile)
    window = np.hanning(n)
    windowed = profile_demeaned * window

    # FFT power spectrum
    fft_vals = np.fft.rfft(windowed)
    power = np.abs(fft_vals) ** 2

    # For candidate story count N, the expected fundamental FFT bin is:
    #   k_fund = N   (since period = n/N samples → frequency = N/n → bin = N)
    # We score the bin neighborhood [k-1, k, k+1] to handle non-integer periods.
    scores = {}
    for N in range(min_stories, max_stories + 1):
        k_center = N
        k_lo = max(1, k_center - 1)
        k_hi = min(len(power) - 1, k_center + 1)
        # Sum power in the bin neighborhood (fundamental + adjacent bins)
        scores[N] = float(np.sum(power[k_lo : k_hi + 1]))

    best_N = max(scores, key=scores.get)
    return best_N, power, scores


def save_debug_h1_1(address, photo_name, gray, profile, power, scores, top, bottom,
                    best_N, blobs, fen_pct):
    slug = address.split(",")[0].replace(" ", "_")
    addr_debug = DEBUG_DIR / slug
    addr_debug.mkdir(exist_ok=True)

    n = len(profile)
    y_ax = np.arange(n)

    fig, axes = plt.subplots(1, 4, figsize=(18, 4))
    fig.suptitle(
        f"{address}\n{photo_name}  |  stories={best_N}  fen={fen_pct}%",
        fontsize=9,
    )

    # Original image with facade bounds
    axes[0].imshow(gray, cmap="gray")
    axes[0].axhline(top,    color="lime",   linewidth=1.5)
    axes[0].axhline(bottom, color="orange", linewidth=1.5)
    axes[0].set_title("Original", fontsize=8)
    axes[0].axis("off")

    # Edge profile
    axes[1].plot(profile, y_ax, color="steelblue", linewidth=0.8)
    axes[1].invert_yaxis()
    axes[1].set_title("Horizontal edge profile", fontsize=8)
    axes[1].set_xlabel("edge strength", fontsize=7)

    # FFT power spectrum (low-freq only)
    max_k = min(20, len(power) - 1)
    k_range = np.arange(1, max_k + 1)
    axes[2].bar(k_range, power[1:max_k+1], color="steelblue", alpha=0.7)
    axes[2].axvline(best_N, color="red", linewidth=2, label=f"best N={best_N}")
    axes[2].set_xticks(range(1, max_k + 1))
    axes[2].set_xlabel("FFT bin (= story count hypothesis)", fontsize=7)
    axes[2].set_title("FFT power (low freq)", fontsize=8)
    axes[2].legend(fontsize=7)

    # Score bar chart
    ns = sorted(scores.keys())
    colors = ["red" if n == best_N else "steelblue" for n in ns]
    axes[3].bar(ns, [scores[n] for n in ns], color=colors, alpha=0.8)
    axes[3].set_xlabel("Story count hypothesis N", fontsize=7)
    axes[3].set_title("Scores per N", fontsize=8)

    stem = Path(photo_name).stem
    out  = addr_debug / f"{stem}_h1_1.png"
    plt.tight_layout()
    plt.savefig(str(out), dpi=100, bbox_inches="tight")
    plt.close(fig)


def process_address(address: str) -> dict:
    photos = find_front_photos(address)
    if not photos:
        return {"address": address, "error": "no front photos found", "method": "H1.1"}

    story_list, fen_list, per_photo = [], [], []

    for path in photos:
        gray = load_gray(path)
        top, bottom = estimate_facade_region(gray)
        facade_h = bottom - top
        facade_w = gray.shape[1]

        if facade_h < 40:
            continue

        profile = compute_edge_profile(gray, top, bottom)
        best_N, power, scores = fft_floor_period(profile)

        # Fenestration from H5 blob detection (unchanged)
        _, blobs = detect_window_blobs(gray, top, bottom)
        fen_pct = fenestration_from_blobs(blobs, facade_h, facade_w)

        save_debug_h1_1(address, path.name, gray, profile, power, scores,
                        top, bottom, best_N, blobs, fen_pct)

        story_list.append(best_N)
        fen_list.append(fen_pct)
        per_photo.append({
            "photo":   path.name,
            "stories": best_N,
            "fen_pct": fen_pct,
            "scores":  {str(k): round(v, 2) for k, v in scores.items()},
        })

    if not story_list:
        return {"address": address, "error": "no processable photos", "method": "H1.1"}

    return {
        "address":                      address,
        "method":                       "H1.1",
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

    out_path = OUT_DIR / "facade_cv_h1_1_output.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
