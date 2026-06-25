#!/usr/bin/env python3
from __future__ import annotations
"""Adversarial critic pass over every populated field on each building's detail page.

Combines cheap rule-based sanity checks with a per-building Claude call that has the
same before/after photos assess.py uses, so it can fact-check field values against the
images, not just check internal text consistency. Nothing in BUILDINGS/COMMON or the
three live JSON sources has been independently human-verified -- including values that
read as confident assertions -- so this covers every field, not just the LLM-tagged ones.

Usage:
    python3 critic.py
    python3 critic.py --model claude-opus-4-7
"""

import argparse
import json
import re
import tempfile
from pathlib import Path

from generate_detail_pages import (
    BUILDINGS, SECTIONS, resolve_building_data, iter_populated_fields,
)
from photos import normalize_photo, to_image_block
from vision_client import MODEL_ID, get_client

ROOT = Path(__file__).parent
BEFORE_ROOT = ROOT / "ref_photos" / "before"
AFTER_ROOT = ROOT / "ref_photos" / "after"
OUTPUT_PATH = ROOT / "critic_findings.json"

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}

# ── Rule-based checks ──────────────────────────────────────────────────────────

# Fields that should be address-specific AND high-cardinality enough that an exact
# match across two different buildings is essentially impossible by chance -- this is
# the auto-collection collision bug class documented in LESSONS_LEARNED.md (27 Langdon
# St and 90 Main St silently sharing one OSM polygon, hence identical area/wall lengths).
# Deliberately excludes low-cardinality fields like number_stories (small integer) and
# front_elevation_orientation (one of 4 values) -- with only 5 buildings on the same
# downtown street, those collide by genuine coincidence, not a collection bug, and
# flagging them would just be noise.
COLLISION_FIELDS = [
    "latitude", "longitude", "building_area_m2", "wall_length_front", "wall_length_side",
]

PERCENT_FIELD_SUFFIXES = ("_per", "_percent")
UNC_VALUES = {"1", "2", "3"}
CONFIDENCE_VALUES = {"high", "medium", "low"}
POSITIVE_NUMBER_FIELDS = {
    "buidling_height_m", "building_area_m2", "wall_thickness",
    "wall_length_front", "wall_length_side",
}


def check_cross_building_collisions(resolved: dict[str, dict]) -> dict[str, list[dict]]:
    findings: dict[str, list[dict]] = {addr: [] for addr in resolved}
    for field in COLLISION_FIELDS:
        by_value: dict[str, list[str]] = {}
        for addr, data in resolved.items():
            val = data.get(field)
            if val in (None, "", "un"):
                continue
            by_value.setdefault(str(val), []).append(addr)
        for val, addrs in by_value.items():
            if len(addrs) > 1:
                for addr in addrs:
                    others = [a for a in addrs if a != addr]
                    findings[addr].append({
                        "field": field, "severity": "high", "source": "rule",
                        "issue": f"Value {val!r} for {field!r} is identical to "
                                 f"{', '.join(others)} -- check for an auto-collection "
                                 f"collision (see LESSONS_LEARNED.md) before trusting it.",
                    })
    return findings


def check_field_ranges(address: str, data: dict) -> list[dict]:
    findings = []
    for key, val, *_ in iter_populated_fields(address, data):
        low = key.lower()
        try:
            if low.endswith(PERCENT_FIELD_SUFFIXES):
                num = float(val)
                if not (0 <= num <= 100):
                    findings.append({"field": key, "severity": "medium", "source": "rule",
                                      "issue": f"{val!r} is outside the expected 0-100 percent range."})
            elif low.endswith("_unc"):
                if val not in UNC_VALUES:
                    findings.append({"field": key, "severity": "low", "source": "rule",
                                      "issue": f"{val!r} is not a valid certainty code (expected 1, 2, or 3)."})
            elif key in ("_llm_confidence", "confidence"):
                if val not in CONFIDENCE_VALUES:
                    findings.append({"field": key, "severity": "low", "source": "rule",
                                      "issue": f"{val!r} is not one of high/medium/low."})
            elif key == "_llm_damage_level" or low == "damage_level":
                level_str = str(val).split()[0]
                if not level_str.isdigit() or not (0 <= int(level_str) <= 4):
                    findings.append({"field": key, "severity": "medium", "source": "rule",
                                      "issue": f"{val!r} is not a valid 0-4 damage level."})
            elif low in POSITIVE_NUMBER_FIELDS:
                if float(val) <= 0:
                    findings.append({"field": key, "severity": "high", "source": "rule",
                                      "issue": f"{val!r} should be a positive number for {key}."})
        except (ValueError, TypeError):
            pass  # non-numeric where a number was expected is exactly what the LLM critic should catch
    return findings


