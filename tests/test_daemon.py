import contextlib
import io
import unittest
from unittest import mock
from collections import namedtuple

from evdev import ecodes as e

from gnome_x11_touchpad_gestures import daemon
from gnome_x11_touchpad_gestures.daemon import (
    Machines, is_single_touch_pad, is_touchpad, preference, pump)
from gnome_x11_touchpad_gestures.gestures import ButtonDown, ButtonUp, GestureMachine, State
from gnome_x11_touchpad_gestures.momentum import MomentumMachine
from gnome_x11_touchpad_gestures.slots import SlotTracker

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


BUS_USB, BUS_BLUETOOTH, BUS_I8042, BUS_I2C = 0x03, 0x05, 0x11, 0x18


class DeviceNameTest(unittest.TestCase):
    """GNOME sorts input devices by words in their names.

    Its window manager lower-cases a device's name and looks for these words.
    One that matches gets that kind of device's settings: a virtual wheel
    called "... touchpad ..." is given the touchpad's natural scrolling, which
    turns every glide round.
    """

    # mutter 46, src/backends/x11/meta-seat-x11.c, create_device().
    MUTTER = ("eraser", "cursor", " pad", "wacom", "pen", "touchpad")
    # GDK, which GTK programs use to sort devices in their turn.
    GDK = ("stylus", "trackpoint", "dualpoint stick")
    CLAIMED = MUTTER + GDK + ("touchscreen",)
    NAMES = (daemon.POINTER_NAME, daemon.KEYBOARD_NAME, daemon.WHEEL_NAME)

    def test_no_name_would_be_taken_for_another_kind_of_device(self):
        for name in self.NAMES:
            for word in self.CLAIMED:
                with self.subTest(name=name, word=word):
                    self.assertNotIn(word, name.lower())

    def test_names_are_distinct_and_short_enough_for_the_kernel(self):
        self.assertEqual(len(set(self.NAMES)), 3)
        for name in self.NAMES:
            self.assertLess(len(name), 80)

    def test_names_say_what_made_them(self):
        for name in self.NAMES:
            self.assertTrue(name.startswith("gnome-x11-gestures "), name)


class GlidesTest(unittest.TestCase):
    """The momentum machine the daemon runs asks the shell before gliding."""

    class Shell:
        overview_open = False

        def may_glide(self):
            return not self.overview_open

    def flick(self, machine):
        for i in range(12):
            machine.update(i * 0.007, 2, 50.0, 25.0 + 1.4 * i)
        machine.update(12 * 0.007, 0, 0.0, 0.0)

    def test_flick_outside_the_overview_glides(self):
        machine = daemon.glides(self.Shell())
        self.flick(machine)
        self.assertIsNotNone(machine.next_deadline())

    def test_flick_in_the_overview_never_starts_a_glide(self):
        shell = self.Shell()
        shell.overview_open = True
        machine = daemon.glides(shell)
        self.flick(machine)
        self.assertIsNone(machine.next_deadline())


class PreferenceTest(unittest.TestCase):
    def best(self, *candidates):
        return min(candidates, key=lambda c: preference(*c))[0]

    def test_event_numbers_are_compared_as_numbers(self):
        self.assertEqual(
            self.best(("/dev/input/event21", BUS_I2C),
                      ("/dev/input/event3", BUS_I2C),
                      ("/dev/input/event8", BUS_I2C)),
            "/dev/input/event3")

    def test_built_in_pad_beats_a_usb_one_with_a_lower_number(self):
        self.assertEqual(
            self.best(("/dev/input/event2", BUS_USB),
                      ("/dev/input/event8", BUS_I2C)),
            "/dev/input/event8")

    def test_built_in_pad_beats_a_bluetooth_one(self):
        self.assertEqual(
            self.best(("/dev/input/event5", BUS_BLUETOOTH),
                      ("/dev/input/event9", BUS_I8042)),
            "/dev/input/event9")

    def test_external_pad_is_used_when_it_is_the_only_one(self):
        self.assertEqual(self.best(("/dev/input/event21", BUS_USB)),
                         "/dev/input/event21")


class FakeInfo:
    def __init__(self, bustype):
        self.bustype = bustype


