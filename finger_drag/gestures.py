"""Pure gesture state machine: finger frames in, actions out. No device access."""
from __future__ import annotations

import enum
import math
from dataclasses import dataclass

DRAG_START_MM = 2.0
DRAG_RELEASE_S = 0.3
POINTER_COUNTS_PER_MM = 12.0
SWIPE_MM = 15.0
SWIPE_AXIS_RATIO = 1.5


class Direction(enum.Enum):
    NEXT = "next"
    PREVIOUS = "previous"


@dataclass(frozen=True)
class ButtonDown:
    pass


@dataclass(frozen=True)
class ButtonUp:
    pass


@dataclass(frozen=True)
class Move:
    dx: float
    dy: float


@dataclass(frozen=True)
class SwitchWorkspace:
    direction: Direction


Action = ButtonDown | ButtonUp | Move | SwitchWorkspace


class State(enum.Enum):
    IDLE = "idle"
    DRAGGING = "dragging"
    RELEASE_WAIT = "release_wait"
    SWIPE_TRACKING = "swipe_tracking"
    SWIPE_DONE = "swipe_done"


def release_delay(remaining: int) -> float:
    """Seconds to keep the button held once fewer than three fingers remain.

    remaining is the number of fingers still on the pad: 0, 1 or 2.
    Must return a finite value >= 0. Returning 0 releases immediately.
    """
    return DRAG_RELEASE_S


class GestureMachine:
    def __init__(self) -> None:
        self.state = State.IDLE
        self._count = 0
        self._ref: tuple[float, float] | None = None
        self._travel = (0.0, 0.0)
        self._swipe = (0.0, 0.0)
        self._deadline: float | None = None

    def next_deadline(self) -> float | None:
        return self._deadline

    def tick(self, t: float) -> list[Action]:
        if self._deadline is not None and t >= self._deadline:
            self._enter_idle()
            return [ButtonUp()]
        return []

    def update(self, t: float, count: int, cx: float, cy: float) -> list[Action]:
        actions = self.tick(t)
        dx, dy = self._delta(count, cx, cy)
        if self.state is State.IDLE:
            actions += self._idle(count, dx, dy)
        elif self.state is State.DRAGGING:
            actions += self._dragging(t, count, dx, dy)
        elif self.state is State.RELEASE_WAIT:
            actions += self._release_wait(count)
        elif self.state is State.SWIPE_TRACKING:
            actions += self._swipe_tracking(count, dx, dy)
        else:
            actions += self._swipe_done(count)
        return actions

    def _delta(self, count: int, cx: float, cy: float) -> tuple[float, float]:
        # The centroid jumps whenever a different set of fingers is averaged,
        # so a frame where the count changed carries no usable motion.
        changed = count != self._count
        previous = self._ref
        self._count = count
        self._ref = (cx, cy) if count else None
        if changed or previous is None:
            self._travel = (0.0, 0.0)
            return 0.0, 0.0
        return cx - previous[0], cy - previous[1]

    def _enter_idle(self) -> None:
        self.state = State.IDLE
        self._deadline = None
        self._travel = (0.0, 0.0)

    def _enter_swipe(self) -> None:
        self.state = State.SWIPE_TRACKING
        self._deadline = None
        self._swipe = (0.0, 0.0)

    def _idle(self, count: int, dx: float, dy: float) -> list[Action]:
        if count >= 4:
            self._enter_swipe()
        elif count == 3:
            self._travel = (self._travel[0] + dx, self._travel[1] + dy)
            if math.hypot(*self._travel) >= DRAG_START_MM:
                self.state = State.DRAGGING
                return [ButtonDown()]
        return []

    def _dragging(self, t: float, count: int, dx: float, dy: float) -> list[Action]:
        if count >= 4:
            self._enter_swipe()
            return [ButtonUp()]
        if count == 3:
            if dx or dy:
                return [Move(dx * POINTER_COUNTS_PER_MM, dy * POINTER_COUNTS_PER_MM)]
            return []
        self.state = State.RELEASE_WAIT
        self._deadline = t + release_delay(count)
        return []

    def _release_wait(self, count: int) -> list[Action]:
        if count >= 4:
            self._enter_swipe()
            return [ButtonUp()]
        if count == 3:
            self.state = State.DRAGGING
            self._deadline = None
        return []

    def _swipe_tracking(self, count: int, dx: float, dy: float) -> list[Action]:
        if count == 0:
            self._enter_idle()
            return []
        if count < 4:
            # Fingers lifting unevenly must not turn a swipe into a drag.
            return []
        self._swipe = (self._swipe[0] + dx, self._swipe[1] + dy)
        sx, sy = self._swipe
        if abs(sx) >= SWIPE_MM and abs(sx) >= SWIPE_AXIS_RATIO * abs(sy):
            self.state = State.SWIPE_DONE
            # Content follows the fingers: moving left reveals the next one.
            return [SwitchWorkspace(Direction.NEXT if sx < 0 else Direction.PREVIOUS)]
        return []

    def _swipe_done(self, count: int) -> list[Action]:
        if count == 0:
            self._enter_idle()
        return []
