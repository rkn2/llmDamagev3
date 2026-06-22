from __future__ import annotations
"""Core flood-damage assessment for a single building from before/after photos.

The model only ever sees photos and the address string here -- no historic
context, no ground-truth labels -- and the result is never written back into
a ground-truth file. v2 wrote model predictions into the same xlsx column
used as ground truth, which is the leading suspect for its data leakage;
keeping prediction output and ground truth in separate files is a hard rule
for this rewrite.
"""

import json
import re
import tempfile
from pathlib import Path

from anthropic import AnthropicVertex

from damage_scale import scale_as_prompt_text
from photos import normalize_photo, to_image_block
from vision_client import MODEL_ID

INSTRUCTIONS = f"""You are a structural engineer assessing flood damage.

You will see two photo sets of one building:
- BEFORE: what it looked like before the flood
- AFTER: what it looks like after

Use the BEFORE set only as a baseline for what was already true of the
building, then judge how much of what you see in the AFTER set is flood damage.

{scale_as_prompt_text()}

Reply with one JSON object and nothing else -- no prose, no code fences:
{{
  "damage_level": 0-4, or null if the after-photos don't show enough to judge,
  "confidence": "high", "medium", or "low",
  "estimated_water_depth_ft": a number, or null,
  "assessable": true/false,
  "reasoning": "one or two sentences citing specific visual evidence",
  "limitations": "what would make this assessment more reliable"
}}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(match.group(0) if match else text)


def assess_building(client: AnthropicVertex, address: str, before_photos: list[Path],
                     after_photos: list[Path], model: str = MODEL_ID) -> dict:
    with tempfile.TemporaryDirectory(prefix="flood_") as raw_workdir:
        workdir = Path(raw_workdir)
        blocks: list[dict] = []

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

        blocks.append({"type": "text", "text": f"Address: {address}"})

        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=INSTRUCTIONS,
            messages=[{"role": "user", "content": blocks}],
        )

    assessment = _extract_json(response.content[0].text)
    assessment.update(
        address=address,
        model=model,
        before_photo_count=len(before_photos),
        after_photo_count=len(after_photos),
    )
    return assessment