class FakeInputDevice:
    devices = {}
    opened = []

    def __init__(self, path):
        if FakeInputDevice.devices[path] is None:
            raise OSError(13, f"cannot open {path}")
        abs_codes, props, keys, bustype = FakeInputDevice.devices[path]
        self.path = path
        self.name = f"fake {path}"
        self.info = FakeInfo(bustype)
        self._capabilities = {e.EV_ABS: abs_codes, e.EV_KEY: keys}
        self._props = props
        self.closed = False
        FakeInputDevice.opened.append(self)

    def capabilities(self, absinfo=True):
        return self._capabilities

    def input_props(self):
        return self._props

    def close(self):
        self.closed = True


MT = [e.ABS_X, e.ABS_Y, e.ABS_MT_SLOT, e.ABS_MT_POSITION_X, e.ABS_MT_POSITION_Y]
PAD_KEYS = [e.BTN_LEFT, e.BTN_TOOL_FINGER, e.BTN_TOUCH]
MULTITOUCH = (MT, [e.INPUT_PROP_POINTER], PAD_KEYS)
SINGLE_TOUCH = ([e.ABS_X, e.ABS_Y], [e.INPUT_PROP_POINTER], PAD_KEYS)
KEYBOARD = ([], [], [e.KEY_A])


class FindTouchpadTest(unittest.TestCase):
    def setUp(self):
        FakeInputDevice.opened = []
        listing = mock.patch.object(
            daemon.evdev, "list_devices",
            side_effect=lambda: list(FakeInputDevice.devices))
        opening = mock.patch.object(daemon.evdev, "InputDevice", FakeInputDevice)
        listing.start()
        opening.start()
        self.addCleanup(listing.stop)
        self.addCleanup(opening.stop)

    def having(self, **devices):
        FakeInputDevice.devices = {
            f"/dev/input/{name}": spec for name, spec in devices.items()}

    def left_open(self):
        return [d.path for d in FakeInputDevice.opened if not d.closed]

    def test_built_in_pad_is_chosen_over_one_plugged_in_later(self):
        self.having(event21=MULTITOUCH + (BUS_USB,),
                    event3=KEYBOARD + (BUS_I8042,),
                    event8=MULTITOUCH + (BUS_I2C,))
        self.assertEqual(daemon.find_touchpad().path, "/dev/input/event8")

    def test_only_the_chosen_pad_is_left_open(self):
        self.having(event21=MULTITOUCH + (BUS_USB,),
                    event3=KEYBOARD + (BUS_I8042,),
                    event8=MULTITOUCH + (BUS_I2C,))
        daemon.find_touchpad()
        self.assertEqual(self.left_open(), ["/dev/input/event8"])

    def test_device_that_cannot_be_opened_does_not_stop_the_search(self):
        self.having(event3=None, event8=MULTITOUCH + (BUS_I2C,), event9=None)
        self.assertEqual(daemon.find_touchpad().path, "/dev/input/event8")

    def test_daemon_refuses_to_start_on_a_pad_that_cannot_tell_fingers_apart(self):
        self.having(event8=SINGLE_TOUCH + (BUS_I8042,))
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(daemon.main([]), daemon.EXIT_UNSUPPORTED)
        self.assertIn("each finger", stderr.getvalue())

    def test_nothing_is_found_among_other_devices(self):
        self.having(event3=KEYBOARD + (BUS_I8042,))
        self.assertIsNone(daemon.find_touchpad())
        self.assertEqual(self.left_open(), [])

    def test_check_says_when_the_only_pad_cannot_tell_fingers_apart(self):
        self.having(event8=SINGLE_TOUCH + (BUS_I8042,))
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(daemon.check(), daemon.EXIT_UNSUPPORTED)
        self.assertIn("each finger", stderr.getvalue())
        self.assertEqual(self.left_open(), [])

    def test_check_still_reports_missing_access_when_nothing_is_readable(self):
        self.having()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(daemon.check(), daemon.EXIT_NO_ACCESS)

    def checked(self, loaded):
        class Shell:
            def extension_version(self):
                return loaded

        self.having(event8=MULTITOUCH + (BUS_I2C,))
        with mock.patch.object(daemon, "Shell", Shell), \
                mock.patch.object(daemon.os, "access", return_value=True), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            status = daemon.check()
        return status, stdout.getvalue()

    def test_check_says_the_extension_is_answering(self):
        status, said = self.checked(daemon.__version__)
        self.assertEqual(status, 0)
        self.assertIn(f"extension: answering, version {daemon.__version__}", said)

    def test_check_says_how_to_load_an_extension_that_is_not_answering(self):
        status, said = self.checked(None)
        self.assertEqual(status, 0)
        self.assertIn("extension: not answering", said)
        self.assertIn("Alt+F2", said)

    def test_check_says_when_the_shell_still_runs_an_older_extension(self):
        status, said = self.checked("0.0.1")
        self.assertEqual(status, 0)
        self.assertIn("0.0.1", said)
        self.assertIn(daemon.__version__, said)
        self.assertIn("Alt+F2", said)

    def test_the_three_exit_statuses_are_distinct(self):
        self.assertEqual(
            len({0, 1, 2, daemon.EXIT_NO_ACCESS, daemon.EXIT_UNSUPPORTED}), 5)


