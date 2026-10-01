# gnome-touchpad-gestures Implementation Plan

> **Historical record.** This is the plan the first version was built from,
> when the project was called `finger-drag` and lived at `~/finger-drag`. It
> has since been renamed, moved and extended. The names and paths below were
> updated with it, so they are not the ones the first version had, and the
> steps no longer describe a sequence that can be followed as written. The
> spec is the current design.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three-finger drag and four-finger workspace switching to this GNOME-on-X11 laptop with one unprivileged Python daemon.

**Architecture:** A daemon reads raw multitouch events from the touchpad through evdev without grabbing it, runs them through a pure state machine, and writes synthesised pointer and key events to two uinput devices. Device I/O is kept at the edges so the gesture logic, slot tracking and event output are each testable with fakes.

**Tech Stack:** Python 3.12, `python3-evdev` 1.7.0 (already installed), stdlib `unittest`, udev, systemd user services.

**Spec:** `docs/superpowers/specs/2026-09-27-gnome-touchpad-gestures-design.md`

## Global Constraints

- Session is GNOME Shell 46 on **X11**. Wayland stays disabled.
- Install no new packages and add no third-party repositories. No `pip install`.
- Do not add the user to the `input` group.
- The daemon runs unprivileged. The only `sudo` use is inside `install/install.sh`.
- The daemon never grabs the touchpad (no `EVIOCGRAB`, no `device.grab()`).
- Tests use stdlib `unittest` only. Run all tests from the repo root with
  `python3 -m unittest discover -s tests`.
- Work in place in `~/gnome-touchpad-gestures` on branch `daemon`. Do not use a separate
  git worktree: the systemd unit runs the code from `%h/gnome-touchpad-gestures`.
- Tunables are module-level constants. No configuration file.
- Touchpad name for the udev rule, verbatim: `SYNA8017:00 06CB:CEB2 Touchpad`.
- Virtual device names, verbatim: `gnome-gestures pointer`, `gnome-gestures keyboard`.

## Deviation from the spec layout

The spec lists `gestures.py` and `daemon.py`. This plan splits two pure pieces
out of `daemon.py` so they can be unit tested without a device:

| File | Responsibility |
|---|---|
| `gnome_touchpad_gestures/gestures.py` | State machine: finger frames in, actions out |
| `gnome_touchpad_gestures/slots.py` | Multitouch slot tracking: raw events in, frames (count + centroid in mm) out |
| `gnome_touchpad_gestures/output.py` | Actions in, events on two virtual devices out |
| `gnome_touchpad_gestures/daemon.py` | Device discovery, event loop, signal handling, `--check` |

Behaviour is unchanged from the spec.

## Review Focus

Conditions the spec implies but does not list as test cases, most likely first:

1. **Kernel omits unchanged values.** A new touch landing at the same
   coordinate as the previous touch in that slot sends no position event. The
   finger must still be counted at the retained position. *(Task 3)*
2. **Current slot is non-zero at startup.** The kernel only sends
   `ABS_MT_SLOT` when the slot changes, so the tracker must start from the
   device's current slot, not assume 0. *(Task 3)*
3. **`SYN_DROPPED` (event buffer overrun).** Slot state is stale afterwards.
   Any drag in progress must end and the button must be released, not stuck.
   *(Task 5)*
4. **Five fingers, or finger count changing 4 to 5 mid-swipe.** Must still
   switch workspace exactly once, with no travel lost or double counted.
   *(Task 2)*
5. **Touchpad reports axis resolution 0.** Must not divide by zero; fall back
   to an assumed physical size. *(Task 3)*

---

### Task 1: Gesture machine, three-finger drag

**Files:**
- Create: `.gitignore`
- Create: `gnome_touchpad_gestures/__init__.py`
- Create: `gnome_touchpad_gestures/gestures.py`
- Test: `tests/test_gestures.py`

**Interfaces:**
- Consumes: nothing.
- Produces, all from `gnome_touchpad_gestures.gestures`:
  - Constants `DRAG_START_MM`, `DRAG_RELEASE_S`, `POINTER_COUNTS_PER_MM`, `SWIPE_MM`, `SWIPE_AXIS_RATIO` (floats)
  - `Direction` enum with members `NEXT`, `PREVIOUS`
  - Frozen dataclasses `ButtonDown()`, `ButtonUp()`, `Move(dx: float, dy: float)`, `SwitchWorkspace(direction: Direction)`
  - `Action` type alias for the union of the four
  - `State` enum with members `IDLE`, `DRAGGING`, `RELEASE_WAIT`, `SWIPE_TRACKING`, `SWIPE_DONE`
  - `release_delay(remaining: int) -> float`
  - `GestureMachine` with attribute `state: State` and methods
    `update(t: float, count: int, cx: float, cy: float) -> list[Action]`,
    `tick(t: float) -> list[Action]`,
    `next_deadline() -> float | None`

- [ ] **Step 1: Create the branch and scaffold**

```bash
cd ~/gnome-touchpad-gestures
git switch -c daemon
mkdir -p gnome_touchpad_gestures tests
printf '__pycache__/\n*.pyc\n' > .gitignore
: > gnome_touchpad_gestures/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_gestures.py`:

