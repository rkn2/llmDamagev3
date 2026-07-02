#!/usr/bin/env python3
"""Deterministic cross-source invariant checks over the pipeline's JSON artifacts.

The automated version of the manual audits that caught this repo's worst bugs:
the 54 Elm "dry but street flooded" miscall (CONTEXT.md), the 100 Main / 27 Langdon
footprint collision (LESSONS_LEARNED.md §1), and the 112 State URM-that-isn't
(nrhp/nrhp_cross_validation.json). Each of those is now a rule that fails loudly.

Reads (all optional except building_attributes_auto.json):
    building_attributes_auto.json, flood_depth_hwm.json, visual_attributes.json,
    nrhp/nrhp_matches.json, facade_cv/facade_cv_h1_3_output.json
Writes:
    sanity_findings.json   — [{rule, severity, address, detail}, ...]

Run standalone (`python3 sanity_checks.py`) or at the end of run_pipeline.py.
Exit code 1 iff any high-severity finding — suitable as a gate.

Adjudications: a finding that has been investigated and resolved downstream (e.g. the
112 State URM call is superseded in generate_detail_pages.py, but the raw LLM output in
visual_attributes.json intentionally still says URM) is recorded in
sanity_adjudications.json keyed by "rule|address" with a rationale. Adjudicated findings
are downgraded to info and stop gating — but never deleted, so the disagreement between
raw sources stays visible.

Severity meanings:
  high   — a downstream number is likely wrong; do not publish without resolving
  medium — sources disagree; needs a documented adjudication
  info   — worth a look; not blocking
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
OUT = REPO / "sanity_findings.json"

# NRHP free-text construction -> pipeline construction_type_u vocabulary
NRHP_CONSTRUCTION_MAP = {"brick": "URM", "stone": "URM", "wood frame": "wood frame"}
UNCERTAIN_MARGIN_FT = 1.0  # keep in sync with compute_flood_depth_hwm.py


def _load(rel: str) -> dict | None:
    p = REPO / rel
    return json.loads(p.read_text()) if p.exists() else None


def run_checks() -> list[dict]:
    attrs = _load("building_attributes_auto.json") or {}
    flood = _load("flood_depth_hwm.json") or {}
    visual = _load("visual_attributes.json") or {}
    nrhp = _load("nrhp/nrhp_matches.json") or {}
    cv = _load("facade_cv/facade_cv_h1_3_output.json") or {}

    findings: list[dict] = []

    def add(rule: str, severity: str, address: str, detail: str, **extra):
        findings.append({"rule": rule, "severity": severity,
                         "address": address, "detail": detail, **extra})

    # ---- R1: flood-state internal consistency -------------------------------
    for addr, f in flood.items():
        if f.get("above_grade_ft", 0) > 0 and not f.get("flooded"):
            add("flood_state_consistency", "high", addr,
                f"above_grade={f['above_grade_ft']} ft but flooded={f.get('flooded')} — "
                "water at the perimeter cannot coexist with a 'dry' call "
                "(the 54 Elm failure mode)")
        margin = abs(f.get("above_ffe_ft", 99.0))
        band = max(UNCERTAIN_MARGIN_FT, 2 * f.get("wse_sigma_ft", 0.0))
        if margin < band and not f.get("uncertain"):
            add("uncertainty_flag_missing", "high", addr,
                f"|above_ffe|={margin:.2f} ft is inside the noise band ({band:.2f} ft) "
                "but uncertain=False — binary call not supported by the data")

    # ---- R2: above_grade_only needs external resolution ---------------------
    for addr, f in flood.items():
        if f.get("status") == "above_grade_only" and "confirmed_flooded" not in f:
            add("unresolved_above_grade_only", "high", addr,
                "street/perimeter inundated but front FFE not overtopped; interior "
                "state unresolved — verify via news/business records/site visit and "
                "record confirmed_flooded + flood_evidence in flood_depth_hwm.json")

    # ---- R3: URM claim vs NRHP build year -----------------------------------
    for addr, m in nrhp.items():
        if not m.get("matched"):
            add("nrhp_unmatched", "info", addr, "no NRHP resource match")
            continue
        v = visual.get(addr, {})
        year = m.get("year_built")
        if year and year > 1978 and v.get("construction_type_u") == "URM":
            add("urm_after_1978", "high", addr,
                f"pipeline says URM but NRHP resource #{m['resource_number']} is a "
                f"{year} building — post-1978 brick is veneer over frame, not "
                "load-bearing URM; wall_thickness/archetype assumptions do not apply")

    # ---- R4: construction source disagreement -------------------------------
    for addr, m in nrhp.items():
        if not m.get("matched"):
            continue
        mapped = NRHP_CONSTRUCTION_MAP.get(m.get("construction") or "")
        pipe = visual.get(addr, {}).get("construction_type_u")
        if mapped and pipe and mapped != pipe:
            add("construction_disagreement", "high", addr,
                f"NRHP '{m['construction']}' (→{mapped}) vs pipeline '{pipe}'")

    # ---- R5: stories disagreement across sources ----------------------------
    for addr in attrs:
        vals = {
            "nrhp": (nrhp.get(addr) or {}).get("stories"),
            "llm": (visual.get(addr) or {}).get("number_stories"),
            "cv": (cv.get(addr) or {}).get("number_stories"),
        }
        present = {k: v for k, v in vals.items() if v is not None}
        if len({float(v) for v in present.values()}) > 1:
            add("stories_disagreement", "medium", addr,
                f"sources disagree: {present} — adjudicate and record the ruling")

    # ---- R6: footprint collisions (identical auto-collected geometry) -------
    seen: dict[tuple, str] = {}
    for addr, a in attrs.items():
        key = (a.get("building_area_m2"),
               a.get("approx_wall_length_a_m"), a.get("approx_wall_length_b_m"))
        if key in seen and key != (None, None, None):
            add("footprint_collision", "high", addr,
                f"identical footprint values {key} as {seen[key].split(',')[0]} — "
                "geocode/Overpass collision, disambiguate via parcel layer "
                "(LESSONS_LEARNED.md §1/§3)", other=seen[key])
        else:
            seen[key] = addr

    # ---- R7: front FFE must not be below ground -----------------------------
    for addr, a in attrs.items():
        ffe, gnd = a.get("first_floor_elevation_m"), a.get("ground_elevation_m")
        if ffe is not None and gnd is not None and ffe < gnd:
            add("ffe_below_grade", "high", addr,
                f"front FFE {ffe} m < ground {gnd} m — step height cannot be negative")

    # ---- R8: NRHP year plausibility ------------------------------------------
    for addr, m in nrhp.items():
        y = m.get("year_built")
        if y and not (1780 <= y <= 2017):
            add("nrhp_year_implausible", "medium", addr, f"year_built={y}")

    # ---- R9: every attrs building present in flood output --------------------
    for addr in attrs:
        if flood and addr not in flood:
            add("missing_flood_record", "medium", addr,
                "in building_attributes_auto.json but absent from flood_depth_hwm.json")

    return findings


def apply_adjudications(findings: list[dict]) -> list[dict]:
    adj = _load("sanity_adjudications.json") or {}
    for f in findings:
        ruling = adj.get(f"{f['rule']}|{f['address']}")
        if ruling:
            f["severity"] = "info"
            f["adjudicated"] = True
            f["adjudication"] = ruling
    return findings


def main() -> int:
    findings = apply_adjudications(run_checks())
    OUT.write_text(json.dumps(findings, indent=1))
    counts = {"high": 0, "medium": 0, "info": 0}
    for f in findings:
        counts[f["severity"]] += 1
        print(f"[{f['severity']:6s}] {f['rule']:28s} {f['address'].split(',')[0]}: {f['detail'][:110]}")
    print(f"\n{len(findings)} finding(s): {counts} -> {OUT.name}")
    return 1 if counts["high"] else 0


if __name__ == "__main__":
    sys.exit(main())