class SingleTouchPadTest(unittest.TestCase):
    def test_pad_without_slots_is_a_single_touch_pad(self):
        abs_codes, props, keys = SINGLE_TOUCH
        self.assertTrue(is_single_touch_pad(
            {e.EV_ABS: abs_codes, e.EV_KEY: keys}, props))

    def test_multitouch_pad_is_not(self):
        abs_codes, props, keys = MULTITOUCH
        self.assertFalse(is_single_touch_pad(
            {e.EV_ABS: abs_codes, e.EV_KEY: keys}, props))

    def is_one(self, abs_codes, props, keys):
        return is_single_touch_pad({e.EV_ABS: abs_codes, e.EV_KEY: keys}, props)

    def test_it_must_be_a_pointing_device(self):
        self.assertFalse(self.is_one([e.ABS_X, e.ABS_Y], [e.INPUT_PROP_DIRECT], PAD_KEYS))
        self.assertFalse(self.is_one([e.ABS_X, e.ABS_Y], [], PAD_KEYS))

    def test_it_must_report_a_position(self):
        self.assertFalse(self.is_one([], [e.INPUT_PROP_POINTER], PAD_KEYS))

    def test_it_must_be_touched_with_a_finger(self):
        # A drawing tablet reports a position and points, but with a pen.
        self.assertFalse(self.is_one(
            [e.ABS_X, e.ABS_Y], [e.INPUT_PROP_POINTER], [e.BTN_TOOL_PEN, e.BTN_TOUCH]))

    def test_mouse_is_not(self):
        self.assertFalse(is_single_touch_pad(
            {e.EV_REL: [e.REL_X, e.REL_Y], e.EV_KEY: [e.BTN_LEFT]},
            [e.INPUT_PROP_POINTER]))


class Stamped(namedtuple("Stamped", "type code value at")):
    """An event as the kernel hands it over, with the time it happened."""

    def timestamp(self):
        return self.at


def stamped(events, at):
    return [Stamped(ev.type, ev.code, ev.value, at) for ev in events]


class RecordingMachine:
    def __init__(self):
        self.times = []

    def update(self, t, *frame):
        self.times.append(("frame", t))
        return []

    def interrupt(self, t):
        self.times.append(("interrupt", t))
        return []