```python
import math
import unittest

from gnome_touchpad_gestures import gestures as g
from gnome_touchpad_gestures.gestures import ButtonDown, ButtonUp, GestureMachine, Move, State


def feed(machine, frames):
    """Feed (t, count, cx, cy) frames; return every action in order."""
    actions = []
    for t, count, cx, cy in frames:
        actions += machine.update(t, count, cx, cy)
    return actions


def start_drag(machine):
    """Three fingers land at (50, 25) mm and travel 2.5 mm right."""
    return feed(machine, [
        (0.00, 3, 50.0, 25.0),
        (0.01, 3, 51.0, 25.0),
        (0.02, 3, 52.5, 25.0),
    ])


class ThreeFingerDragTest(unittest.TestCase):
    def setUp(self):
        self.machine = GestureMachine()

    def test_fresh_machine_tick_produces_nothing(self):
        self.assertEqual(self.machine.tick(0.0), [])
        self.assertIsNone(self.machine.next_deadline())

    def test_three_finger_tap_produces_nothing(self):
        actions = feed(self.machine, [
            (0.00, 3, 50.0, 25.0),
            (0.01, 3, 50.5, 25.0),
            (0.02, 0, 0.0, 0.0),
        ])
        self.assertEqual(actions, [])
        self.assertEqual(self.machine.tick(1.0), [])
        self.assertIs(self.machine.state, State.IDLE)

    def test_drag_starts_after_threshold(self):
        self.assertEqual(start_drag(self.machine), [ButtonDown()])
        self.assertIs(self.machine.state, State.DRAGGING)

    def test_drag_moves_pointer_per_frame(self):
        start_drag(self.machine)
        actions = self.machine.update(0.03, 3, 53.5, 25.5)
        self.assertEqual(actions, [Move(
            1.0 * g.POINTER_COUNTS_PER_MM, 0.5 * g.POINTER_COUNTS_PER_MM)])

    def test_stationary_frame_produces_no_move(self):
        start_drag(self.machine)
        self.assertEqual(self.machine.update(0.03, 3, 52.5, 25.0), [])

    def test_count_change_frame_produces_no_move(self):
        start_drag(self.machine)
        self.assertEqual(self.machine.update(0.50, 2, 80.0, 40.0), [])
        self.assertEqual(self.machine.update(0.60, 3, 20.0, 10.0), [])
        self.assertIs(self.machine.state, State.DRAGGING)
        self.assertEqual(
            self.machine.update(0.61, 3, 21.0, 10.0),
            [Move(1.0 * g.POINTER_COUNTS_PER_MM, 0.0)])

    def test_button_released_after_deadline(self):
        start_drag(self.machine)
        self.assertEqual(self.machine.update(1.0, 0, 0.0, 0.0), [])
        self.assertIs(self.machine.state, State.RELEASE_WAIT)
        deadline = self.machine.next_deadline()
        self.assertAlmostEqual(deadline, 1.0 + g.DRAG_RELEASE_S)
        self.assertEqual(self.machine.tick(deadline - 0.01), [])
        self.assertEqual(self.machine.tick(deadline), [ButtonUp()])
        self.assertIs(self.machine.state, State.IDLE)
        self.assertIsNone(self.machine.next_deadline())

    def test_button_released_only_once(self):
        start_drag(self.machine)
        self.machine.update(1.0, 0, 0.0, 0.0)
        self.assertEqual(self.machine.tick(5.0), [ButtonUp()])
        self.assertEqual(self.machine.tick(6.0), [])

    def test_regrip_before_deadline_keeps_button_held(self):
        start_drag(self.machine)
        self.machine.update(1.0, 0, 0.0, 0.0)
        self.assertEqual(self.machine.update(1.1, 3, 40.0, 20.0), [])
        self.assertIs(self.machine.state, State.DRAGGING)
        self.assertIsNone(self.machine.next_deadline())
        self.assertEqual(
            self.machine.update(1.11, 3, 41.0, 20.0),
            [Move(1.0 * g.POINTER_COUNTS_PER_MM, 0.0)])
        self.assertEqual(self.machine.tick(5.0), [])

    def test_update_after_deadline_releases_before_anything_else(self):
        start_drag(self.machine)
        self.machine.update(1.0, 0, 0.0, 0.0)
        self.assertEqual(self.machine.update(2.0, 3, 40.0, 20.0), [ButtonUp()])
        self.assertIs(self.machine.state, State.IDLE)

    def test_fourth_finger_ends_drag(self):
        start_drag(self.machine)
        self.assertEqual(self.machine.update(1.0, 4, 50.0, 25.0), [ButtonUp()])
        self.assertIs(self.machine.state, State.SWIPE_TRACKING)

    def test_fourth_finger_during_release_wait_ends_drag(self):
        start_drag(self.machine)
        self.machine.update(1.0, 0, 0.0, 0.0)
        self.assertEqual(self.machine.update(1.1, 4, 50.0, 25.0), [ButtonUp()])
        self.assertIs(self.machine.state, State.SWIPE_TRACKING)
        self.assertIsNone(self.machine.next_deadline())
        self.assertEqual(self.machine.tick(5.0), [])

    def test_one_and_two_fingers_produce_nothing(self):
        actions = feed(self.machine, [
            (0.00, 1, 10.0, 10.0),
            (0.01, 1, 60.0, 40.0),
            (0.02, 2, 10.0, 10.0),
            (0.03, 2, 60.0, 40.0),
            (0.04, 0, 0.0, 0.0),
        ])
        self.assertEqual(actions, [])
        self.assertIs(self.machine.state, State.IDLE)


class ReleaseDelayTest(unittest.TestCase):
    def test_full_lift_uses_the_spec_delay(self):
        self.assertAlmostEqual(g.release_delay(0), g.DRAG_RELEASE_S)

    def test_delay_is_finite_and_not_negative(self):
        for remaining in (0, 1, 2):
            with self.subTest(remaining=remaining):
                delay = g.release_delay(remaining)
                self.assertTrue(math.isfinite(delay))
                self.assertGreaterEqual(delay, 0.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s tests`
Expected: FAIL with `ImportError` / `cannot import name 'gestures'`.

- [ ] **Step 4: Write the implementation, leaving `release_delay` for the user**

Create `gnome_touchpad_gestures/gestures.py`:

```python
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
    raise NotImplementedError("user contribution, see plan Task 1 Step 5")


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

    def _swipe_done(self, count: int) -> list[Action]:
        if count == 0:
            self._enter_idle()
        return []
```

- [ ] **Step 5: User contribution, `release_delay`**

This is reserved for the user (learning mode). **Stop and ask the user to
write the body of `release_delay` in `gnome_touchpad_gestures/gestures.py`.** Give them
this context:

- It decides how forgiving a drag is when fingers leave the pad. It is the
  single choice that most shapes how the gesture feels.
- `remaining == 0` means a full lift. The tests pin this case to
  `DRAG_RELEASE_S`, as the spec requires.
- `remaining` of 1 or 2 means a partial lift. Options worth weighing:
  - Same delay as a full lift: simplest, and what the spec says.
  - Longer delay: a partial lift usually means the user is repositioning, so
    be more patient. Risk: a finger resting on the pad keeps the drag alive
    and libinput moves the pointer with the button held.
  - Zero: release at once, so a resting finger can never move a held item.
    Risk: an accidental finger lift drops the drag.
- Constraint: return a finite float >= 0.

If the user declines, or asks for the default, use the spec-conforming body:

```python
    return DRAG_RELEASE_S
```

