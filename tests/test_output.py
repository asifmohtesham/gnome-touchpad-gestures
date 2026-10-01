import unittest

from evdev import ecodes as e

from gnome_x11_touchpad_gestures.gestures import (
    SWIPE_FULL_MM, SWIPE_MM, ButtonDown, ButtonUp, Direction, Move, Overview,
    SwipeBegin, SwipeCancel, SwipeEnd, SwipeMove, SwitchWorkspace)
from gnome_x11_touchpad_gestures.momentum import Scroll
from gnome_x11_touchpad_gestures.output import (
    GLIDE_CHECK_STEPS, KEY_HOLD_S, WORKSPACE_KEYS, DragOnly, NoDevice, Output)

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
        self.overview_open = False
        self.asked = 0
        self.follows_fingers = True
        self.swipes = []

    def show_overview(self, show):
        self.requests.append(show)

    def may_glide(self):
        self.asked += 1
        return not self.overview_open

    def swipe_begin(self, t):
        self.swipes.append(("begin", t))
        return self.follows_fingers

    def swipe_update(self, t, fraction):
        self.swipes.append(("update", t, fraction))

    def swipe_end(self, t):
        self.swipes.append(("end", t))

    def swipe_cancel(self):
        self.swipes.append(("cancel",))


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

    NEXT_CHORD = [
        (e.EV_KEY, e.KEY_LEFTCTRL, 1), SYN,
        (e.EV_KEY, e.KEY_LEFTALT, 1), SYN,
        (e.EV_KEY, e.KEY_RIGHT, 1), SYN,
        (e.EV_KEY, e.KEY_RIGHT, 0), SYN,
        (e.EV_KEY, e.KEY_LEFTALT, 0), SYN,
        (e.EV_KEY, e.KEY_LEFTCTRL, 0), SYN,
    ]

    def test_swipe_is_passed_to_a_shell_that_follows_fingers(self):
        self.output.emit([SwipeBegin(1.0), SwipeMove(1.0, -5.0)])
        self.output.emit([SwipeMove(1.01, -2.0)])
        self.output.emit([SwipeEnd(1.2)])
        self.assertEqual(self.shell.swipes, [
            ("begin", 1.0),
            ("update", 1.0, 5.0 / SWIPE_FULL_MM),
            ("update", 1.01, 2.0 / SWIPE_FULL_MM),
            ("end", 1.2),
        ])
        self.assertEqual(self.keyboard.events, [])

    def test_shell_that_follows_starts_from_where_the_fingers_are(self):
        # What they travelled before the swipe began would reach the shell
        # as travel made in no time, and a drift would pass for a flick.
        self.output.emit([SwipeBegin(1.0, travel=-4.3), SwipeEnd(1.4)])
        self.assertEqual(self.shell.swipes, [("begin", 1.0), ("end", 1.4)])
        self.assertEqual(self.keyboard.events, [])

    def test_shell_that_cannot_follow_counts_the_travel_made_before(self):
        self.shell.follows_fingers = False
        self.output.emit([SwipeBegin(1.0, travel=-5.0)])
        self.output.emit([SwipeMove(1.01, -SWIPE_MM + 5.5)])
        self.assertEqual(self.keyboard.events, [])
        self.output.emit([SwipeMove(1.02, -0.5)])
        self.assertEqual(self.keyboard.events, self.NEXT_CHORD)

    def test_fingers_held_still_are_passed_on_as_such(self):
        self.output.emit([SwipeBegin(1.0), SwipeMove(1.3, 0.0)])
        self.assertEqual(self.shell.swipes[-1], ("update", 1.3, 0.0))

    def test_fingers_moving_left_move_towards_the_next_workspace(self):
        self.output.emit([SwipeBegin(1.0), SwipeMove(1.0, -SWIPE_FULL_MM)])
        self.assertEqual(self.shell.swipes[-1], ("update", 1.0, 1.0))
        self.output.emit([SwipeMove(1.1, SWIPE_FULL_MM / 2)])
        self.assertEqual(self.shell.swipes[-1], ("update", 1.1, -0.5))

    def test_followed_swipe_presses_no_keys_however_far_it_goes(self):
        self.output.emit([SwipeBegin(1.0), SwipeMove(1.0, -3 * SWIPE_MM),
                          SwipeEnd(1.2)])
        self.assertEqual(self.keyboard.events, [])

    def test_shell_that_cannot_follow_gets_one_switch_by_keyboard(self):
        self.shell.follows_fingers = False
        self.output.emit([SwipeBegin(1.0), SwipeMove(1.0, -SWIPE_MM / 2)])
        self.assertEqual(self.keyboard.events, [])
        self.output.emit([SwipeMove(1.01, -SWIPE_MM / 2)])
        self.assertEqual(self.keyboard.events, self.NEXT_CHORD)
        self.output.emit([SwipeMove(1.02, -SWIPE_MM), SwipeEnd(1.2)])
        self.assertEqual(self.keyboard.events, self.NEXT_CHORD)
        self.assertEqual(self.shell.swipes, [("begin", 1.0)])

    def test_every_swipe_asks_the_shell_anew(self):
        self.shell.follows_fingers = False
        self.output.emit([SwipeBegin(1.0), SwipeMove(1.0, -5.0), SwipeEnd(1.2)])
        self.shell.follows_fingers = True
        self.output.emit([SwipeBegin(2.0), SwipeMove(2.0, -5.0), SwipeEnd(2.2)])
        self.assertEqual(self.shell.swipes, [
            ("begin", 1.0), ("begin", 2.0),
            ("update", 2.0, 5.0 / SWIPE_FULL_MM), ("end", 2.2)])
        self.assertEqual(self.keyboard.events, [])

    def test_swipe_begun_on_top_of_another_still_asks_the_shell(self):
        self.output.emit([SwipeBegin(1.0), SwipeMove(1.0, -5.0)])
        self.shell.follows_fingers = False
        self.output.emit([SwipeBegin(2.0), SwipeMove(2.0, -SWIPE_MM)])
        self.assertEqual(self.shell.swipes.count(("begin", 2.0)), 1)
        self.assertEqual(self.keyboard.events, self.NEXT_CHORD)

    def test_abandoned_swipe_is_put_back(self):
        self.output.emit([SwipeBegin(1.0), SwipeMove(1.0, -5.0), SwipeCancel()])
        self.assertEqual(self.shell.swipes[-1], ("cancel",))
        self.output.emit([SwipeMove(1.1, -5.0), SwipeEnd(1.2)])
        self.assertEqual(self.shell.swipes[-1], ("cancel",))

    def test_abandoning_a_swipe_the_shell_never_took_says_nothing(self):
        self.shell.follows_fingers = False
        self.output.emit([SwipeBegin(1.0), SwipeMove(1.0, -5.0), SwipeCancel()])
        self.assertEqual(self.shell.swipes, [("begin", 1.0)])

    def test_release_all_puts_a_swipe_under_way_back(self):
        self.output.emit([SwipeBegin(1.0), SwipeMove(1.0, -5.0)])
        self.output.release_all()
        self.assertEqual(self.shell.swipes[-1], ("cancel",))
        self.output.release_all()
        self.assertEqual(self.shell.swipes.count(("cancel",)), 1)

    def glide(self, steps, units=40.0):
        self.output.emit([Scroll(0.0, units, first=True)])
        for _ in range(steps - 1):
            self.output.emit([Scroll(0.0, units)])

    def test_glide_in_the_overview_turns_no_wheel(self):
        # There every notch of the wheel moves one workspace along.
        self.shell.overview_open = True
        self.glide(30)
        self.assertEqual(self.wheel.events, [])

    def test_glide_outside_the_overview_is_untouched(self):
        self.glide(3)
        self.assertEqual(total(self.wheel.events, e.REL_WHEEL_HI_RES), 120)

    def test_overview_opening_part_way_stops_the_rest(self):
        self.glide(GLIDE_CHECK_STEPS)
        written = len(self.wheel.events)
        self.assertGreater(written, 0)
        self.shell.overview_open = True
        for _ in range(GLIDE_CHECK_STEPS * 2):
            self.output.emit([Scroll(0.0, 40.0)])
        self.assertEqual(len(self.wheel.events), written)

    def test_glide_held_back_stays_held_back_to_its_end(self):
        self.shell.overview_open = True
        self.glide(3)
        self.shell.overview_open = False
        for _ in range(GLIDE_CHECK_STEPS * 3):
            self.output.emit([Scroll(0.0, 40.0)])
        self.assertEqual(self.wheel.events, [])

    def test_next_glide_is_judged_afresh(self):
        self.shell.overview_open = True
        self.glide(3)
        self.shell.overview_open = False
        self.glide(3)
        self.assertEqual(total(self.wheel.events, e.REL_WHEEL_HI_RES), 120)

    def test_glide_begun_in_the_overview_is_held_back_from_its_first_step(self):
        # However far through its count the glide before it had got.
        self.glide(5)
        self.wheel.events.clear()
        self.shell.overview_open = True
        self.glide(GLIDE_CHECK_STEPS)
        self.assertEqual(self.wheel.events, [])

    def test_shell_is_asked_now_and_then_not_at_every_step(self):
        self.glide(GLIDE_CHECK_STEPS * 2 + 1)
        self.assertEqual(self.shell.asked, 3)

    def test_shell_is_not_asked_again_once_a_glide_is_held_back(self):
        self.shell.overview_open = True
        self.glide(GLIDE_CHECK_STEPS * 3)
        self.assertEqual(self.shell.asked, 1)

    def test_glide_after_a_held_back_one_starts_from_a_clean_notch(self):
        self.glide(1, units=100.0)
        self.shell.overview_open = True
        self.glide(2, units=100.0)
        self.shell.overview_open = False
        self.wheel.events.clear()
        self.glide(1, units=30.0)
        self.assertEqual(self.wheel.events, [
            (e.EV_REL, e.REL_WHEEL_HI_RES, 30), SYN,
        ])

    def test_release_all_drops_pending_scroll(self):
        self.output.emit([Scroll(0.0, 100.75)])
        self.output.release_all()
        self.wheel.events.clear()
        self.output.emit([Scroll(0.0, 0.5)])
        self.output.emit([Scroll(0.0, 30.0)])
        self.assertEqual(self.wheel.events, [
            (e.EV_REL, e.REL_WHEEL_HI_RES, 30), SYN,
        ])


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

    def test_nothing_else_gets_through_in_the_middle_of_a_drag_either(self):
        self.drag.emit([ButtonDown()])
        self.drag.emit([Overview(show=True), SwitchWorkspace(Direction.NEXT),
                        SwipeBegin(1.0), SwipeMove(1.01, -20.0),
                        Scroll(0.0, 400.0, first=True)])
        self.assertEqual(self.pointer.events, self.PRESS)
        self.assertEqual(self.shell.requests, [])
        self.assertEqual(self.shell.swipes, [])
        self.assertEqual(self.shell.asked, 0)

    def test_moves_after_a_drag_has_ended_are_not_a_drag(self):
        self.drag.emit([ButtonDown(), ButtonUp()])
        self.drag.emit([Move(50.0, 0.0)])
        self.drag.emit([ButtonUp()])
        self.assertEqual(self.pointer.events, self.PRESS + self.RELEASE)

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

if __name__ == "__main__":
    unittest.main()
