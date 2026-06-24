#!/usr/bin/env python3
"""
analyze_roof_satellite.py

Street View was blocked for 54 Elm St's roof, so this falls back to a
top-down Google Maps satellite screenshot to determine roof_shape_u, then
merges the result into visual_attributes.json (leaving other fields as-is).

Usage:
    python3 analyze_roof_satellite.py
    python3 analyze_roof_satellite.py --skip-capture   # reuse existing screenshot
"""

from __future__ import annotations
import argparse, asyncio, base64, json, sys
from pathlib import Path

ADDRESS = "54 Elm St, Montpelier, VT 05602"
LAT, LON = 44.2616257, -72.5757163  # from building_attributes_auto.json
SCREENSHOT = "/tmp/sat_54_Elm_St.png"
VISUAL_ATTRS = Path(__file__).parent / "visual_attributes.json"

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


async def capture_screenshot() -> None:
    sys.path.insert(0, str(Path.home() / "Library/Python/3.9/lib/python/site-packages"))
    from playwright.async_api import async_playwright  # type: ignore

    url = f"https://www.google.com/maps/@{LAT},{LON},20z/data=!3m1!1e3"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        print(f"  Capturing satellite view of {ADDRESS} …")
        await page.goto(url, wait_until="load", timeout=30_000)
        await asyncio.sleep(9)
        await page.screenshot(path=SCREENSHOT)
        await browser.close()


def analyze_image(client, path: str) -> dict:
    with open(path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64},
                },
                {"type": "text", "text": EXTRACT_PROMPT.format(address=ADDRESS)},
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
    parser.add_argument("--skip-capture", action="store_true",
                         help="Skip satellite capture and reuse existing screenshot")
    args = parser.parse_args()

    if not args.skip_capture:
        print("Capturing satellite screenshot …")
        asyncio.run(capture_screenshot())
        print()

    if not Path(SCREENSHOT).exists():
        print(f"SKIP — screenshot not found at {SCREENSHOT}")
        return

    from vision_client import get_client
    client = get_client()

    print(f"Analyzing roof for {ADDRESS} …")
    try:
        result = analyze_image(client, SCREENSHOT)
    except Exception as exc:
        print(f"  FAILED: {exc}")
        return

    print(f"  roof_shape_u={result.get('roof_shape_u')}, "
          f"parapet_present={result.get('parapet_present')}, "
          f"confidence={result.get('confidence')}")

    attrs = json.loads(VISUAL_ATTRS.read_text())
    entry = attrs.setdefault(ADDRESS, {})
    entry["roof_shape_u"] = result.get("roof_shape_u")
    if result.get("parapet_present") is not None:
        entry["parapet_present"] = result.get("parapet_present")
    entry["aerial_screenshot"] = SCREENSHOT
    entry["notes"] = (entry.get("notes", "") +
                       f" [Aerial pass for roof: {result.get('notes', '')}]").strip()
    VISUAL_ATTRS.write_text(json.dumps(attrs, indent=2))
    print(f"\nUpdated → {VISUAL_ATTRS.name}")


if __name__ == "__main__":
    main()
