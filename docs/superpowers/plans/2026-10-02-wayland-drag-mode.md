# Wayland Drag Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On GNOME on Wayland, three fingers drag with the left button held, and nothing else is added; the X11 mode stays exactly as in 0.2.1.

**Architecture:** The daemon picks a mode from `XDG_SESSION_TYPE`. In drag mode it makes only the virtual pointer, carries out only drag actions, takes a name on the session bus, and asks the extension before every drag. The extension, on Wayland, swallows whole swipes of exactly three fingers while that name is on the bus, using public event calls only.

**Tech Stack:** Python 3 (`evdev`, `dbus-python`, `unittest`), GNOME Shell extension in JavaScript (ESM, tested with `gjs`), bash installer, systemd user unit.

**Spec:** `docs/superpowers/specs/2026-10-02-wayland-drag-mode-design.md`

## Global Constraints

- Nothing inside the shell ever presses a button or moves the pointer.
- The X11 mode behaves exactly as in 0.2.1. No existing test may be weakened to make room.
- Mode: `XDG_SESSION_TYPE == "wayland"` is drag mode; anything else, including unset, is full mode.
- Bus name: `io.github.asifmohtesham.Gestures.Daemon`.
- Extension `shell-version`: `["46", "50"]`, nothing between.
- Target version: `0.3.0`, written in `gnome_x11_touchpad_gestures/__init__.py`, the extension's `metadata.json` and `CHANGELOG.md`.
- The README names no three-number version of anything (a test enforces it): write "libinput 1.31", "Ubuntu 26.04".
- Run the suite from the repository root: `python3 -W ignore -m unittest discover -s tests` and `gjs -m tests/js/test_extension.js`.
- Tests must not depend on the session they run in. This machine is on Wayland now; a test that needs a mode sets `XDG_SESSION_TYPE` itself.
- Edit `install/*.sh` with the Edit tool, never with a shell command whose text holds an `rm -rf` on a variable.
- Commits: author email is the repository's own setting (`30242354+asifmohtesham@users.noreply.github.com`); write the message to a file and use `git commit -F <file>`; every message ends with these two lines:

```
Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01H1SxrMukWMk6jN9EQ9fcCM
```

- Work on branch `wayland-drag`. Nothing is merged, pushed, tagged or released by this plan.
- Never install in the same command as a merge or a checkout. Check the branch and `git status` first.

## Review Focus

Conditions the spec implies but does not spell out as tests, most likely first. Each is pinned by a test in the task named.

1. **The session bus is not there when the daemon starts.** The daemon must not crash, must not drag, and must take its name later without a restart. (Task 1: the name is tried again after a rest. Task 3: `main` survives.)
2. **Four fingers land during a three-finger drag.** The button must come up, and the swipe that follows must be left to GNOME. (Task 2.)
3. **The extension answers, but says no.** That drag is dropped whole, including its release, and the next drag is asked about afresh. (Task 2.)
4. **A touchpad event that is not a swipe, or has no phase the filter knows, reaches the filter.** Never swallowed, never an exception into the shell. (Task 4 and Task 5.)
5. **`XDG_SESSION_TYPE` is unset, empty or something else (`tty`, `mir`).** Full mode, as before. The installer names both sessions the service starts in. (Task 3 and Task 6.)

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `gnome_x11_touchpad_gestures/shell.py` | modify | `DAEMON_NAME`, `announce()`, `three_fingers_free()`, the complaint per mode |
| `gnome_x11_touchpad_gestures/output.py` | modify | `NoDevice`, `DragOnly` |
| `gnome_x11_touchpad_gestures/daemon.py` | modify | `session_mode()`, `main` per mode, `--check` per mode |
| `extension/.../gestures.js` | modify | `SwipeFilter`, pure |
| `extension/.../extension.js` | modify | Wayland behaviour, `ThreeFingersFree` |
| `extension/.../metadata.json` | modify | shell versions, name, description, version |
| `tests/js/stand_ins/meta.js` | create | stands in for `gi://Meta` |
| `tests/js/stand_ins/clutter.js` | modify | swipe event names |
| `tests/js/run_extension.js` | modify | a stage that events can be put through, a mode |
| `tests/helpers/drive_extension.py` | modify | drives the Wayland behaviour too |
| `install/gnome-x11-touchpad-gestures.service` | modify | starts in both sessions |
| `install/install.sh` | modify | advice per session |
| `README.md`, `CHANGELOG.md`, first spec | modify | both modes |

`EXT` below stands for `extension/gnome-x11-touchpad-gestures@asifmohtesham.github.io`.

---

### Task 1: The daemon's name, and the question it asks

**Files:**
- Modify: `gnome_x11_touchpad_gestures/shell.py`
- Modify: `EXT/extension.js` (declare the method only)
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `Asked`, `Shell._ask`, `Shell._connected`, `SHELL_RETRY_S`, `PREFIX` (all in `shell.py` already).
- Produces: `shell.DAEMON_NAME: str`, `shell.FULL_COMPLAINT: str`, `shell.DRAG_COMPLAINT: str`, `Shell(connect=..., clock=..., complaint=FULL_COMPLAINT)`, `Shell.announce() -> bool`, `Shell.three_fingers_free() -> bool`, and `"ThreeFingersFree": ""` in `shell.EXTENSION_CALLS`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_shell.py`, extend the import and `FakeBus`:

```python
from gnome_x11_touchpad_gestures.shell import (
    DAEMON_NAME, DRAG_COMPLAINT, EXTENSION_INTERFACE, EXTENSION_PATH,
    OVERVIEW_TIMEOUT_S, SHELL_RETRY_S, Shell)
```

Add to `FakeBus.__init__`:

```python
        self.names = []
        self.name_attempts = 0
        self.name_error = None
        self.name_reply = 1   # the name is ours
```

Add to `FakeBus`:

```python
    def request_name(self, name, flags=0):
        self.name_attempts += 1
        if self.name_error:
            raise self.name_error
        self.names.append(name)
        return self.name_reply
```

Add before the `if __name__` line:

```python
class DragModeTest(unittest.TestCase):
    """What the daemon needs of the bus where the desktop does the other
    gestures itself: a name the extension can see, and its leave to drag."""

    def setUp(self):
        self.bus = FakeBus()
        self.now = 100.0
        self.connect_error = None
        self.shell = Shell(connect=self.connect, clock=lambda: self.now,
                           complaint=DRAG_COMPLAINT)

    def connect(self):
        if self.connect_error:
            raise self.connect_error
        return self.bus

    def quietly(self, call, *args):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            answer = call(*args)
        return answer, stderr.getvalue()

    def test_name_is_the_one_the_extension_watches_for(self):
        self.assertEqual(DAEMON_NAME, "io.github.asifmohtesham.Gestures.Daemon")

    def test_name_is_taken(self):
        self.assertEqual(self.quietly(self.shell.announce), (True, ""))
        self.assertEqual(self.bus.names, [DAEMON_NAME])

    def test_name_already_ours_counts(self):
        self.bus.name_reply = 4
        self.assertIs(self.quietly(self.shell.announce)[0], True)

    def test_name_held_by_another_program_is_said_and_is_no(self):
        for reply in (2, 3):
            with self.subTest(reply=reply):
                self.setUp()
                self.bus.name_reply = reply
                taken, said = self.quietly(self.shell.announce)
                self.assertIs(taken, False)
                self.assertIn(DAEMON_NAME, said)
                self.assertIn("three-finger drag is off", said)

    def test_bus_that_refuses_the_name_is_said_and_survived(self):
        self.bus.name_error = RuntimeError("not allowed")
        taken, said = self.quietly(self.shell.announce)
        self.assertIs(taken, False)
        self.assertIn("not allowed", said)

    def test_bus_that_is_not_there_is_survived(self):
        self.connect_error = RuntimeError("no session bus")
        taken, said = self.quietly(self.shell.announce)
        self.assertIs(taken, False)
        self.assertIn("no session bus", said)

    def test_name_once_taken_is_not_asked_for_again(self):
        for _ in range(3):
            self.quietly(self.shell.announce)
        self.assertEqual(self.bus.name_attempts, 1)

    def test_name_that_could_not_be_taken_is_left_alone_for_a_while(self):
        self.bus.name_error = RuntimeError("not allowed")
        said = [self.quietly(self.shell.announce)[1] for _ in range(3)]
        self.assertEqual(self.bus.name_attempts, 1)
        self.assertEqual([bool(text) for text in said], [True, False, False])

    def test_name_is_tried_again_after_that_while_and_then_had(self):
        self.connect_error = RuntimeError("no session bus")
        self.quietly(self.shell.announce)
        self.connect_error = None
        self.now += SHELL_RETRY_S
        self.assertEqual(self.quietly(self.shell.announce), (True, ""))
        self.assertEqual(self.bus.names, [DAEMON_NAME])

    def test_extension_s_leave_is_asked(self):
        self.bus.extension_answers["ThreeFingersFree"] = dbus.Boolean(True)
        self.assertEqual(self.quietly(self.shell.three_fingers_free), (True, ""))
        self.assertEqual(self.bus.calls, [(
            "org.gnome.Shell", EXTENSION_PATH, EXTENSION_INTERFACE,
            "ThreeFingersFree", "", (), OVERVIEW_TIMEOUT_S)])

    def test_extension_that_says_no_is_taken_at_its_word(self):
        self.bus.extension_answers["ThreeFingersFree"] = dbus.Boolean(False)
        self.assertEqual(self.quietly(self.shell.three_fingers_free), (False, ""))

    def test_asking_takes_the_name_first(self):
        # A daemon started before the bus was there has no name yet.
        self.bus.extension_answers["ThreeFingersFree"] = dbus.Boolean(True)
        self.quietly(self.shell.three_fingers_free)
        self.quietly(self.shell.three_fingers_free)
        self.assertEqual(self.bus.names, [DAEMON_NAME])

    def test_silent_extension_means_no_and_says_what_that_costs_here(self):
        self.bus.extension_error = RuntimeError("No such interface")
        free, said = self.quietly(self.shell.three_fingers_free)
        self.assertIs(free, False)
        self.assertIn("three-finger drag is off", said)
        self.assertNotIn("workspaces", said)

    def test_silent_extension_is_left_alone_for_a_while(self):
        self.bus.extension_error = RuntimeError("No such interface")
        self.quietly(self.shell.three_fingers_free)
        asked = len(self.bus.calls)
        self.assertEqual(self.quietly(self.shell.three_fingers_free), (False, ""))
        self.assertEqual(len(self.bus.calls), asked)

    def test_where_every_gesture_is_done_the_complaint_is_the_old_one(self):
        shell = Shell(connect=self.connect, clock=lambda: self.now)
        self.bus.extension_error = RuntimeError("No such interface")
        said = self.quietly(shell.pointer_over_window)[1]
        self.assertIn("workspaces", said)
        self.assertNotIn("three-finger drag", said)
