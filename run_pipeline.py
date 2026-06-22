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
    already_done = {row["address"] for row in existing}

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

        existing.append(row)
        OUTPUT_PATH.write_text(json.dumps(existing, indent=2))

    print(f"\nDone -- {len(existing)} addresses in {OUTPUT_PATH.name}")


if __name__ == "__main__":
    main()
