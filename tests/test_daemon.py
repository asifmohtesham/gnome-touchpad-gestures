import unittest
from collections import namedtuple

from evdev import ecodes as e

from finger_drag.daemon import is_touchpad, pump
from finger_drag.gestures import ButtonDown, ButtonUp, GestureMachine, State
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