```

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -W ignore -m unittest tests.test_shell 2>&1 | tail -3`
Expected: an `ImportError` naming `DAEMON_NAME`.

- [ ] **Step 3: Write the client's side**

In `gnome_x11_touchpad_gestures/shell.py`, add `import dbus.bus` under `import dbus`, add `"ThreeFingersFree": "",` to `EXTENSION_CALLS`, and add after `EXTENSION_CALLS`:

```python
# The name the daemon takes on the session bus where it only drags. The
# extension frees three fingers for it while the name is there, and the
# bus takes the name back by itself when the daemon stops for any reason.
DAEMON_NAME = "io.github.asifmohtesham.Gestures.Daemon"
NAME_IS_OURS = (dbus.bus.REQUEST_NAME_REPLY_PRIMARY_OWNER,
                dbus.bus.REQUEST_NAME_REPLY_ALREADY_OWNER)
# What a silent extension costs, which depends on what the daemon is doing.
FULL_COMPLAINT = (
    "the shell extension is not answering, so workspaces will not follow "
    "the fingers and glides are held back only in the overview")
DRAG_COMPLAINT = (
    "the shell extension is not answering, so three-finger drag is off")
```

Change `Shell.__init__` to:

```python
    def __init__(self, connect=dbus.SessionBus, clock=time.monotonic,
                 complaint=FULL_COMPLAINT) -> None:
        self._connect = connect
        self._bus = None
        self._overview = Asked(
            clock, "could not ask whether the overview is open")
        self._extension = Asked(clock, complaint)
        self._name = Asked(
            clock, f"could not take the name {DAEMON_NAME} on the session "
                   "bus, so three-finger drag is off")
        self._named = False
```

Add to `Shell`, after `swipe_cancel`:

```python
    # Where the desktop does the other gestures itself.

    def announce(self) -> bool:
        """Takes the daemon's name on the bus. Whether it is ours."""
        if self._named:
            return True
        if self._name.resting():
            return False
        try:
            reply = self._connected().request_name(DAEMON_NAME)
        except Exception as error:
            self._name.failed(error)
            return False
        if reply not in NAME_IS_OURS:
            self._name.failed(RuntimeError("another program holds it"))
            return False
        self._name.answered()
        self._named = True
        return True

    def three_fingers_free(self) -> bool:
        """Whether the extension is keeping the shell's swipes off three fingers."""
        # Taken here too: a daemon started before the bus was there has no
        # name yet, and without one the extension frees nothing.
        self.announce()
        return bool(self._ask("ThreeFingersFree"))
```

- [ ] **Step 4: Declare the method in the extension, answering no for now**

`tests/test_extension.py` holds the extension's interface to `EXTENSION_CALLS`, so the method is declared now and given its behaviour in Task 5. In `EXT/extension.js`, add inside the `<interface>` after `SwipeCancel`:

```xml
    <method name="ThreeFingersFree">
      <arg type="b" direction="out" name="free"/>
    </method>
```

and add to the class, after `SwipeCancel()`:

```js
    ThreeFingersFree() {
        return false;
    }
```

- [ ] **Step 5: Run everything**

Run: `python3 -W ignore -m unittest discover -s tests 2>&1 | tail -3 && gjs -m tests/js/test_extension.js | tail -1`
Expected: `OK` and `35 tests, 0 failed`.

- [ ] **Step 6: Commit**

Message: `Give the daemon a name on the bus, and a question for the extension`.

---

### Task 2: Carrying out the drag and nothing else

**Files:**
- Modify: `gnome_x11_touchpad_gestures/output.py`
- Test: `tests/test_output.py`

**Interfaces:**
- Consumes: `Output(pointer, keyboard, wheel, shell, sleep=...)`, with `emit(actions)` and `release_all()`; the actions `ButtonDown`, `ButtonUp`, `Move` from `gestures.py`.
- Produces: `output.NoDevice()` with `write(etype, code, value)` and `syn()` that do nothing; `output.DragOnly(output, allowed)` where `allowed` is a function of no arguments returning a truth value, with `emit(actions)` and `release_all()`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_output.py`, change the import of the output module to:

```python
from gnome_x11_touchpad_gestures.output import (
    GLIDE_CHECK_STEPS, KEY_HOLD_S, WORKSPACE_KEYS, DragOnly, NoDevice, Output)
```

Add before the `if __name__` line:

```python
class DragOnlyTest(unittest.TestCase):
    """Where the desktop does the other gestures itself, only the drag is
    carried out, and only when the extension has freed three fingers."""

    PRESS = [(e.EV_KEY, e.BTN_LEFT, 1), SYN]
    RELEASE = [(e.EV_KEY, e.BTN_LEFT, 0), SYN]

    def setUp(self):
        self.pointer = FakeDevice()
        self.shell = FakeShell()
        self.answers = []
        self.asked = 0
        self.drag = DragOnly(
            Output(self.pointer, NoDevice(), NoDevice(), self.shell,
                   sleep=lambda seconds: None),
            self.allowed)

    def allowed(self):
        self.asked += 1
        return self.answers.pop(0) if self.answers else True

    def test_drag_is_carried_out(self):
        self.drag.emit([ButtonDown()])
        self.drag.emit([Move(12.0, 0.0)])
        self.drag.emit([ButtonUp()])
        self.assertEqual(
            self.pointer.events,
            self.PRESS + [(e.EV_REL, e.REL_X, 12), SYN] + self.RELEASE)

    def test_everything_else_is_left_to_the_desktop(self):
        self.drag.emit([
            SwitchWorkspace(Direction.NEXT), Overview(show=True),
            SwipeBegin(1.0, travel=-5.0), SwipeMove(1.01, -20.0),
            SwipeEnd(1.2), SwipeCancel(), Scroll(0.0, 400.0, first=True)])
        self.assertEqual(self.pointer.events, [])
        self.assertEqual(self.shell.requests, [])
        self.assertEqual(self.shell.swipes, [])
        self.assertEqual(self.shell.asked, 0)

    def test_drag_the_extension_did_not_agree_to_is_dropped_whole(self):
        self.answers = [False]
        self.drag.emit([ButtonDown()])
        self.drag.emit([Move(12.0, 0.0)])
        self.drag.emit([ButtonUp()])
        self.assertEqual(self.pointer.events, [])

    def test_extension_is_asked_once_for_each_drag(self):
        self.drag.emit([ButtonDown(), Move(5.0, 0.0), Move(5.0, 0.0), ButtonUp()])
        self.assertEqual(self.asked, 1)
        self.drag.emit([ButtonDown(), ButtonUp()])
        self.assertEqual(self.asked, 2)

    def test_it_is_not_asked_about_anything_but_a_drag(self):
        self.drag.emit([Move(5.0, 0.0), ButtonUp(), Overview(show=True)])
        self.assertEqual(self.asked, 0)

    def test_drag_after_a_refused_one_is_asked_about_afresh(self):
        self.answers = [False, True]
        self.drag.emit([ButtonDown(), Move(5.0, 0.0), ButtonUp()])
        self.drag.emit([ButtonDown(), ButtonUp()])
        self.assertEqual(self.pointer.events, self.PRESS + self.RELEASE)

    def test_answer_need_only_be_true_or_false_in_spirit(self):
        self.answers = [None]
        self.drag.emit([ButtonDown(), ButtonUp()])
        self.assertEqual(self.pointer.events, [])

    def test_release_without_a_press_writes_nothing(self):
        self.drag.emit([ButtonUp()])
        self.drag.emit([Move(5.0, 0.0)])
        self.assertEqual(self.pointer.events, [])

    def test_fourth_finger_ends_the_drag_and_the_swipe_is_the_desktop_s(self):
        # What the gesture machine makes of a fourth finger landing.
        self.drag.emit([ButtonDown()])
        self.drag.emit([ButtonUp()])
        self.drag.emit([SwipeBegin(1.0, travel=-6.0), SwipeMove(1.01, -9.0)])
        self.assertEqual(self.pointer.events, self.PRESS + self.RELEASE)
        self.assertEqual(self.shell.swipes, [])

    def test_release_all_lets_go_of_the_button(self):
        self.drag.emit([ButtonDown()])
        self.drag.release_all()
        self.assertEqual(self.pointer.events, self.PRESS + self.RELEASE)

    def test_moves_after_release_all_are_not_a_drag(self):
        self.drag.emit([ButtonDown()])
        self.drag.release_all()
        self.drag.emit([Move(50.0, 0.0), ButtonUp()])
        self.assertEqual(self.pointer.events, self.PRESS + self.RELEASE)

    def test_release_all_needs_no_keyboard_or_wheel(self):
        self.drag.release_all()
        self.assertEqual(self.pointer.events, self.RELEASE)
        self.assertEqual(self.shell.swipes, [])


class NoDeviceTest(unittest.TestCase):
    def test_it_takes_what_a_device_takes_and_does_nothing(self):
        device = NoDevice()
        self.assertIsNone(device.write(e.EV_KEY, e.KEY_LEFTCTRL, 0))
        self.assertIsNone(device.syn())
```

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -W ignore -m unittest tests.test_output 2>&1 | tail -3`
Expected: an `ImportError` naming `DragOnly`.

- [ ] **Step 3: Write `NoDevice` and `DragOnly`**

Append to `gnome_x11_touchpad_gestures/output.py`:

```python
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
```

- [ ] **Step 4: Run everything**

Run: `python3 -W ignore -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 5: Commit**

Message: `Carry out the drag alone where the desktop does the rest`.

---

### Task 3: The daemon picks its mode

**Files:**
- Modify: `gnome_x11_touchpad_gestures/daemon.py`
- Test: `tests/test_daemon.py`, `tests/test_daemon_main.py`

**Interfaces:**
- Consumes: `Shell(complaint=...)`, `Shell.three_fingers_free`, `Shell.announce`, `DRAG_COMPLAINT` (Task 1); `DragOnly`, `NoDevice` (Task 2).
- Produces: `daemon.session_mode(environ=None) -> "drag" | "full"`; `daemon.extension_state(loaded, mode="full") -> str`; a `session:` line from `--check`; a startup line that names the mode.

- [ ] **Step 1: Pin the mode in the tests that exist**

The suite now runs in a Wayland session, so every test that runs `main` or `check` must say which session it means.

In `tests/test_daemon_main.py`, give `FakeShell` what drag mode asks of it:

```python
class FakeShell:
    requests = []
    overview_open = False
    follows_fingers = True
    swipes = []
    made_with = None
    three_free = True
    asked_free = 0
    announced = 0

    def __init__(self, **kwargs):
        FakeShell.made_with = kwargs

    def announce(self):
        FakeShell.announced += 1
        return True

    def three_fingers_free(self):
        FakeShell.asked_free += 1
        return FakeShell.three_free
