#!/usr/bin/env python3
"""
analyze_back_fenestration.py

Estimates wall_fenesteration_back_per and wall_fenesteration_back_lowerlevel_per
for all 5 buildings using:
  - Existing ref_photos/before Back*.png where available (100 Main, 112 State, 54 Elm)
  - Playwright Street View capture for buildings without back photos (27 Langdon, 40 Main)

Patches visual_attributes.json with back-fenestration values.
Writes raw results to facade_cv/facade_cv_back_output.json.

Usage:
    python3 analyze_back_fenestration.py               # vision-only (3 buildings)
    python3 analyze_back_fenestration.py --capture     # also try Street View for missing 2
"""

from __future__ import annotations
import argparse, asyncio, base64, json
from pathlib import Path

from vision_client import get_client

REPO          = Path(__file__).parent
PHOTOS_BEFORE = REPO / "ref_photos" / "before"
PHOTOS_AFTER  = REPO / "ref_photos" / "after"
VA_PATH       = REPO / "visual_attributes.json"
OUT_PATH      = REPO / "facade_cv" / "facade_cv_back_output.json"

ADDRESSES = [
    "100 Main St, Montpelier, VT 05602",
    "112 State St, Montpelier, VT 05602",
    "27 Langdon St, Montpelier, VT 05602",
    "40 Main St, Montpelier, VT 05602",
    "54 Elm St, Montpelier, VT 05602",
]

# Street View camera positions for buildings without back photos.
# Camera is placed behind the building (opposite side from front) and aimed inward.
# front orientations from urban_attrs.json (OSM-derived):
#   27 Langdon: front=N → back=S → camera south of building, heading=0 (north)
#   40 Main:    front=E → back=W → camera west of building, heading=90 (east)
SV_CAMERAS: dict[str, dict] = {
    "27 Langdon St, Montpelier, VT 05602": {
        "screenshot": "/tmp/sv_back_27_Langdon_St.png",
        "camera_lat": 44.26051 - 0.0003,   # ~33 m south
        "camera_lon": -72.5755308,
        "heading":    0,                     # looking north
    },
    "40 Main St, Montpelier, VT 05602": {
        "screenshot": "/tmp/sv_back_40_Main_St.png",
        "camera_lat": 44.2593998,
        "camera_lon": -72.5764979 - 0.001,  # ~80 m west
        "heading":    90,                    # looking east
    },
}

BACK_PROMPT = """\
This photo shows the REAR (back) facade of {address} in Montpelier, Vermont.

Estimate the fenestration (window/glass area) of the visible rear wall.
Ignore porches, balconies, and fire escapes — focus on openings in the wall itself.

Answer ONLY with a JSON object (no markdown fences):

{{
  "wall_fenesteration_back_per": <integer 0-100 — estimated % of total rear wall \
area that is window or glazed opening>,
  "wall_fenesteration_back_lowerlevel_per": <integer 0-100 — same restricted to \
the ground-floor level only>,
  "notes": <one sentence — what was and was not visible>,
  "confidence": <"high", "medium", or "low">
}}

If the rear facade is not visible or too obstructed to estimate, return nulls for \
both percentage fields and confidence="low"."""


def find_back_photo(address: str) -> Path | None:
    for photos_dir in [PHOTOS_BEFORE, PHOTOS_AFTER]:
        addr_dir = photos_dir / address
        if not addr_dir.is_dir():
            continue
        candidates = sorted(
            p for p in addr_dir.iterdir()
            if "back" in p.stem.lower()
            and " - copy" not in p.stem.lower()
            and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        )
        if candidates:
            return candidates[0]
    return None


def vision_from_file(client, address: str, photo: Path) -> dict:
    b64 = base64.standard_b64encode(photo.read_bytes()).decode()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": BACK_PROMPT.format(address=address)},
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
        raise ValueError(f"Could not parse JSON:\n{raw}")


# ── Street View capture ────────────────────────────────────────────────────────