class FrameTimesTest(unittest.TestCase):
    """Frames read together after a hold-up did not happen together."""

    def event(self, at):
        return Stamped(e.EV_SYN, e.SYN_REPORT, 0, at)

    def test_kernel_s_word_is_taken_for_when_a_frame_happened(self):
        times = daemon.FrameTimes(stamped=True)
        self.assertEqual(times.of(self.event(9.990), now=10.0), 9.990)
        self.assertEqual(times.of(self.event(9.997), now=10.0), 9.997)

    def test_without_it_a_frame_happened_when_it_was_read(self):
        times = daemon.FrameTimes(stamped=False)
        self.assertEqual(times.of(self.event(9.990), now=10.0), 10.0)

    def test_events_that_bear_no_time_are_not_asked_for_one(self):
        times = daemon.FrameTimes(stamped=False)
        self.assertEqual(times.of(Event(e.EV_SYN, e.SYN_REPORT, 0), now=10.0), 10.0)

    def test_no_frame_happens_after_it_was_read(self):
        times = daemon.FrameTimes(stamped=True)
        self.assertEqual(times.of(self.event(10.2), now=10.0), 10.0)

    def test_time_does_not_run_backwards(self):
        # A deadline passed at 10.0, and then a frame turns up from before.
        times = daemon.FrameTimes(stamped=True)
        self.assertEqual(times.passed(10.0), 10.0)
        self.assertEqual(times.of(self.event(9.999), now=10.001), 10.0)
        self.assertEqual(times.of(self.event(10.0005), now=10.001), 10.0005)

    def test_frames_out_of_order_do_not_turn_time_back(self):
        times = daemon.FrameTimes(stamped=True)
        times.of(self.event(5.0), now=5.1)
        self.assertEqual(times.of(self.event(4.9), now=5.1), 5.0)

    def test_time_that_cannot_be_true_is_not_believed(self):
        # By another clock, or none: the frame is taken to be new.
        times = daemon.FrameTimes(stamped=True)
        for at in (0.0, 10.0 - daemon.STAMP_MAX_AGE_S - 0.1, -5.0):
            with self.subTest(at=at):
                self.assertEqual(times.of(self.event(at), now=10.0), 10.0)

    def test_frame_held_up_for_as_long_as_the_shell_may_take_is_believed(self):
        from gnome_x11_touchpad_gestures.shell import OVERVIEW_TIMEOUT_S
        times = daemon.FrameTimes(stamped=True)
        held_up = 2 * OVERVIEW_TIMEOUT_S
        self.assertLess(held_up, daemon.STAMP_MAX_AGE_S)
        self.assertEqual(times.of(self.event(10.0 - held_up), now=10.0),
                         10.0 - held_up)

    def test_passing_time_is_not_turned_back_either(self):
        times = daemon.FrameTimes(stamped=True)
        times.of(self.event(5.0), now=5.0)
        self.assertEqual(times.passed(4.0), 5.0)


class PumpTimesTest(unittest.TestCase):
    def setUp(self):
        self.tracker = SlotTracker(10.0, 10.0)
        self.machine = RecordingMachine()
        self.output = RecordingOutput()

    def pump(self, events, now, times=None):
        if times is None:
            pump(events, lambda: (0, False), self.tracker, self.machine,
                 self.output, now)
        else:
            pump(events, lambda: (0, False), self.tracker, self.machine,
                 self.output, now, times)

    def test_each_frame_of_a_batch_bears_its_own_time(self):
        batch = (stamped(three_fingers_at(500), 0.970)
                 + stamped(three_fingers_at(510), 0.980)
                 + stamped(three_fingers_at(520), 0.990))
        self.pump(batch, 1.0, daemon.FrameTimes(stamped=True))
        self.assertEqual(self.machine.times,
                         [("frame", 0.970), ("frame", 0.980), ("frame", 0.990)])

    def test_frame_s_time_is_that_of_the_report_that_closes_it(self):
        frame = stamped(three_fingers_at(500), 0.970)
        frame[-1] = frame[-1]._replace(at=0.975)
        self.pump(frame, 1.0, daemon.FrameTimes(stamped=True))
        self.assertEqual(self.machine.times, [("frame", 0.975)])

    def test_without_the_kernel_s_word_every_frame_bears_the_time_of_reading(self):
        batch = (stamped(three_fingers_at(500), 0.970)
                 + stamped(three_fingers_at(510), 0.980))
        self.pump(batch, 1.0, daemon.FrameTimes(stamped=False))
        self.assertEqual(self.machine.times, [("frame", 1.0), ("frame", 1.0)])

    def test_pump_given_no_times_uses_the_time_of_reading(self):
        self.pump(three_fingers_at(500) + three_fingers_at(510), 1.0)
        self.assertEqual(self.machine.times, [("frame", 1.0), ("frame", 1.0)])

    def test_times_are_kept_from_one_batch_to_the_next(self):
        times = daemon.FrameTimes(stamped=True)
        self.pump(stamped(three_fingers_at(500), 2.0), 2.0, times)
        self.pump(stamped(three_fingers_at(510), 1.9), 2.1, times)
        self.assertEqual(self.machine.times, [("frame", 2.0), ("frame", 2.0)])

    def test_lost_events_end_the_gesture_when_that_was_found_out(self):
        times = daemon.FrameTimes(stamped=True)
        self.pump(stamped(three_fingers_at(500), 2.0), 2.0, times)
        self.pump([Stamped(e.EV_SYN, e.SYN_DROPPED, 0, 1.0)], 2.05, times)
        self.assertEqual(self.machine.times[-1], ("interrupt", 2.05))

    def test_no_frame_precedes_the_end_of_the_gesture_before_it(self):
        times = daemon.FrameTimes(stamped=True)
        self.pump([Stamped(e.EV_SYN, e.SYN_DROPPED, 0, 2.0),
                   Stamped(e.EV_SYN, e.SYN_REPORT, 0, 2.0)], 2.05, times)
        self.pump(stamped(three_fingers_at(500), 2.04), 2.06, times)
        self.assertEqual(self.machine.times,
                         [("interrupt", 2.05), ("frame", 2.05)])


