import unittest

from evdev import ecodes as e

from finger_drag.gestures import (
    ButtonDown, ButtonUp, Direction, Move, Overview, SwitchWorkspace)
from finger_drag.momentum import Scroll
from finger_drag.output import KEY_HOLD_S, WORKSPACE_KEYS, Output

SYN = "syn"


class FakeDevice:
    def __init__(self):
        self.events = []

    def write(self, etype, code, value):
        self.events.append((etype, code, value))

    def syn(self):
        self.events.append(SYN)


class FakeShell:
    def __init__(self):
        self.requests = []

    def show_overview(self, show):
        self.requests.append(show)


def total(events, code):
    return sum(ev[2] for ev in events if ev != SYN and ev[0] == e.EV_REL and ev[1] == code)


class OutputTest(unittest.TestCase):
    def setUp(self):
        self.pointer = FakeDevice()
        self.keyboard = FakeDevice()
        self.wheel = FakeDevice()
        self.shell = FakeShell()
        self.sleeps = []
        self.output = Output(
            self.pointer, self.keyboard, self.wheel, self.shell,
            sleep=self.sleeps.append)

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

    def test_overview_goes_to_the_shell_and_touches_no_device(self):
        self.output.emit([Overview(show=True), Overview(show=False)])
        self.assertEqual(self.shell.requests, [True, False])
        self.assertEqual(self.pointer.events, [])
        self.assertEqual(self.keyboard.events, [])
        self.assertEqual(self.wheel.events, [])

    def test_scroll_writes_high_resolution_wheel_units(self):
        self.output.emit([Scroll(0.0, 40.0)])
        self.assertEqual(self.wheel.events, [
            (e.EV_REL, e.REL_WHEEL_HI_RES, 40), SYN,
        ])
        self.assertEqual(self.pointer.events, [])

    def test_scroll_adds_a_notch_for_every_120_units(self):
        for _ in range(7):
            self.output.emit([Scroll(0.0, 40.0)])
        notches = [ev for ev in self.wheel.events
                   if ev != SYN and ev[1] == e.REL_WHEEL]
        self.assertEqual(notches, [(e.EV_REL, e.REL_WHEEL, 1)] * 2)
        self.assertEqual(total(self.wheel.events, e.REL_WHEEL_HI_RES), 280)

    def test_notch_travels_in_the_same_frame_as_the_units_that_complete_it(self):
        self.output.emit([Scroll(0.0, 100.0)])
        self.wheel.events.clear()
        self.output.emit([Scroll(0.0, 30.0)])
        self.assertEqual(self.wheel.events, [
            (e.EV_REL, e.REL_WHEEL_HI_RES, 30),
            (e.EV_REL, e.REL_WHEEL, 1),
            SYN,
        ])

    def test_scroll_down_and_sideways(self):
        self.output.emit([Scroll(-130.0, -250.0)])
        self.assertEqual(self.wheel.events, [
            (e.EV_REL, e.REL_HWHEEL_HI_RES, -130),
            (e.EV_REL, e.REL_HWHEEL, -1),
            (e.EV_REL, e.REL_WHEEL_HI_RES, -250),
            (e.EV_REL, e.REL_WHEEL, -2),
            SYN,
        ])

    def test_scroll_fractions_carry_over(self):
        for _ in range(8):
            self.output.emit([Scroll(0.25, -0.25)])
        self.assertEqual(total(self.wheel.events, e.REL_HWHEEL_HI_RES), 2)
        self.assertEqual(total(self.wheel.events, e.REL_WHEEL_HI_RES), -2)

    def test_new_glide_starts_from_a_clean_notch(self):
        self.output.emit([Scroll(0.0, 100.0, first=True)])
        self.wheel.events.clear()
        self.output.emit([Scroll(0.0, -30.0, first=True)])
        self.assertEqual(self.wheel.events, [
            (e.EV_REL, e.REL_WHEEL_HI_RES, -30), SYN,
        ])
        self.wheel.events.clear()
        self.output.emit([Scroll(0.0, -100.0)])
        self.assertEqual(self.wheel.events, [
            (e.EV_REL, e.REL_WHEEL_HI_RES, -100),
            (e.EV_REL, e.REL_WHEEL, -1),
            SYN,
        ])

    def test_steps_within_one_glide_share_their_notch(self):
        self.output.emit([Scroll(0.0, 100.0, first=True)])
        self.wheel.events.clear()
        self.output.emit([Scroll(0.0, 30.0)])
        self.assertIn((e.EV_REL, e.REL_WHEEL, 1), self.wheel.events)

    def test_release_all_drops_pending_scroll(self):
        self.output.emit([Scroll(0.0, 100.75)])
        self.output.release_all()
        self.wheel.events.clear()
        self.output.emit([Scroll(0.0, 0.5)])
        self.output.emit([Scroll(0.0, 30.0)])
        self.assertEqual(self.wheel.events, [
            (e.EV_REL, e.REL_WHEEL_HI_RES, 30), SYN,
        ])


if __name__ == "__main__":
    unittest.main()
