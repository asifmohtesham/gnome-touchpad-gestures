"""Momentum scrolling: carries a two-finger flick on after the fingers lift.

Pure logic, no device access. While fingers are on the pad libinput does the
scrolling; this only measures how fast they were moving when they left.
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass

from finger_drag.gestures import DRAG_RELEASE_S
from finger_drag.motion import moving_together

# How the glide feels.
GLIDE_TAU_S = 0.5            # slowdown time constant: longer is a longer tail
GLIDE_MIN_MM_S = 40.0        # slowest flick that glides
GLIDE_MAX_MM_S = 600.0       # faster than fingers move; caps a misread speed
GLIDE_STOP_UNITS_S = 480.0   # the glide ends below this; 120 units is one notch
GLIDE_FRAME_S = 0.008        # time between glide steps
GLIDE_LATE_FRAMES = 3        # a late step never covers more than this many

# How finger travel maps to scrolling. SCROLL_UNITS_PER_MM equals what libinput
# scrolls per millimetre, so the page keeps its speed at the lift: 39.37 units
# per mm at 1000 dpi, times libinput's unaccelerated touchpad factor of
# 0.9 * 0.2968, times the 8 wheel units the X driver gives each of those.
SCROLL_UNITS_PER_MM = 84.0
NATURAL_SCROLL = True        # the touchpad's setting; the page follows the fingers

# What counts as scrolling, as opposed to two contacts that merely moved.
GLIDE_MIN_TRAVEL_MM = 3.0    # further than a tap ever travels
SPOIL_LINGER_S = DRAG_RELEASE_S  # a drag still holds its button this long

# How finger speed is measured.
SPEED_WINDOW_S = 0.06        # speed is averaged over this long
SPEED_MIN_SPAN_S = 0.02      # too short a span gives a meaningless speed
LIFT_GRACE_S = 0.10          # fingers may leave this far apart
STILL_S = 0.03               # silence this long before a lift means they stopped


@dataclass(frozen=True)
class Scroll:
    """Wheel motion in high-resolution units; 120 is one notch."""
    dx: float
    dy: float
    first: bool = False  # the first step of a glide


def glide_speed(elapsed: float) -> float:
    """Fraction of the starting speed left after `elapsed` seconds of gliding."""
    return math.exp(-elapsed / GLIDE_TAU_S)


class MomentumMachine:
    def __init__(self) -> None:
        self._count = 0
        self._history: collections.deque = collections.deque()
        self._velocity = (0.0, 0.0)   # mm/s of the last two-finger motion
        self._velocity_at = -math.inf
        self._two_at = -math.inf      # when two fingers last reported
        self._travel = 0.0            # mm scrolled during this touch
        self._spoiled = False         # this touch cannot end in a glide
        self._dragged = False         # this touch has had three or more fingers
        self._no_glide_before = -math.inf
        self._glide: tuple[float, float] | None = None  # starting units/s
        self._started = 0.0
        self._last = 0.0
        self._first = False
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
        # After a stall, skip the distance missed instead of jumping it.
        step = min(t - self._last, GLIDE_LATE_FRAMES * GLIDE_FRAME_S)
        first, self._first = self._first, False
        self._last = t
        self._deadline = t + GLIDE_FRAME_S
        return [Scroll(vx * step, vy * step, first)]

    def update(self, t: float, count: int, cx: float, cy: float,
               regrouped: bool = False, fingers: tuple = (),
               pressed: bool = False) -> list[Scroll]:
        changed = count != self._count or regrouped
        if self._count == 2 and count != 2 and t - self._two_at > STILL_S:
            # A pad reports nothing while fingers rest, so a gap before the
            # fingers leave means they had stopped moving.
            self._velocity = (0.0, 0.0)
        self._count = count
        if count > 0:
            self._touching(t, count, cx, cy, changed, fingers, pressed)
        else:
            self._lifted(t)
        return []

    def interrupt(self, t: float) -> list[Scroll]:
        """Finger state was lost; this is not a lift, so nothing may glide."""
        self._end_glide()
        self._forget_touch()
        self._count = 0
        return []

    def _touching(self, t: float, count: int, cx: float, cy: float,
                  changed: bool, fingers: tuple, pressed: bool) -> None:
        if self._deadline is not None:
            # Any touch stops a glide.
            self._end_glide()
        if count >= 3 or pressed:
            # Drags, swipes and clicks are not scrolling.
            self._spoiled = True
        if count >= 3:
            self._dragged = True
        if count != 2:
            # A lone finger may be the second one lifting late, so the speed
            # measured so far is kept; it expires through LIFT_GRACE_S.
            self._history.clear()
            return
        self._two_at = t
        if changed:
            # A different set of fingers: the centroid is not comparable.
            self._history.clear()
        if self._history:
            _, x, y, _ = self._history[-1]
            self._travel += math.hypot(cx - x, cy - y)
        self._history.append((t, cx, cy, fingers))
        while t - self._history[0][0] > SPEED_WINDOW_S:
            self._history.popleft()
        then, x, y, before = self._history[0]
        span = t - then
        if span < SPEED_MIN_SPAN_S:
            return
        shared = (cx - x, cy - y)
        self._velocity_at = t
        if fingers and not moving_together(before, fingers, shared):
            self._velocity = (0.0, 0.0)
        else:
            self._velocity = (shared[0] / span, shared[1] / span)

    def _lifted(self, t: float) -> None:
        fresh = t - self._velocity_at <= LIFT_GRACE_S
        fast = math.hypot(*self._velocity) >= GLIDE_MIN_MM_S
        scrolled = self._travel >= GLIDE_MIN_TRAVEL_MM
        # A drag holds its button for a moment after its fingers leave, and
        # nothing may scroll under a held button. Only the lift is judged, so
        # one refused flick cannot refuse the next.
        released = t >= self._no_glide_before
        if (fresh and fast and scrolled and released and not self._spoiled
                and self._deadline is None):
            self._glide = self._wheel_velocity(*self._velocity)
            self._started = self._last = t
            self._first = True
            self._deadline = t + GLIDE_FRAME_S
        if self._dragged:
            self._no_glide_before = t + SPOIL_LINGER_S
        self._forget_touch()

    def _wheel_velocity(self, vx: float, vy: float) -> tuple[float, float]:
        # One axis only, as libinput itself scrolls: it keeps a single
        # direction per device, and a wheel turning both ways at once makes
        # it hold events back until half a notch has built up.
        if abs(vy) >= abs(vx):
            vx = 0.0
        else:
            vy = 0.0
        vx = max(-GLIDE_MAX_MM_S, min(GLIDE_MAX_MM_S, vx))
        vy = max(-GLIDE_MAX_MM_S, min(GLIDE_MAX_MM_S, vy))
        # Wheel up (positive) moves the page down; scrolling right (positive)
        # moves the page left. With natural scrolling the page follows the
        # fingers, so down is up and right is left.
        follow = 1.0 if NATURAL_SCROLL else -1.0
        return (-follow * vx * SCROLL_UNITS_PER_MM,
                follow * vy * SCROLL_UNITS_PER_MM)

    def _forget_touch(self) -> None:
        self._history.clear()
        self._velocity = (0.0, 0.0)
        self._velocity_at = -math.inf
        self._travel = 0.0
        self._spoiled = False
        self._dragged = False

    def _end_glide(self) -> None:
        self._glide = None
        self._deadline = None