class KernelStampTest(unittest.TestCase):
    def test_kernel_is_asked_to_stamp_by_the_clock_the_daemon_reads(self):
        import struct
        import time
        asked = []
        device = mock.Mock(fd=42)
        with mock.patch.object(daemon.fcntl, "ioctl",
                               lambda *args: asked.append(args)):
            self.assertIs(daemon.stamp_by_our_clock(device), True)
        # EVIOCSCLOCKID, from linux/input.h: _IOW('E', 0xa0, int).
        self.assertEqual(asked, [(42, 0x400445A0,
                                  struct.pack("i", time.CLOCK_MONOTONIC))])
        self.assertEqual(time.get_clock_info("monotonic").implementation,
                         "clock_gettime(CLOCK_MONOTONIC)")

    def test_kernel_that_will_not_is_survived(self):
        import os
        read_end, write_end = os.pipe()
        self.addCleanup(os.close, read_end)
        self.addCleanup(os.close, write_end)
        self.assertIs(daemon.stamp_by_our_clock(mock.Mock(fd=read_end)), False)

    def test_device_without_a_descriptor_is_survived(self):
        self.assertIs(daemon.stamp_by_our_clock(object()), False)

    def test_this_machine_s_touchpad_agrees(self):
        import warnings
        with warnings.catch_warnings():
            # The library's own business, when it opens a device.
            warnings.simplefilter("ignore", DeprecationWarning)
            device = daemon.find_touchpad()
        if device is None:
            self.skipTest("no touchpad that can be read")
        self.addCleanup(device.close)
        self.assertIs(daemon.stamp_by_our_clock(device), True)


class RunTimesTest(unittest.TestCase):
    """The loop hands frames and deadlines to the machine by one clock."""

    def test_deadline_that_passed_keeps_later_frames_from_preceding_it(self):
        machine = mock.Mock()
        machine.next_deadline.side_effect = [1.0, None]
        machine.tick.return_value = []
        machine.update.side_effect = KeyboardInterrupt
        device = mock.Mock(fd=7)
        device.read.return_value = stamped(three_fingers_at(500), 0.999)
        clock = iter([0.9, 1.0, 1.001]).__next__
        ready = iter([([], [], []), ([7], [], [])]).__next__
        with mock.patch.object(daemon.select, "select", lambda *a: ready()):
            with self.assertRaises(KeyboardInterrupt):
                daemon.run(device, SlotTracker(10.0, 10.0), machine,
                           RecordingOutput(), clock=clock,
                           times=daemon.FrameTimes(stamped=True))
        machine.tick.assert_called_once_with(1.0)
        self.assertEqual(machine.update.call_args.args[0], 1.0)


