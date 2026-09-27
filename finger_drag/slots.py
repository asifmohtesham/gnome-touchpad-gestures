"""Multitouch protocol B slot tracking: raw events in, finger frames out."""
from __future__ import annotations

from dataclasses import dataclass

from evdev import ecodes as e

FALLBACK_WIDTH_MM = 100.0
FALLBACK_HEIGHT_MM = 60.0


def units_per_mm(minimum: int, maximum: int, resolution: int, fallback_mm: float) -> float:
    if resolution > 0:
        return float(resolution)
    span = maximum - minimum
    if span <= 0:
        raise ValueError("touchpad axis reports neither a resolution nor a range")
    return span / fallback_mm


@dataclass(frozen=True)
class Frame:
    count: int
    cx: float
    cy: float


@dataclass
class _Slot:
    active: bool = False
    x: int | None = None
    y: int | None = None


class SlotTracker:
    def __init__(self, x_units_per_mm: float, y_units_per_mm: float,
                 current_slot: int = 0) -> None:
        self._x_units_per_mm = x_units_per_mm
        self._y_units_per_mm = y_units_per_mm
        self._slots: dict[int, _Slot] = {}
        self._current = current_slot

    def reset(self, current_slot: int) -> None:
        self._slots.clear()
        self._current = current_slot

    def feed(self, etype: int, code: int, value: int) -> Frame | None:
        if etype == e.EV_SYN and code == e.SYN_REPORT:
            return self._frame()
        if etype != e.EV_ABS:
            return None
        if code == e.ABS_MT_SLOT:
            self._current = value
            return None
        # The kernel only sends values that changed within a slot, so a slot
        # keeps its last position after the finger lifts.
        slot = self._slots.setdefault(self._current, _Slot())
        if code == e.ABS_MT_TRACKING_ID:
            slot.active = value != -1
        elif code == e.ABS_MT_POSITION_X:
            slot.x = value
        elif code == e.ABS_MT_POSITION_Y:
            slot.y = value
        return None

    def _frame(self) -> Frame:
        fingers = [s for s in self._slots.values()
                   if s.active and s.x is not None and s.y is not None]
        if not fingers:
            return Frame(0, 0.0, 0.0)
        count = len(fingers)
        return Frame(
            count,
            sum(s.x for s in fingers) / count / self._x_units_per_mm,
            sum(s.y for s in fingers) / count / self._y_units_per_mm,
        )
