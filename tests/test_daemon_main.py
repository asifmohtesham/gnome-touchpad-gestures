"""Drives daemon.main() end to end. A pipe stands in for the touchpad."""
import contextlib
import io
import os
import signal
import threading
import time
import unittest
from collections import namedtuple
from unittest import mock

from evdev import ecodes as e

from gnome_touchpad_gestures import daemon, shell
from gnome_touchpad_gestures.gestures import DRAG_RELEASE_S, DRAG_SETTLE_S
from gnome_touchpad_gestures.output import WORKSPACE_KEYS

POINTER = daemon.POINTER_NAME
KEYBOARD = daemon.KEYBOARD_NAME
WHEEL = daemon.WHEEL_NAME

Event = namedtuple("Event", "type code value")
AbsInfo = namedtuple("AbsInfo", "value min max fuzz flat resolution")


class FakeTouchpad:
    """Each byte in the pipe announces one scripted step for read() to run."""

    path = "/dev/input/fake"
    name = "fake touchpad"

    def __init__(self, steps, touching=False):
        self.fd, self._write_fd = os.pipe()
        self._touching = touching
        self._steps = []
        self.closed = False
        for step in steps:
            self.push(step)

    def push(self, step):
        self._steps.append(step)
        os.write(self._write_fd, b"x")

    def absinfo(self, code):
        if code == e.ABS_MT_SLOT:
            return AbsInfo(0, 0, 4, 0, 0, 0)
        return AbsInfo(0, 0, 1300, 0, 0, 10)

    def active_keys(self):
        return [e.BTN_TOUCH] if self._touching else []

    def read(self):
        os.read(self.fd, 1)
        return self._steps.pop(0)()

    def close(self):
        self.closed = True
        os.close(self.fd)
        os.close(self._write_fd)


class FakeUInput:
    # Names of devices that fail, for the tests that need one to.
    cannot_be_created = set()
    cannot_be_closed = set()
    cannot_be_written = set()

    def __init__(self, events, name):
        if name in FakeUInput.cannot_be_created:
            raise OSError(13, f"cannot create {name}")
        self.name = name
        self.written = []
        self.closed = False

    def write(self, etype, code, value):
        if self.name in FakeUInput.cannot_be_written:
            raise OSError(19, f"cannot write to {self.name}")
        self.written.append((etype, code, value))

    def syn(self):
        pass

    def close(self):
        self.closed = True
        if self.name in FakeUInput.cannot_be_closed:
            raise OSError(19, f"cannot close {self.name}")


def three_fingers_at(x):
    events = []
    for slot in range(3):
        events += [
            Event(e.EV_ABS, e.ABS_MT_SLOT, slot),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, 100 + slot),
            Event(e.EV_ABS, e.ABS_MT_POSITION_X, x + slot),
            Event(e.EV_ABS, e.ABS_MT_POSITION_Y, 200),
        ]
    return events + [Event(e.EV_SYN, e.SYN_REPORT, 0)]


def all_fingers_lift():
    events = []
    for slot in range(3):
        events += [
            Event(e.EV_ABS, e.ABS_MT_SLOT, slot),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1),
        ]
    return events + [Event(e.EV_SYN, e.SYN_REPORT, 0)]


def four_fingers_at(y):
    events = []
    for slot in range(4):
        events += [
            Event(e.EV_ABS, e.ABS_MT_SLOT, slot),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, 500 + slot),
            Event(e.EV_ABS, e.ABS_MT_POSITION_X, 300 + 100 * slot),
            Event(e.EV_ABS, e.ABS_MT_POSITION_Y, y),
        ]
    return events + [Event(e.EV_SYN, e.SYN_REPORT, 0)]


class FakeShell:
    requests = []
    overview_open = False
    follows_fingers = True
    swipes = []
    made_with = None
    three_free = True
    asked_free = 0
    asked_glide = 0
    announced = 0

    def __init__(self, **kwargs):
        FakeShell.made_with = kwargs

    def announce(self):
        FakeShell.announced += 1
        return True

    def three_fingers_free(self):
        FakeShell.asked_free += 1
        return FakeShell.three_free

    def show_overview(self, show):
        FakeShell.requests.append(show)

    def may_glide(self):
        FakeShell.asked_glide += 1
        return not FakeShell.overview_open

    def swipe_begin(self, t):
        FakeShell.swipes.append("begin")
        return FakeShell.follows_fingers

    def swipe_update(self, t, fraction):
        FakeShell.swipes.append(round(fraction, 3))

    def swipe_end(self, t):
        FakeShell.swipes.append("end")

    def swipe_cancel(self):
        FakeShell.swipes.append("cancel")