class PumpTest(unittest.TestCase):
    def setUp(self):
        self.tracker = SlotTracker(10.0, 10.0)
        self.machine = GestureMachine()
        self.output = RecordingOutput()

    def pump(self, events, now, current_slot=0, touching=False):
        pump(events, lambda: (current_slot, touching), self.tracker,
             self.machine, self.output, now)

    def start_drag(self):
        self.pump(three_fingers_at(500), 0.00)
        self.pump(three_fingers_at(530), 0.06)

    def test_three_finger_motion_presses_the_button(self):
        self.start_drag()
        self.assertEqual(self.output.actions, [ButtonDown()])
        self.assertIs(self.machine.state, State.DRAGGING)

    def test_finger_swap_mid_drag_does_not_move_the_pointer(self):
        self.start_drag()
        self.pump([
            Event(e.EV_ABS, e.ABS_MT_SLOT, 2),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1),
            Event(e.EV_ABS, e.ABS_MT_SLOT, 3),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, 200),
            Event(e.EV_ABS, e.ABS_MT_POSITION_X, 1200),
            Event(e.EV_ABS, e.ABS_MT_POSITION_Y, 200),
            Event(e.EV_SYN, e.SYN_REPORT, 0),
        ], 0.50)
        self.assertEqual(self.output.actions, [ButtonDown()])

    def test_events_without_a_report_produce_nothing(self):
        self.pump(three_fingers_at(500)[:-1], 0.0)
        self.assertEqual(self.output.actions, [])

    def test_syn_dropped_ends_the_drag(self):
        self.start_drag()
        self.pump([Event(e.EV_SYN, e.SYN_DROPPED, 0)], 1.0)
        self.assertIs(self.machine.state, State.RELEASE_WAIT)
        self.assertEqual(self.machine.tick(60.0), [ButtonUp()])

    def test_events_after_a_drop_are_discarded_until_the_next_report(self):
        self.pump([
            Event(e.EV_SYN, e.SYN_DROPPED, 0),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, 77),
            Event(e.EV_ABS, e.ABS_MT_POSITION_X, 400),
            Event(e.EV_ABS, e.ABS_MT_POSITION_Y, 300),
            Event(e.EV_SYN, e.SYN_REPORT, 0),
        ], 0.0)
        frame = self.tracker.feed(e.EV_SYN, e.SYN_REPORT, 0)
        self.assertEqual(frame.count, 0)

    def test_discarding_continues_across_read_batches(self):
        self.pump([Event(e.EV_SYN, e.SYN_DROPPED, 0)], 0.0)
        self.pump([
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, 77),
            Event(e.EV_ABS, e.ABS_MT_POSITION_X, 400),
            Event(e.EV_ABS, e.ABS_MT_POSITION_Y, 300),
            Event(e.EV_SYN, e.SYN_REPORT, 0),
        ], 0.1)
        frame = self.tracker.feed(e.EV_SYN, e.SYN_REPORT, 0)
        self.assertEqual(frame.count, 0)

    def test_gestures_work_again_after_a_drop(self):
        self.pump([
            Event(e.EV_SYN, e.SYN_DROPPED, 0),
            Event(e.EV_SYN, e.SYN_REPORT, 0),
        ], 0.0)
        self.pump(three_fingers_at(500), 1.00)
        self.pump(three_fingers_at(530), 1.06)
        self.assertEqual(self.output.actions, [ButtonDown()])

    def test_drop_with_fingers_still_down_waits_for_a_full_lift(self):
        self.pump([
            Event(e.EV_SYN, e.SYN_DROPPED, 0),
            Event(e.EV_SYN, e.SYN_REPORT, 0),
        ], 0.0, touching=True)
        self.pump(three_fingers_at(500), 1.00)
        self.pump(three_fingers_at(530), 1.06)
        self.assertEqual(self.output.actions, [])

        lift = [Event(e.EV_KEY, e.BTN_TOUCH, 0), Event(e.EV_SYN, e.SYN_REPORT, 0)]
        self.pump(lift, 2.00)
        self.pump(three_fingers_at(500), 3.00)
        self.pump(three_fingers_at(530), 3.06)
        self.assertEqual(self.output.actions, [ButtonDown()])

    def test_syn_dropped_resyncs_the_current_slot(self):
        self.pump([
            Event(e.EV_SYN, e.SYN_DROPPED, 0),
            Event(e.EV_SYN, e.SYN_REPORT, 0),
        ], 0.0, current_slot=3)
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


