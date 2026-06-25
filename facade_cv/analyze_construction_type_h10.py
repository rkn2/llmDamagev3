#!/usr/bin/env python3
"""
H10: Construction type (wood frame vs. URM) from facade photos.

HYPOTHESIS REJECTED after five probes (Signals A, B, C, D-v1, D-v2).

All signals tested; none separates 100 Main (wood frame) from URM buildings:

  Signal A -- Fine-scale Sobel-Y FFT (3-8 px clapboard band):
    wood_frame=0.041  URM=0.040-0.064
    FAILS: dark maroon paint kills clapboard edge contrast. Close-up side
    photos bring brick coursing into the same 3-8px band (scale-dependent).

  Signal B -- Brick-red HSV pixel fraction:
    100 Main (wood)=0.226  112 State (URM)=0.133  others=0.35-0.39
    FAILS: dark maroon paint on 100 Main overlaps brick-red HSV range.
    Photo lighting variation makes 112 State read as grey (0.133 < wood 0.226).

  Signal C -- Horizontal/vertical Sobel ratio in non-window wall zone:
    wood_frame=1.32  URM=0.89-1.67  (no separating threshold)
    FAILS: horizontal elements (window sills, cornices, string courses)
    push H/V > 1 for URM buildings too.

  Signal D-v1 -- Long horizontal HoughLinesP density (full facade, ±5°):
    100 Main=0.78  112 State=0.58  27 Langdon=0.29
    40 Main=4.96  54 Elm=5.76  ← FALSE POSITIVES
    FAILS: commercial GF awnings at 40 Main and 54 Elm generate dense long
    horizontal lines. Side-view photos of 100 Main show clapboard at oblique
    angle → boards appear >5° from horizontal → not detected.

  Signal D-v2 -- Long horizontal HoughLinesP, upper 2/3 only, ±12°:
    100 Main=4.17 (Right side)  112 State=1.11  27 Langdon=5.06
    40 Main=7.43  54 Elm=12.21  ← WORSE FALSE POSITIVES
    FAILS: URM upper floors have horizontal architectural banding (sill bands,
    string courses, cornices, lintels) that produces even MORE long horizontal
    lines than the GF awnings. Relaxing to ±12° picks up oblique clapboard
    but also picks up all architectural horizontals in brick buildings.

OSM building:material tags: queried for all 5 buildings -- NONE populated.

Root cause (confirmed across all probes):
  1. 100 Main's dark maroon paint destroys clapboard texture contrast — boards
     are near-invisible in Sobel/FFT/color at Street View distance.
  2. URM buildings have abundant horizontal architectural elements at ALL floor
     levels (not just GF): no zone exclusion removes this interference.
  3. The one wood-frame building is the hardest possible case.

Decision: NO changes to construction_type_u in visual_attributes.json.
  LLM values are correct for all 5 buildings (4 URM, 1 wood frame).
  A reliable CV approach requires: close-up wall texture patches (not
  available here) OR a CNN trained on hundreds of labeled building photos.

LEAKAGE POLICY: reads only image files from ref_photos/before/.
Debug images remain in facade_cv/debug_h10/ for reference.
"""

from __future__ import annotations
import json, math, sys
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

PHOTO_DIR = Path(__file__).parent.parent / "ref_photos" / "before"

def find_all_photos(address: str) -> list[Path]:
    """Return all before photos (front + back + left + right), sorted."""
    addr_dir = PHOTO_DIR / address
    if not addr_dir.exists():
        return []
    return sorted([
        p for p in addr_dir.iterdir()
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".avif")
        and not p.stem.startswith(".")
        and "Copy" not in p.name          # skip duplicate copies
        and "Screenshot" not in p.name    # skip screenshots
    ])

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


# ── Signal D: long horizontal line density (scale-invariant) ─────────────────