```

(the other methods stay as they are). In `MainTest.setUp` add:

```python
        FakeShell.made_with = None
        FakeShell.three_free = True
        FakeShell.asked_free = 0
        FakeShell.announced = 0
```

Change `run_main` to take the session and set it:

```python
    def run_main(self, touchpad, session="x11"):
        if touchpad is not None:
            # A test that goes wrong must fail, not leave the daemon waiting.
            watchdog = threading.Timer(5.0, touchpad.push, [interrupt])
            watchdog.daemon = True
            watchdog.start()
            self.addCleanup(watchdog.cancel)
        with mock.patch.object(daemon, "find_touchpad", return_value=touchpad), \
                mock.patch.object(daemon.os, "access", return_value=True), \
                mock.patch.object(daemon.evdev, "UInput", self.make_uinput), \
                mock.patch.object(daemon, "Shell", FakeShell), \
                mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": session}), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            status = daemon.main([])
        self.said = stdout.getvalue()
        return status
```

Replace `test_startup_line_names_the_version` with:

```python
    def test_startup_line_names_the_version(self):
        import gnome_x11_touchpad_gestures
        self.run_main(FakeTouchpad([interrupt]))
        self.assertIn(gnome_x11_touchpad_gestures.__version__, self.said)
```

In `tests/test_daemon.py`, change `checked` to:

```python
    def checked(self, loaded, session="x11"):
        class Shell:
            def extension_version(self):
                return loaded

        self.having(event8=MULTITOUCH + (BUS_I2C,))
        with mock.patch.object(daemon, "Shell", Shell), \
                mock.patch.object(daemon.os, "access", return_value=True), \
                mock.patch.dict(daemon.os.environ, {"XDG_SESSION_TYPE": session}), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            status = daemon.check()
        return status, stdout.getvalue()
```

Run: `python3 -W ignore -m unittest tests.test_daemon tests.test_daemon_main 2>&1 | tail -3`
Expected: `OK`. Nothing has changed in the daemon yet.

- [ ] **Step 2: Write the failing tests**

In `tests/test_daemon.py`, add after the three `test_check_says_...` tests:

```python
    def test_check_names_the_mode_it_would_run_in(self):
        self.assertIn("session: x11, so every gesture", self.checked("0.0.1")[1])
        self.assertIn("session: wayland, so three-finger drag only",
                      self.checked("0.0.1", session="wayland")[1])

    def test_check_on_wayland_says_to_log_out_not_to_restart_the_shell(self):
        for loaded in (None, "0.0.1"):
            with self.subTest(loaded=loaded):
                status, said = self.checked(loaded, session="wayland")
                self.assertEqual(status, 0)
                self.assertIn("log out and back in", said)
                self.assertNotIn("Alt+F2", said)

    def test_check_on_wayland_says_what_a_silent_extension_costs_there(self):
        said = self.checked(None, session="wayland")[1]
        self.assertIn("three-finger drag is off", said)
        self.assertNotIn("glides", said)

    def test_check_on_wayland_says_the_extension_is_answering(self):
        said = self.checked(daemon.__version__, session="wayland")[1]
        self.assertIn(f"extension: answering, version {daemon.__version__}", said)
        self.assertNotIn("log out", said)
```

and add a class at the end, before the `if __name__` line:

```python
class SessionModeTest(unittest.TestCase):
    def test_wayland_is_where_only_the_drag_is_wanted(self):
        self.assertEqual(daemon.session_mode({"XDG_SESSION_TYPE": "wayland"}), "drag")

    def test_every_other_session_gets_every_gesture_as_before(self):
        for session in ("x11", "", "tty", "mir", "Wayland", "wayland "):
            with self.subTest(session=session):
                self.assertEqual(
                    daemon.session_mode({"XDG_SESSION_TYPE": session}), "full")

    def test_no_word_of_the_session_is_every_gesture_too(self):
        self.assertEqual(daemon.session_mode({}), "full")

    def test_session_is_read_from_the_environment_when_none_is_given(self):
        with mock.patch.dict(daemon.os.environ, {"XDG_SESSION_TYPE": "wayland"}):
            self.assertEqual(daemon.session_mode(), "drag")
        with mock.patch.dict(daemon.os.environ, {"XDG_SESSION_TYPE": "x11"}):
            self.assertEqual(daemon.session_mode(), "full")
```

In `tests/test_daemon_main.py`, add to `MainTest` (and `from gnome_x11_touchpad_gestures import shell` to the imports):

```python
    def test_drag_mode_makes_the_pointer_and_no_other_device(self):
        self.assertEqual(self.run_main(FakeTouchpad([interrupt]), "wayland"), 0)
        self.assertEqual(set(self.devices), {POINTER})

    def test_every_other_session_makes_all_three_as_before(self):
        for session in ("x11", "tty", ""):
            with self.subTest(session=session):
                self.devices = {}
                self.run_main(FakeTouchpad([interrupt]), session)
                self.assertEqual(set(self.devices), {POINTER, KEYBOARD, WHEEL})

    def test_drag_mode_drags(self):
        touchpad = FakeTouchpad([land, move_after_settling, interrupt])
        self.assertEqual(self.run_main(touchpad, "wayland"), 0)
        self.assertEqual(self.button_values(), [1, 0])
        self.assertEqual(FakeShell.asked_free, 1)

    def test_drag_mode_presses_nothing_without_the_extension_s_leave(self):
        FakeShell.three_free = False
        touchpad = FakeTouchpad([land, move_after_settling, interrupt])
        self.assertEqual(self.run_main(touchpad, "wayland"), 0)
        # Never pressed; the only write is the release on shutdown.
        self.assertEqual(self.button_values(), [0])

    def test_drag_mode_takes_its_name_as_it_starts(self):
        self.run_main(FakeTouchpad([interrupt]), "wayland")
        self.assertEqual(FakeShell.announced, 1)

    def test_no_name_is_taken_where_every_gesture_is_done(self):
        self.run_main(FakeTouchpad([interrupt]), "x11")
        self.assertEqual(FakeShell.announced, 0)

    def test_drag_mode_leaves_swipes_and_the_overview_to_the_desktop(self):
        touchpad = FakeTouchpad(swipe_left() + [interrupt])
        self.assertEqual(self.run_main(touchpad, "wayland"), 0)
        self.assertEqual(FakeShell.swipes, [])
        self.assertEqual(FakeShell.requests, [])
        self.assertNotIn(KEYBOARD, self.devices)

    def test_drag_mode_survives_a_name_it_cannot_take(self):
        with mock.patch.object(FakeShell, "announce", return_value=False):
            self.assertEqual(self.run_main(FakeTouchpad([interrupt]), "wayland"), 0)

    def test_shell_is_told_what_its_silence_costs_in_each_mode(self):
        self.run_main(FakeTouchpad([interrupt]), "wayland")
        self.assertEqual(FakeShell.made_with, {"complaint": shell.DRAG_COMPLAINT})
        self.run_main(FakeTouchpad([interrupt]), "x11")
        self.assertEqual(FakeShell.made_with, {})

    def test_startup_line_names_the_mode(self):
        self.run_main(FakeTouchpad([interrupt]), "wayland")
        self.assertIn("three-finger drag only", self.said)
        self.run_main(FakeTouchpad([interrupt]), "x11")
        self.assertIn("every gesture", self.said)

    def test_drag_mode_lets_go_of_everything_on_the_way_out(self):
        touchpad = FakeTouchpad([land, move_after_settling, interrupt])
        self.run_main(touchpad, "wayland")
        self.assertEqual(self.button_values()[-1], 0)
        self.assertTrue(self.devices[POINTER].closed)
        self.assertTrue(touchpad.closed)
```

- [ ] **Step 3: Run them and see them fail**

Run: `python3 -W ignore -m unittest tests.test_daemon tests.test_daemon_main 2>&1 | grep -E "^(FAIL|ERROR|FAILED)" | head -30`
Expected: the new tests fail or error; `AttributeError: ... has no attribute 'session_mode'` among them. No older test fails.

- [ ] **Step 4: Write the daemon's side**

In `gnome_x11_touchpad_gestures/daemon.py`:

Change the two imports:

```python
from gnome_x11_touchpad_gestures.output import (
    WORKSPACE_KEYS, DragOnly, NoDevice, Output)
from gnome_x11_touchpad_gestures.shell import (
    DRAG_COMPLAINT, OVERVIEW_TIMEOUT_S, Shell)
```

Add after `STAMP_MAX_AGE_S`:

```python
# What the daemon does, which depends on what the desktop does itself.
MODE_SAYS = {
    "full": "every gesture",
    "drag": "three-finger drag only",
}
```

Add before `class Machines`:

```python
def session_mode(environ=None) -> str:
    """ "drag" where the desktop does the other gestures itself, else "full".

    GNOME on Wayland has its own swipes and its toolkits their own momentum.
    What it lacks is three-finger drag.
    """
    environ = os.environ if environ is None else environ
    return "drag" if environ.get("XDG_SESSION_TYPE") == "wayland" else "full"
```

Replace `RESTART_THE_SHELL`, `extension_state` and `check` with:

```python
RESTART_THE_SHELL = "press Alt+F2, type r, press Enter"
LOG_OUT = "log out and back in"


def extension_state(loaded: str | None, mode: str = "full") -> str:
    """What to tell someone about the shell extension."""
    if mode == "drag":
        # A Wayland shell cannot be restarted in place.
        if loaded is None:
            return ("not answering, so three-finger drag is off. If it is "
                    f"installed, {LOG_OUT} to load it")
        if loaded != __version__:
            return (f"answering, but the shell still runs version {loaded} "
                    f"and this is {__version__}. To load it, {LOG_OUT}")
        return f"answering, version {loaded}"
    if loaded is None:
        return ("not answering, so workspaces snap across and glides are held "
                "back only in the overview. If it is installed, restart the "
                f"shell to load it: {RESTART_THE_SHELL}")
    if loaded != __version__:
        return (f"answering, but the shell still runs version {loaded} and "
                f"this is {__version__}. Restart the shell: {RESTART_THE_SHELL}")
    return f"answering, version {loaded}"


