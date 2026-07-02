#!/usr/bin/env python3
"""Match pipeline building addresses to NRHP inventory resources.

Consumer script (may read pipeline files — the parser itself may not; see PROTOCOL.md).
Reads nrhp/nrhp_inventory.json + the pipeline's addresses, writes nrhp/nrhp_matches.json.

Match ladder (first hit wins; confidence decreases down the ladder):
  1. exact_current  — normalized address equals a record's current address
  2. exact_former   — equals a record's former address
  3. range_current  — house number falls inside a current-address range (same street, same parity)
  4. range_former   — same, against former ranges (e.g. pipeline "40 Main St" is inside
                      #72 French Block's former range "32-50 Main Street")

Historic-district address drift is real (LESSONS_LEARNED.md §2): storefront addresses,
E911 parcel addresses, and NRHP inventory addresses are three different conventions.
Range matches are flagged medium-confidence and carry the matched record's full header
so a human can confirm.
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

SUFFIX = {
    "St": "Street", "St.": "Street", "Ave": "Avenue", "Ave.": "Avenue",
    "Rd": "Road", "Rd.": "Road", "Ln": "Lane", "Ln.": "Lane",
    "Ct": "Court", "Ct.": "Court", "Dr": "Drive", "Dr.": "Drive",
    "Pl": "Place", "Pl.": "Place", "Ter": "Terrace", "Ter.": "Terrace",
}


def normalize(addr):
    """'100 Main St, Montpelier, VT 05602' → ('100', 'Main Street')"""
    addr = addr.split(",")[0].strip()
    words = addr.split()
    words = [SUFFIX.get(w, w) for w in words]
    addr = " ".join(words)
    m = re.match(r"(\d+[a-z]?)\s+(.*)", addr)
    return (m.group(1), m.group(2)) if m else (None, addr)


def parse_range(entry_addr):
    """'32-50 Main Street' → (32, 50, 'Main Street'); '54 Elm Street' → (54, 54, 'Elm Street')"""
    m = re.match(r"(\d+)(?:\s*-\s*(\d+))?[a-z]?\s+(.*)", entry_addr.strip())
    if not m:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    return (lo, hi, m.group(3).strip())


def street_eq(a, b):
    return a.lower().rstrip(".") == b.lower().rstrip(".")


def match_one(addr, records):
    num_s, street = normalize(addr)
    num = int(re.sub(r"[a-z]", "", num_s)) if num_s else None

    ladders = [
        ("exact_current", "high", "current", True),
        ("exact_former", "medium-high", "former", True),
        ("range_current", "medium", "current", False),
        ("range_former", "medium", "former", False),
    ]
    for method, confidence, key, exact in ladders:
        for rec in records:
            for entry_addr in rec["addresses"][key]:
                pr = parse_range(entry_addr)
                if not pr or not street_eq(pr[2], street):
                    continue
                if exact:
                    if pr[0] == pr[1] == num:
                        return _hit(rec, method, confidence, entry_addr)
                else:
                    lo, hi, _ = pr
                    if num is not None and lo <= num <= hi and (num - lo) % 2 == 0:
                        return _hit(rec, method, confidence, entry_addr)
    return {"matched": False}


def _hit(rec, method, confidence, entry_addr):
    return {
        "matched": True,
        "resource_number": rec["resource_number"],
        "method": method,
        "confidence": confidence,
        "matched_on": entry_addr,
        "historic_name": rec["historic_name"],
        "header_raw": rec["header_raw"],
        "status": rec["status"],
        "year_built": rec["year_built"],
        "stories": rec["stories"],
        "construction": rec["construction"],
        "cladding": rec["cladding"],
        "roof_shape": rec["roof_shape"],
        "roof_material": rec["roof_material"],
        "styles": rec["styles"],
    }


def main():
    inv = json.loads((HERE / "nrhp_inventory.json").read_text())
    # primary (unsuffixed) resources only — secondary garages/barns share the address
    records = [r for r in inv["records"] if not r["resource_number"][-1].isalpha()
               or not any(x["resource_number"] == str(r["resource_number_int"]) for x in inv["records"])]

    if len(sys.argv) > 1:
        addresses = sys.argv[1:]
    else:
        auto = json.loads((REPO / "building_attributes_auto.json").read_text())
        addresses = list(auto.keys())

    out = {}
    for addr in addresses:
        out[addr] = match_one(addr, records)
    (HERE / "nrhp_matches.json").write_text(json.dumps(out, indent=1))
    for addr, m in out.items():
        tag = f"#{m['resource_number']} via {m['method']} ({m['confidence']})" if m["matched"] else "NO MATCH"
        print(f"{addr:45s} -> {tag}")
    return 0 if all(m["matched"] for m in out.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