def long_hline_density(gray: np.ndarray, top: int, bottom: int,
                       blobs: list, n_stories: int = 3) -> tuple[float, list]:
    """
    Density of long horizontal Hough line segments in the UPPER-FLOOR wall zone.

    Restricted to the top (n_stories-1)/n_stories of the facade to exclude
    ground-floor commercial awnings, fascia, and canopy elements which create
    spurious long horizontal lines in brick buildings.

    Angle tolerance ±12° (vs. naive ±5°) to handle oblique side-view photos
    where clapboard lines appear slightly tilted due to perspective.

    Returns:
      density: long_lines_per_100px_upper_facade_height
      long_lines: list of (x1,y1,x2,y2) in ROI coords for debug plotting
    """
    roi_h = bottom - top
    roi_w = gray.shape[1]

    # Upper facade zone: exclude bottom 1/n_stories (GF band)
    gf_top_px = int(roi_h * (n_stories - 1) / n_stories)
    upper_top  = 0
    upper_bot  = gf_top_px       # pixel row in ROI coords

    if upper_bot < 30:
        return 0.0, []

    min_span = max(10, int(roi_w * 0.20))

    # Build non-window mask for upper zone only
    upper_h = upper_bot
    mask = np.ones((upper_h, roi_w), dtype=np.uint8) * 255
    border = 8
    for (x, y, cw, ch) in blobs:
        if y + ch < upper_h:     # blob entirely in upper zone
            y0, y1 = max(0, y - border), min(upper_h, y + ch + border)
            x0, x1 = max(0, x - border), min(roi_w, x + cw + border)
            mask[y0:y1, x0:x1] = 0

    upper_roi = gray[top:top + upper_h, :]
    masked    = cv2.bitwise_and(upper_roi, upper_roi, mask=mask)
    edges     = cv2.Canny(masked, 30, 90)

    lines = cv2.HoughLinesP(edges, rho=1,
                            theta=np.pi / 180,
                            threshold=15,
                            minLineLength=min_span,
                            maxLineGap=6)

    long_lines = []
    if lines is not None:
        for seg in lines:
            x1, y1, x2, y2 = seg[0]
            dx, dy = abs(x2 - x1), abs(y2 - y1)
            length = math.sqrt(dx**2 + dy**2)
            if dx == 0:
                continue
            angle_deg = abs(math.degrees(math.atan2(dy, dx)))
            if angle_deg <= 12 and length >= min_span:
                long_lines.append((x1, y1, x2, y2))

    density = len(long_lines) / (upper_h / 100.0) if upper_h > 0 else 0.0
    return round(density, 3), long_lines


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

