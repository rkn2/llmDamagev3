from __future__ import annotations
"""Photo loading and preparation for the Claude vision API."""

import base64
import subprocess
from pathlib import Path

MAX_EDGE_PX = 1568  # Claude's recommended max long-edge dimension
MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def normalize_photo(src: Path, workdir: Path) -> Path | None:
    """Return a copy of src in workdir, converted/resized for the API. None if unsupported."""
    ext = src.suffix.lower()

    if ext == ".avif":
        dest = workdir / f"{src.stem}.png"
        converted = subprocess.run(
            ["sips", "-s", "format", "png", str(src), "--out", str(dest)],
            capture_output=True,
        )
        if converted.returncode != 0:
            return None
    elif ext in MIME_BY_EXT:
        dest = workdir / src.name
        dest.write_bytes(src.read_bytes())
    else:
        return None

    subprocess.run(["sips", "-Z", str(MAX_EDGE_PX), str(dest)], capture_output=True)
    return dest


def to_image_block(path: Path) -> dict:
    mime = MIME_BY_EXT.get(path.suffix.lower(), "image/png")
    encoded = base64.standard_b64encode(path.read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": encoded}}