def check() -> int:
    device = find_touchpad()
    if device is None:
        return no_touchpad()
    print(f"touchpad: {device.path} ({device.name})")
    device.close()
    if not os.access(UINPUT_PATH, os.W_OK):
        print(NO_UINPUT, file=sys.stderr)
        return EXIT_NO_ACCESS
    print(f"uinput: {UINPUT_PATH} writable")
    mode = session_mode()
    session = os.environ.get("XDG_SESSION_TYPE") or "unknown"
    print(f"session: {session}, so {MODE_SAYS[mode]}")
    print(f"extension: {extension_state(Shell().extension_version(), mode)}")
    return 0
```

In `main`, replace everything from `pointer = create(` down to and including the `print(` call with:

```python
        mode = session_mode()
        pointer = create(
            {e.EV_REL: [e.REL_X, e.REL_Y], e.EV_KEY: [e.BTN_LEFT]},
            POINTER_NAME)
        if mode == "drag":
            # The desktop does the other gestures, so their devices are
            # not made and their actions not carried out.
            shell = Shell(complaint=DRAG_COMPLAINT)
            output = DragOnly(
                Output(pointer, NoDevice(), NoDevice(), shell),
                shell.three_fingers_free)
            machine = GestureMachine()
            shell.announce()
        else:
            keyboard = create(
                {e.EV_KEY: list(WORKSPACE_KEYS)}, KEYBOARD_NAME)
            # The motion axes and buttons are never used; they are what makes
            # libinput accept the device as a mouse and listen to its wheel.
            wheel = create(
                {e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL,
                            e.REL_WHEEL_HI_RES, e.REL_HWHEEL_HI_RES],
                 e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE]},
                WHEEL_NAME)
            shell = Shell()
            output = Output(pointer, keyboard, wheel, shell)
            machine = Machines(GestureMachine(), glides(shell))
        # A crash must never leave a drag or a modifier stuck.
        cleanup.callback(output.release_all)
        print(f"gnome-x11-touchpad-gestures {__version__}: "
              f"{MODE_SAYS[mode]}, "
              f"listening on {device.path} ({device.name})", flush=True)
```

and change the `run(...)` call below it to use `machine`:

```python
        try:
            run(device, make_tracker(device), machine, output,
                times=FrameTimes(stamped=stamp_by_our_clock(device)))
        except KeyboardInterrupt:
            pass
```

- [ ] **Step 5: Run everything**

Run: `python3 -W ignore -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 6: Commit**

Message: `Pick the daemon's mode from the session: only the drag on Wayland`.

---

### Task 4: Which swipes the shell is not to see

**Files:**
- Modify: `EXT/gestures.js`
- Test: `tests/js/test_extension.js`

**Interfaces:**
- Produces: `DRAG_FINGERS = 3` and `class SwipeFilter` with `handle(phase, fingers, daemonPresent) -> boolean`, where `phase` is `'begin'`, `'update'` or `'end'` (an end and a cancel are both `'end'`).

- [ ] **Step 1: Write the failing tests**

In `tests/js/test_extension.js`, change the import to:

```js
import {
    BASE_DISTANCE, DRAG_FINGERS, SwipeFilter, WATCHDOG_MS, WorkspaceSwipe,
    isOverWindow,
} from '../../extension/gnome-x11-touchpad-gestures@asifmohtesham.github.io/gestures.js';
```

and add before the final `print(` line:

```js
// Which swipes the shell is kept from seeing, where the daemon drags.

function whole(filter, fingers, daemonPresent) {
    return ['begin', 'update', 'update', 'end'].map(
        phase => filter.handle(phase, fingers, daemonPresent));
}

test('three fingers are the ones that drag', () => {
    same(DRAG_FINGERS, 3);
});

test('a swipe of three fingers is swallowed whole while the daemon is there', () => {
    same(whole(new SwipeFilter(), 3, true), [true, true, true, true]);
});

test('it is left alone while the daemon is not', () => {
    same(whole(new SwipeFilter(), 3, false), [false, false, false, false]);
});

test('a swipe of four fingers, or two, or five, is never swallowed', () => {
    for (const fingers of [2, 4, 5])
        same(whole(new SwipeFilter(), fingers, true), [false, false, false, false]);
});

test('a daemon that goes in the middle of a swipe does not leave half of one', () => {
    const filter = new SwipeFilter();
    same(filter.handle('begin', 3, true), true);
    same(filter.handle('update', 3, false), true);
    same(filter.handle('end', 3, false), true);
});

test('a daemon that comes in the middle of a swipe does not cut it short', () => {
    const filter = new SwipeFilter();
    same(filter.handle('begin', 3, false), false);
    same(filter.handle('update', 3, true), false);
    same(filter.handle('end', 3, true), false);
});

test('each swipe is decided afresh at its begin', () => {
    const filter = new SwipeFilter();
    whole(filter, 3, true);
    same(whole(filter, 4, true), [false, false, false, false]);
    same(whole(filter, 3, true), [true, true, true, true]);
    same(whole(filter, 3, false), [false, false, false, false]);
});

test('nothing is swallowed once a swipe has ended', () => {
    const filter = new SwipeFilter();
    whole(filter, 3, true);
    same(filter.handle('update', 3, true), false);
    same(filter.handle('end', 3, true), false);
});

test('a swipe whose begin was never seen is not swallowed', () => {
    const filter = new SwipeFilter();
    same(filter.handle('update', 3, true), false);
    same(filter.handle('end', 3, true), false);
});

test('a begin on top of a swipe that never ended starts over', () => {
    const filter = new SwipeFilter();
    filter.handle('begin', 3, true);
    same(filter.handle('begin', 4, true), false);
    same(filter.handle('update', 4, true), false);
});

test('a phase the filter does not know changes nothing and is never a begin', () => {
    const filter = new SwipeFilter();
    same(filter.handle(undefined, 3, true), false);
    same(filter.handle('begin', 3, true), true);
    same(filter.handle(undefined, 3, true), true);
    same(filter.handle('later', 3, true), true);
    same(filter.handle('end', 3, true), true);
});

test('only a plain yes counts as the daemon being there', () => {
    for (const present of [undefined, null, 1, 'yes'])
        same(new SwipeFilter().handle('begin', 3, present), false);
});

test('a count of fingers that is not three in every way is not three', () => {
    for (const fingers of ['3', 3.5, undefined, null, NaN])
        same(new SwipeFilter().handle('begin', fingers, true), false);
});
```

- [ ] **Step 2: Run them and see them fail**

Run: `gjs -m tests/js/test_extension.js 2>&1 | tail -3`
Expected: a `SyntaxError` naming `DRAG_FINGERS` or `SwipeFilter` (the module does not export it), and a non-zero exit.

- [ ] **Step 3: Write the filter**

Append to `EXT/gestures.js`:

```js
// How many fingers drag. A swipe of this many is the daemon's.
export const DRAG_FINGERS = 3;

// Decides which touchpad swipes the shell is not to see.
//
// Where the daemon drags with three fingers, the shell's own swipes, which
// it makes of three fingers or more, would land on top of the drag. They
// are kept from it for as long as the daemon is there.
//
// A swipe is a run of events: a begin, updates, an end. It is swallowed
// whole or not at all, and that is decided at its begin. A daemon that
// came or went in the middle would otherwise leave the shell with half a
// swipe: a begin without an end, and a workspace stuck part way across.
export class SwipeFilter {
    constructor() {
        this._swallowing = false;
    }

    // `phase` is 'begin', 'update' or 'end'. Whether to swallow the event.
    handle(phase, fingers, daemonPresent) {
        if (phase === 'begin')
            this._swallowing = fingers === DRAG_FINGERS && daemonPresent === true;
        const swallow = this._swallowing;
        if (phase === 'end')
            this._swallowing = false;
        return swallow;
    }
}
```

- [ ] **Step 4: Run everything**

Run: `gjs -m tests/js/test_extension.js | tail -1 && python3 -W ignore -m unittest tests.test_extension 2>&1 | tail -1`
Expected: `48 tests, 0 failed` and `OK`.

- [ ] **Step 5: Commit**

Message: `Decide which swipes the shell is not to see, a whole swipe at a time`.

---

### Task 5: The extension frees three fingers on Wayland

**Files:**
- Modify: `EXT/extension.js`, `EXT/metadata.json`
- Create: `tests/js/stand_ins/meta.js`
- Modify: `tests/js/stand_ins/clutter.js`, `tests/js/run_extension.js`, `tests/helpers/drive_extension.py`
- Test: `tests/test_extension_runs.py`, `tests/test_extension.py`

**Interfaces:**
- Consumes: `SwipeFilter` (Task 4); `shell.DAEMON_NAME`, `Shell.announce`, `Shell.three_fingers_free` (Task 1).
- Produces: an extension that, when `Meta.is_wayland_compositor()` is true, watches for `io.github.asifmohtesham.Gestures.Daemon`, swallows swipes as the filter says, and answers `ThreeFingersFree` with whether the daemon is there.

- [ ] **Step 1: Give the stand-ins what the extension will ask for**

Replace `tests/js/stand_ins/clutter.js` with:

```js
// Stands in for gi://Clutter, which only the shell's own process can load.
// The figures are Clutter's own.
export default {
    PickMode: {NONE: 0, REACTIVE: 1, ALL: 2},
    EventType: {TOUCHPAD_SWIPE: 20, TOUCHPAD_PINCH: 21, TOUCHPAD_HOLD: 22},
    TouchpadGesturePhase: {BEGIN: 0, UPDATE: 1, END: 2, CANCEL: 3},
    EVENT_PROPAGATE: false,
    EVENT_STOP: true,
};
```

Create `tests/js/stand_ins/meta.js`:

```js
// Stands in for gi://Meta. The runner says which kind of session it is.
let wayland = false;

export function setWayland(is) {
    wayland = is;
}

export default {is_wayland_compositor: () => wayland};
```

- [ ] **Step 2: Let the runner put swipes through a stage**

In `tests/js/run_extension.js`:

Change the usage comment's first lines to:

```js
// Usage: gjs -m run_extension.js <directory holding a copy of the extension
// whose imports from the shell point at the stand-ins> <x11|wayland>
```

After `const Main = await import(...)` add:

```js
const {default: Clutter} = await import(`file://${directory}/stand_in_clutter.js`);
const Meta = await import(`file://${directory}/stand_in_meta.js`);
Meta.setWayland(ARGV[1] === 'wayland');

// What is connected to the stage, so that events can be put through it.
const handlers = new Map();
let nextHandler = 1;

function through(event) {
    for (const {signal, handler} of handlers.values()) {
        if (signal === 'captured-event::touchpad' && handler(stage, event) === true)
            return true;
    }
    return false;
}