def four_fingers_across(x):
    """Four fingers in a row, the leftmost at x, 10 units to the millimetre."""
    events = []
    for slot in range(4):
        events += [
            Event(e.EV_ABS, e.ABS_MT_SLOT, slot),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, 700 + slot),
            Event(e.EV_ABS, e.ABS_MT_POSITION_X, x + 150 * slot),
            Event(e.EV_ABS, e.ABS_MT_POSITION_Y, 300),
        ]
    return events + [Event(e.EV_SYN, e.SYN_REPORT, 0)]


def four_fingers_lift():
    events = []
    for slot in range(4):
        events += [
            Event(e.EV_ABS, e.ABS_MT_SLOT, slot),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1),
        ]
    return events + [Event(e.EV_KEY, e.BTN_TOUCH, 0),
                     Event(e.EV_SYN, e.SYN_REPORT, 0)]


def swipe_left():
    """60 mm to the left in steps of 10, then every finger up."""
    return [(lambda x=x: four_fingers_across(x))
            for x in range(800, 199, -100)] + [four_fingers_lift]


def two_fingers_at(y):
    events = []
    for slot in range(2):
        events += [
            Event(e.EV_ABS, e.ABS_MT_SLOT, slot),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, 300 + slot),
            Event(e.EV_ABS, e.ABS_MT_POSITION_X, 400 + 100 * slot),
            Event(e.EV_ABS, e.ABS_MT_POSITION_Y, y),
        ]
    return events + [Event(e.EV_SYN, e.SYN_REPORT, 0)]


def two_finger_flick():
    """Five frames, 10 ms apart, moving down at 200 mm/s (10 units per mm)."""
    def frame(index):
        def step():
            time.sleep(0.01)
            return two_fingers_at(100 + 20 * index)
        return step
    return [frame(index) for index in range(5)]


def both_fingers_lift():
    time.sleep(0.01)
    return all_fingers_lift()


def land():
    return three_fingers_at(500)


def move_after_settling():
    time.sleep(DRAG_SETTLE_S + 0.01)
    return three_fingers_at(530)


def interrupt():
    raise KeyboardInterrupt


def terminate():
    os.kill(os.getpid(), signal.SIGTERM)
    return []


def vanish():
    raise OSError(19, "No such device")


