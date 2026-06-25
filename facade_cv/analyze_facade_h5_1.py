#!/usr/bin/env python3
"""
H5.1: Window-blob detection with top-margin cornice exclusion.

Change from H5: before centroid clustering, drop blobs whose centroid_y is in the
top 9% of the facade ROI height. This removes cornice artifacts that sit right at
the roofline (centroid ~4% for 54 Elm) while preserving real top-floor windows
(lowest real centroid observed: 10% for 100 Main).

Evidence: diagnostic centroid dump showed 54 Elm cornice at 4%, 100 Main top floor
at 10%, 40 Main artifact blobs at 5–7% (removing them still yields 3 rows from
the remaining blobs), 27 Langdon all blobs at 24%+.

LEAKAGE POLICY: reads ONLY image files from ref_photos/before/.
Output: facade_cv/facade_cv_h5_1_output.json
"""
import numpy as np
import json
from pathlib import Path

from analyze_facade_h5 import (
    ADDRESSES,
    OUT_DIR,
    DEBUG_DIR,
    find_front_photos,
    load_gray,
    estimate_facade_region,
    detect_window_blobs,
    fenestration_from_blobs,
    save_debug_h5,
)

TOP_MARGIN_FRAC = 0.09   # drop blobs with centroid_y < 9% of facade_h


def count_stories_h5_1(blobs: list, facade_h_px: int) -> tuple:
    """H5 centroid clustering with top-margin cornice filter."""
    if not blobs:
        return 1, []

    # Drop blobs whose centroid is in the top 9% (cornice artifacts)
    margin_px = facade_h_px * TOP_MARGIN_FRAC
    filtered = [(x, y, cw, ch) for (x, y, cw, ch) in blobs
                if (y + ch / 2) >= margin_px]

    if not filtered:
        return 1, []

    centroids_y = sorted(int(y + ch / 2) for (x, y, cw, ch) in filtered)

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


def process_address(address: str) -> dict:
    photos = find_front_photos(address)
    if not photos:
        return {"address": address, "error": "no front photos found", "method": "H5.1"}

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
        stories, row_centers = count_stories_h5_1(blobs, facade_h)
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
        return {"address": address, "error": "no processable photos", "method": "H5.1"}

    return {
        "address":                      address,
        "method":                       "H5.1",
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

    out_path = OUT_DIR / "facade_cv_h5_1_output.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