const PHASES = {
    begin: Clutter.TouchpadGesturePhase.BEGIN,
    update: Clutter.TouchpadGesturePhase.UPDATE,
    end: Clutter.TouchpadGesturePhase.END,
    cancel: Clutter.TouchpadGesturePhase.CANCEL,
    unknown: 99,
};
```

(`stage` is declared just below; the functions only use it when called.) Change the `globalThis.global = {` block's `stage:` entry to:

```js
    stage: Object.assign(stage, {
        get_actor_at_pos(mode, x, y) {
            picks.push([mode, x, y]);
            return under[picked];
        },
        connect(signal, handler) {
            handlers.set(nextHandler, {signal, handler});
            return nextHandler++;
        },
        disconnect(id) {
            if (!handlers.delete(id))
                throw new Error(`no handler ${id} to disconnect`);
        },
    }),
```

Add to the `CONTROL` interface, before `<method name="Quit"/>`:

```xml
    <method name="Swipe">
      <arg type="s" direction="in"/><arg type="u" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <method name="Pinch"><arg type="b" direction="out"/></method>
    <method name="Broken"><arg type="b" direction="out"/></method>
    <method name="Handlers"><arg type="u" direction="out"/></method>
```

and to the object given to `wrapJSObject`, before `Quit()`:

```js
    Swipe(phase, fingers) {
        return through({
            type: () => Clutter.EventType.TOUCHPAD_SWIPE,
            get_gesture_phase: () => PHASES[phase],
            get_touchpad_gesture_finger_count: () => fingers,
        });
    },
    Pinch() {
        return through({
            type: () => Clutter.EventType.TOUCHPAD_PINCH,
            get_gesture_phase: () => Clutter.TouchpadGesturePhase.BEGIN,
            get_touchpad_gesture_finger_count: () => 3,
        });
    },
    // An event the shell has changed under the extension's feet.
    Broken() {
        return through({
            type: () => Clutter.EventType.TOUCHPAD_SWIPE,
            get_gesture_phase() {
                throw new Error('no such call any more');
            },
            get_touchpad_gesture_finger_count: () => 3,
        });
    },
    Handlers() {
        return handlers.size;
    },
```

- [ ] **Step 3: Drive the Wayland behaviour**

Replace `tests/helpers/drive_extension.py` with:

```python
"""Drives the real extension, run outside a shell, with the daemon's own client.

Run by tests/test_extension_runs.py inside `dbus-run-session`, so on a bus of
its own. Prints what happened as JSON.
"""
import json
import pathlib
import subprocess
import sys
import time

import dbus

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from gnome_x11_touchpad_gestures.shell import (  # noqa: E402
    DAEMON_NAME, EXTENSION_INTERFACE, EXTENSION_PATH, Shell)


def as_on_x11(bus, control, seen):
    shell = Shell()
    seen["version"] = shell.extension_version()
    seen["over"] = {}
    for what in ("window", "popup", "panel", "stage", "nothing"):
        control("Pick", what, signature="s")
        seen["over"][what] = shell.pointer_over_window()
    control("Pick", "window", signature="s")
    control("Overview", True, signature="b")
    seen["over"]["window, overview open"] = shell.pointer_over_window()
    seen["may glide, overview open"] = shell.may_glide()
    control("Overview", False, signature="b")
    seen["may glide"] = shell.may_glide()

    seen["begin"] = shell.swipe_begin(12.5)
    shell.swipe_update(12.507, 0.25)
    shell.swipe_update(12.514, -0.5)
    shell.swipe_end(12.6)
    seen["begin again"] = shell.swipe_begin(20.0)
    shell.swipe_cancel()

    control("Mode", dbus.UInt32(128), signature="u")
    seen["begin, menu open"] = shell.swipe_begin(30.0)
    control("Mode", dbus.UInt32(1), signature="u")
    control("TrackerEnabled", False, signature="b")
    seen["begin, tracker off"] = shell.swipe_begin(31.0)
    control("TrackerEnabled", True, signature="b")

    # Nothing of the Wayland behaviour is to happen here.
    seen["three fingers free"] = Shell().three_fingers_free()
    seen["handlers"] = int(control("Handlers"))
    seen["three fingers"] = [
        bool(control("Swipe", phase, dbus.UInt32(3), signature="su"))
        for phase in ("begin", "update", "end")]

    # Left open on purpose: switching the extension off must put it back.
    seen["begin, then switched off"] = Shell().swipe_begin(40.0)
    control("Disable")
    seen.update(json.loads(str(control("Seen"))))
    gone = Shell()
    seen["version once off"] = gone.extension_version()
    seen["over window once off"] = gone.pointer_over_window()
    control("Enable")
    seen["version once on again"] = Shell().extension_version()


def as_on_wayland(bus, control, seen):
    def free():
        """Asked without taking the name, which asking as the daemon would."""
        return bool(bus.call_blocking(
            "org.gnome.Shell", EXTENSION_PATH, EXTENSION_INTERFACE,
            "ThreeFingersFree", "", (), timeout=5))

    def learns(wanted):
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if free() is wanted:
                return True
            time.sleep(0.02)
        return False

    def one(phase, fingers=3):
        return bool(control("Swipe", phase, dbus.UInt32(fingers), signature="su"))

    def swipe(fingers=3):
        return [one(phase, fingers) for phase in ("begin", "update", "end")]

    seen["version"] = Shell().extension_version()
    seen["handlers"] = int(control("Handlers"))
    seen["free before the daemon"] = free()
    seen["swipe before the daemon"] = swipe()

    seen["name taken"] = Shell().announce()
    seen["learned of the daemon"] = learns(True)
    seen["free with the daemon"] = Shell().three_fingers_free()
    seen["three fingers"] = swipe(3)
    seen["four fingers"] = swipe(4)
    seen["two fingers"] = swipe(2)
    seen["pinch"] = bool(control("Pinch"))
    seen["cancelled"] = [one("begin"), one("cancel"), one("update")]
    seen["unknown phase"] = [one("begin"), one("unknown"), one("end")]
    seen["broken event"] = bool(control("Broken"))
    seen["swipe after a broken event"] = swipe()

    began = one("begin")
    bus.release_name(DAEMON_NAME)
    seen["learned the daemon went"] = learns(False)
    seen["swipe the daemon left during"] = [began, one("update"), one("end")]
    seen["swipe after it left"] = swipe()

    began = one("begin")
    Shell().announce()
    seen["learned it came back"] = learns(True)
    seen["swipe the daemon came during"] = [began, one("update"), one("end")]
    seen["swipe after it came back"] = swipe()

    # What the X11 behaviour offers must answer, and do nothing.
    asked = Shell()
    seen["over window"] = asked.pointer_over_window()
    seen["may glide"] = asked.may_glide()
    seen["begin"] = asked.swipe_begin(12.5)
    asked.swipe_update(12.507, 0.25)
    asked.swipe_end(12.6)
    asked.swipe_cancel()

    control("Disable")
    seen["handlers once off"] = int(control("Handlers"))
    seen["swipe once off"] = swipe()
    seen.update(json.loads(str(control("Seen"))))
    seen["version once off"] = Shell().extension_version()
    control("Enable")
    seen["version once on again"] = Shell().extension_version()
    seen["handlers once on again"] = int(control("Handlers"))
    seen["free once on again"] = learns(True)
    seen["swipe once on again"] = swipe()


def main(directory, real_bus, mode):
    bus = dbus.SessionBus()
    # The runner takes the shell's name. On the desktop's own bus that name
    # belongs to the real shell, and nothing here may go near it.
    address = bus.get_object("org.freedesktop.DBus", "/").GetId(
        dbus_interface="org.freedesktop.DBus")
    if real_bus and str(address) == real_bus:
        sys.exit("refusing to run on the desktop's own bus")
    if bus.name_has_owner("org.gnome.Shell"):
        sys.exit("refusing to run where a shell is already answering")

    runner = subprocess.Popen(
        ["gjs", "-m", str(REPO / "tests/js/run_extension.js"), directory, mode],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready = runner.stdout.readline().strip()
        if ready != "READY":
            sys.exit(f"runner said {ready!r}: {runner.stderr.read()}")

        def control(method, *args, signature=""):
            return bus.call_blocking(
                "org.gnome.Shell", "/test/Control", "test.Control", method,
                signature, args, timeout=5)

        seen = {}
        (as_on_wayland if mode == "wayland" else as_on_x11)(bus, control, seen)
        control("Quit")
        runner.wait(timeout=10)
        seen["runner exit"] = runner.returncode
        seen["runner complaints"] = runner.stderr.read()
    finally:
        if runner.poll() is None:
            runner.kill()
    print(json.dumps(seen))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
```

- [ ] **Step 4: Write the failing tests**

In `tests/test_extension_runs.py`:

Add `"'gi://Meta'": "'./stand_in_meta.js'",` to `IMPORTS`, add `assert "gi://Meta" not in source, "an import without a stand-in"` beside the like line for Clutter, and change the loop that copies the stand-ins to `for name in ("clutter", "extension", "main", "meta"):`.

Replace `ExtensionRunsTest.setUpClass` and `setUp` with a helper and a base class:

```python
def run_extension(mode):
    """Runs the extension in a session of the kind named. What happened, or why not."""
    with tempfile.TemporaryDirectory(prefix="gnome-x11-touchpad-gestures-test-") as directory:
        copy_with_stand_ins(directory)
        environment = {key: value for key, value in os.environ.items()
                       if key not in ("DBUS_SESSION_BUS_ADDRESS", "DISPLAY")}
        # Keeps gjs from starting the desktop's file services on a bus
        # that is about to go away.
        environment["GIO_USE_VFS"] = "local"
        result = subprocess.run(
            ["dbus-run-session", "--", sys.executable,
             str(REPO / "tests/helpers/drive_extension.py"), directory,
             os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""), mode],
            capture_output=True, text=True, timeout=90, env=environment)
    if result.returncode != 0:
        return {}, f"exit {result.returncode}\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout.splitlines()[-1]), None


class Ran(unittest.TestCase):
    MODE = None

    @classmethod
    def setUpClass(cls):
        cls.seen, cls.failure = run_extension(cls.MODE)

    def setUp(self):
        if self.failure:
            self.fail(self.failure)
```

Make the existing class `@unittest.skipUnless(...) class ExtensionRunsTest(Ran):` with `MODE = "x11"`, keeping all its tests, and add to it:

```python
    def test_on_x11_three_fingers_are_not_freed_and_no_swipe_is_swallowed(self):
        self.assertIs(self.seen["three fingers free"], False)
        self.assertEqual(self.seen["handlers"], 0)
        self.assertEqual(self.seen["three fingers"], [False, False, False])
```

Add a second class after it:

```python
@unittest.skipUnless(all(shutil.which(tool) for tool in NEEDED),
                     "needs gjs and dbus-run-session")
class OnWaylandTest(Ran):
    """Where the daemon only drags, the extension keeps the shell's swipes
    off three fingers for as long as the daemon is there."""

    MODE = "wayland"
    WHOLE = [True, True, True]
    NONE = [False, False, False]

    def test_it_runs_and_stops_without_complaint(self):
        self.assertEqual(self.seen["runner exit"], 0)
        self.assertEqual(self.seen["runner complaints"], "")

    def test_it_tells_its_version(self):
        self.assertEqual(self.seen["version"], gnome_x11_touchpad_gestures.__version__)

    def test_it_listens_on_the_stage_once(self):
        self.assertEqual(self.seen["handlers"], 1)

    def test_nothing_is_freed_or_swallowed_before_the_daemon_is_there(self):
        self.assertIs(self.seen["free before the daemon"], False)
        self.assertEqual(self.seen["swipe before the daemon"], self.NONE)

    def test_it_learns_of_the_daemon_by_its_name_on_the_bus(self):
        self.assertIs(self.seen["name taken"], True)
        self.assertIs(self.seen["learned of the daemon"], True)
        self.assertIs(self.seen["free with the daemon"], True)

    def test_swipe_of_three_fingers_is_swallowed_whole(self):
        self.assertEqual(self.seen["three fingers"], self.WHOLE)

    def test_swipes_of_other_counts_are_the_shell_s(self):
        self.assertEqual(self.seen["four fingers"], self.NONE)
        self.assertEqual(self.seen["two fingers"], self.NONE)

    def test_gesture_that_is_not_a_swipe_is_the_shell_s(self):
        self.assertIs(self.seen["pinch"], False)

    def test_cancelled_swipe_is_over(self):
        self.assertEqual(self.seen["cancelled"], [True, True, False])

    def test_phase_it_does_not_know_follows_the_swipe_it_is_in(self):
        self.assertEqual(self.seen["unknown phase"], self.WHOLE)

    def test_event_it_cannot_read_is_let_through_and_survived(self):
        self.assertIs(self.seen["broken event"], False)
        self.assertEqual(self.seen["swipe after a broken event"], self.WHOLE)

    def test_daemon_leaving_during_a_swipe_does_not_leave_half_of_one(self):
        self.assertIs(self.seen["learned the daemon went"], True)
        self.assertEqual(self.seen["swipe the daemon left during"], self.WHOLE)
        self.assertEqual(self.seen["swipe after it left"], self.NONE)

    def test_daemon_coming_during_a_swipe_does_not_cut_it_short(self):
        self.assertIs(self.seen["learned it came back"], True)
        self.assertEqual(self.seen["swipe the daemon came during"], self.NONE)
        self.assertEqual(self.seen["swipe after it came back"], self.WHOLE)

    def test_what_is_for_x11_answers_no_and_touches_nothing(self):
        self.assertIs(self.seen["over window"], False)
        self.assertIs(self.seen["may glide"], False)
        self.assertIs(self.seen["begin"], False)
        self.assertEqual(self.seen["calls"], [])
        self.assertEqual(self.seen["picks"], [])

    def test_switched_off_it_listens_to_nothing_and_swallows_nothing(self):
        self.assertEqual(self.seen["handlers once off"], 0)
        self.assertEqual(self.seen["swipe once off"], self.NONE)
        self.assertIsNone(self.seen["version once off"])

    def test_switched_on_again_it_finds_the_daemon_still_there(self):
        self.assertEqual(self.seen["version once on again"],
                         gnome_x11_touchpad_gestures.__version__)
        self.assertEqual(self.seen["handlers once on again"], 1)
        self.assertIs(self.seen["free once on again"], True)
        self.assertEqual(self.seen["swipe once on again"], self.WHOLE)
```

Change `StandInsTest`'s expected list to:

```python
        self.assertEqual(files, ["extension.js", "gestures.js", "metadata.json",
                                 "stand_in_clutter.js", "stand_in_extension.js",
                                 "stand_in_main.js", "stand_in_meta.js"])
```

In `tests/test_extension.py`, replace `test_only_the_shell_it_was_written_against_is_named` with:

```python
    def test_only_the_shells_it_was_seen_to_work_in_are_named(self):
        # 46 for X11, where it works on parts of the shell that are the
        # shell's own business. 50 for Wayland. A version is named here
        # once the extension has been seen to work in it, and not before.
        self.assertEqual(self.metadata["shell-version"], ["46", "50"])
```

- [ ] **Step 5: Run them and see them fail**

Run: `python3 -W ignore -m unittest tests.test_extension_runs tests.test_extension 2>&1 | grep -E "^(FAIL|ERROR|FAILED|Ran)" | head -30`
Expected: every test of `OnWaylandTest` fails (the copy has no `gi://Meta` to replace, so the helper's own `assert` stops it), and the shell-versions test fails.

- [ ] **Step 6: Write the extension's Wayland behaviour**

In `EXT/metadata.json` set:

```json
    "name": "Touchpad gestures",
    "description": "Works with the gnome-x11-touchpad-gestures daemon and does nothing without it. On X11 it moves workspaces under your fingers and tells the daemon whether the pointer is over a window. On Wayland it keeps the shell's own swipes off three fingers while the daemon is running, so that three fingers can drag.",
    "shell-version": ["46", "50"]
```

In `EXT/extension.js`:

Change the top comment and imports to:

```js
// Works with the gnome-x11-touchpad-gestures daemon. On X11 it answers the
// daemon over D-Bus; on Wayland it keeps the shell's own swipes off three
// fingers while the daemon is there. Everything that can be worked out
// without the shell is in gestures.js; this file only hands it the pieces
// of the shell.
//
// One rule holds here whatever else changes: nothing in this file presses
// a button or moves the pointer. That is the daemon's, whose pointer is an
// input device like any other and does not depend on how the shell hands
// events round.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {SwipeFilter, WorkspaceSwipe, isOverWindow} from './gestures.js';

const OBJECT_PATH = '/io/github/asifmohtesham/Gestures';
// The name the daemon takes on the session bus where it only drags.
const DAEMON_NAME = 'io.github.asifmohtesham.Gestures.Daemon';
```

Replace the class body's `enable()` and `disable()` with:

```js
    enable() {
        this._wayland = Meta.is_wayland_compositor();
        this._daemonPresent = false;
        if (this._wayland)
            this._freeThreeFingers();
        else
            this._followSwipes();
        this._exported = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this);
        this._exported.export(Gio.DBus.session, OBJECT_PATH);
    }

    disable() {
        this._exported?.unexport();
        this._exported = null;
        this._swipe?.destroy();
        this._swipe = null;
        if (this._watch)
            Gio.bus_unwatch_name(this._watch);
        this._watch = null;
        if (this._captured)
            global.stage.disconnect(this._captured);
        this._captured = null;
        this._filter = null;
        this._daemonPresent = false;
    }

    // On X11: the shell's own workspace animation, fed by the daemon.
    _followSwipes() {
        this._swipe = new WorkspaceSwipe({
            tracker: () => Main.wm._workspaceAnimation._swipeTracker,
            mode: () => Main.actionMode,
            pointer: () => global.get_pointer(),
            schedule: (ms, fire) => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
                fire();
                return GLib.SOURCE_REMOVE;
            }),
            cancel: id => GLib.source_remove(id),
        });
    }

    // On Wayland: the shell makes a swipe of three fingers or more, which
    // would land on top of the daemon's drag. Its swipes of exactly three
    // are kept from it while the daemon is there, and no longer.
    _freeThreeFingers() {
        this._filter = new SwipeFilter();
        this._watch = Gio.bus_watch_name_on_connection(
            Gio.DBus.session, DAEMON_NAME, Gio.BusNameWatcherFlags.NONE,
            () => {
                this._daemonPresent = true;
            },
            () => {
                this._daemonPresent = false;
            });
        // Captured events reach this before the shell's own swipe handling.
        this._captured = global.stage.connect(
            'captured-event::touchpad', (_actor, event) => this._onTouchpad(event));
    }

    _onTouchpad(event) {
        try {
            if (event.type() !== Clutter.EventType.TOUCHPAD_SWIPE)
                return Clutter.EVENT_PROPAGATE;
            const swallow = this._filter.handle(
                PHASES.get(event.get_gesture_phase()),
                event.get_touchpad_gesture_finger_count(),
                this._daemonPresent);
            return swallow ? Clutter.EVENT_STOP : Clutter.EVENT_PROPAGATE;
        } catch (_error) {
            // A shell that has changed keeps its swipes.
            return Clutter.EVENT_PROPAGATE;
        }
    }
```

Add above the class, after `INTERFACE`:

```js
// The filter's names for the phases of a swipe.
const PHASES = new Map([
    [Clutter.TouchpadGesturePhase.BEGIN, 'begin'],
    [Clutter.TouchpadGesturePhase.UPDATE, 'update'],
    [Clutter.TouchpadGesturePhase.END, 'end'],
    [Clutter.TouchpadGesturePhase.CANCEL, 'end'],
]);
```

Replace the methods from `PointerOverWindow()` to the end of the class with:

```js
    PointerOverWindow() {
        if (this._wayland)
            return false;
        const [x, y] = global.get_pointer();
        const actor = global.stage.get_actor_at_pos(
            Clutter.PickMode.REACTIVE, x, y);
        return isOverWindow(
            actor, [global.window_group, global.top_window_group],
            Main.overview.visible, global.stage);
    }

    SwipeBegin(time) {
        return this._swipe ? this._swipe.begin(time) : false;
    }

    SwipeUpdate(time, fraction) {
        this._swipe?.update(time, fraction);
    }

    SwipeEnd(time) {
        this._swipe?.end(time);
    }

    SwipeCancel() {
        this._swipe?.cancel();
    }

    ThreeFingersFree() {
        return this._wayland === true && this._daemonPresent === true;
    }
}
```

- [ ] **Step 7: Run everything**

Run: `python3 -W ignore -m unittest discover -s tests 2>&1 | tail -3 && gjs -m tests/js/test_extension.js | tail -1`
Expected: `OK` and `48 tests, 0 failed`.

- [ ] **Step 8: Commit**

Message: `Keep the shell's swipes off three fingers while the daemon is there`.

---

### Task 6: A service for both sessions, and advice that fits each

**Files:**
- Modify: `install/gnome-x11-touchpad-gestures.service`, `install/install.sh`
- Test: `tests/test_install.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `explain_extension loaded version changed [session]` in `install.sh`, where `session` is the value of `XDG_SESSION_TYPE`; `session_is_supported` in `install.sh`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_install.py`, replace `test_service_runs_in_an_x11_session_only` with:

```python
    def test_service_starts_in_an_x11_or_a_wayland_session_and_no_other(self):
        unit = (INSTALL / "gnome-x11-touchpad-gestures.service").read_text()
        conditions = [line for line in unit.splitlines()
                      if line.startswith("ConditionEnvironment=")]
        # The bar makes each a condition of which one is enough.
        self.assertEqual(conditions, [
            "ConditionEnvironment=|XDG_SESSION_TYPE=x11",
            "ConditionEnvironment=|XDG_SESSION_TYPE=wayland"])

    def test_service_does_not_promise_what_one_mode_lacks(self):
        unit = (INSTALL / "gnome-x11-touchpad-gestures.service").read_text()
        (description,) = [line for line in unit.splitlines()
                          if line.startswith("Description=")]
        self.assertIn("three-finger drag", description)
        self.assertNotIn("momentum", description)
        self.assertNotIn("four-finger", description)
```

Add to `RestartAdviceTest`:

```python
    LOG_OUT = "Log out and log back in"

    def advice_on_wayland(self, loaded, version, changed):
        result = call("install.sh", "explain_extension", loaded, version,
                      changed, "wayland")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_on_wayland_the_shell_cannot_be_restarted_so_logging_out_is_advised(self):
        for loaded, changed in (("", "yes"), ("0.2.0", "no"), ("0.2.1", "yes")):
            with self.subTest(loaded=loaded, changed=changed):
                said = self.advice_on_wayland(loaded, "0.2.1", changed)
                self.assertIn(self.LOG_OUT, said)
                self.assertNotIn(self.RESTART, said)
                self.assertNotIn("windows stay open", said)

    def test_on_wayland_an_extension_not_loaded_costs_the_drag(self):
        said = self.advice_on_wayland("", "0.2.1", "yes")
        self.assertIn("has not loaded", said)
        self.assertIn("three-finger drag", said)
        self.assertNotIn("snap", said)
        self.assertNotIn("glides", said)

    def test_on_wayland_too_an_extension_loaded_as_installed_needs_nothing(self):
        said = self.advice_on_wayland("0.2.1", "0.2.1", "no")
        self.assertIn("No restart is needed", said)
        self.assertNotIn(self.LOG_OUT, said)

    def test_every_other_session_is_advised_as_x11_was(self):
        for session in ("x11", "", "tty"):
            with self.subTest(session=session):
                result = call("install.sh", "explain_extension", "", "0.2.1",
                              "yes", session)
                self.assertIn(self.RESTART, result.stdout)
                self.assertIn("snap", result.stdout)

    def installed_in(self, session):
        said = 'export XDG_SESSION_TYPE="$1"; ' if session is not None else ""
        shell = 'loaded_extension_version() { echo ""; }; '
        with recording_sandbox() as (_, run, _commands):
            result = run("install.sh", said + self.ACCESS + shell + "main_as 1000",
                         *([session] if session is not None else []))
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout, result.stderr

    def test_install_on_wayland_advises_logging_out_and_warns_of_nothing(self):
        said, warned = self.installed_in("wayland")
        self.assertIn(self.LOG_OUT, said)
        self.assertNotIn(self.RESTART, said)
        self.assertNotIn("not x11", warned)
        self.assertEqual(warned, "")
        self.assertNotIn("has not started", said)

    def test_install_on_x11_warns_of_nothing_either(self):
        said, warned = self.installed_in("x11")
        self.assertIn(self.RESTART, said)
        self.assertEqual(warned, "")
        self.assertNotIn("has not started", said)

    def test_install_in_any_other_session_says_where_the_service_starts(self):
        for session in ("tty", None):
            with self.subTest(session=session):
                said, warned = self.installed_in(session)
                self.assertIn("X11", warned)
                self.assertIn("Wayland", warned)
                self.assertIn("has not started", said)
                self.assertIn("X11 or Wayland", said)
```

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -W ignore -m unittest tests.test_install 2>&1 | grep -E "^(FAIL|ERROR|FAILED|Ran)"`
Expected: the new tests fail; no older test fails.

- [ ] **Step 3: Change the unit**

In `install/gnome-x11-touchpad-gestures.service`, replace the `Description=` line and the comment and condition under `PartOf=` with:

```ini
Description=Touchpad gestures: three-finger drag, and on X11 the gestures GNOME lacks there
PartOf=graphical-session.target
# On X11 the daemon does every gesture. On Wayland GNOME has gestures of its
# own, and the daemon does the one it lacks: three-finger drag. The bar
# makes each a condition of which one is enough.
ConditionEnvironment=|XDG_SESSION_TYPE=x11
ConditionEnvironment=|XDG_SESSION_TYPE=wayland
```

Check that systemd reads it as meant:

Run: `systemd-analyze --user verify install/gnome-x11-touchpad-gestures.service; systemd-analyze --user condition 'ConditionEnvironment=|XDG_SESSION_TYPE=x11' 'ConditionEnvironment=|XDG_SESSION_TYPE=wayland'; echo "exit=$?"`
Expected: no complaint about the conditions, and on this Wayland session `exit=0` with "Conditions succeeded". (`verify` may complain that the program directory is missing in a clean checkout; that is not about the conditions.)

- [ ] **Step 4: Change the installer**

With the Edit tool, in `install/install.sh`:

Replace the whole of `explain_extension` with:

```bash
# Says whether the shell has to be made to load the extension. It loads it
# when it starts, and goes on running what it loaded whatever is installed
# after. On X11 the shell can be restarted where it stands. On Wayland it
# cannot, and what is needed is to log out.
explain_extension() {
    local loaded="$1" version="$2" changed="$3" session="${4:-}"
    local how="Restart the shell to load it:
press Alt+F2, type r, press Enter. Your windows stay open."
    local until="Until then workspaces snap across, and glides are held back only in
the overview."
    if [ "$session" = "wayland" ]; then
        how="Log out and log back in to load it."
        until="Until then there is no three-finger drag."
    fi
    if [ -z "$loaded" ]; then
        echo "The shell has not loaded the extension. $how"
        echo "$until"
    elif [ "$loaded" != "$version" ]; then
        echo "The shell still runs version $loaded of the extension, and this is"
        echo "$version. $how"
    elif [ "$changed" = "yes" ]; then
        echo "The extension has changed, and the shell still runs it as it was."
        echo "$how"
    else
        echo "The shell has the extension loaded, version $loaded. No restart is needed."
    fi
}

# Whether the service starts in this kind of session.
session_is_supported() {
    case "${XDG_SESSION_TYPE:-}" in
        x11|wayland) return 0 ;;
        *) return 1 ;;
    esac
}
```

In `main_as`, replace

```bash
    if [ "${XDG_SESSION_TYPE:-}" != "x11" ]; then
        echo "Warning: this session is '${XDG_SESSION_TYPE:-unknown}', not x11." >&2
        echo "The service only starts in an X11 session." >&2
    fi
