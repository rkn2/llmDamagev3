#!/usr/bin/env python3
"""
analyze_entrance_step.py

Estimates first_floor_elevation_m for each building by:
  1. Sending the before-flood front photo to Claude vision.
  2. Asking Claude to count entrance steps and estimate rise above sidewalk grade.
  3. Computing: first_floor_elevation_m = ground_elevation_m + step_height_m
  4. Patching building_attributes_auto.json with the result.

Skips any address that already has first_floor_elevation_m set.
"""

from __future__ import annotations
import base64, json, glob
from pathlib import Path

from vision_client import get_client

REPO = Path(__file__).parent
AUTO_JSON = REPO / "building_attributes_auto.json"
PHOTOS_DIR = REPO / "ref_photos" / "before"

STEP_RISER_M = 0.18  # standard step riser height

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]

PROMPT = """\
This photo shows the front facade of {address} in Montpelier, Vermont.

Focus on the building entrance — specifically the transition between the sidewalk \
and the first-floor interior level.

Answer ONLY with a JSON object (no markdown fences):

{{
  "entrance_steps_count": <integer — number of visible steps from sidewalk to \
first-floor entrance; 0 if the entrance is flush with the sidewalk>,
  "step_height_m": <float — total rise in metres from sidewalk to first-floor \
threshold; use entrance_steps_count × 0.18 m as your baseline and adjust if \
the photo suggests taller risers or a ramp>,
  "at_grade": <true if entrance appears flush with sidewalk, false otherwise>,
  "confidence": <"high", "medium", or "low">,
  "notes": <one sentence — what you could and could not see clearly>
}}

If the entrance is not visible, set entrance_steps_count to null, step_height_m \
to null, and confidence to "low"."""


def find_front_photo(address: str) -> Path | None:
    addr_dir = PHOTOS_DIR / address
    if not addr_dir.is_dir():
        return None
    candidates = sorted(addr_dir.glob("Front*.png"))
    return candidates[0] if candidates else None


def estimate_step_height(client, address: str, photo: Path) -> dict:
    b64 = base64.standard_b64encode(photo.read_bytes()).decode()
    media_type = "image/png"

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64},
                },
                {"type": "text", "text": PROMPT.format(address=address)},
            ],
        }],
    )

    raw = response.content[0].text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise ValueError(f"Could not parse JSON from response:\n{raw}")


def main() -> None:
    attrs: dict = json.loads(AUTO_JSON.read_text())
    client = get_client()

    for addr in ADDRESSES:
        rec = attrs.get(addr, {})
        if "first_floor_elevation_m" in rec:
            print(f"\n{addr}\n  SKIP — first_floor_elevation_m already set: {rec['first_floor_elevation_m']}")
            continue

        ground_elev = rec.get("ground_elevation_m")
        if ground_elev is None:
            print(f"\n{addr}\n  SKIP — no ground_elevation_m")
            continue

        photo = find_front_photo(addr)
        if photo is None:
            print(f"\n{addr}\n  SKIP — no front photo found in ref_photos/before")
            continue

        print(f"\n{addr}")
        print(f"  Photo            : {photo.name}")
        print(f"  Ground elev      : {ground_elev} m (NAVD88)")

        result = estimate_step_height(client, addr, photo)
        print(f"  Steps            : {result.get('entrance_steps_count')}")
        print(f"  Step height      : {result.get('step_height_m')} m")
        print(f"  At grade         : {result.get('at_grade')}")
        print(f"  Confidence       : {result.get('confidence')}")
        print(f"  Notes            : {result.get('notes')}")

        step_h = result.get("step_height_m")
        if step_h is None:
            print("  → step_height_m is null; skipping first_floor_elevation_m")
            continue

        ffe = round(ground_elev + step_h, 3)
        print(f"  first_floor_elev : {ground_elev} + {step_h} = {ffe} m")

        rec["first_floor_elevation_m"] = ffe
        rec["first_floor_elevation_source"] = (
            f"ground_elevation_m ({ground_elev} m NAVD88) + entrance step height "
            f"({step_h} m, {result.get('entrance_steps_count')} step(s) via Claude vision "
            f"on ref_photos/before front photo; confidence={result.get('confidence')})"
        )
        attrs[addr] = rec

    AUTO_JSON.write_text(json.dumps(attrs, indent=2))
    print(f"\nWrote → {AUTO_JSON.name}")


if __name__ == "__main__":
    main()
