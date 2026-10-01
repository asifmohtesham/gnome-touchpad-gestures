"""Telling fingers that move together from contacts that merely share a pad."""
from __future__ import annotations

import math

TOGETHER_RATIO = 0.5  # each finger covers this much of the shared motion


def moving_together(before: tuple, after: tuple, shared: tuple) -> bool:
    """Whether every finger took a fair part in the motion they share.

    A resting thumb takes none and a pinching finger goes against it; in both
    cases the centroid moves although the fingers are not acting as one.
    """
    length = math.hypot(*shared)
    if length == 0.0:
        return False
    for (x0, y0), (x1, y1) in zip(before, after):
        along = ((x1 - x0) * shared[0] + (y1 - y0) * shared[1]) / length
        if along < TOGETHER_RATIO * length:
            return False
    return True
