import math
import unittest

from finger_drag import gestures as g
from finger_drag.gestures import (
    ButtonDown, ButtonUp, Direction, GestureMachine, Move, Overview, State,
    SwitchWorkspace)


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
        (0.03, 3, 51.0, 25.0),
        (0.06, 3, 52.5, 25.0),
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

    def test_drag_waits_for_three_fingers_to_settle(self):
        actions = feed(self.machine, [
            (0.00, 3, 50.0, 25.0),
            (0.01, 3, 53.0, 25.0),
        ])
        self.assertEqual(actions, [])
        self.assertIs(self.machine.state, State.IDLE)
        self.assertEqual(
            self.machine.update(g.DRAG_SETTLE_S, 3, 53.5, 25.0), [ButtonDown()])

    def test_settle_time_restarts_when_the_count_changes(self):
        actions = feed(self.machine, [
            (0.00, 3, 50.0, 25.0),
            (0.04, 2, 50.0, 25.0),
            (0.05, 3, 50.0, 25.0),
            (0.06, 3, 53.0, 25.0),
        ])
        self.assertEqual(actions, [])

    def test_drag_moves_pointer_per_frame(self):
        start_drag(self.machine)
        actions = self.machine.update(0.07, 3, 53.5, 25.5)
        self.assertEqual(actions, [Move(
            1.0 * g.POINTER_COUNTS_PER_MM, 0.5 * g.POINTER_COUNTS_PER_MM)])

    def test_stationary_frame_produces_no_move(self):
        start_drag(self.machine)
        self.assertEqual(self.machine.update(0.07, 3, 52.5, 25.0), [])

    def test_count_change_frame_produces_no_move(self):
        start_drag(self.machine)
        self.assertEqual(self.machine.update(0.50, 2, 80.0, 40.0), [])
        self.assertEqual(self.machine.update(0.60, 3, 20.0, 10.0), [])
        self.assertIs(self.machine.state, State.DRAGGING)
        self.assertEqual(
            self.machine.update(0.61, 3, 21.0, 10.0),
            [Move(1.0 * g.POINTER_COUNTS_PER_MM, 0.0)])

    def test_regrouped_frame_produces_no_move(self):
        start_drag(self.machine)
        self.assertEqual(
            self.machine.update(0.50, 3, 20.0, 10.0, regrouped=True), [])
        self.assertIs(self.machine.state, State.DRAGGING)
        self.assertEqual(
            self.machine.update(0.51, 3, 21.0, 10.0),
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


class InterruptTest(unittest.TestCase):
    def test_interrupt_mid_drag_releases_the_button_after_the_wait(self):
        machine = GestureMachine()
        start_drag(machine)
        self.assertEqual(machine.interrupt(1.0), [])
        self.assertIs(machine.state, State.RELEASE_WAIT)
        self.assertEqual(machine.tick(1.0 + g.DRAG_RELEASE_S), [ButtonUp()])


class ReleaseDelayTest(unittest.TestCase):
    def test_full_lift_uses_the_spec_delay(self):
        self.assertAlmostEqual(g.release_delay(0), g.DRAG_RELEASE_S)

    def test_delay_is_finite_and_not_negative(self):
        for remaining in (0, 1, 2):
            with self.subTest(remaining=remaining):
                delay = g.release_delay(remaining)
                self.assertTrue(math.isfinite(delay))
                self.assertGreaterEqual(delay, 0.0)


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

    def test_fourth_finger_landing_late_does_not_click(self):
        # Three fingers are already moving fast when the fourth lands 21 ms in.
        actions = feed(self.machine, [
            (0.000, 3, 60.0, 25.0),
            (0.007, 3, 59.0, 25.0),
            (0.014, 3, 58.0, 25.0),
            (0.021, 4, 57.0, 25.0),
            (0.028, 4, 50.0, 25.0),
            (0.035, 4, 41.0, 25.0),
        ])
        self.assertEqual(actions, [SwitchWorkspace(Direction.NEXT)])

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

    def test_regrouped_frame_adds_no_swipe_travel(self):
        feed(self.machine, [
            (0.00, 4, 60.0, 25.0),
            (0.01, 4, 52.0, 25.0),
        ])
        self.assertEqual(
            self.machine.update(0.02, 4, 30.0, 25.0, regrouped=True), [])
        self.assertEqual(
            self.machine.update(0.03, 4, 22.0, 25.0),
            [SwitchWorkspace(Direction.NEXT)])

    def test_swipe_up_opens_the_overview(self):
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 40.0),
            (0.01, 4, 60.0, 32.0),
            (0.02, 4, 60.0, 24.0),
        ])
        self.assertEqual(actions, [Overview(show=True)])
        self.assertIs(self.machine.state, State.SWIPE_DONE)

    def test_swipe_down_closes_the_overview(self):
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 10.0),
            (0.01, 4, 60.0, 18.0),
            (0.02, 4, 60.0, 26.0),
        ])
        self.assertEqual(actions, [Overview(show=False)])

    def test_vertical_travel_below_threshold_produces_nothing(self):
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 40.0),
            (0.01, 4, 60.0, 26.0),
            (0.02, 0, 0.0, 0.0),
        ])
        self.assertEqual(actions, [])

    def test_only_one_overview_action_per_swipe(self):
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 50.0),
            (0.01, 4, 60.0, 30.0),
            (0.02, 4, 60.0, 10.0),
            (0.03, 4, 60.0, 45.0),
        ])
        self.assertEqual(actions, [Overview(show=True)])

    def test_swipe_up_cannot_also_switch_workspace(self):
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 50.0),
            (0.01, 4, 60.0, 30.0),
            (0.02, 4, 20.0, 30.0),
        ])
        self.assertEqual(actions, [Overview(show=True)])

    def test_diagonal_swipe_is_neither(self):
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 40.0),
            (0.01, 4, 40.0, 20.0),
        ])
        self.assertEqual(actions, [])
        self.assertIs(self.machine.state, State.SWIPE_TRACKING)

    def test_steep_but_not_straight_swipe_up_produces_nothing(self):
        # 16 mm up passes SWIPE_MM, but 12 mm sideways needs 18 up.
        actions = feed(self.machine, [
            (0.00, 4, 60.0, 40.0),
            (0.01, 4, 48.0, 24.0),
        ])
        self.assertEqual(actions, [])

    def test_three_finger_vertical_motion_is_a_drag_not_an_overview(self):
        actions = start_drag(self.machine)
        actions += feed(self.machine, [(0.07, 3, 52.5, 5.0)])
        self.assertNotIn(Overview(show=True), actions)
        self.assertEqual(actions[0], ButtonDown())

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


if __name__ == "__main__":
    unittest.main()
