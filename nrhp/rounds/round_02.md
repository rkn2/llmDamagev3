# Round 2 — P2 sequence state machine

**Node:** P2 (sequence-state-machine)
**Action:** `parse_nrhp.py` stages 1–3: strip per-page NPS continuation-sheet
boilerplate, normalize unicode (`’ – — ½ ‟`), then accept a line-start `N.` candidate
only if it advances the running number (N == expected, or a +2..+4 jump logged loudly)
AND the line carries header evidence (street word | 4-digit year | status | demolished).

## Measure (before → after)

- entries found: 380 → **515 / 563**
- duplicates: 0; rejected candidates: 33; jumps: 1 (`297` missing, accepted 298)
- field coverage (new): status .965, year .99, stories .963, construction .839

## Failure modes (from `nrhp_parse_audit.json`)

1. **Cascade at 517.** Entries #517–530 (the 1989 East State St boundary increase,
   re-numbered into the main sequence) use `517 (formerly 1). 70 East State Street...`
   — the `(formerly N)` clause sits *before* the period, so they never became
   candidates. The machine stalled at expected=517 and rejected every real header
   531–563 as a too-large jump. One format miss cost 47 entries — cascades are the
   failure mode of sequence machines.
2. **#297 doesn't exist.** The resource is split as `297a.` / `297b.` (two 1968
   apartment buildings on one lot). Lettered numbers are part of the grammar, not noise.

## Verdict

Right architecture, incomplete number grammar. → P2.1.