```

with

```bash
    if ! session_is_supported; then
        echo "Warning: this session is '${XDG_SESSION_TYPE:-unknown}'." >&2
        echo "The service only starts in an X11 or a Wayland session." >&2
    fi
```

and replace

```bash
    if [ "${XDG_SESSION_TYPE:-}" != "x11" ]; then
        echo "This is not an X11 session, so the service has not started."
        echo "It starts by itself at your next X11 login."
    fi
```

with

```bash
    if ! session_is_supported; then
        echo "This is not an X11 or Wayland session, so the service has not started."
        echo "It starts by itself at your next login to one."
    fi
```

and change the last call in `main_as` to pass the session:

```bash
    explain_extension "$(loaded_extension_version)" "$(this_version)" \
        "$extension_changed" "${XDG_SESSION_TYPE:-}"
```

- [ ] **Step 5: Mend the one older test this changes**

`test_install_finishes_when_the_service_is_not_started_yet` asserts `"X11"` in the output, which still holds. Two older tests assert the X11 advice's exact shape, where the first line used to end at "to load it:" with the keys on the next line; the new text keeps both on consecutive lines, so `self.RESTART` ("Alt+F2") and "windows stay open" still appear. Run the file and read any failure before changing a test: only a test whose assertion was about line breaks may be changed, and none is expected.

Run: `bash -n install/install.sh && python3 -W ignore -m unittest tests.test_install 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 6: Run everything and commit**