def check_footprint_consistency(data: dict) -> list[dict]:
    """wall_length_front/side are the OSM footprint's N-S/E-W bounding-box extents
    (collect_building_attributes.py's osm_footprint()), while building_area_m2 is the
    true Shoelace-formula polygon area. A polygon's area can never exceed its bounding
    box, so area > front*side is mathematically impossible and means the two fields came
    from inconsistent sources -- but area well *below* front*side is normal and expected
    for any non-rectangular footprint (rowhouses, L-shapes), not a sign of a bug.
    """
    findings = []
    try:
        area = float(data.get("building_area_m2"))
        front = float(data.get("wall_length_front"))
        side = float(data.get("wall_length_side"))
    except (TypeError, ValueError):
        return findings
    bbox_area = front * side
    if bbox_area > 0 and area > bbox_area * 1.05:  # 5% tolerance for rounding
        findings.append({
            "field": "building_area_m2", "severity": "high", "source": "rule",
            "issue": f"building_area_m2={area} exceeds the bounding-box area implied by "
                     f"wall_length_front*wall_length_side={bbox_area:.1f} -- a polygon's area "
                     f"can never exceed its bounding box, so these two fields are inconsistent.",
        })
    return findings


def check_story_height_plausibility(data: dict) -> list[dict]:
    findings = []
    try:
        height = float(data.get("buidling_height_m"))
        stories = float(data.get("number_stories"))
    except (TypeError, ValueError):
        return findings
    if stories <= 0:
        return findings
    per_story = height / stories
    if not (2.5 <= per_story <= 5.0):
        findings.append({
            "field": "buidling_height_m", "severity": "medium", "source": "rule",
            "issue": f"buidling_height_m={height} / number_stories={stories} = "
                     f"{per_story:.1f} m/story, outside the plausible ~2.5-5 m/story range.",
        })
    return findings


def run_rule_checks(resolved: dict[str, dict]) -> dict[str, list[dict]]:
    findings = check_cross_building_collisions(resolved)
    for addr, data in resolved.items():
        findings[addr].extend(check_field_ranges(addr, data))
        findings[addr].extend(check_footprint_consistency(data))
        findings[addr].extend(check_story_height_plausibility(data))
    return findings


# ── LLM-based adversarial critic ───────────────────────────────────────────────

CRITIC_INSTRUCTIONS = """You are an adversarial fact-checker reviewing a flood-damage \
assessment record for one building in Montpelier, VT (July 2023 riverine flood).

IMPORTANT CONTEXT: nothing in this record has been independently human-verified. Some \
fields come from a prior LLM session inferring values without ground truth (e.g. \
guessing a heritage value as "considerable" because the building is in a historic \
district, or asserting "no wind damage" without separating wind from flood evidence). \
Other fields come from automated geocoding/footprint tools that have previously \
duplicated values across two different buildings by mistake. Treat every field as an \
unverified claim, including ones that read as confident and well-justified -- confident \
phrasing is not evidence.

You will see:
1. A list of populated fields for this building: field name, value, the field's \
definition (from the schema), allowed options, and any existing note.
2. BEFORE photos (pre-flood) and AFTER photos (post-flood) of the same building.

Find problems in three categories:
- CONTRADICTS PHOTOS: the value doesn't match what the before/after photos show \
(e.g. claims brick when photos show clapboard, claims no wind damage but you see \
roof/window damage unrelated to water line, claims a story count that doesn't match \
what's visible).
- CONTRADICTS ANOTHER FIELD: two fields in this same record are mutually inconsistent \
(e.g. construction_type_u says "wood_frame" but mwfrs_u_wall says \
"wall_diaphragm_masonry"; wall_cladding_u says "brick" but soffit/photos say wood).
- IMPLAUSIBLE VALUE: the value is a poor fit for the field's stated definition/options, \
or is suspiciously generic/templated for this specific building (e.g. a heritage-value \
rating justified only by "it's in a historic district" with no building-specific \
reasoning, or a damage-cause claim with no supporting visual detail).

Do NOT flag a field just because it is uncertain or marked "un" -- only flag fields that \
are actively wrong, contradictory, or implausible given the evidence you have. Do not \
produce an entry for every field; only for fields with a real, specific problem. If you \
find nothing wrong, return an empty array.

Reply with one JSON array and nothing else -- no prose, no code fences:
[
  {
    "field": "<exact field name from the list given>",
    "severity": "high" | "medium" | "low",
    "issue": "one or two sentences, specific and falsifiable -- cite the photo evidence or the conflicting field name"
  }
]
"""


