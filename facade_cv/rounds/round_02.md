# Round 2 — H2: SegFormer-b0 ADE20K segmentation

**Node:** H2  
**Date:** 2026-06-25

## Measure
- CV stories: 1/5 (all predict 1 — no window components found, all cluster counts = 0)
- Fenestration: 0% for all buildings

## Evidence of failure

Diagnostic shows: 95.3% of 40 Main St facade classified as "building" (class 1),
zero pixels as "windowpane" (class 8). Remaining classes: sky 4%, trade name 0.5%,
streetlight 0.2%, awning trace.

**Root cause: severe domain shift.**
- SegFormer-b0 trained on ADE20K interior/residential scenes
- These 19th-c. commercial masonry buildings with arched windows and large storefronts
  are classified as one undifferentiated "building" blob — no per-window discrimination
- b0 is the smallest model; larger SegFormer variants would likely fail the same way
  since the issue is fundamental label-space mismatch, not just model capacity

## Decision
H2 (SegFormer ADE20K) = **dead**. Mark exhausted.

H2.1 variants (SAM, CMP Facade-specific model) remain open as new children,
but gate should assess whether H1.1 (parameter refinement, free, immediate)
is higher-information given H2's failure reveals the facades DO have
detectable structural features — just not via semantic segmentation.

## What H2 failure tells us
- The windows ARE visible in the photos (clear rectangles/arches in raw image)
- The problem is not image quality but label-space: no off-shelf ADE20K model
  will segment these commercial windows without domain-specific fine-tuning
- This pushes evidence toward approaches that work on structural geometry
  (H1.1, H4) rather than semantic labels

## Per-building H2 result
All buildings: stories=1, fen=0.0% — all zero window detections
