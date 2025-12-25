from __future__ import annotations

import re
from typing import Tuple

GEOMETRY_RE = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")


def parse_geometry(geometry: str) -> Tuple[int, int, int, int]:
    match = GEOMETRY_RE.match(geometry.strip())
    if not match:
        raise ValueError(f"Invalid geometry: {geometry}")
    width, height, x, y = (int(value) for value in match.groups())
    return width, height, x, y


def window_bbox(x: int, y: int, width: int, height: int) -> Tuple[int, int, int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("Window size must be positive.")
    return (x, y, x + width, y + height)
