#!/usr/bin/env python3
from __future__ import annotations
"""Run flood-damage assessment for every address with before+after photos.

Usage:
    python3 run_pipeline.py
    python3 run_pipeline.py --model claude-opus-4-7
"""

import argparse
import json
from pathlib import Path

from assess import assess_building
from vision_client import MODEL_ID, get_client

ROOT = Path(__file__).parent
BEFORE_ROOT = ROOT / "ref_photos" / "before"
AFTER_ROOT = ROOT / "ref_photos" / "after"
OUTPUT_PATH = ROOT / "address_assessments.json"


def _read_photos(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=MODEL_ID)
    args = parser.parse_args()

    existing = json.loads(OUTPUT_PATH.read_text()) if OUTPUT_PATH.exists() else []
    # Only a real assessment counts as "done" - a row with an error or no damage_level
    # (e.g. a transient API failure) is excluded so it gets retried automatically on the
    # next run instead of being permanently stuck (found during the 2026-06-24 audit).
    already_done = {
        row["address"] for row in existing
        if not row.get("error") and row.get("damage_level") is not None
    }

    addresses = sorted(p.name for p in AFTER_ROOT.iterdir() if p.is_dir())
    pending = [a for a in addresses if a not in already_done]

    print(f"{len(addresses)} addresses total, {len(already_done)} already assessed, {len(pending)} to go")
    print(f"Model: {args.model}\n")

    client = get_client()
    for n, address in enumerate(pending, 1):
        before = _read_photos(BEFORE_ROOT / address)
        after = _read_photos(AFTER_ROOT / address)
        print(f"[{n}/{len(pending)}] {address} ({len(before)} before, {len(after)} after)")

        try:
            row = assess_building(client, address, before, after, args.model)
            print(f"    damage level {row.get('damage_level')} ({row.get('confidence')})")
        except Exception as exc:
            print(f"    failed: {exc}")
            row = {
                "address": address, "error": str(exc), "damage_level": None,
                "confidence": "low", "assessable": False,
                "before_photo_count": len(before), "after_photo_count": len(after),
                "model": args.model,
            }

        # Replace any prior row for this address (e.g. a stale error row being retried)
        # rather than appending a duplicate.
        existing = [r for r in existing if r.get("address") != address]
        existing.append(row)
        OUTPUT_PATH.write_text(json.dumps(existing, indent=2))

    print(f"\nDone -- {len(existing)} addresses in {OUTPUT_PATH.name}")

    # Deterministic cross-source invariant checks (sanity_checks.py) — report only,
    # never blocks the pipeline; high-severity findings need resolving before the
    # numbers are used (see sanity_findings.json / sanity_adjudications.json).
    try:
        from sanity_checks import apply_adjudications, run_checks
        findings = apply_adjudications(run_checks())
        high = [f for f in findings if f["severity"] == "high"]
        print(f"\nSanity checks: {len(findings)} finding(s), {len(high)} high-severity")
        for f in high:
            print(f"  [high] {f['rule']} {f['address'].split(',')[0]}: {f['detail'][:100]}")
    except Exception as exc:
        print(f"\nSanity checks failed to run: {exc}")


if __name__ == "__main__":
    main()