Run: `python3 -W ignore -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK`.

Message: `Start the service on Wayland too, and advise logging out there`.

---

### Task 7: Version 0.3.0 and the documents

**Files:**
- Modify: `gnome_x11_touchpad_gestures/__init__.py`, `EXT/metadata.json`, `CHANGELOG.md`, `README.md`, `docs/superpowers/specs/2026-09-27-gnome-x11-touchpad-gestures-design.md`

**Interfaces:** none.

- [ ] **Step 1: The version**

Set `__version__ = "0.3.0"` in `gnome_x11_touchpad_gestures/__init__.py` and `"version-name": "0.3.0"` in `EXT/metadata.json`.

In `CHANGELOG.md`, add above `## 0.2.1`:

```markdown
## 0.3.0 (unreleased)

Added
- Three-finger drag on GNOME on Wayland. GNOME has its own workspace
  swipes and overview there, and its toolkits their own momentum; the one
  thing it lacks is the drag. On Wayland the daemon does that and nothing
  else.
- The extension keeps GNOME's own swipes off three fingers while the daemon
  is running, so that the two do not land on top of each other. With the
  daemon stopped they are GNOME's again at once.
- `--check` names the mode it would run in.

Changed
- The service starts in a Wayland session as well as an X11 one.
- On Wayland, while the daemon runs, GNOME's workspace and overview swipes
  need four fingers. Three no longer do them.
- The extension declares GNOME Shell 46 and 50. On Wayland the shell cannot
  be restarted in place, so the installer and `--check` say to log out and
  back in where they used to say to restart the shell.

On X11 nothing has changed.
```

- [ ] **Step 2: The README**

Replace the opening, from the line under the title down to and including the line `Design: ...`, with:

```markdown
macOS-style touchpad gestures for GNOME: three-finger drag everywhere, and
on X11 the four-finger swipes and momentum scrolling that GNOME lacks there.

One small unprivileged Python daemon reads the touchpad and writes to
virtual input devices. It never grabs the touchpad, so normal pointing,
scrolling and tapping are untouched. A small GNOME Shell extension helps it.

What it does depends on the session, because GNOME does more for itself on
Wayland:

**On Wayland** (GNOME Shell 50)

| Gesture | Result |
|---|---|
| Three fingers, moving | Drags with the left button held |
| Four fingers, swipe sideways or up and down | GNOME's own workspace switch and overview. They need four fingers while this runs, where GNOME alone takes three |

**On X11** (GNOME Shell 46)

| Gesture | Result |
|---|---|
| Three fingers, moving | Drags with the left button held |
| Four fingers, swipe left | The next workspace slides in under your fingers |
| Four fingers, swipe right | The previous workspace, likewise |
| Four fingers, swipe up | Open the Activities overview |
| Four fingers, swipe down | Close the Activities overview |
| Two fingers, flick and lift | The page keeps gliding and slows to a stop |
| Flick again while it glides | The page glides faster, a little more each time |
| Any touch during a glide | The glide stops at once |
| Flick over the dock, the top bar, a menu or the overview | No glide. A wheel steps through things there |

Design: `docs/superpowers/specs/2026-09-27-gnome-x11-touchpad-gestures-design.md`
for the X11 mode, `docs/superpowers/specs/2026-10-02-wayland-drag-mode-design.md`
for the Wayland one.
```