def _extract_json_array(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    match = re.search(r"\[.*\]", text, re.DOTALL)
    return json.loads(match.group(0) if match else text)


def _format_fields_block(address: str, data: dict) -> str:
    lines = [f"Address: {address}", "", "Populated fields:"]
    for key, val, defn, opts, note in iter_populated_fields(address, data):
        line = f"- {key} = {val!r}"
        if defn:
            line += f"  [defn: {defn}]"
        if opts:
            line += f"  [options: {opts}]"
        if note:
            line += f"  [existing note: {note}]"
        lines.append(line)
    return "\n".join(lines)


def _read_photos(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))


def critique_building(client, address: str, data: dict, model: str = MODEL_ID) -> list[dict]:
    before_photos = _read_photos(BEFORE_ROOT / address)
    after_photos = _read_photos(AFTER_ROOT / address)

    with tempfile.TemporaryDirectory(prefix="critic_") as raw_workdir:
        workdir = Path(raw_workdir)
        blocks: list[dict] = [{"type": "text", "text": _format_fields_block(address, data)}]

        if before_photos:
            blocks.append({"type": "text", "text": "BEFORE photos:"})
            for photo in before_photos:
                prepped = normalize_photo(photo, workdir)
                if prepped:
                    blocks.append(to_image_block(prepped))

        if after_photos:
            blocks.append({"type": "text", "text": "AFTER photos:"})
            for photo in after_photos:
                prepped = normalize_photo(photo, workdir)
                if prepped:
                    blocks.append(to_image_block(prepped))

        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=CRITIC_INSTRUCTIONS,
            messages=[{"role": "user", "content": blocks}],
        )

    findings = _extract_json_array(response.content[0].text)
    for f in findings:
        f["source"] = "llm"
    return findings


# ── Merge ───────────────────────────────────────────────────────────────────

def merge_findings(rule_findings: list[dict], llm_findings: list[dict],
                    valid_keys: set[str]) -> dict[str, dict]:
    """Combine rule + llm findings for one address into {field: {severity, issue, source}}.
    If both flag the same field: severity = max(rule, llm), issue text concatenated,
    source becomes "rule+llm". A finding naming a field that isn't a real key (LLM
    typo/hallucination) is kept under its literal string but tagged -- it simply won't
    match any rendered row in generate_detail_pages.py, so it stays inert rather than
    silently dropped.
    """
    merged: dict[str, dict] = {}
    for f in rule_findings + llm_findings:
        field = f["field"]
        if field not in valid_keys:
            f = {**f, "issue": f"[unrecognized field name from critic] {f['issue']}"}
        existing = merged.get(field)
        if existing is None:
            merged[field] = {"severity": f["severity"], "issue": f["issue"], "source": f["source"]}
        else:
            if SEVERITY_RANK[f["severity"]] > SEVERITY_RANK[existing["severity"]]:
                existing["severity"] = f["severity"]
            existing["issue"] += f" | {f['issue']}"
            if existing["source"] != f["source"]:
                existing["source"] = "rule+llm"
    return merged


# ── Orchestration ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=MODEL_ID)
    args = parser.parse_args()

    resolved = {addr: resolve_building_data(addr, data) for addr, data in BUILDINGS.items()}
    valid_keys = ({k for _, ks in SECTIONS for k in ks}
                  | set().union(*(d.keys() for d in resolved.values())))

    rule_findings_by_addr = run_rule_checks(resolved)

    existing = json.loads(OUTPUT_PATH.read_text()) if OUTPUT_PATH.exists() else {}

    addresses = sorted(resolved)
    client = get_client()
    for n, address in enumerate(addresses, 1):
        print(f"[{n}/{len(addresses)}] {address}")
        try:
            llm_findings = critique_building(client, address, resolved[address], args.model)
        except Exception as exc:
            print(f"    LLM critic failed: {exc}")
            llm_findings = []

        merged = merge_findings(rule_findings_by_addr[address], llm_findings, valid_keys)
        existing[address] = merged
        OUTPUT_PATH.write_text(json.dumps(existing, indent=2))
        print(f"    {len(merged)} finding(s)")

    print(f"\nDone -- {OUTPUT_PATH.name} written for {len(addresses)} addresses.")


if __name__ == "__main__":
    main()
