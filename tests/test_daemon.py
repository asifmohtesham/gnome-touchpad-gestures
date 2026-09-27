import unittest
from collections import namedtuple

from evdev import ecodes as e

from finger_drag.daemon import Machines, is_touchpad, pump
from finger_drag.gestures import ButtonDown, ButtonUp, GestureMachine, State
from finger_drag.momentum import MomentumMachine
from finger_drag.slots import SlotTracker

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
