"""Translates gesture actions into events on two virtual devices."""
from __future__ import annotations

import time

from evdev import ecodes as e

from finger_drag.gestures import (
    Action, ButtonDown, ButtonUp, Direction, Move, SwitchWorkspace)

WORKSPACE_KEYS = (e.KEY_LEFTCTRL, e.KEY_LEFTALT, e.KEY_LEFT, e.KEY_RIGHT)
KEY_HOLD_S = 0.01

_ARROW = {Direction.NEXT: e.KEY_RIGHT, Direction.PREVIOUS: e.KEY_LEFT}


class Output:
    def __init__(self, pointer, keyboard, sleep=time.sleep) -> None:
        self._pointer = pointer
        self._keyboard = keyboard
        self._sleep = sleep
        self._rest_x = 0.0
        self._rest_y = 0.0

    def emit(self, actions: list[Action]) -> None:
        for action in actions:
            if isinstance(action, Move):
                self._move(action.dx, action.dy)
            elif isinstance(action, ButtonDown):
                self._button(1)
            elif isinstance(action, ButtonUp):
                self._button(0)
            elif isinstance(action, SwitchWorkspace):
                self._chord(_ARROW[action.direction])

    def release_all(self) -> None:
        self._button(0)
        for key in WORKSPACE_KEYS:
            self._keyboard.write(e.EV_KEY, key, 0)
        self._keyboard.syn()
        self._rest_x = self._rest_y = 0.0

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
