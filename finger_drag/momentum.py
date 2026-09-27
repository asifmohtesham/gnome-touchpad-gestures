"""Momentum scrolling: carries a two-finger flick on after the fingers lift.

Pure logic, no device access. While fingers are on the pad libinput does the
scrolling; this only measures how fast they were moving when they left.
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass

# How the glide feels.
GLIDE_TAU_S = 0.5            # slowdown time constant: longer is a longer tail
GLIDE_MIN_MM_S = 40.0        # slowest flick that glides
GLIDE_STOP_UNITS_S = 40.0    # the glide ends below this; 120 units is one notch
GLIDE_FRAME_S = 0.008        # time between glide steps

# How finger travel maps to scrolling. SCROLL_UNITS_PER_MM should equal what
# libinput scrolls per millimetre, so the page keeps its speed at the lift.
SCROLL_UNITS_PER_MM = 94.0
NATURAL_SCROLL = True        # the touchpad's setting; the page follows the fingers
AXIS_LOCK_RATIO = 2.0        # a flick this lopsided glides along one axis only

# How finger speed is measured.
SPEED_WINDOW_S = 0.06        # speed is averaged over this long
SPEED_MIN_SPAN_S = 0.02      # too short a span gives a meaningless speed
LIFT_GRACE_S = 0.10          # fingers may leave this far apart


@dataclass(frozen=True)
class Scroll:
    """Wheel motion in high-resolution units; 120 is one notch."""
    dx: float
    dy: float


def glide_speed(elapsed: float) -> float:
    """Fraction of the starting speed left after `elapsed` seconds of gliding."""
    return math.exp(-elapsed / GLIDE_TAU_S)


class MomentumMachine:
    def __init__(self) -> None:
        self._count = 0
        self._history: collections.deque = collections.deque()
        self._velocity = (0.0, 0.0)   # mm/s of the last two-finger motion
        self._velocity_at = -math.inf
        self._spoiled = False         # this touch has had three or more fingers
        self._glide: tuple[float, float] | None = None  # starting units/s
        self._started = 0.0
        self._last = 0.0
        self._deadline: float | None = None

    def next_deadline(self) -> float | None:
        return self._deadline

    def tick(self, t: float) -> list[Scroll]:
        if self._deadline is None or t < self._deadline:
            return []
        remaining = glide_speed(t - self._started)
        vx, vy = self._glide[0] * remaining, self._glide[1] * remaining
        if math.hypot(vx, vy) < GLIDE_STOP_UNITS_S:
            self._end_glide()
            return []
        step = t - self._last
        self._last = t
        self._deadline = t + GLIDE_FRAME_S
        return [Scroll(vx * step, vy * step)]

    def update(self, t: float, count: int, cx: float, cy: float,
               regrouped: bool = False) -> list[Scroll]:
        changed = count != self._count or regrouped
        self._count = count
        if count > 0:
            self._touching(t, count, cx, cy, changed)
        else:
            self._lifted(t)
        return []

    def interrupt(self, t: float) -> list[Scroll]:
        """Finger state was lost; this is not a lift, so nothing may glide."""
        self._end_glide()
        self._history.clear()
        self._forget_speed()
        self._spoiled = False
        self._count = 0
        return []

    def _touching(self, t: float, count: int, cx: float, cy: float,
                  changed: bool) -> None:
        if self._deadline is not None:
            # Any touch stops a glide.
            self._end_glide()
        if count >= 3:
            self._spoiled = True
        if count != 2:
            # A lone finger may be the second one lifting late, so the speed
            # measured so far is kept; it expires through LIFT_GRACE_S.
            self._history.clear()
            return
        if changed:
            # A different set of fingers: the centroid is not comparable.
            self._history.clear()
        self._history.append((t, cx, cy))
        while t - self._history[0][0] > SPEED_WINDOW_S:
            self._history.popleft()
        then, x, y = self._history[0]
        span = t - then
        if span >= SPEED_MIN_SPAN_S:
            self._velocity = ((cx - x) / span, (cy - y) / span)
            self._velocity_at = t

    def _lifted(self, t: float) -> None:
        fresh = t - self._velocity_at <= LIFT_GRACE_S
        fast = math.hypot(*self._velocity) >= GLIDE_MIN_MM_S
        if fresh and fast and not self._spoiled and self._deadline is None:
            self._glide = self._wheel_velocity(*self._velocity)
            self._started = self._last = t
            self._deadline = t + GLIDE_FRAME_S
        self._history.clear()
        self._forget_speed()
        self._spoiled = False

    def _wheel_velocity(self, vx: float, vy: float) -> tuple[float, float]:
        if abs(vy) >= AXIS_LOCK_RATIO * abs(vx):
            vx = 0.0
        elif abs(vx) >= AXIS_LOCK_RATIO * abs(vy):
            vy = 0.0
        # Wheel up (positive) moves the page down; scrolling right (positive)
        # moves the page left. With natural scrolling the page follows the
        # fingers, so down is up and right is left.
        follow = 1.0 if NATURAL_SCROLL else -1.0
        return (-follow * vx * SCROLL_UNITS_PER_MM,
                follow * vy * SCROLL_UNITS_PER_MM)

    def _forget_speed(self) -> None:
        self._velocity = (0.0, 0.0)
        self._velocity_at = -math.inf

    def _end_glide(self) -> None:
        self._glide = None
        self._deadline = None