If the user's policy treats partial lifts differently, update the
`RELEASE_WAIT` paragraph and the "Known behaviour" section of the spec in the
same commit so the spec stays true.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 15 tests` and `OK`.

- [ ] **Step 7: Commit**

```bash
git add .gitignore gnome_touchpad_gestures tests docs
git commit -m "Add gesture state machine with three-finger drag"
```

---

### Task 2: Gesture machine, four-finger workspace swipe

**Files:**
- Modify: `gnome_touchpad_gestures/gestures.py` (methods `_swipe_tracking` and `_swipe_done` only)
- Test: `tests/test_gestures.py` (append one test class)

**Interfaces:**
- Consumes from Task 1: `GestureMachine`, `State`, `Direction`,
  `SwitchWorkspace`, `SWIPE_MM`, `SWIPE_AXIS_RATIO`, and the test helper
  `feed(machine, frames)` already defined in `tests/test_gestures.py`.
- Produces: `GestureMachine.update` now returns
  `SwitchWorkspace(Direction.NEXT)` for a leftward four-finger swipe and
  `SwitchWorkspace(Direction.PREVIOUS)` for a rightward one. No signature
  changes.

- [ ] **Step 1: Write the failing tests**

In `tests/test_gestures.py`, change the import line to:

```python
from gnome_touchpad_gestures.gestures import (
    ButtonDown, ButtonUp, Direction, GestureMachine, Move, State, SwitchWorkspace)
```

Then add this class above the `if __name__ == "__main__":` line:

```python
class FourFingerSwipeTest(unittest.TestCase):
    def setUp(self):
        self.machine = GestureMachine()

    def test_swipe_left_switches_to_next(self):
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 25.0),
            (0.01, 4, 52.0, 25.0),
            (0.02, 4, 44.0, 25.0),
        ])
        self.assertEqual(actions, [SwitchWorkspace(Direction.NEXT)])
        self.assertIs(self.machine.state, State.SWIPE_DONE)

    def test_swipe_right_switches_to_previous(self):
        actions = feed(self.machine, [
            (0.00, 4, 40.0, 25.0),
            (0.01, 4, 48.0, 25.0),
            (0.02, 4, 56.0, 25.0),
        ])
        self.assertEqual(actions, [SwitchWorkspace(Direction.PREVIOUS)])

    def test_travel_below_threshold_produces_nothing(self):
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 25.0),
            (0.01, 4, 46.0, 25.0),
            (0.02, 0, 0.0, 0.0),
        ])
        self.assertEqual(actions, [])
        self.assertIs(self.machine.state, State.IDLE)

    def test_only_one_switch_per_swipe(self):
        actions = feed(self.machine, [
            (0.00, 4, 90.0, 25.0),
            (0.01, 4, 70.0, 25.0),
            (0.02, 4, 50.0, 25.0),
            (0.03, 4, 30.0, 25.0),
            (0.04, 4, 10.0, 25.0),
        ])
        self.assertEqual(actions, [SwitchWorkspace(Direction.NEXT)])

    def test_second_swipe_after_full_lift(self):
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 25.0),
            (0.01, 4, 40.0, 25.0),
            (0.02, 0, 0.0, 0.0),
            (0.50, 4, 40.0, 25.0),
            (0.51, 4, 60.0, 25.0),
        ])
        self.assertEqual(actions, [
            SwitchWorkspace(Direction.NEXT),
            SwitchWorkspace(Direction.PREVIOUS),
        ])

    def test_vertical_swipe_produces_nothing(self):
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 10.0),
            (0.01, 4, 60.0, 30.0),
            (0.02, 4, 60.0, 50.0),
        ])
        self.assertEqual(actions, [])
        self.assertIs(self.machine.state, State.SWIPE_TRACKING)

    def test_diagonal_below_axis_ratio_produces_nothing(self):
        # 16 mm sideways passes SWIPE_MM, but 12 mm vertical needs 18 sideways.
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 20.0),
            (0.01, 4, 44.0, 32.0),
        ])
        self.assertEqual(actions, [])
        self.assertIs(self.machine.state, State.SWIPE_TRACKING)

    def test_fifth_finger_mid_swipe_still_switches_once(self):
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 25.0),
            (0.01, 4, 52.0, 25.0),
            (0.02, 5, 70.0, 30.0),
            (0.03, 5, 62.0, 30.0),
        ])
        self.assertEqual(actions, [SwitchWorkspace(Direction.NEXT)])

    def test_five_finger_swipe_switches(self):
        actions = feed(self.machine, [
            (0.00, 5, 60.0, 25.0),
            (0.01, 5, 40.0, 25.0),
        ])
        self.assertEqual(actions, [SwitchWorkspace(Direction.NEXT)])

    def test_uneven_lift_before_threshold_cannot_start_drag(self):
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 25.0),
            (0.01, 4, 55.0, 25.0),
            (0.02, 3, 50.0, 25.0),
            (0.03, 3, 30.0, 25.0),
        ])
        self.assertEqual(actions, [])
        self.assertIs(self.machine.state, State.SWIPE_TRACKING)

    def test_uneven_lift_after_switch_cannot_start_drag(self):
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 25.0),
            (0.01, 4, 40.0, 25.0),
            (0.02, 3, 50.0, 25.0),
            (0.03, 3, 30.0, 25.0),
            (0.04, 0, 0.0, 0.0),
        ])
        self.assertEqual(actions, [SwitchWorkspace(Direction.NEXT)])
        self.assertIs(self.machine.state, State.IDLE)

    def test_three_finger_frames_do_not_add_swipe_travel(self):
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 25.0),
            (0.01, 4, 52.0, 25.0),
            (0.02, 3, 50.0, 25.0),
            (0.03, 3, 20.0, 25.0),
            (0.04, 4, 45.0, 25.0),
            (0.05, 4, 40.0, 25.0),
        ])
        self.assertEqual(actions, [])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s tests`
Expected: FAIL. The tests expecting a `SwitchWorkspace` action get `[]`
(7 failures); the rest pass.

- [ ] **Step 3: Implement swipe detection**

In `gnome_touchpad_gestures/gestures.py`, replace the `_swipe_tracking` method with:

```python
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
```

Leave `_swipe_done` as it is.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 27 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add gnome_touchpad_gestures/gestures.py tests/test_gestures.py
git commit -m "Add four-finger workspace swipe to gesture machine"
```

---

### Task 3: Multitouch slot tracker

**Files:**
- Create: `gnome_touchpad_gestures/slots.py`
- Test: `tests/test_slots.py`

