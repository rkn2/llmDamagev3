#!/usr/bin/env python3
"""
analyze_roof_satellite_all.py

Extends analyze_roof_satellite.py to all 5 buildings.
Captures Google Maps satellite screenshots and uses Claude vision to determine
roof_shape_u and parapet_present for each building.

Skips buildings that already have both fields confirmed from satellite (marked
with 'aerial_screenshot' in visual_attributes.json).

LEAKAGE POLICY: reads only image files (screenshots). Writes only to
visual_attributes.json (roof_shape_u, parapet_present, aerial_screenshot, notes).

Usage:
    python3 analyze_roof_satellite_all.py
    python3 analyze_roof_satellite_all.py --skip-capture   # reuse existing screenshots
    python3 analyze_roof_satellite_all.py --force          # re-analyze all buildings
"""

from __future__ import annotations
import argparse, asyncio, base64, json, sys, time
from pathlib import Path

REPO = Path(__file__).parent

BUILDINGS = {
    "100 Main St, Montpelier, VT 05602":   (44.2605489,  -72.5752484),
    "112 State St, Montpelier, VT 05602":  (44.2608121,  -72.579971),
    "27 Langdon St, Montpelier, VT 05602": (44.26051,    -72.5755308),
    "40 Main St, Montpelier, VT 05602":    (44.2593998,  -72.5764979),
    "54 Elm St, Montpelier, VT 05602":     (44.2616257,  -72.5757163),
}

VISUAL_ATTRS = REPO / "visual_attributes.json"
SCRATCHPAD   = Path("/private/tmp/claude-502/-Users-becca-Code-compvision/bcf10b2a-073e-4443-9ccb-9439661a200d/scratchpad")

EXTRACT_PROMPT = """\
This is a top-down satellite screenshot centered on a building at {address} \
in Montpelier, Vermont. Focus on the roof of the building nearest the center \
of the image.

Respond ONLY with a JSON object (no markdown fences). Use null for anything \
you genuinely cannot determine.

{{
  "roof_shape_u": <"flat", "gable", "hip", "mansard", "shed", or null>,
  "parapet_present": <true or false — visible as a raised edge/wall ringing the roof>,
  "confidence": <"high", "medium", or "low">,
  "notes": <one sentence — what was clear and what was uncertain>
}}"""

ZOOM = 20


def screenshot_path(address: str) -> str:
    slug = address.split(",")[0].replace(" ", "_")
    return str(SCRATCHPAD / f"sat_{slug}.png")


async def capture_all(addresses: list[str], skip: set[str]) -> None:
    sys.path.insert(0, str(Path.home() / "Library/Python/3.9/lib/python/site-packages"))
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        for addr in addresses:
            if addr in skip:
                print(f"  SKIP capture — {addr.split(',')[0]}")
                continue
            lat, lon = BUILDINGS[addr]
            url = f"https://www.google.com/maps/@{lat},{lon},{ZOOM}z/data=!3m1!1e3"
            path = screenshot_path(addr)
            print(f"  Capturing {addr.split(',')[0]} …")
            await page.goto(url, wait_until="load", timeout=30_000)
            await asyncio.sleep(9)
            await page.screenshot(path=path)
            print(f"    → {path}")
            await asyncio.sleep(2)

        await browser.close()


def analyze_image(client, addr: str) -> dict | None:
    path = screenshot_path(addr)
    if not Path(path).exists():
        print(f"    no screenshot at {path}")
        return None

    with open(path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": EXTRACT_PROMPT.format(address=addr)},
            ],
        }],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-capture", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="Re-analyze buildings already marked with aerial_screenshot")
    args = parser.parse_args()

    SCRATCHPAD.mkdir(parents=True, exist_ok=True)
    attrs = json.loads(VISUAL_ATTRS.read_text())

    # Decide which buildings to process
    to_process = list(BUILDINGS.keys())
    skip_capture: set[str] = set()
    if args.skip_capture:
        skip_capture = set(to_process)
    if not args.force:
        # Skip buildings already confirmed via satellite
        already_done = {a for a in to_process if attrs.get(a, {}).get("aerial_screenshot")}
        if already_done:
            print(f"Already have aerial for: {[a.split(',')[0] for a in already_done]}")
            print("  (use --force to re-analyze)\n")

    if not args.skip_capture:
        print("Capturing satellite screenshots …")
        asyncio.run(capture_all(to_process, skip_capture))
        print()

    from vision_client import get_client
    client = get_client()

    changed = False
    for addr in to_process:
        if not args.force and attrs.get(addr, {}).get("aerial_screenshot"):
            continue

        print(f"Analyzing {addr.split(',')[0]} …")
        try:
            result = analyze_image(client, addr)
        except Exception as exc:
            print(f"  FAILED: {exc}")
            continue
        if result is None:
            continue

        print(f"  roof_shape_u={result.get('roof_shape_u')}  "
              f"parapet_present={result.get('parapet_present')}  "
              f"confidence={result.get('confidence')}")
        print(f"  notes: {result.get('notes')}")

        entry = attrs.setdefault(addr, {})
        entry["roof_shape_u"]     = result.get("roof_shape_u")
        if result.get("parapet_present") is not None:
            entry["parapet_present"] = result.get("parapet_present")
        entry["aerial_screenshot"] = screenshot_path(addr)
        note = result.get("notes", "")
        existing = entry.get("notes", "")
        if note and f"[Aerial: {note}" not in existing:
            entry["notes"] = (existing + f" [Aerial: {note}]").strip()
        changed = True
        time.sleep(1)

    if changed:
        VISUAL_ATTRS.write_text(json.dumps(attrs, indent=2))
        print(f"\nUpdated → {VISUAL_ATTRS.name}")
    else:
        print("\nNo changes.")


if __name__ == "__main__":
    main()
