#!/usr/bin/env python3
"""
Evaluate facade_cv_output.json against ground truth and LLM baseline.

This script CAN read pipeline JSONs (visual_attributes.json, critic_findings.json)
— it is the EVALUATION layer, separate from the prediction layer (analyze_facade_cv.py).
The prediction script must never import these.
"""
import json
from pathlib import Path

import sys

REPO     = Path(__file__).parent.parent
OUT_DIR  = Path(__file__).parent
VA_PATH  = REPO / "visual_attributes.json"

# Pick output file from args, default to latest available
def find_cv_output():
    if len(sys.argv) > 1:
        return OUT_DIR / sys.argv[1]
    for candidate in ["facade_cv_h1_3_output.json", "facade_cv_h1_2_output.json", "facade_cv_h1_1_output.json", "facade_cv_h5_1_output.json", "facade_cv_h6_output.json", "facade_cv_h5_output.json", "facade_cv_h4_output.json", "facade_cv_h2_output.json", "facade_cv_output.json"]:
        p = OUT_DIR / candidate
        if p.exists():
            return p
    return None

CV_OUT = find_cv_output()

# ── Ground truth: critic-corrected story counts ────────────────────────────
# Source: critic_findings.json HIGH-severity number_stories items +
#         visual_attributes.json where no critic correction exists.
# ⚠ IMPORTANT: this dict must NOT be read by analyze_facade_cv.py (it would be
#   leakage). It lives only in this evaluation script.
GROUND_TRUTH_STORIES = {
    "100 Main St, Montpelier, VT 05602":  3,  # visual_attrs=3, no critic correction
    "112 State St, Montpelier, VT 05602": 5,  # visual_attrs=4, critic HIGH: 5 stories + mansard
    "27 Langdon St, Montpelier, VT 05602":3,  # visual_attrs=3, critic notes stepped massing but 3 primary
    "40 Main St, Montpelier, VT 05602":   3,  # visual_attrs=3, critic notes 3-story main block
    "54 Elm St, Montpelier, VT 05602":    3,  # visual_attrs=4, critic HIGH: photos show 3 stories
}

# ── LLM baseline (from visual_attributes.json) ────────────────────────────
LLM_STORIES = {
    "100 Main St, Montpelier, VT 05602":  3,
    "112 State St, Montpelier, VT 05602": 4,
    "27 Langdon St, Montpelier, VT 05602":3,
    "40 Main St, Montpelier, VT 05602":   3,
    "54 Elm St, Montpelier, VT 05602":    4,
}
LLM_FEN = {
    "100 Main St, Montpelier, VT 05602":  25.0,
    "112 State St, Montpelier, VT 05602": 45.0,
    "27 Langdon St, Montpelier, VT 05602":35.0,
    "40 Main St, Montpelier, VT 05602":   30.0,
    "54 Elm St, Montpelier, VT 05602":    15.0,
}


def evaluate():
    if CV_OUT is None or not CV_OUT.exists():
        print("ERROR: no CV output file found. Run analyze_facade_cv.py or analyze_facade_h2.py first.")
        return

    print(f"Evaluating: {CV_OUT.name}")
    cv = json.loads(CV_OUT.read_text())
    va = json.loads(VA_PATH.read_text()) if VA_PATH.exists() else {}

    print("\n=== Story Count Accuracy ===")
    print(f"{'Address':40s}  GT  LLM  CV   LLM✓  CV✓")
    print("-" * 72)

    llm_correct, cv_correct = 0, 0
    addresses = list(GROUND_TRUTH_STORIES.keys())

    for addr in addresses:
        gt    = GROUND_TRUTH_STORIES[addr]
        llm   = LLM_STORIES[addr]
        entry = cv.get(addr, {})
        cv_s  = entry.get("number_stories", "?")
        llm_ok = "✓" if llm == gt else "✗"
        cv_ok  = "✓" if cv_s == gt else "✗"
        if llm == gt:
            llm_correct += 1
        if cv_s == gt:
            cv_correct += 1
        name = addr.split(",")[0]
        print(f"  {name:38s}  {gt}   {llm}    {cv_s!s:<4} {llm_ok}     {cv_ok}")

    n = len(addresses)
    print("-" * 72)
    print(f"  {'TOTAL':38s}  {'':3s}   {llm_correct}/{n}  {cv_correct}/{n}")
    print(f"\n  LLM baseline:  {llm_correct}/{n} ({100*llm_correct/n:.0f}%)")
    print(f"  CV H1 result:  {cv_correct}/{n} ({100*cv_correct/n:.0f}%)")
    delta = cv_correct - llm_correct
    sign = "+" if delta >= 0 else ""
    print(f"  Delta vs LLM:  {sign}{delta}")

    print("\n=== Fenestration % (no hard ground truth — LLM baseline comparison) ===")
    print(f"{'Address':40s}  LLM   CV     |diff|")
    print("-" * 68)

    abs_diffs = []
    for addr in addresses:
        llm_f = LLM_FEN[addr]
        entry = cv.get(addr, {})
        cv_f  = entry.get("wall_fenesteration_front_per", None)
        if cv_f is not None:
            diff = abs(cv_f - llm_f)
            abs_diffs.append(diff)
            name = addr.split(",")[0]
            print(f"  {name:38s}  {llm_f:5.1f}  {cv_f:5.1f}  {diff:5.1f}")
        else:
            print(f"  {addr.split(',')[0]:38s}  {llm_f:5.1f}  {'?':>5}  {'?':>5}")

    if abs_diffs:
        mae = sum(abs_diffs) / len(abs_diffs)
        print(f"\n  Fenestration MAE vs LLM: {mae:.1f} pp")
    else:
        print("\n  No fenestration data.")

    print("\n=== Per-photo detail ===")
    for addr in addresses:
        entry = cv.get(addr, {})
        if "error" in entry:
            print(f"  {addr.split(',')[0]}: ERROR — {entry['error']}")
            continue
        print(f"  {addr.split(',')[0]}:")
        for pp in entry.get("per_photo", []):
            extra = {k: v for k, v in pp.items() if k not in ("photo", "stories", "fen_pct")}
        print(f"    {pp['photo']:40s}  stories={pp['stories']}  fen={pp['fen_pct']}%  {extra}")

    print()
    return cv_correct, n, abs_diffs


if __name__ == "__main__":
    evaluate()
