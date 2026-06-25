#!/usr/bin/env python3
"""
H2: Pretrained semantic segmentation (SegFormer-b0 on ADE20K) for
story counting and fenestration estimation.

ADE20K class "window" (class id looked up at runtime from model.config.id2label)
is used to extract a window mask.  Window centroids are clustered by y-coordinate
to count story rows = number_stories.  window_mask_area / facade_area =
wall_fenesteration_front_per.

LEAKAGE POLICY: reads ONLY image files from ref_photos/before/.
Does NOT import or read any pipeline JSON. Evaluation is in evaluate_cv.py.

Output: facade_cv/facade_cv_h2_output.json
Debug:  facade_cv/debug_h2/{address_slug}/{photo}_h2.png
"""
import cv2
import numpy as np
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

import torch
from PIL import Image
from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor

REPO      = Path(__file__).parent.parent
PHOTO_DIR = REPO / "ref_photos" / "before"   # READ-ONLY
OUT_DIR   = Path(__file__).parent
DEBUG_DIR = OUT_DIR / "debug_h2"
DEBUG_DIR.mkdir(exist_ok=True)

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]

MODEL_NAME = "nvidia/segformer-b0-finetuned-ade-512-512"


# ── Model loading (cached after first download) ───────────────────────────────

def load_model():
    print(f"  Loading {MODEL_NAME} (downloads on first run) ...", flush=True)
    processor = SegformerImageProcessor.from_pretrained(MODEL_NAME)
    model     = SegformerForSemanticSegmentation.from_pretrained(MODEL_NAME)
    model.eval()

    # Find "window" class id in ADE20K label set
    window_id = None
    for cid, label in model.config.id2label.items():
        if "window" in label.lower():
            window_id = int(cid)
            break
    if window_id is None:
        raise RuntimeError("Could not find 'window' class in model id2label. "
                           "Check model config.")
    print(f"  'window' = class {window_id} ({model.config.id2label[window_id]})", flush=True)
    return processor, model, window_id


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


def load_rgb_pil(path: Path) -> Image.Image:
    """Load as RGB PIL image (handles RGBA, AVIF, etc.)."""
    img = Image.open(path).convert("RGB")
    return img


# ── Facade region (reuse H1 sky-detection heuristic) ─────────────────────────

def estimate_facade_rows(h: int) -> tuple:
    """Simple crop: skip top 8% (sky) and bottom 18% (street/foreground)."""
    top    = int(h * 0.08)
    bottom = int(h * 0.82)
    return top, bottom


# ── SegFormer inference ───────────────────────────────────────────────────────

@torch.no_grad()
def segment_image(pil_img: Image.Image, processor, model) -> np.ndarray:
    """
    Run SegFormer and return an (H, W) uint8 label map at the ORIGINAL image
    resolution (upsampled from model's 128×128 output).
    """
    inputs  = processor(images=pil_img, return_tensors="pt")
    outputs = model(**inputs)

    # logits shape: (1, num_classes, H/4, W/4) → argmax → upsample
    logits = outputs.logits  # (1, 150, h/4, w/4)
    pred   = logits.argmax(dim=1)  # (1, h/4, w/4)

    # Upsample to original size
    orig_h, orig_w = pil_img.height, pil_img.width
    pred_up = torch.nn.functional.interpolate(
        pred.unsqueeze(1).float(),
        size=(orig_h, orig_w),
        mode="nearest",
    ).squeeze().long().numpy().astype(np.uint8)

    return pred_up


# ── Story count from window mask ──────────────────────────────────────────────

def count_stories_from_windows(
    window_mask: np.ndarray,
    top: int,
    bottom: int,
    facade_h_px: int,
) -> tuple:
    """
    Cluster window centroids by y-coordinate to find distinct story rows.

    Returns (story_count, row_y_list).

    Method:
      1. Find connected components in the window mask (facade region only)
      2. Get centroid y of each component
      3. 1-D cluster centroids with a gap threshold of ~10% facade height
         (gaps > this = story boundary)
      4. Count clusters = number_stories
    """
    roi_mask = window_mask[top:bottom, :]
    n_px = roi_mask.sum()
    if n_px < 50:
        return 1, []

    # Connected components of the window mask
    n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        roi_mask.astype(np.uint8), connectivity=8
    )

    if n_labels <= 1:
        return 1, []

    # Filter by minimum size (ignore tiny slivers)
    min_comp_px = facade_h_px * 2   # at least a thin horizontal band worth of pixels
    valid_ys = []
    for lbl in range(1, n_labels):
        area = stats[lbl, cv2.CC_STAT_AREA]
        if area >= min_comp_px:
            valid_ys.append(int(centroids[lbl, 1]))  # y centroid (relative to ROI)

    if not valid_ys:
        return 1, []

    valid_ys.sort()

    # Gap-based 1-D clustering: group centroids within 10% of facade height of each other
    gap_threshold = max(int(facade_h_px * 0.10), 5)
    rows = [[valid_ys[0]]]
    for y in valid_ys[1:]:
        if y - rows[-1][-1] <= gap_threshold:
            rows[-1].append(y)
        else:
            rows.append([y])

    row_centers = [int(np.mean(r)) for r in rows]
    stories = max(1, min(8, len(rows)))
    return stories, row_centers


