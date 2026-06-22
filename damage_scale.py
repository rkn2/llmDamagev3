from __future__ import annotations
"""Flood damage severity scale used for model assessments.

Five-point scale (0-4), adapted from FEMA/HAZUS-style flood damage
categories, ranging from no structural impact to total loss.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DamageLevel:
    level: int
    label: str
    description: str


DAMAGE_SCALE = [
    DamageLevel(
        0, "None",
        "Flooding stayed below the first floor: limited to crawlspace or basement "
        "contact with the foundation. Garage interiors may show light water staining. "
        "No backup into living spaces."
    ),
    DamageLevel(
        1, "Minor",
        "Water rose to floor-joist height. Carpets, baseboards, and flooring need "
        "replacement. Drywall is untouched. Light mold risk on the subfloor."
    ),
    DamageLevel(
        2, "Moderate",
        "Water reached partway up interior walls. Lower drywall, outlets, water "
        "heater, and furnace are damaged. First-floor furniture and lower cabinets "
        "are a total loss. Doors and windows may need replacing."
    ),
    DamageLevel(
        3, "Extensive",
        "Water covered the full first floor and reached upper stories. Mid-wall "
        "electrical and upper cabinetry are destroyed. Framing is largely salvageable "
        "but heavily fouled with mold."
    ),
    DamageLevel(
        4, "Complete",
        "Structural framing (studs, joists, trusses) is compromised. Interiors are a "
        "total loss and the foundation may have shifted. The building is not repairable."
    ),
]


def scale_as_prompt_text() -> str:
    lines = ["Flood damage severity scale (0-4):"]
    for d in DAMAGE_SCALE:
        lines.append(f"{d.level} ({d.label}): {d.description}")
    return "\n".join(lines)
