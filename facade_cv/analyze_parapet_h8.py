#!/usr/bin/env python3
"""
H8: Top-band Sobel-Y analysis for parapet and soffit/cornice detection.

Hypothesis:
  - parapet_present: the parapet (solid wall above roof) shows as a LOW-activity
    zone in the top ~15% of the facade Sobel-Y profile (no windows = no edges).
  - soffit_present_u: a projecting cornice/soffit shows as a HIGH peak near the
    top (dark underside of overhang = strong horizontal edge).

Evidence from probe (top-15% Sobel mean / rest-of-facade mean ratio):
  100 Main  (soffit=yes): ratio=0.59  ← LOW despite soffit
  112 State (soffit=no):  ratio=0.44  ← LOW (consistent with no soffit)
  27 Langdon(soffit=no):  ratio=0.66  ← matches expectation
  40 Main   (soffit=no):  ratio=1.61  ← HIGH despite no soffit (arched windows near top)
  54 Elm    (soffit=yes): ratio=1.15  ← elevated, but not clearly above threshold

Result: HYPOTHESIS REJECTED.
  - No threshold on top-band Sobel ratio reliably separates soffit=yes from soffit=no.
  - Root cause: 40 Main's arched 2nd-floor windows fall in the top band and produce
    high Sobel activity unrelated to a soffit. 100 Main's cornice is decorative and
    projects less than its window arches.
  - For parapet detection: all 5 buildings have parapets (binary discrimination not
    possible; cannot calibrate a threshold from this dataset).

Decision: No changes to visual_attributes.json from this analysis.
  - parapet_present: all True, validated by satellite (54 Elm) and photos (others).
  - soffit_present_u: 100 Main and 54 Elm = "yes" (confirmed in NOTES_OVERRIDE),
    112 State / 27 Langdon / 40 Main = "no" (per photo inspection, validated by critic).
  - parapet_height_m: 112 State = "un" (mansard, not a flat-roof parapet),
    others = "0.6" (critic did not flag).

LEAKAGE POLICY: reads only image files from ref_photos/before/.
"""

import sys, numpy as np, cv2
from scipy.ndimage import gaussian_filter1d
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analyze_facade_h5 import find_front_photos, load_gray, estimate_facade_region

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]

KNOWN = {
    "100 Main St, Montpelier, VT 05602":   "soffit=yes",
    "112 State St, Montpelier, VT 05602":  "soffit=no ",
    "27 Langdon St, Montpelier, VT 05602": "soffit=no ",
    "40 Main St, Montpelier, VT 05602":    "soffit=no ",
    "54 Elm St, Montpelier, VT 05602":     "soffit=yes",
}

TOP_FRAC = 0.15
SIGMA    = 8


def probe():
    print("H8 parapet/soffit probe (top-band Sobel ratio)\n")
    print(f"{'building':<15} {'known':<11} {'top15_mean':>10} {'rest_mean':>9} {'ratio':>6}")
    for addr in ADDRESSES:
        label = addr.split(",")[0]
        photos = find_front_photos(addr)
        if not photos:
            print(f"{label:<15} no photos")
            continue

        top15s, rests = [], []
        for p in photos:
            gray = load_gray(p)
            top, bot = estimate_facade_region(gray)
            h = bot - top
            roi = gray[top:bot, :]
            gy = cv2.Sobel(roi, cv2.CV_64F, 0, 1, ksize=3)
            prof = gaussian_filter1d(np.mean(np.abs(gy), axis=1), sigma=SIGMA)
            t15 = max(1, int(h * TOP_FRAC))
            top15s.append(prof[:t15].mean())
            rests.append(prof[t15:].mean())

        tm = np.mean(top15s)
        rm = np.mean(rests)
        ratio = tm / rm if rm > 0 else 0
        print(f"{label:<15} {KNOWN[addr]:<11} {tm:>10.1f} {rm:>9.1f} {ratio:>6.2f}")

    print("\nConclusion: no reliable threshold — see module docstring.")


if __name__ == "__main__":
    probe()
