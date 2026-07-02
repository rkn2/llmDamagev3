#!/usr/bin/env python3
"""Parse the Montpelier Historic District NRHP 2017 amendment PDF into structured
per-resource records.

Reads ONLY the PDF (leakage rail: no pipeline JSONs — see nrhp/PROTOCOL.md).
Writes:
  nrhp/nrhp_inventory.json   — one record per numbered resource (#1–563)
  nrhp/nrhp_parse_audit.json — rejected header candidates, unparsed fields, warnings

Stages (kept separable so a fix in one can't silently regress another):
  1. extract  — pypdf text layer, per page
  2. clean    — strip NPS continuation-sheet boilerplate, normalize unicode
  3. segment  — sequence-driven state machine over numbered header candidates
  4. header   — address(es) / historic name / dates / status / demolished
  5. body     — construction, cladding, stories, roof shape+material, style

Inventory format (recon notes, nrhp/PROCESS_NOTES.md):
  N. <address>[ & <address>] [(formerly <range>)], [<name>,] [dates]. <status>
  <blank line>
  <Construction>, [cladding,] <N stories>, <roof> roof [sheathed in <material>]. ...
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PDF = REPO / "montpelierContext" / "National Register of Historic Places- Downtown Montpelier update (2017).pdf"
OUT_INVENTORY = Path(__file__).resolve().parent / "nrhp_inventory.json"
OUT_AUDIT = Path(__file__).resolve().parent / "nrhp_parse_audit.json"

MAX_RESOURCE = 563  # stated by the document's own inventory numbering (recon-verified)

# ---------------------------------------------------------------- stage 1: extract

def extract_pages(pdf_path):
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    return [(page.extract_text() or "") for page in reader.pages]


# ---------------------------------------------------------------- stage 2: clean

# The NPS continuation-sheet boilerplate repeats on every page, ending with the
# "Town, county and State" caption line.
BOILERPLATE = re.compile(
    r"NPS Form 10-900-a.*?Town, county and State\s*", re.S
)

UNICODE_MAP = {
    "’": "'", "‘": "'", "‟": '"', "“": '"', "”": '"',
    "–": "-", "—": "-", "½": "1/2", " ": " ",
}


def clean_page(text):
    text = BOILERPLATE.sub("\n", text)
    for k, v in UNICODE_MAP.items():
        text = text.replace(k, v)
    return text


# ---------------------------------------------------------------- stage 3: segment

# Number forms seen in the document (recon + audit, rounds 1-3):
#   "184. "            — plain
#   "297a. " / "297b." — one resource split into lettered parts
#   "517 (formerly 1). " — 1989 boundary-increase entries renumbered into the main sequence
HEADER_CAND = re.compile(
    r"^[ \t]*(\d{1,3})([a-z])?\s*(?:\(formerly\s+#?\s*(\d+)\))?\.[ \t]+(\S[^\n]*)$", re.M
)

# A real inventory header line contains at least one of: a street-type word, a
# 4-digit year, a contributing status, or a demolition note. List items inside
# descriptions ("... 2. the second bay ...") almost never do at line start.
HEADER_EVIDENCE = re.compile(
    r"(Street|Avenue|Lane|Road|Terrace|Court|Circle|Drive|Place|Way|Loop|"
    r"\b1[6-9]\d\d\b|\b20[01]\d\b|[Cc]ontributing|demolished|Demolished)"
)


def segment(full_text, audit):
    """Sequence-driven state machine: accept candidate N only if it advances the
    inventory numbering (N == expected, or a small forward jump — the document
    itself has no gaps 1..563, so jumps indicate *our* missed headers and are
    logged loudly)."""
    cands = []
    for m in HEADER_CAND.finditer(full_text):
        cands.append(
            {
                "num": int(m.group(1)),
                "suffix": m.group(2) or "",
                "former_number": int(m.group(3)) if m.group(3) else None,
                "off": m.start(),
                "line": m.group(4),
            }
        )

    accepted = []
    last_num, last_suffix = 0, ""
    for c in cands:
        num, suffix, line = c["num"], c["suffix"], c["line"]
        advances = (num == last_num + 1) or (
            num == last_num and suffix and suffix > last_suffix  # 297a → 297b
        )
        small_jump = last_num + 1 < num <= last_num + 4
        if advances and HEADER_EVIDENCE.search(line):
            accepted.append(c)
            last_num, last_suffix = num, suffix
        elif small_jump and HEADER_EVIDENCE.search(line):
            # forward jump: we missed expected..num-1 — accept but log loudly
            audit["sequence_jumps"].append(
                {"missed": list(range(last_num + 1, num)), "accepted": num, "line": line[:120]}
            )
            accepted.append(c)
            last_num, last_suffix = num, suffix
        else:
            audit["rejected_candidates"].append(
                {"num": num, "expected": last_num + 1, "line": line[:120]}
            )

    blocks = []
    for i, c in enumerate(accepted):
        end = accepted[i + 1]["off"] if i + 1 < len(accepted) else len(full_text)
        blocks.append((c, full_text[c["off"]:end]))
    return blocks


# ---------------------------------------------------------------- stage 4: header

STATUS_RE = re.compile(r"\b(Non-?\s?[Cc]ontributing|Contributing)\b")
DEMOLISHED_RE = re.compile(r"\(?\bdemolished(?:\s+in)?(?:\s+c?\.?\s*(\d{4}s?))?\)?", re.I)
YEAR_RE = re.compile(r"\b(1[6-9]\d\d|20[01]\d)(s)?\b")
DATE_TOKEN = re.compile(r"c?\.?\s*\b(?:1[6-9]\d\d|20[01]\d)")
STREET_TYPE = (
    "Street|Avenue|Lane|Road|Terrace|Court|Circle|Drive|Place|Way|Loop"
)
ADDR_RE = re.compile(
    rf"(\d+[\dA-Za-z\-&,\s(){{}}\./]*?\s(?:{STREET_TYPE})\b|[A-Z][A-Za-z\s]+(?:{STREET_TYPE})\b)"
)


def split_header_body(block):
    """Header runs to the first blank line (page-cleaned text keeps blank
    separators); headers may wrap over 1-2 lines."""
    m = re.search(r"\n\s*\n", block)
    if m:
        header = block[: m.start()]
        body = block[m.end():]
    else:
        lines = block.split("\n", 1)
        header, body = lines[0], (lines[1] if len(lines) > 1 else "")
    header = re.sub(r"\s+", " ", header).strip()
    return header, body


def parse_header(cand, header, audit):
    num = cand["num"]
    rec = {
        "resource_number": f"{num}{cand['suffix']}",
        "resource_number_int": num,
        "former_number": cand["former_number"],
        "header_raw": header,
    }
    text = re.sub(rf"^{num}{cand['suffix']}\s*(?:\(formerly\s+#?\s*\d+\))?\.\s*", "", header)

    smatch = STATUS_RE.search(text)
    if smatch:
        rec["status"] = (
            "non-contributing" if smatch.group(1).lower().startswith("non") else "contributing"
        )
    else:
        rec["status"] = None

    dmatch = DEMOLISHED_RE.search(text)
    # "(demolished ...) replaced with ..." = the record describes the REPLACEMENT
    # building (e.g. #487, 112 State St 1994); the demolition applies to its
    # predecessor, not the current resource.
    rec["replaced"] = bool(dmatch and re.search(r"replaced\s+with", text, re.I))
    rec["demolished"] = bool(dmatch) and not rec["replaced"]
    rec["demolished_year"] = dmatch.group(1) if dmatch and dmatch.group(1) else None

    if rec["replaced"]:
        # predecessor info sits before "replaced with"; the current building after
        text = re.split(r"replaced\s+with", text, flags=re.I)[1]
        text = STATUS_RE.split(text)[0] + (f" {smatch.group(1)}" if smatch else "")

    # years: first date token outside a "(demolished ...)" clause = build year
    no_demo = DEMOLISHED_RE.sub(" ", text)
    years = YEAR_RE.findall(no_demo)
    rec["year_built"] = int(years[0][0]) if years else None
    rec["year_built_circa"] = bool(re.search(r"c\.\s*" + str(rec["year_built"]), no_demo)) if years else None
    rec["all_years"] = [int(y[0]) for y in years]

    # addresses: text up to the first comma-delimited segment containing a date
    # or status is address+name territory. Split segments on commas.
    pre = STATUS_RE.split(no_demo)[0]
    segs = [s.strip(" .") for s in pre.split(",")]
    addr_segs, name_segs = [], []
    for seg in segs:
        if not seg:
            continue
        if DATE_TOKEN.search(seg) and not re.search(rf"(?:{STREET_TYPE})\b", seg):
            continue  # pure date segment
        if re.search(rf"(?:{STREET_TYPE})\b", seg):
            addr_segs.append(seg)
        else:
            name_segs.append(seg)
    rec["addresses"] = _explode_addresses(addr_segs)
    name_segs = list(dict.fromkeys(name_segs))  # dedupe, keep order
    rec["historic_name"] = ", ".join(name_segs) or None
    if not rec["addresses"]["current"]:
        audit["headers_without_address"].append({"num": rec["resource_number"], "header": header[:140]})
    return rec


def _explode_addresses(addr_segs):
    """'90 Main Street & 27 Langdon Street (formerly 90-98 Main Street)' →
    current: ['90 Main Street', '27 Langdon Street'], former: ['90-98 Main Street'].
    '54 (formerly 52-54) Elm Street' → current ['54 Elm Street'], former ['52-54 Elm Street']."""
    current, former = [], []
    for seg in addr_segs:
        formers = re.findall(r"\(formerly ([^)]*)\)", seg)
        seg_wo = re.sub(r"\(formerly [^)]*\)", "", seg)
        seg_wo = re.sub(r"\([^)]*\)", "", seg_wo)  # drop other parentheticals
        seg_wo = re.sub(r"\s+", " ", seg_wo).strip(" .")
        street = re.search(rf"([A-Z][A-Za-z\s]*?(?:{STREET_TYPE}))\b", seg_wo)
        street_name = street.group(1).strip() if street else None
        for part in re.split(r"\s*&\s*|\s+and\s+", seg_wo):
            part = part.strip(" .")
            if not part:
                continue
            if street_name and not re.search(rf"(?:{STREET_TYPE})\b", part):
                part = f"{part} {street_name}"
            current.append(part)
        for f in formers:
            for part in re.split(r"\s*&\s*|\s+and\s+", f.strip()):
                part = part.strip(" .")
                if not part:
                    continue
                if street_name and not re.search(rf"(?:{STREET_TYPE})\b", part):
                    part = f"{part} {street_name}"
                former.append(part)
    return {"current": current, "former": former}


# ---------------------------------------------------------------- stage 5: body

WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
STORIES_RE = re.compile(
    r"\b(one|two|three|four|five|six|\d)\s*(?:and\s+(?:a\s+|one\s+)?half\s*|\s*1/2\s*)?[- ]?stor(?:y|ies)\b",
    re.I,
)
HALF_RE = re.compile(r"\b(?:one|two|three|four|five|six|\d)\s*(?:1/2|and\s+(?:a|one)\s+half)\s*[- ]?stor(?:y|ies)\b", re.I)
CONSTRUCTION_RE = re.compile(
    r"^\s*(Brick veneer|Brick|Wood frame|Wood-frame|Stone|Granite|Marble|Concrete block|"
    r"Concrete|Steel frame|Steel|Log|Post and beam)\b", re.I
)
CLADDING_RE = re.compile(
    r"\b(clapboard(?:ed|s)?|wood shingle[sd]?|shingle[sd]? sid|vinyl sid|aluminum sid|"
    r"asbestos(?: shingle)? sid|asphalt sid|board and batten|stucco(?:ed)?|brick veneer|"
    r"novelty sid|composite sid|flushboard)\w*", re.I
)
ROOF_SHAPE_RE = re.compile(
    r"\b(flat|gabled?|hipped?|mansard|gambrel|shed|saltbox|jerkinhead|pyramidal|conical|barrel)\b[^.]{0,20}?roofs?",
    re.I,
)
ROOF_MATERIAL_RE = re.compile(r"roofs?[^.]{0,40}?sheathed in ([^.,;]+)", re.I)
STYLES = [
    "Italianate", "Greek Revival", "Queen Anne", "Second Empire", "Romanesque Revival",
    "Romanesque", "Colonial Revival", "Classical Revival", "Neoclassical", "Beaux Arts",
    "Gothic Revival", "Federal", "Art Deco", "Moderne", "Craftsman", "Bungalow",
    "Cape Cod", "Tudor Revival", "Shingle Style", "Stick Style", "Ranch", "Foursquare",
    "vernacular",
]


def parse_body(rec, body, audit):
    first_para = body.strip().split("\n\n")[0]
    flat = re.sub(r"\s+", " ", body).strip()
    first_sentence = re.sub(r"\s+", " ", first_para).split(". ")[0] if first_para else ""

    cmatch = CONSTRUCTION_RE.match(first_sentence)
    rec["construction"] = cmatch.group(1).lower().replace("wood-frame", "wood frame") if cmatch else None

    # stories: prefer the first sentence (structural summary), fall back to full text.
    # For "replaced with" records the body opens by describing the demolished
    # predecessor — take the LAST stories mention (the present building) instead.
    for scope in (first_sentence, flat):
        matches = list(STORIES_RE.finditer(scope))
        sm = (matches[-1] if rec.get("replaced") else matches[0]) if matches else None
        if sm:
            n = WORD_NUM.get(sm.group(1).lower(), None)
            if n is None:
                try:
                    n = int(sm.group(1))
                except ValueError:
                    n = None
            if n is not None:
                rec["stories"] = n + (0.5 if HALF_RE.search(scope[: sm.end() + 4]) else 0.0)
                rec["stories_source"] = "first_sentence" if scope is first_sentence else "full_text"
                break
    else:
        rec["stories"] = None
        rec["stories_source"] = None

    clm = CLADDING_RE.search(first_sentence) or CLADDING_RE.search(flat[:400])
    rec["cladding"] = clm.group(0).lower().rstrip("d ").replace(" sid", " siding") if clm else None

    rsm = ROOF_SHAPE_RE.search(first_sentence) or ROOF_SHAPE_RE.search(flat[:400])
    rec["roof_shape"] = rsm.group(1).lower() if rsm else None
    rmm = ROOF_MATERIAL_RE.search(flat[:600])
    rec["roof_material"] = rmm.group(1).strip().lower() if rmm else None

    rec["styles"] = [s for s in STYLES if re.search(rf"\b{re.escape(s)}\b", flat, re.I if s == "vernacular" else 0)]
    rec["description_first_sentence"] = first_sentence[:300]
    rec["description_chars"] = len(flat)

    if rec["stories"] is None and not rec["demolished"] and rec["description_chars"] > 200:
        audit["no_stories"].append({"num": rec["resource_number"], "first_sentence": first_sentence[:140]})
    return rec


# ---------------------------------------------------------------- main

def main():
    audit = {
        "rejected_candidates": [],
        "sequence_jumps": [],
        "headers_without_address": [],
        "no_stories": [],
        "warnings": [],
    }
    pages = extract_pages(PDF)
    cleaned = [clean_page(p) for p in pages]
    full = "\n".join(cleaned)

    blocks = segment(full, audit)
    records = []
    for cand, block in blocks:
        header, body = split_header_body(block)
        rec = parse_header(cand, header, audit)
        rec = parse_body(rec, body, audit)
        records.append(rec)

    keys = [r["resource_number"] for r in records]
    nums = [r["resource_number_int"] for r in records]
    summary = {
        "entries_found": len(records),
        "distinct_numbers": len(set(nums)),
        "expected": MAX_RESOURCE,
        "duplicates": sorted({k for k in keys if keys.count(k) > 1}),
        "missing": sorted(set(range(1, MAX_RESOURCE + 1)) - set(nums)),
        "rejected_candidates": len(audit["rejected_candidates"]),
        "sequence_jumps": len(audit["sequence_jumps"]),
    }
    OUT_INVENTORY.write_text(json.dumps({"summary": summary, "records": records}, indent=1))
    OUT_AUDIT.write_text(json.dumps(audit, indent=1))
    print(json.dumps(summary, indent=2))
    cov = {
        f: sum(1 for r in records if r.get(f) is not None) / max(len(records), 1)
        for f in ("status", "year_built", "stories", "construction", "roof_shape")
    }
    print("coverage:", json.dumps({k: round(v, 3) for k, v in cov.items()}))
    return 0 if not summary["missing"] and not summary["duplicates"] else 1


if __name__ == "__main__":
    sys.exit(main())
