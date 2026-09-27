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

from finger_drag import daemon
from finger_drag.gestures import DRAG_RELEASE_S, DRAG_SETTLE_S
from finger_drag.output import WORKSPACE_KEYS

Event = namedtuple("Event", "type code value")
AbsInfo = namedtuple("AbsInfo", "value min max fuzz flat resolution")


class FakeTouchpad:
    """Each byte in the pipe announces one scripted step for read() to run."""

    path = "/dev/input/fake"
    name = "fake touchpad"

    def __init__(self, steps):
        self.fd, self._write_fd = os.pipe()
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

    def read(self):
        os.read(self.fd, 1)
        return self._steps.pop(0)()

    def close(self):
        self.closed = True
        os.close(self.fd)
        os.close(self._write_fd)


class FakeUInput:
    def __init__(self, events, name):
        self.name = name
        self.written = []
        self.closed = False

    def write(self, etype, code, value):
        self.written.append((etype, code, value))

    def syn(self):
        pass

    def close(self):
        self.closed = True


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
        self.addCleanup(
            signal.signal, signal.SIGTERM, signal.getsignal(signal.SIGTERM))

    def make_uinput(self, events, name):
        device = FakeUInput(events, name)
        self.devices[name] = device
        return device

    def run_main(self, touchpad):
        with mock.patch.object(daemon, "find_touchpad", return_value=touchpad), \
                mock.patch.object(daemon.os, "access", return_value=True), \
                mock.patch.object(daemon.evdev, "UInput", self.make_uinput), \
                contextlib.redirect_stdout(io.StringIO()):
            return daemon.main([])

    def button_values(self):
        return [value for etype, code, value in
                self.devices["finger-drag pointer"].written
                if etype == e.EV_KEY and code == e.BTN_LEFT]

    def assert_everything_released_and_closed(self, touchpad):
        self.assertEqual(self.button_values()[-1], 0)
        self.assertEqual(
            self.devices["finger-drag keyboard"].written[-len(WORKSPACE_KEYS):],
            [(e.EV_KEY, key, 0) for key in WORKSPACE_KEYS])
        self.assertTrue(self.devices["finger-drag pointer"].closed)
        self.assertTrue(self.devices["finger-drag keyboard"].closed)
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

    def test_no_touchpad_exits_with_an_error_and_creates_no_devices(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(self.run_main(None), 1)
        self.assertIn("no accessible touchpad", stderr.getvalue())
        self.assertEqual(self.devices, {})


if __name__ == "__main__":
    unittest.main()
