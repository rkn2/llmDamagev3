#!/usr/bin/env python3
"""Cross-validate NRHP-parsed attributes against the pipeline's independent sources.

Reads:  nrhp/nrhp_matches.json (parser output routed through the address matcher),
        visual_attributes.json (LLM Street View pass, post-critic),
        facade_cv/facade_cv_h1_3_output.json (pure-CV pass)
Writes: nrhp/nrhp_cross_validation.json

Three fully independent measurement paths for the same attributes:
  A. document (NRHP 2017 inventory, this module — deterministic text parse)
  B. LLM vision (Street View screenshot -> claude)
  C. classical CV (Sobel-Y/FFT + blob detection, facade_cv H1.3)
Agreement across A/B/C is the strongest validation any attribute in this pipeline can
get without a site visit. Disagreement is routed to `findings` with a severity.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# critic-corrected story counts (facade_cv/PROTOCOL.md — treated as GT there)
CRITIC_GT_STORIES = {
    "100 Main St, Montpelier, VT 05602": 3,
    "112 State St, Montpelier, VT 05602": 5,
    "27 Langdon St, Montpelier, VT 05602": 3,
    "40 Main St, Montpelier, VT 05602": 3,
    "54 Elm St, Montpelier, VT 05602": 3,
}

# schema vocab mapping: NRHP free text -> pipeline construction_type_u vocabulary
NRHP_CONSTRUCTION_MAP = {"brick": "URM", "wood frame": "wood frame", "stone": "URM"}


def main():
    matches = json.loads((HERE / "nrhp_matches.json").read_text())
    visual = json.loads((REPO / "visual_attributes.json").read_text())
    cv = json.loads((REPO / "facade_cv" / "facade_cv_h1_3_output.json").read_text())

    out, findings = {}, []
    for addr, m in matches.items():
        if not m.get("matched"):
            findings.append({"severity": "high", "address": addr, "issue": "no NRHP match"})
            continue
        short = addr.split(",")[0]
        v = visual.get(addr, {})
        c = cv.get(addr) or cv.get(short) or {}

        row = {
            "nrhp_resource": m["resource_number"],
            "nrhp_match_confidence": m["confidence"],
            "historic_name": m["historic_name"],
            "nrhp_status": m["status"],
            "year_built_nrhp": m["year_built"],
            "stories": {
                "nrhp_document": m["stories"],
                "llm_vision": v.get("number_stories"),
                "classical_cv": c.get("number_stories"),
                "critic_gt": CRITIC_GT_STORIES.get(addr),
            },
            "construction": {
                "nrhp_document": m["construction"],
                "nrhp_mapped": NRHP_CONSTRUCTION_MAP.get(m["construction"]),
                "pipeline": v.get("construction_type_u"),
            },
            "cladding": {"nrhp_document": m["cladding"], "pipeline": v.get("wall_cladding_u")},
            "roof_shape": {"nrhp_document": m["roof_shape"], "pipeline": v.get("roof_shape_u")},
        }

        s = row["stories"]
        agree = {k: val for k, val in s.items() if val is not None}
        if len(set(agree.values())) > 1:
            findings.append({
                "severity": "medium", "address": addr, "issue": "stories disagreement",
                "detail": s,
            })

        # construction reconciliation
        nm, pl = row["construction"]["nrhp_mapped"], row["construction"]["pipeline"]
        if nm and pl and nm != pl:
            findings.append({
                "severity": "high", "address": addr, "issue": "construction disagreement",
                "detail": {"nrhp": m["construction"], "pipeline": pl},
            })

        # special case the parser surfaces: a post-1978 replacement building inside the
        # district is NOT historic URM regardless of what brick cladding looks like.
        if m["year_built"] and m["year_built"] > 1978 and pl == "URM":
            findings.append({
                "severity": "high", "address": addr,
                "issue": "URM assumption contradicted by NRHP build year",
                "detail": {
                    "year_built_nrhp": m["year_built"],
                    "nrhp_text": "brick clad ... constructed in 1994 (non-contributing due to age)",
                    "implication": "wall system is almost certainly brick veneer over a modern "
                                   "frame, not load-bearing URM; wall_thickness=0.46 m masonry "
                                   "assumption and URM fragility archetype do not apply",
                },
            })
        out[addr] = row

    result = {"buildings": out, "findings": findings}
    (HERE / "nrhp_cross_validation.json").write_text(json.dumps(result, indent=1))
    for addr, row in out.items():
        s = row["stories"]
        print(f"{addr.split(',')[0]:14s} #{row['nrhp_resource']:>4s} {row['year_built_nrhp']} "
              f"stories[doc/llm/cv/gt]={s['nrhp_document']}/{s['llm_vision']}/{s['classical_cv']}/{s['critic_gt']} "
              f"constr[doc->mapped/pipeline]={row['construction']['nrhp_document']}->"
              f"{row['construction']['nrhp_mapped']}/{row['construction']['pipeline']}")
    print(f"\nfindings: {len(findings)}")
    for f in findings:
        print(f"  [{f['severity']}] {f['address'].split(',')[0]}: {f['issue']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