async def capture_sv_back(addresses: list[str]) -> None:
    import sys
    sys.path.insert(0, str(Path.home() / "Library/Python/3.9/lib/python/site-packages"))
    from playwright.async_api import async_playwright  # type: ignore

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        for addr in addresses:
            cfg = SV_CAMERAS[addr]
            url = (
                f"https://www.google.com/maps?layer=c"
                f"&cbll={cfg['camera_lat']},{cfg['camera_lon']}"
                f"&cbp=12,{cfg['heading']},,0,5"
            )
            print(f"  Capturing back of {addr} ...")
            print(f"    camera: {cfg['camera_lat']:.5f}, {cfg['camera_lon']:.5f}  "
                  f"heading={cfg['heading']}°")
            await page.goto(url, wait_until="load", timeout=30_000)
            await asyncio.sleep(9)
            await page.screenshot(path=cfg["screenshot"])
            print(f"    saved → {cfg['screenshot']}")

        await browser.close()


# ── Main ─────────────────────────────────────────────────────────────────────

def main(do_capture: bool = False) -> None:
    client = get_client()
    va: dict = json.loads(VA_PATH.read_text())
    results: dict = {}

    # Buildings needing Street View capture
    sv_needed = []

    for addr in ADDRESSES:
        label = addr.split(",")[0]
        photo = find_back_photo(addr)

        if photo:
            print(f"\n{label}  → {photo.name}")
            result = vision_from_file(client, addr, photo)
            result["source"] = str(photo.relative_to(REPO))
            results[addr] = result
            print(f"  back%={result.get('wall_fenesteration_back_per')}  "
                  f"lower%={result.get('wall_fenesteration_back_lowerlevel_per')}  "
                  f"conf={result.get('confidence')}")
            print(f"  notes: {result.get('notes')}")
        elif addr in SV_CAMERAS:
            sv_path = Path(SV_CAMERAS[addr]["screenshot"])
            if sv_path.exists():
                print(f"\n{label}  → Street View screenshot {sv_path.name}")
                result = vision_from_file(client, addr, sv_path)
                result["source"] = f"Street View (back capture, {sv_path.name})"
                results[addr] = result
                print(f"  back%={result.get('wall_fenesteration_back_per')}  "
                      f"lower%={result.get('wall_fenesteration_back_lowerlevel_per')}  "
                      f"conf={result.get('confidence')}")
                print(f"  notes: {result.get('notes')}")
            else:
                print(f"\n{label}  → no back photo; Street View capture needed")
                sv_needed.append(addr)
                results[addr] = {"source": "missing — no back photo or Street View screenshot"}
        else:
            print(f"\n{label}  → no back photo and no SV camera defined")
            results[addr] = {"source": "missing"}

    # Optionally capture Street View for the missing buildings
    if sv_needed and do_capture:
        print(f"\nLaunching Playwright to capture {len(sv_needed)} back Street View(s)…")
        asyncio.run(capture_sv_back(sv_needed))
        print("Capture done. Re-running vision analysis on new screenshots…")
        for addr in sv_needed:
            sv_path = Path(SV_CAMERAS[addr]["screenshot"])
            if sv_path.exists():
                label = addr.split(",")[0]
                result = vision_from_file(client, addr, sv_path)
                result["source"] = f"Street View (back capture, {sv_path.name})"
                results[addr] = result
                print(f"  {label}: back%={result.get('wall_fenesteration_back_per')}  "
                      f"lower%={result.get('wall_fenesteration_back_lowerlevel_per')}  "
                      f"conf={result.get('confidence')}")
            else:
                print(f"  Screenshot not found for {addr}")

    # Patch visual_attributes.json
    for addr, result in results.items():
        back_per   = result.get("wall_fenesteration_back_per")
        lower_per  = result.get("wall_fenesteration_back_lowerlevel_per")
        if back_per is not None:
            va.setdefault(addr, {})["wall_fenesteration_back_per"] = back_per
        if lower_per is not None:
            va.setdefault(addr, {})["wall_fenesteration_back_lowerlevel_per"] = lower_per

    VA_PATH.write_text(json.dumps(va, indent=2))
    OUT_PATH.write_text(json.dumps(results, indent=2))

    print(f"\nWrote → {VA_PATH.name}")
    print(f"Wrote → {OUT_PATH.relative_to(REPO)}")

    if sv_needed and not do_capture:
        print(f"\n⚠  {len(sv_needed)} building(s) still missing back photos:")
        for addr in sv_needed:
            print(f"   {addr}")
        print("   Re-run with --capture to attempt Street View screenshots.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", action="store_true",
                        help="Launch Playwright to capture Street View for missing buildings "
                             "(requires a visible browser — run locally, not headless)")
    args = parser.parse_args()
    main(do_capture=args.capture)
