"""Multitouch protocol B slot tracking: raw events in, finger frames out."""
from __future__ import annotations

from dataclasses import dataclass, field

from evdev import ecodes as e

FALLBACK_WIDTH_MM = 100.0
FALLBACK_HEIGHT_MM = 60.0
# Value of ABS_MT_TOOL_TYPE for a contact the firmware classified as a palm.
# python-evdev 1.7 does not export the MT_TOOL_* constants.
MT_TOOL_PALM = 2


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
    # True when a different set of fingers is being averaged than in the
    # previous frame, so the centroid is not comparable with the last one.
    regrouped: bool = field(default=False, compare=False)


@dataclass
class _Slot:
    active: bool = False
    tracking_id: int = -1
    x: int | None = None
    y: int | None = None
    palm: bool = False


class SlotTracker:
    def __init__(self, x_units_per_mm: float, y_units_per_mm: float,
                 current_slot: int = 0, touching: bool = False) -> None:
        self._x_units_per_mm = x_units_per_mm
        self._y_units_per_mm = y_units_per_mm
        self._slots: dict[int, _Slot] = {}
        self._current = current_slot
        self._identity: frozenset = frozenset()
        # Fingers that were already down cannot be seen: they have no touch
        # event left to send. Report nothing until the pad has been empty.
        self._waiting_for_lift = touching
        self.resyncing = False

    def begin_resync(self) -> None:
        """Forget every finger; the caller discards events until reset()."""
        self._slots.clear()
        self.resyncing = True

    def reset(self, current_slot: int, touching: bool = False) -> None:
        self._slots.clear()
        self._current = current_slot
        self._waiting_for_lift = touching
        self.resyncing = False

    def feed(self, etype: int, code: int, value: int) -> Frame | None:
        if etype == e.EV_SYN and code == e.SYN_REPORT:
            return self._frame()
        if etype == e.EV_KEY and code == e.BTN_TOUCH and value == 0:
            self._pad_is_empty()
            return None
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
            slot.tracking_id = value
        elif code == e.ABS_MT_POSITION_X:
            slot.x = value
        elif code == e.ABS_MT_POSITION_Y:
            slot.y = value
        elif code == e.ABS_MT_TOOL_TYPE:
            slot.palm = value == MT_TOOL_PALM
        return None

    def _pad_is_empty(self) -> None:
        # Also heals a finger whose lift was never seen.
        for slot in self._slots.values():
            slot.active = False
            slot.tracking_id = -1
        self._waiting_for_lift = False

    def _frame(self) -> Frame:
        fingers = {} if self._waiting_for_lift else {
            index: s for index, s in self._slots.items()
            if s.active and not s.palm
            and s.x is not None and s.y is not None}
        identity = frozenset((index, s.tracking_id) for index, s in fingers.items())
        regrouped = identity != self._identity
        self._identity = identity
        if not fingers:
            return Frame(0, 0.0, 0.0, regrouped)
        count = len(fingers)
        return Frame(
            count,
            sum(s.x for s in fingers.values()) / count / self._x_units_per_mm,
            sum(s.y for s in fingers.values()) / count / self._y_units_per_mm,
            regrouped,
        )
