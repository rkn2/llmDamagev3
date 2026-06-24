#!/usr/bin/env python3
"""
analyze_visual_attributes.py

Uses Claude vision (via AnthropicVertex) to extract building attributes from
Google Street View screenshots captured by this script.

Steps:
  1. Capture one Street View screenshot per building (Playwright + Chromium).
  2. Send each image to Claude with a structured extraction prompt.
  3. Write results to visual_attributes.json.
  4. Print a combined attribute table.

Usage:
    python3 analyze_visual_attributes.py
    python3 analyze_visual_attributes.py --skip-capture   # reuse existing screenshots
"""

from __future__ import annotations
import argparse, asyncio, base64, json, sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Building definitions
# Camera is placed at (lat, lon) and aimed at heading° to show the facade.
# ---------------------------------------------------------------------------
BUILDINGS: dict[str, dict] = {
    "100 Main St, Montpelier, VT 05602": {
        "screenshot": "/tmp/sv_100_Main_St_south.png",  # south of building, looking north
        "camera_lat": 44.2605489 - 0.0001,
        "camera_lon": -72.5752484,
        "heading":    0,
    },
    "112 State St, Montpelier, VT 05602": {
        "screenshot": "/tmp/sv_112_State_St.png",
        "camera_lat": 44.2608121 + 0.00015,
        "camera_lon": -72.579971,
        "heading":    180,
    },
    "27 Langdon St, Montpelier, VT 05602": {
        "screenshot": "/tmp/sv_27_Langdon_St.png",
        "camera_lat": 44.26051 + 0.00015,
        "camera_lon": -72.5755308,
        "heading":    180,
    },
    "40 Main St, Montpelier, VT 05602": {
        "screenshot": "/tmp/sv_40_Main_St.png",
        "camera_lat": 44.2593998 - 0.0001,   # south of building
        "camera_lon": -72.5764979,
        "heading":    0,                       # looking north
    },
    "54 Elm St, Montpelier, VT 05602": {
        "screenshot": "/tmp/sv_54_Elm_St.png",
        "camera_lat": 44.2616257 + 0.00015,
        "camera_lon": -72.5757163,
        "heading":    180,
    },
}

EXTRACT_PROMPT = """\
This is a Google Street View screenshot near {address} in Montpelier, Vermont \
(July 2023 flood area). Focus on the primary building visible in the image.

Extract the following building attributes and respond ONLY with a JSON object \
(no markdown fences). Use null for anything you genuinely cannot determine.

{{
  "number_stories": <integer — count above-ground floors>,
  "buidling_height_m": <float — estimated total height in metres; use ~3.5 m per \
commercial story, ~3.0 m per residential story>,
  "wall_cladding_u": <string — "brick", "wood siding", "stucco", "metal panel", \
"stone", "EIFS", or other>,
  "wall_fenesteration_front_per": <integer 0-100 — rough % of visible facade that \
is window/glass>,
  "roof_shape_u": <"flat", "gable", "hip", "mansard", "shed", or null>,
  "soffit_present_u": <true or false>,
  "parapet_present": <true or false>,
  "parapet_height_m": <float or null — estimated parapet height above roof line>,
  "front_elevation_orientation": <"N", "S", "E", "W" — cardinal direction the \
street-facing facade is looking toward>,
  "construction_type_u": <"URM" (unreinforced masonry), "RM" (reinforced masonry), \
"wood frame", "steel frame", "concrete", or null>,
  "confidence": <"high", "medium", or "low">,
  "notes": <one sentence — what was clear and what was uncertain>
}}"""

OUTPUT = Path(__file__).parent / "visual_attributes.json"


# ---------------------------------------------------------------------------
# Screenshot capture (Playwright)
# ---------------------------------------------------------------------------

async def capture_screenshots() -> None:
    sys.path.insert(0, str(Path.home() / "Library/Python/3.9/lib/python/site-packages"))
    from playwright.async_api import async_playwright  # type: ignore

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        for addr, cfg in BUILDINGS.items():
            url = (
                f"https://www.google.com/maps?layer=c"
                f"&cbll={cfg['camera_lat']},{cfg['camera_lon']}"
                f"&cbp=12,{cfg['heading']},,0,5"
            )
            print(f"  Capturing {addr} …")
            await page.goto(url, wait_until="load", timeout=30_000)
            await asyncio.sleep(9)
            await page.screenshot(path=cfg["screenshot"])

        await browser.close()


# ---------------------------------------------------------------------------
# Vision analysis (Claude on Vertex)
# ---------------------------------------------------------------------------

def analyze_image(client, address: str, path: str) -> dict:
    with open(path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode()

    prompt = EXTRACT_PROMPT.format(address=address)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64},
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    raw = response.content[0].text.strip()
    # Strip markdown fences if model added them
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-capture",
        action="store_true",
        help="Skip Street View capture and reuse existing screenshots",
    )
    args = parser.parse_args()

    if not args.skip_capture:
        print("Capturing Street View screenshots …")
        asyncio.run(capture_screenshots())
        print()

    # Import vision client from project
    from vision_client import get_client
    client = get_client()

    results: dict[str, dict] = {}
    for addr, cfg in BUILDINGS.items():
        path = cfg["screenshot"]
        if not Path(path).exists():
            print(f"  SKIP {addr} — screenshot not found at {path}")
            continue
        print(f"Analyzing {addr} …")
        try:
            attrs = analyze_image(client, addr, path)
            attrs["screenshot"] = path
            results[addr] = attrs
            print(
                f"  {attrs.get('number_stories')} stories, "
                f"{attrs.get('wall_cladding_u')}, "
                f"{attrs.get('roof_shape_u')} roof, "
                f"confidence={attrs.get('confidence')}"
            )
        except Exception as exc:
            print(f"  FAILED: {exc}")
            results[addr] = {"error": str(exc)}

    OUTPUT.write_text(json.dumps(results, indent=2))
    print(f"\nWrote → {OUTPUT.name}")

    # Summary table
    print("\n" + "=" * 80)
    print(f"{'Address':<45} {'Stories':>7} {'Height':>8} {'Cladding':<15} {'Roof':<12} {'Conf'}")
    print("-" * 80)
    for addr, r in results.items():
        if "error" in r:
            print(f"  {addr:<43}  ERROR")
            continue
        print(
            f"  {addr:<43}"
            f"  {str(r.get('number_stories', '?')):>5}"
            f"  {str(r.get('buidling_height_m', '?')):>7} m"
            f"  {str(r.get('wall_cladding_u', '?')):<15}"
            f"  {str(r.get('roof_shape_u', '?')):<12}"
            f"  {r.get('confidence', '?')}"
        )


if __name__ == "__main__":
    main()
