"""Carries out gesture actions: three virtual devices and the desktop shell."""
from __future__ import annotations

import time

from evdev import ecodes as e

from gnome_x11_touchpad_gestures.gestures import (
    SWIPE_FULL_MM, Action, ButtonDown, ButtonUp, Direction, Move, Overview, Snap,
    SwipeBegin, SwipeCancel, SwipeEnd, SwipeMove, SwitchWorkspace)
from gnome_x11_touchpad_gestures.momentum import Scroll

WORKSPACE_KEYS = (e.KEY_LEFTCTRL, e.KEY_LEFTALT, e.KEY_LEFT, e.KEY_RIGHT)
KEY_HOLD_S = 0.01
WHEEL_NOTCH = 120  # high-resolution units in one notch of a wheel
GLIDE_CHECK_STEPS = 12  # a glide asks this often whether it is still wanted

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
        self._glide_steps = 0
        self._held_back = False
        self._following = False   # the shell is moving the workspace itself
        self._snap = Snap()
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
            elif isinstance(action, (SwipeBegin, SwipeMove, SwipeEnd, SwipeCancel)):
                self._swipe(action)
            elif isinstance(action, Overview):
                self._shell.show_overview(action.show)

    def release_all(self) -> None:
        self._button(0)
        for key in WORKSPACE_KEYS:
            self._keyboard.write(e.EV_KEY, key, 0)
        self._keyboard.syn()
        self._rest_x = self._rest_y = 0.0
        self._forget_scroll()
        self._swipe(SwipeCancel())

    def _forget_scroll(self) -> None:
        # Per axis: the fraction of a unit not yet sent, and the units sent
        # towards the next whole notch.
        self._scroll_rest = {e.REL_HWHEEL: 0.0, e.REL_WHEEL: 0.0}
        self._notch_rest = {e.REL_HWHEEL: 0, e.REL_WHEEL: 0}

    def _scroll(self, dx: float, dy: float, first: bool = False) -> None:
        if first:
            # What the last glide left over belongs to that glide.
            self._forget_scroll()
            self._glide_steps = 0
            self._held_back = False
        # Over what the shell draws itself a notch of the wheel steps through
        # something, workspaces or the volume, so a glide would race through
        # it. It is asked about now and then because the overview can open,
        # or a mouse move the pointer, while a glide runs. A glide once held
        # back stays so: resuming it somewhere else would be a surprise.
        if not self._held_back and self._glide_steps % GLIDE_CHECK_STEPS == 0:
            self._held_back = not self._shell.may_glide()
        self._glide_steps += 1
        if self._held_back:
            return
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

    def _swipe(self, action) -> None:
        """A sideways swipe, for a shell that follows the fingers or one that cannot."""
        if isinstance(action, SwipeBegin):
            # Asked at every swipe: the extension may have been installed,
            # or the shell restarted, since the last one.
            self._following = self._shell.swipe_begin(action.t)
        if not self._following:
            for switch in self._snap.feed(action):
                self._chord(_ARROW[switch.direction])
            return
        if isinstance(action, SwipeMove):
            # The page follows the fingers, so moving them left is moving
            # on towards the next workspace.
            self._shell.swipe_update(action.t, -action.dx / SWIPE_FULL_MM)
        elif isinstance(action, SwipeEnd):
            self._following = False
            self._shell.swipe_end(action.t)
        elif isinstance(action, SwipeCancel):
            self._following = False
            self._shell.swipe_cancel()

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


class NoDevice:
    """Stands in for a virtual device that a mode does not make."""

    def write(self, etype: int, code: int, value: int) -> None:
        pass

    def syn(self) -> None:
        pass


class DragOnly:
    """Carries out the drag and nothing else, and only where it may.

    For a desktop that does the other gestures itself. Its own swipe of
    three fingers would land on top of a drag, so a drag is made only if
    `allowed()` says, as the drag starts, that the desktop is keeping its
    swipes off three fingers. A drag it does not allow is dropped whole:
    its press, its moves and its release.
    """

    def __init__(self, output: Output, allowed) -> None:
        self._output = output
        self._allowed = allowed
        self._dragging = False

    def emit(self, actions: list[Action | Scroll]) -> None:
        kept = []
        for action in actions:
            if isinstance(action, ButtonDown):
                self._dragging = bool(self._allowed())
            if self._dragging and isinstance(action, (ButtonDown, Move, ButtonUp)):
                kept.append(action)
            if isinstance(action, ButtonUp):
                self._dragging = False
        if kept:
            self._output.emit(kept)

    def release_all(self) -> None:
        self._dragging = False
        self._output.release_all()