**Interfaces:**
- Consumes: event code constants from `evdev.ecodes`.
- Produces, all from `gnome_touchpad_gestures.slots`:
  - `FALLBACK_WIDTH_MM = 100.0`, `FALLBACK_HEIGHT_MM = 60.0`
  - `units_per_mm(minimum: int, maximum: int, resolution: int, fallback_mm: float) -> float`
  - Frozen dataclass `Frame(count: int, cx: float, cy: float)`; `cx`, `cy` in millimetres, both `0.0` when `count == 0`
  - `SlotTracker(x_units_per_mm: float, y_units_per_mm: float, current_slot: int = 0)` with
    `feed(etype: int, code: int, value: int) -> Frame | None` (returns a `Frame` only on `SYN_REPORT`) and
    `reset(current_slot: int) -> None`

Background for the implementer: the touchpad uses multitouch protocol B. Each
finger occupies a numbered slot. `ABS_MT_SLOT` selects the slot that following
events apply to, and is sent only when the slot changes.
`ABS_MT_TRACKING_ID` of `-1` means the finger in that slot lifted; any other
value means a finger is present. The kernel sends a value only when it differs
from the last value **in that slot**, so positions must be retained across
touches. `SYN_REPORT` ends one frame.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_slots.py`:

```python
import unittest

from evdev import ecodes as e

from gnome_touchpad_gestures.slots import Frame, SlotTracker, units_per_mm


def touch(tracker, slot, tracking_id, x=None, y=None):
    tracker.feed(e.EV_ABS, e.ABS_MT_SLOT, slot)
    tracker.feed(e.EV_ABS, e.ABS_MT_TRACKING_ID, tracking_id)
    if x is not None:
        tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_X, x)
    if y is not None:
        tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_Y, y)


def report(tracker):
    return tracker.feed(e.EV_SYN, e.SYN_REPORT, 0)


class SlotTrackerTest(unittest.TestCase):
    def setUp(self):
        self.tracker = SlotTracker(10.0, 20.0)

    def test_no_fingers(self):
        self.assertEqual(report(self.tracker), Frame(0, 0.0, 0.0))

    def test_abs_events_return_nothing_until_report(self):
        self.assertIsNone(self.tracker.feed(e.EV_ABS, e.ABS_MT_TRACKING_ID, 1))
        self.assertIsNone(self.tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_X, 100))

    def test_one_finger_in_millimetres(self):
        touch(self.tracker, 0, 1, 100, 200)
        self.assertEqual(report(self.tracker), Frame(1, 10.0, 10.0))

    def test_three_finger_centroid(self):
        touch(self.tracker, 0, 1, 100, 200)
        touch(self.tracker, 1, 2, 200, 400)
        touch(self.tracker, 2, 3, 300, 600)
        self.assertEqual(report(self.tracker), Frame(3, 20.0, 20.0))

    def test_motion_updates_only_the_selected_slot(self):
        touch(self.tracker, 0, 1, 100, 200)
        touch(self.tracker, 1, 2, 300, 200)
        report(self.tracker)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_SLOT, 0)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_X, 200)
        self.assertEqual(report(self.tracker), Frame(2, 25.0, 10.0))

    def test_lift_removes_finger(self):
        touch(self.tracker, 0, 1, 100, 200)
        touch(self.tracker, 1, 2, 300, 600)
        report(self.tracker)
        touch(self.tracker, 0, -1)
        self.assertEqual(report(self.tracker), Frame(1, 30.0, 30.0))

    def test_new_touch_reuses_retained_position(self):
        touch(self.tracker, 0, 1, 100, 200)
        report(self.tracker)
        touch(self.tracker, 0, -1)
        self.assertEqual(report(self.tracker), Frame(0, 0.0, 0.0))
        touch(self.tracker, 0, 2)
        self.assertEqual(report(self.tracker), Frame(1, 10.0, 10.0))

    def test_finger_with_unknown_position_is_not_counted(self):
        touch(self.tracker, 0, 5)
        self.assertEqual(report(self.tracker).count, 0)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_X, 100)
        self.assertEqual(report(self.tracker).count, 0)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_Y, 200)
        self.assertEqual(report(self.tracker), Frame(1, 10.0, 10.0))

    def test_starts_from_the_given_current_slot(self):
        tracker = SlotTracker(10.0, 20.0, current_slot=2)
        tracker.feed(e.EV_ABS, e.ABS_MT_TRACKING_ID, 7)
        tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_X, 100)
        tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_Y, 200)
        touch(tracker, 0, 8, 300, 600)
        self.assertEqual(report(tracker), Frame(2, 20.0, 20.0))

    def test_reset_forgets_fingers_and_sets_slot(self):
        touch(self.tracker, 0, 1, 100, 200)
        report(self.tracker)
        self.tracker.reset(1)
        self.assertEqual(report(self.tracker), Frame(0, 0.0, 0.0))
        self.tracker.feed(e.EV_ABS, e.ABS_MT_TRACKING_ID, 9)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_X, 300)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_Y, 600)
        touch(self.tracker, 0, 10, 100, 200)
        self.assertEqual(report(self.tracker), Frame(2, 20.0, 20.0))

    def test_unrelated_events_are_ignored(self):
        self.assertIsNone(self.tracker.feed(e.EV_KEY, e.BTN_LEFT, 1))
        self.assertIsNone(self.tracker.feed(e.EV_ABS, e.ABS_X, 500))
        self.assertIsNone(self.tracker.feed(e.EV_SYN, e.SYN_DROPPED, 0))
        self.assertEqual(report(self.tracker), Frame(0, 0.0, 0.0))