def analyse_photo(path: Path, n_stories: int = 3) -> dict:
    gray = load_gray(path)
    bgr  = cv2.imread(str(path))
    if bgr is None:
        bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    top, bottom  = estimate_facade_region(gray)
    _, blobs     = detect_window_blobs(gray, top, bottom)

    roi_h = bottom - top
    gf_top_px = int(roi_h * (n_stories - 1) / n_stories)

    clap_frac, profile, fft_mag = clapboard_energy(gray, top, bottom, blobs)
    brick_frac, hue_hist        = brick_color_fraction(bgr, top, bottom, blobs)
    hv, gy, gx                 = hv_ratio(gray, top, bottom, blobs)
    hline_density, long_lines  = long_hline_density(gray, top, bottom, blobs, n_stories)

    return {
        "clap_energy_frac": round(clap_frac, 5),
        "brick_color_frac": round(brick_frac, 4),
        "hv_ratio":         hv,
        "hline_density":    hline_density,
        "profile":  profile,
        "fft_mag":  fft_mag,
        "hue_hist": hue_hist,
        "long_lines": long_lines,
        "gf_top_px": gf_top_px,
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

        # Panel 3: Long horizontal lines overlaid on facade ROI (upper zone only)
        roi_vis = cv2.cvtColor(r["gray"][r["top"]:r["bottom"], :], cv2.COLOR_GRAY2RGB)
        gf_top = r.get("gf_top_px", roi_h)
        # draw GF boundary in blue
        cv2.line(roi_vis, (0, gf_top), (roi_vis.shape[1], gf_top), (80, 80, 255), 1)
        for (x1, y1, x2, y2) in r.get("long_lines", []):
            cv2.line(roi_vis, (x1, y1), (x2, y2), (255, 80, 0), 1)
        axes[i][3].imshow(roi_vis, aspect="auto")
        axes[i][3].set_title(
            f"Long horiz lines upper zone ±12°  density={r['hline_density']:.2f}/100px",
            fontsize=7)
        axes[i][3].axis("off")

    plt.tight_layout()
    out_path = DEBUG_DIR / f"{slug}_h10.png"
    plt.savefig(str(out_path), dpi=100, bbox_inches="tight")
    plt.close(fig)
    return str(out_path)


# ── Main ─────────────────────────────────────────────────────────────────────

def process(address: str, n_stories: int = 3) -> dict:
    photos = find_all_photos(address)
    if not photos:
        return {"address": address, "error": "no photos"}

    per_photo = []
    for p in photos:
        r = analyse_photo(p, n_stories)
        r["photo"] = p.name
        per_photo.append(r)
        print(f"    {p.name}: clap={r['clap_energy_frac']:.4f}  "
              f"hline_d={r['hline_density']:.2f}  "
              f"brick={r['brick_color_frac']:.3f}  hv={r['hv_ratio']:.3f}")

    # Max clapboard energy across all views: if ANY photo shows clapboard
    # periodicity, the building is wood frame. Brick has none in any view.
    # Use max hline_density as the primary signal: if ANY photo shows clapboard
    # line density, the building is wood frame.
    best_idx       = int(np.argmax([r["hline_density"] for r in per_photo]))
    hline_max      = float(per_photo[best_idx]["hline_density"])
    best_photo     = per_photo[best_idx]["photo"]

    clap_max     = float(max(r["clap_energy_frac"] for r in per_photo))
    clap_median  = float(np.median([r["clap_energy_frac"] for r in per_photo]))
    brick_median = float(np.median([r["brick_color_frac"] for r in per_photo]))
    hv_median    = float(np.median([r["hv_ratio"]         for r in per_photo]))

    # Debug: show only the best-signal photo to keep images manageable
    debug_path = save_debug(address, best_photo,
                            [per_photo[best_idx]], KNOWN.get(address, "?"))

    known = KNOWN.get(address, "?")
    return {
        "address":          address,
        "known":            known,
        "hline_density_max": round(hline_max, 3),
        "clap_energy_max":  round(clap_max, 5),
        "clap_energy_med":  round(clap_median, 5),
        "brick_color_frac": round(brick_median, 4),
        "hv_ratio":         round(hv_median, 4),
        "best_photo":       best_photo,
        "n_photos":         len(photos),
        "debug":            debug_path,
    }


def main():
    h7_path = OUT_DIR / "facade_cv_h7_output.json"
    story_counts: dict[str, int] = {}
    if h7_path.exists():
        h7 = json.loads(h7_path.read_text())
        story_counts = {addr: rec["number_stories"] for addr, rec in h7.items()}

    results = {}
    for addr in ADDRESSES:
        n = story_counts.get(addr, 3)
        label = addr.split(",")[0]
        print(f"\n{label}  (known={KNOWN[addr]}  n_stories={n})")
        r = process(addr, n)
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

    print("\n── Probe results — all photos, max long-hline density ───────────")
    print(f"{'building':<15}  {'known':>10}  {'hline_MAX':>10}  {'clap_MAX':>9}  {'best_photo'}")
    for addr in ADDRESSES:
        r = results[addr]
        print(f"{addr.split(',')[0]:<15}  {r.get('known','?'):>10}  "
              f"{r.get('hline_density_max','?'):>10}  "
              f"{r.get('clap_energy_max','?'):>9}  "
              f"{r.get('best_photo','?')}")

    print(f"\nDebug images (best photo per building) → {DEBUG_DIR}/")


if __name__ == "__main__":
    main()