class PumpMomentumTest(unittest.TestCase):
    def setUp(self):
        self.tracker = SlotTracker(10.0, 10.0)
        self.machine = Machines(GestureMachine(), MomentumMachine())
        self.output = RecordingOutput()

    def pump(self, events, now):
        pump(events, lambda: (0, False), self.tracker, self.machine,
             self.output, now)

    def flick(self):
        for index in range(6):
            self.pump(two_fingers_at(100 + 20 * index), index * 0.01)

    def test_lift_after_a_flick_starts_a_glide(self):
        self.flick()
        self.pump([
            Event(e.EV_ABS, e.ABS_MT_SLOT, 0),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1),
            Event(e.EV_ABS, e.ABS_MT_SLOT, 1),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1),
            Event(e.EV_SYN, e.SYN_REPORT, 0),
        ], 0.06)
        self.assertIsNotNone(self.machine.next_deadline())

    def test_resting_thumb_and_moving_finger_do_not_glide(self):
        for index in range(15):
            self.pump([
                Event(e.EV_ABS, e.ABS_MT_SLOT, 0),
                Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, 300),
                Event(e.EV_ABS, e.ABS_MT_POSITION_X, 300),
                Event(e.EV_ABS, e.ABS_MT_POSITION_Y, 400),
                Event(e.EV_ABS, e.ABS_MT_SLOT, 1),
                Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, 301),
                Event(e.EV_ABS, e.ABS_MT_POSITION_X, 600),
                Event(e.EV_ABS, e.ABS_MT_POSITION_Y, 100 + 7 * index),
                Event(e.EV_SYN, e.SYN_REPORT, 0),
            ], index * 0.007)
        self.pump([
            Event(e.EV_ABS, e.ABS_MT_SLOT, 0),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1),
            Event(e.EV_ABS, e.ABS_MT_SLOT, 1),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1),
            Event(e.EV_SYN, e.SYN_REPORT, 0),
        ], 0.105)
        self.assertIsNone(self.machine.next_deadline())

    def test_clicking_the_pad_while_scrolling_does_not_glide(self):
        self.pump([Event(e.EV_KEY, e.BTN_LEFT, 1)], 0.0)
        self.flick()
        self.pump([
            Event(e.EV_ABS, e.ABS_MT_SLOT, 0),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1),
            Event(e.EV_ABS, e.ABS_MT_SLOT, 1),
            Event(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1),
            Event(e.EV_SYN, e.SYN_REPORT, 0),
        ], 0.06)
        self.assertIsNone(self.machine.next_deadline())

    def test_dropped_events_mid_scroll_do_not_start_a_glide(self):
        self.flick()
        self.pump([Event(e.EV_SYN, e.SYN_DROPPED, 0)], 0.06)
        self.assertIsNone(self.machine.next_deadline())
        self.assertEqual(self.output.actions, [])


class FakeMachine:
    def __init__(self, action, deadline):
        self.action = action
        self.deadline = deadline
        self.updates = []

    def update(self, t, count, cx, cy, regrouped=False, fingers=(),
               pressed=False):
        self.updates.append((t, count, cx, cy, regrouped, fingers, pressed))
        return [self.action]

    def interrupt(self, t):
        return [f"{self.action} interrupted"]

    def tick(self, t):
        return [self.action]

    def next_deadline(self):
        return self.deadline


class MachinesTest(unittest.TestCase):
    def setUp(self):
        self.first = FakeMachine("first", None)
        self.second = FakeMachine("second", None)
        self.machines = Machines(self.first, self.second)

    def test_every_machine_sees_every_frame(self):
        fingers = ((1.0, 2.0), (5.0, 6.0))
        actions = self.machines.update(1.0, 2, 3.0, 4.0, True, fingers, True)
        self.assertEqual(actions, ["first", "second"])
        seen = [(1.0, 2, 3.0, 4.0, True, fingers, True)]
        self.assertEqual(self.first.updates, seen)
        self.assertEqual(self.second.updates, seen)

    def test_tick_reaches_every_machine(self):
        self.assertEqual(self.machines.tick(1.0), ["first", "second"])

    def test_interrupt_reaches_every_machine(self):
        self.assertEqual(
            self.machines.interrupt(1.0),
            ["first interrupted", "second interrupted"])

    def test_no_deadline_when_no_machine_has_one(self):
        self.assertIsNone(self.machines.next_deadline())

    def test_earliest_deadline_wins(self):
        self.first.deadline = 5.0
        self.second.deadline = 2.0
        self.assertEqual(self.machines.next_deadline(), 2.0)

    def test_a_single_deadline_is_used(self):
        self.second.deadline = 7.0
        self.assertEqual(self.machines.next_deadline(), 7.0)


if __name__ == "__main__":
    unittest.main()