class UnitsPerMmTest(unittest.TestCase):
    def test_uses_reported_resolution(self):
        self.assertEqual(units_per_mm(0, 1300, 12, 100.0), 12.0)

    def test_zero_resolution_falls_back_to_assumed_size(self):
        self.assertEqual(units_per_mm(0, 1300, 0, 100.0), 13.0)

    def test_fallback_honours_non_zero_minimum(self):
        self.assertEqual(units_per_mm(100, 1100, 0, 100.0), 10.0)

    def test_no_resolution_and_no_range_is_an_error(self):
        with self.assertRaises(ValueError):
            units_per_mm(0, 0, 0, 100.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s tests`
Expected: FAIL with `ModuleNotFoundError: No module named 'gnome_touchpad_gestures.slots'`.

- [ ] **Step 3: Write the implementation**

Create `gnome_touchpad_gestures/slots.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 42 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add gnome_touchpad_gestures/slots.py tests/test_slots.py
git commit -m "Add multitouch slot tracker"
```

---

### Task 4: Event output

**Files:**
- Create: `gnome_touchpad_gestures/output.py`
- Test: `tests/test_output.py`

**Interfaces:**
- Consumes from Task 1: `Action`, `ButtonDown`, `ButtonUp`, `Move`,
  `SwitchWorkspace`, `Direction` from `gnome_touchpad_gestures.gestures`.
- Produces, all from `gnome_touchpad_gestures.output`:
  - `WORKSPACE_KEYS: tuple[int, ...]` = `(KEY_LEFTCTRL, KEY_LEFTALT, KEY_LEFT, KEY_RIGHT)`
  - `KEY_HOLD_S = 0.01`
  - `Output(pointer, keyboard, sleep=time.sleep)` where `pointer` and
    `keyboard` are any objects with `write(etype: int, code: int, value: int)`
    and `syn()`; methods `emit(actions: list[Action]) -> None` and
    `release_all() -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_output.py`:

```python
import unittest

from evdev import ecodes as e

from gnome_touchpad_gestures.gestures import (
    ButtonDown, ButtonUp, Direction, Move, SwitchWorkspace)
from gnome_touchpad_gestures.output import KEY_HOLD_S, WORKSPACE_KEYS, Output

SYN = "syn"


class FakeDevice:
    def __init__(self):
        self.events = []

    def write(self, etype, code, value):
        self.events.append((etype, code, value))

    def syn(self):
        self.events.append(SYN)


def total(events, code):
    return sum(ev[2] for ev in events if ev != SYN and ev[0] == e.EV_REL and ev[1] == code)


class OutputTest(unittest.TestCase):
    def setUp(self):
        self.pointer = FakeDevice()
        self.keyboard = FakeDevice()
        self.sleeps = []
        self.output = Output(self.pointer, self.keyboard, sleep=self.sleeps.append)

    def test_button_down_and_up(self):
        self.output.emit([ButtonDown(), ButtonUp()])
        self.assertEqual(self.pointer.events, [
            (e.EV_KEY, e.BTN_LEFT, 1), SYN,
            (e.EV_KEY, e.BTN_LEFT, 0), SYN,
        ])
        self.assertEqual(self.keyboard.events, [])

    def test_move_writes_whole_counts(self):
        self.output.emit([Move(12.0, -6.0)])
        self.assertEqual(self.pointer.events, [
            (e.EV_REL, e.REL_X, 12), (e.EV_REL, e.REL_Y, -6), SYN,
        ])

    def test_move_on_one_axis_omits_the_other(self):
        self.output.emit([Move(3.0, 0.0)])
        self.assertEqual(self.pointer.events, [(e.EV_REL, e.REL_X, 3), SYN])

    def test_sub_count_move_writes_nothing(self):
        self.output.emit([Move(0.25, 0.25)])
        self.assertEqual(self.pointer.events, [])

    def test_fractions_carry_over_between_moves(self):
        for _ in range(8):
            self.output.emit([Move(0.25, -0.25)])
        self.assertEqual(total(self.pointer.events, e.REL_X), 2)
        self.assertEqual(total(self.pointer.events, e.REL_Y), -2)

    def test_next_workspace_chord(self):
        self.output.emit([SwitchWorkspace(Direction.NEXT)])
        self.assertEqual(self.keyboard.events, [
            (e.EV_KEY, e.KEY_LEFTCTRL, 1), SYN,
            (e.EV_KEY, e.KEY_LEFTALT, 1), SYN,
            (e.EV_KEY, e.KEY_RIGHT, 1), SYN,
            (e.EV_KEY, e.KEY_RIGHT, 0), SYN,
            (e.EV_KEY, e.KEY_LEFTALT, 0), SYN,
            (e.EV_KEY, e.KEY_LEFTCTRL, 0), SYN,
        ])
        self.assertEqual(self.sleeps, [KEY_HOLD_S])
        self.assertEqual(self.pointer.events, [])

    def test_previous_workspace_uses_left_arrow(self):
        self.output.emit([SwitchWorkspace(Direction.PREVIOUS)])
        self.assertIn((e.EV_KEY, e.KEY_LEFT, 1), self.keyboard.events)
        self.assertNotIn((e.EV_KEY, e.KEY_RIGHT, 1), self.keyboard.events)

    def test_release_all_releases_button_and_every_key(self):
        self.output.emit([ButtonDown()])
        self.pointer.events.clear()
        self.output.release_all()
        self.assertEqual(self.pointer.events, [(e.EV_KEY, e.BTN_LEFT, 0), SYN])
        self.assertEqual(
            self.keyboard.events,
            [(e.EV_KEY, key, 0) for key in WORKSPACE_KEYS] + [SYN])

    def test_release_all_drops_pending_fractions(self):
        self.output.emit([Move(0.75, 0.0)])
        self.output.release_all()
        self.pointer.events.clear()
        self.output.emit([Move(0.5, 0.0)])
        self.assertEqual(self.pointer.events, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s tests`
Expected: FAIL with `ModuleNotFoundError: No module named 'gnome_touchpad_gestures.output'`.

- [ ] **Step 3: Write the implementation**

Create `gnome_touchpad_gestures/output.py`:

```python
"""Translates gesture actions into events on two virtual devices."""
from __future__ import annotations

import time

from evdev import ecodes as e

from gnome_touchpad_gestures.gestures import (
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 51 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add gnome_touchpad_gestures/output.py tests/test_output.py
git commit -m "Add event output for virtual pointer and keyboard"
```

---

### Task 5: Daemon

**Files:**
- Create: `gnome_touchpad_gestures/daemon.py`
- Test: `tests/test_daemon.py`

**Interfaces:**
- Consumes:
  - `GestureMachine` from `gnome_touchpad_gestures.gestures` (Task 1)
  - `SlotTracker`, `units_per_mm`, `FALLBACK_WIDTH_MM`, `FALLBACK_HEIGHT_MM` from `gnome_touchpad_gestures.slots` (Task 3)
  - `Output`, `WORKSPACE_KEYS` from `gnome_touchpad_gestures.output` (Task 4)
- Produces, all from `gnome_touchpad_gestures.daemon`:
  - `is_touchpad(capabilities: dict, props: list) -> bool`
  - `find_touchpad() -> evdev.InputDevice | None`
  - `pump(events, current_slot, tracker, machine, output, now: float) -> None`
    where `events` is an iterable of objects with `.type`, `.code`, `.value`
    and `current_slot` is a zero-argument callable returning an int
  - `check() -> int`
  - `main(argv=None) -> int`
  - Command line: `python3 -m gnome_touchpad_gestures.daemon` runs the daemon;
    `python3 -m gnome_touchpad_gestures.daemon --check` verifies device access and exits
    0 or 1. Task 6's `install.sh` relies on `--check`.

Background for the implementer:

- `evdev.list_devices()` returns only the event nodes the current user can
  open. Before the udev rule is installed it returns `[]`.
- `device.capabilities(absinfo=False)` returns `{event_type: [codes]}`.
- `device.read()` yields events and raises `OSError` if the device vanished.
  Let that propagate: systemd restarts the daemon.
- Event timestamps are wall-clock. The gesture machine needs monotonic time,
  so use `time.monotonic()` and ignore event timestamps.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daemon.py`:

```python
import unittest
from collections import namedtuple

from evdev import ecodes as e

from gnome_touchpad_gestures.daemon import is_touchpad, pump
from gnome_touchpad_gestures.gestures import ButtonDown, ButtonUp, GestureMachine, State
from gnome_touchpad_gestures.slots import SlotTracker

Event = namedtuple("Event", "type code value")


class RecordingOutput:
    def __init__(self):
        self.actions = []

    def emit(self, actions):
        self.actions += actions


def three_fingers_at(x):
    """One frame with three fingers, all at device x, 10 units per mm."""
    events = []
    for slot in range(3):
        events += [
            Event(e.EV_ABS, e.ABS_MT_SLOT, slot),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, 100 + slot),
            Event(e.EV_ABS, e.ABS_MT_POSITION_X, x + slot),
            Event(e.EV_ABS, e.ABS_MT_POSITION_Y, 200),
        ]
    return events + [Event(e.EV_SYN, e.SYN_REPORT, 0)]


class IsTouchpadTest(unittest.TestCase):
    MT = [e.ABS_X, e.ABS_Y, e.ABS_MT_SLOT, e.ABS_MT_POSITION_X,
          e.ABS_MT_POSITION_Y, e.ABS_MT_TRACKING_ID]

    def test_multitouch_pointer_is_a_touchpad(self):
        self.assertTrue(is_touchpad({e.EV_ABS: self.MT}, [e.INPUT_PROP_POINTER]))

    def test_touchscreen_is_not_a_touchpad(self):
        self.assertFalse(is_touchpad({e.EV_ABS: self.MT}, [e.INPUT_PROP_DIRECT]))

    def test_mouse_is_not_a_touchpad(self):
        self.assertFalse(is_touchpad(
            {e.EV_REL: [e.REL_X, e.REL_Y]}, [e.INPUT_PROP_POINTER]))

    def test_single_touch_pad_is_not_a_touchpad(self):
        self.assertFalse(is_touchpad(
            {e.EV_ABS: [e.ABS_X, e.ABS_Y]}, [e.INPUT_PROP_POINTER]))


class PumpTest(unittest.TestCase):
    def setUp(self):
        self.tracker = SlotTracker(10.0, 10.0)
        self.machine = GestureMachine()
        self.output = RecordingOutput()

    def pump(self, events, now, current_slot=0):
        pump(events, lambda: current_slot, self.tracker, self.machine,
             self.output, now)

    def start_drag(self):
        self.pump(three_fingers_at(500), 0.00)
        self.pump(three_fingers_at(530), 0.01)

    def test_three_finger_motion_presses_the_button(self):
        self.start_drag()
        self.assertEqual(self.output.actions, [ButtonDown()])
        self.assertIs(self.machine.state, State.DRAGGING)

    def test_events_without_a_report_produce_nothing(self):
        self.pump(three_fingers_at(500)[:-1], 0.0)
        self.assertEqual(self.output.actions, [])

    def test_syn_dropped_ends_the_drag(self):
        self.start_drag()
        self.pump([Event(e.EV_SYN, e.SYN_DROPPED, 0)], 1.0)
        self.assertIs(self.machine.state, State.RELEASE_WAIT)
        self.assertEqual(self.machine.tick(60.0), [ButtonUp()])

    def test_syn_dropped_resyncs_the_current_slot(self):
        self.pump([Event(e.EV_SYN, e.SYN_DROPPED, 0)], 0.0, current_slot=3)
        self.pump([
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, 50),
            Event(e.EV_ABS, e.ABS_MT_POSITION_X, 100),
            Event(e.EV_ABS, e.ABS_MT_POSITION_Y, 100),
            Event(e.EV_ABS, e.ABS_MT_SLOT, 0),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, 51),
            Event(e.EV_ABS, e.ABS_MT_POSITION_X, 300),
            Event(e.EV_ABS, e.ABS_MT_POSITION_Y, 100),
        ], 0.1)
        frame = self.tracker.feed(e.EV_SYN, e.SYN_REPORT, 0)
        self.assertEqual(frame.count, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s tests`
Expected: FAIL with `ModuleNotFoundError: No module named 'gnome_touchpad_gestures.daemon'`.

- [ ] **Step 3: Write the implementation**

Create `gnome_touchpad_gestures/daemon.py`:

```python
"""Reads the touchpad, runs the gesture machine, writes to virtual devices."""
from __future__ import annotations

import argparse
import os
import select
import signal
import sys
import time

import evdev
from evdev import ecodes as e

from gnome_touchpad_gestures.gestures import GestureMachine
from gnome_touchpad_gestures.output import WORKSPACE_KEYS, Output
from gnome_touchpad_gestures.slots import (
    FALLBACK_HEIGHT_MM, FALLBACK_WIDTH_MM, SlotTracker, units_per_mm)

UINPUT_PATH = "/dev/uinput"
NO_TOUCHPAD = (
    "gnome-touchpad-gestures: no accessible touchpad found. Is "
    "/etc/udev/rules.d/71-gnome-touchpad-gestures.rules installed? "
    "Log out and back in after installing it.")
NO_UINPUT = (
    f"gnome-touchpad-gestures: cannot write to {UINPUT_PATH}. Is "
    "/etc/udev/rules.d/71-gnome-touchpad-gestures.rules installed? "
    "Log out and back in after installing it.")


def is_touchpad(capabilities: dict, props: list) -> bool:
    abs_codes = capabilities.get(e.EV_ABS, [])
    return (e.INPUT_PROP_POINTER in props
            and e.ABS_MT_SLOT in abs_codes
            and e.ABS_MT_POSITION_X in abs_codes)


def find_touchpad():
    for path in sorted(evdev.list_devices()):
        try:
            device = evdev.InputDevice(path)
        except OSError:
            continue
        if is_touchpad(device.capabilities(absinfo=False), device.input_props()):
            return device
        device.close()
    return None


def make_tracker(device) -> SlotTracker:
    x = device.absinfo(e.ABS_MT_POSITION_X)
    y = device.absinfo(e.ABS_MT_POSITION_Y)
    return SlotTracker(
        units_per_mm(x.min, x.max, x.resolution, FALLBACK_WIDTH_MM),
        units_per_mm(y.min, y.max, y.resolution, FALLBACK_HEIGHT_MM),
        current_slot=device.absinfo(e.ABS_MT_SLOT).value,
    )


def pump(events, current_slot, tracker, machine, output, now: float) -> None:
    for event in events:
        if event.type == e.EV_SYN and event.code == e.SYN_DROPPED:
            # The kernel dropped events, so slot state is stale. End whatever
            # gesture was in progress; fingers must touch again to start one.
            tracker.reset(current_slot())
            output.emit(machine.update(now, 0, 0.0, 0.0))
            continue
        frame = tracker.feed(event.type, event.code, event.value)
        if frame is not None:
            output.emit(machine.update(now, frame.count, frame.cx, frame.cy))


def run(device, tracker, machine, output, clock=time.monotonic) -> None:
    def current_slot() -> int:
        return device.absinfo(e.ABS_MT_SLOT).value

    while True:
        deadline = machine.next_deadline()
        timeout = None if deadline is None else max(0.0, deadline - clock())
        ready, _, _ = select.select([device.fd], [], [], timeout)
        if ready:
            pump(device.read(), current_slot, tracker, machine, output, clock())
        else:
            output.emit(machine.tick(clock()))


def check() -> int:
    device = find_touchpad()
    if device is None:
        print(NO_TOUCHPAD, file=sys.stderr)
        return 1
    print(f"touchpad: {device.path} ({device.name})")
    device.close()
    if not os.access(UINPUT_PATH, os.W_OK):
        print(NO_UINPUT, file=sys.stderr)
        return 1
    print(f"uinput: {UINPUT_PATH} writable")
    return 0


def _terminate(signum, frame):
    raise SystemExit(0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="gnome-touchpad-gestures",
        description="Three-finger drag and four-finger workspace switch.")
    parser.add_argument(
        "--check", action="store_true",
        help="verify access to the touchpad and uinput, then exit")
    args = parser.parse_args(argv)
    if args.check:
        return check()

    device = find_touchpad()
    if device is None:
        print(NO_TOUCHPAD, file=sys.stderr)
        return 1
    if not os.access(UINPUT_PATH, os.W_OK):
        print(NO_UINPUT, file=sys.stderr)
        return 1

    signal.signal(signal.SIGTERM, _terminate)
    pointer = evdev.UInput(
        {e.EV_REL: [e.REL_X, e.REL_Y], e.EV_KEY: [e.BTN_LEFT]},
        name="gnome-gestures pointer")
    keyboard = evdev.UInput(
        {e.EV_KEY: list(WORKSPACE_KEYS)}, name="gnome-gestures keyboard")
    output = Output(pointer, keyboard)
    print(f"gnome-touchpad-gestures: listening on {device.path} ({device.name})", flush=True)
    try:
        run(device, make_tracker(device), GestureMachine(), output)
    except KeyboardInterrupt:
        pass
    finally:
        # A crash must never leave a drag or a modifier stuck.
        output.release_all()
        pointer.close()
        keyboard.close()
        device.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 59 tests` and `OK`.

- [ ] **Step 5: Verify the failure path on the real machine**

The udev rule is not installed yet, so the daemon must refuse cleanly.

Run: `python3 -m gnome_touchpad_gestures.daemon --check; echo "exit=$?"`
Expected: the `no accessible touchpad found` message on stderr, then `exit=1`.

Run: `python3 -m gnome_touchpad_gestures.daemon; echo "exit=$?"`
Expected: the same message, then `exit=1`. No traceback.

- [ ] **Step 6: Commit**

```bash
git add gnome_touchpad_gestures/daemon.py tests/test_daemon.py
git commit -m "Add daemon with device discovery and event loop"
```

---

### Task 6: Installation, service and README

**Files:**
- Create: `install/71-gnome-touchpad-gestures.rules`
- Create: `install/gnome-touchpad-gestures.service`
- Create: `install/install.sh` (executable)
- Create: `README.md`

**Interfaces:**
- Consumes: `python3 -m gnome_touchpad_gestures.daemon --check` from Task 5 (exit 0 when
  both devices are accessible, 1 otherwise).
- Produces: an installed, enabled and running `gnome-touchpad-gestures.service` user unit.

**This task changes the system and needs the user.** `install.sh` calls
`sudo`, which needs a password, so the user runs it (in Claude Code:
`! ~/gnome-touchpad-gestures/install/install.sh`). Merge to `main` before installing so
the service runs reviewed code.

- [ ] **Step 1: Write the udev rule**

Create `install/71-gnome-touchpad-gestures.rules`:

```
# gnome-touchpad-gestures: let the user at the active seat read the touchpad and create
# virtual input devices. Must sort before 73-seat-late.rules.
ACTION!="remove", SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="SYNA8017:00 06CB:CEB2 Touchpad", TAG+="uaccess"
KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
```

- [ ] **Step 2: Write the systemd unit**

Create `install/gnome-touchpad-gestures.service`:

```ini
[Unit]
Description=Three-finger drag and four-finger workspace switch
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=/usr/bin/python3 -m gnome_touchpad_gestures.daemon
WorkingDirectory=%h/gnome-touchpad-gestures
Restart=on-failure
RestartSec=2

[Install]
WantedBy=graphical-session.target
```

- [ ] **Step 3: Write the install script**

Create `install/install.sh`:

```bash
#!/usr/bin/env bash
# Installs gnome-touchpad-gestures. Safe to run repeatedly.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"
rule="71-gnome-touchpad-gestures.rules"
unit="gnome-touchpad-gestures.service"
unit_dir="$HOME/.config/systemd/user"

if [ "$repo" != "$HOME/gnome-touchpad-gestures" ]; then
    echo "gnome-touchpad-gestures must live at $HOME/gnome-touchpad-gestures (found $repo)." >&2
    exit 1
fi

echo "Installing udev rule (needs sudo)..."
if ! cmp -s "$here/$rule" "/etc/udev/rules.d/$rule"; then
    sudo install -m 0644 "$here/$rule" "/etc/udev/rules.d/$rule"
fi
sudo udevadm control --reload
sudo udevadm trigger --action=change --subsystem-match=misc --sysname-match=uinput
sudo udevadm trigger --action=change --subsystem-match=input --sysname-match='event*'
sudo udevadm settle

echo "Checking device access..."
if ! (cd "$repo" && python3 -m gnome_touchpad_gestures.daemon --check); then
    echo >&2
    echo "The rule is installed but access has not been granted to this" >&2
    echo "session yet. Log out, log back in, and run this script again." >&2
    exit 1
fi

echo "Installing user service..."
mkdir -p "$unit_dir"
install -m 0644 "$here/$unit" "$unit_dir/$unit"
systemctl --user daemon-reload
systemctl --user enable "$unit"
systemctl --user restart "$unit"

sleep 1
systemctl --user --no-pager --lines=5 status "$unit"
```

Then: `chmod +x install/install.sh`

- [ ] **Step 4: Write the README**

Create `README.md`:

````markdown
# gnome-touchpad-gestures

Three-finger drag and four-finger workspace switching for a GNOME-on-X11
laptop. One unprivileged Python daemon reads the touchpad and writes to two
virtual input devices. It never grabs the touchpad, so normal pointing,
scrolling and tapping are untouched.

| Gesture | Result |
|---|---|
| Three fingers, moving | Drags with the left button held |
| Four fingers, swipe left | Next workspace (`Ctrl+Alt+Right`) |
| Four fingers, swipe right | Previous workspace (`Ctrl+Alt+Left`) |

Design: `docs/superpowers/specs/2026-09-27-gnome-touchpad-gestures-design.md`

## Install

Requires `python3-evdev` (already present on this machine). The repo must
live at `~/gnome-touchpad-gestures`.

```bash
~/gnome-touchpad-gestures/install/install.sh
```

The script asks for your password once to install a udev rule. If it says a
re-login is needed, log out and in, then run it again.

## Uninstall

```bash
systemctl --user disable --now gnome-touchpad-gestures.service
rm ~/.config/systemd/user/gnome-touchpad-gestures.service
systemctl --user daemon-reload
sudo rm /etc/udev/rules.d/71-gnome-touchpad-gestures.rules
sudo udevadm control --reload
```

## Tuning

Edit the constants at the top of `gnome_touchpad_gestures/gestures.py`, then run
`systemctl --user restart gnome-touchpad-gestures`.

| Constant | Default | Raise it to... |
|---|---|---|
| `DRAG_START_MM` | 2.0 | make accidental drags rarer |
| `DRAG_RELEASE_S` | 0.3 | get more time to reposition fingers mid-drag |
| `POINTER_COUNTS_PER_MM` | 12.0 | make the pointer faster while dragging |
| `SWIPE_MM` | 15.0 | require a longer swipe to switch workspace |
| `SWIPE_AXIS_RATIO` | 1.5 | require a straighter swipe |

## Troubleshooting

```bash
systemctl --user status gnome-touchpad-gestures      # is it running?
journalctl --user -u gnome-touchpad-gestures -n 20   # what did it say?
cd ~/gnome-touchpad-gestures && python3 -m gnome_touchpad_gestures.daemon --check
```

## Tests

```bash
cd ~/gnome-touchpad-gestures && python3 -m unittest discover -s tests
```

## Manual checklist

Run after installing or changing a tunable.

- [ ] Drag a window by its title bar with three fingers.
- [ ] Select text with three fingers.
- [ ] Lift and re-place fingers mid-drag; the drag continues.
- [ ] Three-finger tap still pastes (middle click).
- [ ] Two-finger scroll and one-finger pointing are unchanged.
- [ ] Four-finger swipe left and right switches workspace, once per swipe.
- [ ] Suspend and resume; gestures work again within a few seconds.
- [ ] `systemctl --user stop gnome-touchpad-gestures` mid-drag releases the button.

## Known behaviour

- For a moment after lifting from a drag the button is still held, so a
  finger left on the pad can nudge the dragged item.
- Workspace changes snap once per swipe; they do not follow the fingers.
- Holding a physical modifier key while swiping changes what the key chord
  means to GNOME.
````

- [ ] **Step 5: Verify the files without touching the system**

Run: `bash -n install/install.sh && echo "script ok"`
Expected: `script ok`

Run: `udevadm verify install/71-gnome-touchpad-gestures.rules`
Expected: `1 udev rules files have been checked.` with `Success: 1` and
`Fail:    0`, exit status 0.

Run: `systemd-analyze verify --user install/gnome-touchpad-gestures.service 2>&1 | grep -i gnome-touchpad-gestures || echo "unit ok"`
Expected: `unit ok`

Run: `python3 -m unittest discover -s tests`
Expected: `Ran 59 tests` and `OK`.

- [ ] **Step 6: Commit**

```bash
git add install README.md
git commit -m "Add udev rule, user service, installer and README"
```

- [ ] **Step 7: Merge to main**

```bash
git switch main
git merge --no-ff daemon -m "Merge daemon: three-finger drag and four-finger workspace switch"
```

- [ ] **Step 8: Install (user runs this)**

Ask the user to run: `~/gnome-touchpad-gestures/install/install.sh`

Expected output ends with the service status showing `Active: active (running)`
and the log line `gnome-touchpad-gestures: listening on /dev/input/event... (SYNA8017:00 06CB:CEB2 Touchpad)`.

If the script stops with the re-login message, the user logs out and in and
runs it again. That is an expected path, not a failure.

- [ ] **Step 9: Verify the installed system**

Run: `cd ~/gnome-touchpad-gestures && python3 -m gnome_touchpad_gestures.daemon --check`
Expected: a `touchpad:` line and a `uinput:` line, exit 0.

Run: `systemctl --user is-active gnome-touchpad-gestures && systemctl --user is-enabled gnome-touchpad-gestures`
Expected: `active` then `enabled`.

Run: `xinput list --name-only | grep gnome-gestures`
Expected: `gnome-gestures pointer` and `gnome-gestures keyboard`.

Run: `journalctl --user -u gnome-touchpad-gestures -n 20 --no-pager`
Expected: the `listening on` line and no tracebacks.

- [ ] **Step 10: Manual checklist (user)**

Ask the user to work through the manual checklist in `README.md` and report
each item. For anything that feels wrong, adjust the matching constant from
the Tuning table, restart the service, and re-test. Commit any tuned values:

```bash
git add gnome_touchpad_gestures/gestures.py
git commit -m "Tune gesture constants after manual testing"
```