In `## Status`, replace the table with:

```markdown
| | Tested on |
|---|---|
| Wayland mode | Ubuntu 26.04, GNOME Shell 50, libinput 1.31 |
| X11 mode | Ubuntu 24.04, GNOME Shell 46 on X11, libinput 1.25 |
| Touchpad | Synaptics `SYNA8017:00 06CB:CEB2`, a clickpad reporting up to five fingers |

The machine it is written on has moved to Wayland. The X11 mode is no
longer tried by hand there; it rests on the test suite.
```

In `## Requirements`, replace the first bullet with:

```markdown
- GNOME Shell 46 on **X11**, or GNOME Shell 50 on **Wayland**. The
  extension declares those two versions and the shell will not load it on
  another; without it there is no drag on Wayland.
```

and the last bullet with:

```markdown
- On X11: `Ctrl+Alt+Left` and `Ctrl+Alt+Right` bound to switching
  workspace, which is GNOME's default.
```

Replace the paragraph that starts `**Then restart GNOME Shell**` with:

```markdown
**Then make the shell load the extension.** It loads an extension when it
starts and goes on running what it loaded, so the same is needed after an
update that changes the extension. The installer says at the end whether it
is: it asks the shell which version it has loaded. `--check`, under
Troubleshooting, says the same at any time.

- On X11, restart the shell: press Alt+F2, type `r`, press Enter. Your
  windows stay open.
- On Wayland the shell cannot be restarted in place: log out and log back
  in.
```

Add a section after `### With and without the extension` and its text, before `### What the installer changes`:

```markdown
### On Wayland

GNOME makes a swipe of any three fingers or more. Left alone, a
three-finger drag would work for a second and then the workspace would
start to move under it. So the extension keeps swipes of exactly three
fingers from the shell, for as long as the daemon is running, and the
daemon asks it before every drag whether it is doing so. Without the
extension there is no drag at all, which is better than a drag with a
workspace switch on top.

What this costs: GNOME's own workspace and overview swipes need four
fingers while the daemon runs. Stop the daemon and three do them again, at
once and without logging out:

```bash
systemctl --user stop gnome-x11-touchpad-gestures
```

The extension never presses a button or moves the pointer. If it should
misbehave, the worst it can do is swallow three-finger swipes, and either
the command above or
`gnome-extensions disable gnome-x11-touchpad-gestures@asifmohtesham.github.io`
puts them back.
```

In `## Tuning`, add under the first table's introduction line a sentence: `On Wayland only the three `DRAG_` constants and `POINTER_COUNTS_PER_MM` have any effect.`

In `## Manual checklist`, add at the top of the list:

```markdown
On Wayland, after logging out and in:

- [ ] Drag a window by its title bar with three fingers. The workspace
      does not move and the overview does not open.
- [ ] Select text with three fingers.
- [ ] Lift and re-place fingers mid-drag; the drag continues.
- [ ] Three-finger tap still pastes (middle click).
- [ ] A four-finger swipe switches workspace and opens the overview.
- [ ] `systemctl --user stop gnome-x11-touchpad-gestures`: a three-finger
      swipe is GNOME's again. Start it: the drag is back.
- [ ] `gnome-extensions disable` with the service running: no drag, and a
      three-finger swipe is GNOME's.

On X11:
```

In `## Known behaviour`, add at the top:

```markdown
- On Wayland, while the daemon runs, GNOME's workspace and overview swipes
  need four fingers.
- On Wayland the very first drag after the daemon starts may do nothing:
  the extension learns that the daemon is there a moment after it starts.
- On Wayland an application that listens for three-finger swipes itself may
  no longer receive them.
- Everything below is about the X11 mode, except the lines on dragging.
```

- [ ] **Step 3: Point the first spec at the second**

In `docs/superpowers/specs/2026-09-27-gnome-x11-touchpad-gestures-design.md`, add before `## Out of scope`:

```markdown
## A second mode, for Wayland (added 2026-10-02, version 0.3.0)

Everything above is the X11 mode. On GNOME on Wayland the daemon does
three-finger drag and nothing else, and the extension keeps the shell's
swipes off three fingers for it. That mode has a design of its own:
`2026-10-02-wayland-drag-mode-design.md`. "Wayland" under Out of scope
below means the gestures of this document, which GNOME has itself there.
```

- [ ] **Step 4: Run everything**

Run: `python3 -W ignore -m unittest discover -s tests 2>&1 | tail -3 && gjs -m tests/js/test_extension.js | tail -1 && python3 -m gnome_x11_touchpad_gestures.daemon --version`
Expected: `OK`, `48 tests, 0 failed`, `gnome-x11-touchpad-gestures 0.3.0`.

- [ ] **Step 5: Commit**

Message: `Version 0.3.0: document both modes`.

---

### Task 8: Undo each new line and see a test notice; then try it for real

**Files:**
- Create (in the session's scratchpad, not the repository): a mutation script.
- Modify: tests, wherever a mutant survives.

**Interfaces:** none.

- [ ] **Step 1: Mutation checks**

Write a script, with the Write tool, that for each row below replaces the text once in the file, runs the tests named, restores the file in a `finally`, and reports "caught" or "SURVIVED". The suite to run for every row: `python3 -W ignore -m unittest tests.test_shell tests.test_output tests.test_daemon tests.test_daemon_main tests.test_extension tests.test_extension_runs tests.test_install` and `gjs -m tests/js/test_extension.js`.

| File | Undo |
|---|---|
| `shell.py` | `announce` ignores `self._named` and asks every time |
| `shell.py` | `announce` ignores `self._name.resting()` |
| `shell.py` | a reply not in `NAME_IS_OURS` counts as ours |
| `shell.py` | `three_fingers_free` does not call `announce` |
| `shell.py` | `three_fingers_free` asks `PointerOverWindow` |
| `shell.py` | `Shell` ignores `complaint` and uses `FULL_COMPLAINT` |
| `shell.py` | `DAEMON_NAME` spelt differently |
| `output.py` | `DragOnly` never asks (`self._dragging = True`) |
| `output.py` | `DragOnly` keeps every action |
| `output.py` | `DragOnly` does not clear `_dragging` on `ButtonUp` |
| `output.py` | `DragOnly.release_all` does not clear `_dragging` |
| `output.py` | `DragOnly.release_all` does not reach the output |
| `daemon.py` | `session_mode` always `"full"`; always `"drag"`; compares with `"x11"` |
| `daemon.py` | drag mode makes the keyboard and wheel too |
| `daemon.py` | drag mode uses `Output` unwrapped |
| `daemon.py` | drag mode runs the momentum machine |
| `daemon.py` | drag mode does not call `announce` |
| `daemon.py` | full mode calls `announce` |
| `daemon.py` | drag mode's `Shell` gets no complaint |
| `daemon.py` | `check` passes no mode to `extension_state` |
| `daemon.py` | `extension_state` in drag mode says `RESTART_THE_SHELL` |
| `gestures.js` | `DRAG_FINGERS = 4` |
| `gestures.js` | the decision is made at every event, not only the begin |
| `gestures.js` | `daemonPresent === true` becomes `daemonPresent` |
| `gestures.js` | `_swallowing` is not cleared at the end |
| `extension.js` | `Meta.is_wayland_compositor()` replaced by `false`; by `true` |
| `extension.js` | the watch is not taken down in `disable` |
| `extension.js` | the stage handler is not disconnected in `disable` |
| `extension.js` | `_daemonPresent` is not set by the watch |
| `extension.js` | `_onTouchpad` without its `try` |
| `extension.js` | a cancel is not an end (`CANCEL` mapped to `'update'`) |
| `extension.js` | the type check for `TOUCHPAD_SWIPE` removed |
| `extension.js` | `ThreeFingersFree` returns `this._wayland` alone; `this._daemonPresent` alone |
| `extension.js` | `PointerOverWindow` on Wayland not short-circuited |
| `extension.js` | `SwipeBegin` without its guard for a missing `_swipe` |
| `extension.js` | watches another name |
| `metadata.json` | `shell-version` `["46"]`; `["46", "47", "50"]` |
| `install.sh` | `explain_extension` ignores `session` |
| `install.sh` | `session_is_supported` accepts only `x11`; accepts anything |
| `install.sh` | `main_as` does not pass the session to `explain_extension` |
| `.service` | either condition without its bar; either condition removed |

For every survivor, add the test that catches it, see the mutant caught, and commit with the message `Test what undoing a line showed was not tested`.

- [ ] **Step 2: Install on this machine**

This machine's udev rule is already in place, so no password is asked. Confirm that first, then install, as separate commands:

Run: `git branch --show-current; git status --short`
Expected: `wayland-drag` and nothing uncommitted.

Run: `bash -c 'source "$1"; if needs_sudo; then echo yes; else echo no; fi' bash install/install.sh`
Expected: `no`. If it says `yes`, stop and hand the install to the user.

Run: `./install/install.sh`
Expected at the end: `The shell still runs version 0.2.1 of the extension, and this is 0.3.0. Log out and log back in to load it.` or, if the shell has no extension loaded, `The shell has not loaded the extension. Log out and log back in to load it.` The service is `active`, and its log line reads `gnome-x11-touchpad-gestures 0.3.0: three-finger drag only, listening on ...`.

Run: `busctl --user list | grep io.github.asifmohtesham.Gestures.Daemon`
Expected: one line; the daemon holds its name.

Until the user logs out and in, the extension is not loaded, the daemon is refused every drag, and three-finger swipes are GNOME's. That is the designed behaviour and is worth seeing: `journalctl --user -u gnome-x11-touchpad-gestures -n 5` shows the complaint `the shell extension is not answering, so three-finger drag is off` once, after the first three-finger movement.

- [ ] **Step 3: Hand over**

Ask the user to log out and back in, then to go through the Wayland half of the README's manual checklist and to run `python3 -m gnome_x11_touchpad_gestures.daemon --check` from the repository. Report what `--check` should say: `session: wayland, so three-finger drag only` and `extension: answering, version 0.3.0`.

Do not merge, push, tag or release. Those wait for the user's word after the checklist.