class MainTest(unittest.TestCase):
    def setUp(self):
        self.devices = {}
        FakeShell.requests = []
        FakeShell.overview_open = False
        FakeShell.follows_fingers = True
        FakeShell.swipes = []
        FakeShell.made_with = None
        FakeShell.three_free = True
        FakeShell.asked_free = 0
        FakeShell.asked_glide = 0
        FakeShell.announced = 0
        FakeUInput.cannot_be_created = set()
        FakeUInput.cannot_be_closed = set()
        FakeUInput.cannot_be_written = set()
        self.addCleanup(
            signal.signal, signal.SIGTERM, signal.getsignal(signal.SIGTERM))

    def make_uinput(self, events, name):
        device = FakeUInput(events, name)
        self.devices[name] = device
        return device

    def closed(self):
        return {name for name, device in self.devices.items() if device.closed}

    def test_startup_line_names_the_version(self):
        import gnome_touchpad_gestures
        self.run_main(FakeTouchpad([interrupt]))
        self.assertIn(gnome_touchpad_gestures.__version__, self.said)

    def times_given_to_the_loop(self, kernel_agrees):
        touchpad = FakeTouchpad([interrupt])
        with mock.patch.object(daemon, "stamp_by_our_clock",
                               return_value=kernel_agrees) as asked, \
                mock.patch.object(daemon, "run",
                                  side_effect=KeyboardInterrupt) as loop:
            self.assertEqual(self.run_main(touchpad), 0)
        asked.assert_called_once_with(touchpad)
        return loop.call_args.kwargs["times"]

    def test_frames_bear_the_kernel_s_times_where_it_gives_them(self):
        class Report:
            type, code, value = e.EV_SYN, e.SYN_REPORT, 0

            def timestamp(self):
                return 9.99

        self.assertEqual(self.times_given_to_the_loop(True).of(Report(), 10.0), 9.99)
        self.assertEqual(self.times_given_to_the_loop(False).of(Report(), 10.0), 10.0)

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

    def test_drag_mode_has_no_momentum_and_asks_the_shell_nothing_about_it(self):
        touchpad = FakeTouchpad(two_finger_flick() + [both_fingers_lift, interrupt])
        self.assertEqual(self.run_main(touchpad, "wayland"), 0)
        self.assertEqual(FakeShell.asked_glide, 0)
        self.assertNotIn(WHEEL, self.devices)

    def test_a_flick_does_ask_about_momentum_where_every_gesture_is_done(self):
        touchpad = FakeTouchpad(two_finger_flick() + [both_fingers_lift, interrupt])
        self.assertEqual(self.run_main(touchpad, "x11"), 0)
        self.assertGreater(FakeShell.asked_glide, 0)

    def test_help_does_not_promise_every_gesture_in_every_session(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout, \
                self.assertRaises(SystemExit):
            daemon.main(["--help"])
        said = " ".join(stdout.getvalue().split())
        self.assertIn("Three-finger drag", said)
        self.assertIn("on X11 also", said)

    def test_drag_mode_survives_a_name_it_cannot_take(self):
        with mock.patch.object(FakeShell, "announce", return_value=False):
            self.assertEqual(self.run_main(FakeTouchpad([interrupt]), "wayland"), 0)

    def test_shell_is_told_what_its_silence_costs_in_each_mode(self):
        self.run_main(FakeTouchpad([interrupt]), "wayland")
        self.assertEqual(FakeShell.made_with, {
            "complaint": shell.DRAG_COMPLAINT, "retry_s": shell.DRAG_RETRY_S})
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

    def button_values(self):
        return [value for etype, code, value in
                self.devices[POINTER].written
                if etype == e.EV_KEY and code == e.BTN_LEFT]

    def assert_everything_released_and_closed(self, touchpad):
        self.assertEqual(self.button_values()[-1], 0)
        self.assertEqual(
            self.devices[KEYBOARD].written[-len(WORKSPACE_KEYS):],
            [(e.EV_KEY, key, 0) for key in WORKSPACE_KEYS])
        self.assertTrue(self.devices[POINTER].closed)
        self.assertTrue(self.devices[KEYBOARD].closed)
        self.assertTrue(self.devices[WHEEL].closed)
        self.assertTrue(touchpad.closed)

    def test_button_is_released_by_the_deadline_with_no_further_events(self):
        touchpad = FakeTouchpad([land, move_after_settling, all_fingers_lift])
        wake = threading.Timer(
            DRAG_SETTLE_S + DRAG_RELEASE_S + 0.3, touchpad.push, [interrupt])
        wake.start()
        self.addCleanup(wake.cancel)

        self.assertEqual(self.run_main(touchpad), 0)

        # Pressed, released by the deadline, released again on shutdown.
        self.assertEqual(self.button_values(), [1, 0, 0])
        self.assert_everything_released_and_closed(touchpad)

    def test_sigterm_mid_drag_releases_the_button(self):
        touchpad = FakeTouchpad([land, move_after_settling, terminate])

        with self.assertRaises(SystemExit) as raised:
            self.run_main(touchpad)

        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(self.button_values(), [1, 0])
        self.assert_everything_released_and_closed(touchpad)

    def test_touchpad_vanishing_mid_drag_releases_the_button(self):
        touchpad = FakeTouchpad([land, move_after_settling, vanish])

        with self.assertRaises(OSError):
            self.run_main(touchpad)

        self.assertEqual(self.button_values(), [1, 0])
        self.assert_everything_released_and_closed(touchpad)

    def wheel_units(self):
        return [value for etype, code, value in
                self.devices[WHEEL].written
                if etype == e.EV_REL and code == e.REL_WHEEL_HI_RES]

    def test_two_finger_flick_glides_after_the_lift(self):
        touchpad = FakeTouchpad(two_finger_flick() + [both_fingers_lift])
        wake = threading.Timer(0.4, touchpad.push, [interrupt])
        wake.start()
        self.addCleanup(wake.cancel)

        self.assertEqual(self.run_main(touchpad), 0)

        units = self.wheel_units()
        self.assertGreater(len(units), 10)
        self.assertTrue(all(value > 0 for value in units))
        self.assertEqual(self.button_values(), [0])
        self.assert_everything_released_and_closed(touchpad)

    def test_flick_in_the_overview_does_not_glide(self):
        FakeShell.overview_open = True
        touchpad = FakeTouchpad(two_finger_flick() + [both_fingers_lift])
        wake = threading.Timer(0.4, touchpad.push, [interrupt])
        wake.start()
        self.addCleanup(wake.cancel)

        self.assertEqual(self.run_main(touchpad), 0)

        self.assertEqual(self.wheel_units(), [])

    def test_touching_the_pad_stops_the_glide(self):
        touchpad = FakeTouchpad(two_finger_flick() + [both_fingers_lift])

        self.written_after_touch = None

        def touch_then_quit():
            try:
                time.sleep(0.15)
                touchpad.push(land)
                time.sleep(0.05)
                before = len(self.wheel_units())
                time.sleep(0.15)
                self.written_after_touch = len(self.wheel_units()) - before
            finally:
                touchpad.push(interrupt)

        helper = threading.Thread(target=touch_then_quit, daemon=True)
        helper.start()
        self.assertEqual(self.run_main(touchpad), 0)
        helper.join(timeout=2)

        self.assertGreater(len(self.wheel_units()), 5)
        self.assertEqual(self.written_after_touch, 0)

    def keys_pressed(self):
        return [code for etype, code, value in self.devices[KEYBOARD].written
                if etype == e.EV_KEY and value == 1]

    def test_sideways_swipe_is_followed_by_a_shell_that_can(self):
        touchpad = FakeTouchpad(swipe_left() + [interrupt])

        self.assertEqual(self.run_main(touchpad), 0)

        self.assertEqual(FakeShell.swipes[0], "begin")
        self.assertEqual(FakeShell.swipes[-1], "end")
        moved = [s for s in FakeShell.swipes if not isinstance(s, str)]
        # The first step of 10 mm is what set the swipe off. The shell
        # follows from where the fingers were then.
        self.assertAlmostEqual(sum(moved), (60.0 - 10.0) / 40.0, places=2)
        self.assertTrue(all(step > 0 for step in moved))
        self.assertEqual(self.keys_pressed(), [])

    def test_sideways_swipe_falls_back_to_the_keyboard(self):
        FakeShell.follows_fingers = False
        touchpad = FakeTouchpad(swipe_left() + [interrupt])

        self.assertEqual(self.run_main(touchpad), 0)

        self.assertEqual(FakeShell.swipes, ["begin"])
        self.assertEqual(self.keys_pressed(),
                         [e.KEY_LEFTCTRL, e.KEY_LEFTALT, e.KEY_RIGHT])

    def test_shutdown_during_a_swipe_puts_the_workspace_back(self):
        touchpad = FakeTouchpad(swipe_left()[:4] + [interrupt])

        self.assertEqual(self.run_main(touchpad), 0)

        self.assertEqual(FakeShell.swipes[0], "begin")
        self.assertEqual(FakeShell.swipes[-1], "cancel")

    def test_four_finger_swipe_up_asks_the_shell_for_the_overview(self):
        touchpad = FakeTouchpad([
            lambda: four_fingers_at(400),
            lambda: four_fingers_at(300),
            lambda: four_fingers_at(200),
            interrupt,
        ])

        self.assertEqual(self.run_main(touchpad), 0)

        self.assertEqual(FakeShell.requests, [True])
        self.assertEqual(self.button_values(), [0])
        self.assertEqual(self.wheel_units(), [])

    def test_one_device_failing_to_close_does_not_stop_the_others(self):
        FakeUInput.cannot_be_closed = {POINTER}
        touchpad = FakeTouchpad([interrupt])

        with self.assertRaises(OSError), \
                contextlib.redirect_stderr(io.StringIO()):
            self.run_main(touchpad)

        self.assertEqual(self.closed(), {POINTER, KEYBOARD, WHEEL})
        self.assertTrue(touchpad.closed)

    def test_failing_to_release_does_not_stop_the_devices_closing(self):
        FakeUInput.cannot_be_written = {KEYBOARD}
        touchpad = FakeTouchpad([interrupt])

        with self.assertRaises(OSError), \
                contextlib.redirect_stderr(io.StringIO()):
            self.run_main(touchpad)

        self.assertEqual(self.closed(), {POINTER, KEYBOARD, WHEEL})
        self.assertTrue(touchpad.closed)

    def test_device_that_cannot_be_created_leaves_nothing_open(self):
        FakeUInput.cannot_be_created = {WHEEL}
        touchpad = FakeTouchpad([])

        with self.assertRaises(OSError), \
                contextlib.redirect_stderr(io.StringIO()):
            self.run_main(touchpad)

        self.assertEqual(self.closed(), {POINTER, KEYBOARD})
        self.assertTrue(touchpad.closed)

    def test_fingers_down_at_startup_do_not_start_a_drag(self):
        touchpad = FakeTouchpad(
            [land, move_after_settling, interrupt], touching=True)

        self.assertEqual(self.run_main(touchpad), 0)

        # Never pressed; the only write is the release on shutdown.
        self.assertEqual(self.button_values(), [0])

    def test_check_reports_missing_access_with_its_own_exit_status(self):
        with mock.patch.object(daemon, "find_touchpad", return_value=None), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(daemon.main(["--check"]), daemon.EXIT_NO_ACCESS)
        self.assertNotIn(daemon.EXIT_NO_ACCESS, (0, 1, 2))

    def test_no_touchpad_exits_with_an_error_and_creates_no_devices(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(self.run_main(None), daemon.EXIT_NO_ACCESS)
        self.assertIn("no accessible touchpad", stderr.getvalue())
        self.assertEqual(self.devices, {})


if __name__ == "__main__":
    unittest.main()
