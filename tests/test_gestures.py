import math
import unittest

from finger_drag import gestures as g
from finger_drag.gestures import ButtonDown, ButtonUp, GestureMachine, Move, State


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