# ── Fenestration from window mask ─────────────────────────────────────────────

def fenestration_from_mask(window_mask: np.ndarray, top: int, bottom: int) -> float:
    """Window pixel area / facade area within the detected facade region."""
    roi = window_mask[top:bottom, :]
    facade_area = roi.shape[0] * roi.shape[1]
    if facade_area == 0:
        return 0.0
    window_px = float(roi.sum())
    return round(min(95.0, (window_px / facade_area) * 100.0), 1)


# ── Debug visualisation ───────────────────────────────────────────────────────

def save_debug_h2(
    address: str,
    photo_name: str,
    pil_img: Image.Image,
    label_map: np.ndarray,
    window_mask: np.ndarray,
    top: int,
    bottom: int,
    row_centers: list,
    stories: int,
    fen_pct: float,
):
    slug = address.split(",")[0].replace(" ", "_")
    addr_debug = DEBUG_DIR / slug
    addr_debug.mkdir(exist_ok=True)

    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    fig.suptitle(f"{address}\n{photo_name}  |  stories={stories}  fen={fen_pct}%", fontsize=9)

    # Panel 1: original + facade strip + window row lines
    axes[0].imshow(pil_img)
    axes[0].axhline(top,    color="lime",   linewidth=1.5)
    axes[0].axhline(bottom, color="orange", linewidth=1.5)
    for rc in row_centers:
        axes[0].axhline(top + rc, color="red", linewidth=1.2, linestyle="--", alpha=0.8)
    axes[0].set_title(f"Facade strip + window rows", fontsize=8)
    axes[0].axis("off")

    # Panel 2: full segmentation map
    axes[1].imshow(label_map, cmap="tab20", vmin=0, vmax=149)
    axes[1].axhline(top,    color="lime",   linewidth=1.5)
    axes[1].axhline(bottom, color="orange", linewidth=1.5)
    axes[1].set_title("SegFormer label map (ADE20K)", fontsize=8)
    axes[1].axis("off")

    # Panel 3: window mask only
    axes[2].imshow(window_mask * 255, cmap="hot")
    axes[2].set_title(f"Window mask  ({fen_pct}% of facade)", fontsize=8)
    axes[2].axis("off")

    stem = Path(photo_name).stem
    out  = addr_debug / f"{stem}_h2.png"
    plt.tight_layout()
    plt.savefig(str(out), dpi=100, bbox_inches="tight")
    plt.close(fig)


# ── Per-address processing ────────────────────────────────────────────────────

def process_address(address: str, processor, model, window_id: int) -> dict:
    photos = find_front_photos(address)
    if not photos:
        return {"address": address, "error": "no front photos found", "method": "H2"}

    story_list, fen_list, per_photo = [], [], []

    for path in photos:
        pil_img   = load_rgb_pil(path)
        h, w      = pil_img.height, pil_img.width
        top, bot  = estimate_facade_rows(h)
        facade_h  = bot - top

        if facade_h < 40:
            continue

        label_map   = segment_image(pil_img, processor, model)
        window_mask = (label_map == window_id).astype(np.uint8)

        stories, row_centers = count_stories_from_windows(window_mask, top, bot, facade_h)
        fen_pct = fenestration_from_mask(window_mask, top, bot)

        save_debug_h2(address, path.name, pil_img, label_map, window_mask,
                      top, bot, row_centers, stories, fen_pct)

        story_list.append(stories)
        fen_list.append(fen_pct)
        per_photo.append({
            "photo":   path.name,
            "stories": stories,
            "fen_pct": fen_pct,
            "n_window_rows": len(row_centers),
            "row_centers_y": row_centers,
        })

    if not story_list:
        return {"address": address, "error": "no processable photos", "method": "H2"}

    return {
        "address":                      address,
        "method":                       "H2",
        "number_stories":               int(round(float(np.median(story_list)))),
        "wall_fenesteration_front_per":  round(float(np.median(fen_list)), 1),
        "n_photos":                     len(story_list),
        "per_photo":                    per_photo,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    processor, model, window_id = load_model()

    results = {}
    for addr in ADDRESSES:
        print(f"  {addr} ...", end=" ", flush=True)
        r = process_address(addr, processor, model, window_id)
        results[addr] = r
        if "error" in r:
            print(f"ERROR: {r['error']}")
        else:
            print(f"stories={r['number_stories']}  fen={r['wall_fenesteration_front_per']}%"
                  f"  ({r['n_photos']} photo(s))")

    out_path = OUT_DIR / "facade_cv_h2_output.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
