"""Carries out gesture actions: three virtual devices and the desktop shell."""
from __future__ import annotations

import time

from evdev import ecodes as e

from gnome_x11_touchpad_gestures.gestures import (
    Action, ButtonDown, ButtonUp, Direction, Move, Overview, SwitchWorkspace)
from gnome_x11_touchpad_gestures.momentum import Scroll

WORKSPACE_KEYS = (e.KEY_LEFTCTRL, e.KEY_LEFTALT, e.KEY_LEFT, e.KEY_RIGHT)
KEY_HOLD_S = 0.01
WHEEL_NOTCH = 120  # high-resolution units in one notch of a wheel

_ARROW = {Direction.NEXT: e.KEY_RIGHT, Direction.PREVIOUS: e.KEY_LEFT}


class Output:
    def __init__(self, pointer, keyboard, wheel, shell, sleep=time.sleep) -> None:
        self._pointer = pointer
        self._keyboard = keyboard
        self._wheel = wheel
        self._shell = shell
        self._sleep = sleep
        self._rest_x = 0.0
        self._rest_y = 0.0
        self._forget_scroll()

    def emit(self, actions: list[Action | Scroll]) -> None:
        for action in actions:
            if isinstance(action, Move):
                self._move(action.dx, action.dy)
            elif isinstance(action, Scroll):
                self._scroll(action.dx, action.dy, action.first)
            elif isinstance(action, ButtonDown):
                self._button(1)
            elif isinstance(action, ButtonUp):
                self._button(0)
            elif isinstance(action, SwitchWorkspace):
                self._chord(_ARROW[action.direction])
            elif isinstance(action, Overview):
                self._shell.show_overview(action.show)

    def release_all(self) -> None:
        self._button(0)
        for key in WORKSPACE_KEYS:
            self._keyboard.write(e.EV_KEY, key, 0)
        self._keyboard.syn()
        self._rest_x = self._rest_y = 0.0
        self._forget_scroll()

    def _forget_scroll(self) -> None:
        # Per axis: the fraction of a unit not yet sent, and the units sent
        # towards the next whole notch.
        self._scroll_rest = {e.REL_HWHEEL: 0.0, e.REL_WHEEL: 0.0}
        self._notch_rest = {e.REL_HWHEEL: 0, e.REL_WHEEL: 0}

    def _scroll(self, dx: float, dy: float, first: bool = False) -> None:
        if first:
            # What the last glide left over belongs to that glide.
            self._forget_scroll()
        sideways = self._turn(e.REL_HWHEEL, e.REL_HWHEEL_HI_RES, dx)
        upright = self._turn(e.REL_WHEEL, e.REL_WHEEL_HI_RES, dy)
        if sideways or upright:
            self._wheel.syn()

    def _turn(self, notch_code: int, units_code: int, units: float) -> bool:
        self._scroll_rest[notch_code] += units
        whole = int(self._scroll_rest[notch_code])
        self._scroll_rest[notch_code] -= whole
        if not whole:
            return False
        self._wheel.write(e.EV_REL, units_code, whole)
        # Programs that predate high-resolution scrolling only read notches.
        self._notch_rest[notch_code] += whole
        notches = int(self._notch_rest[notch_code] / WHEEL_NOTCH)
        if notches:
            self._notch_rest[notch_code] -= notches * WHEEL_NOTCH
            self._wheel.write(e.EV_REL, notch_code, notches)
        return True

    def _button(self, value: int) -> None:
        self._pointer.write(e.EV_KEY, e.BTN_LEFT, value)
        self._pointer.syn()

    def _move(self, dx: float, dy: float) -> None:
        # uinput takes whole counts; keep the remainder so slow drags still move.
        self._rest_x += dx
        self._rest_y += dy
        whole_x, whole_y = int(self._rest_x), int(self._rest_y)
        self._rest_x -= whole_x
        self._rest_y -= whole_y
        if whole_x:
            self._pointer.write(e.EV_REL, e.REL_X, whole_x)
        if whole_y:
            self._pointer.write(e.EV_REL, e.REL_Y, whole_y)
        if whole_x or whole_y:
            self._pointer.syn()

    def _chord(self, arrow: int) -> None:
        keys = (e.KEY_LEFTCTRL, e.KEY_LEFTALT, arrow)
        for key in keys:
            self._keyboard.write(e.EV_KEY, key, 1)
            self._keyboard.syn()
        self._sleep(KEY_HOLD_S)
        for key in reversed(keys):
            self._keyboard.write(e.EV_KEY, key, 0)
            self._keyboard.syn()
