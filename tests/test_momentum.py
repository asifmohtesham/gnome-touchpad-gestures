import unittest

from finger_drag import momentum as m
from finger_drag.momentum import MomentumMachine, Scroll

STEP = 0.007  # the touchpad reports about every 7 ms


def scroll(machine, vx, vy, start=0.0, frames=12, count=2, origin=(50.0, 25.0)):
    """Fingers moving at a constant velocity in mm/s. Returns (last time, actions)."""
    actions = []
    t = start
    for i in range(frames):
        t = start + i * STEP
        actions += machine.update(
            t, count, origin[0] + vx * i * STEP, origin[1] + vy * i * STEP)
    return t, actions


def flick(machine, vx, vy, start=0.0):
    """Scroll, then lift one frame later. Returns the time of the lift."""
    t, _ = scroll(machine, vx, vy, start)
    machine.update(t + STEP, 0, 0.0, 0.0)
    return t + STEP


def line(origin, velocity, frames=15):
    """One finger's positions, a frame apart, moving at `velocity` mm/s."""
    return [(origin[0] + velocity[0] * i * STEP, origin[1] + velocity[1] * i * STEP)
            for i in range(frames)]


def touch(machine, paths, start=0.0, pressed=False):
    """Several fingers, each following its own path. Returns the last time."""
    t = start
    for i, positions in enumerate(zip(*paths)):
        t = start + i * STEP
        cx = sum(p[0] for p in positions) / len(positions)
        cy = sum(p[1] for p in positions) / len(positions)
        machine.update(t, len(positions), cx, cy,
                       fingers=tuple(positions), pressed=pressed)
    return t


def lift(machine, t):
    machine.update(t + STEP, 0, 0.0, 0.0)
    return t + STEP


def glide(machine, limit=500):
    """Tick at every deadline until the glide ends; return every Scroll."""
    scrolls = []
    for _ in range(limit * 10):
        deadline = machine.next_deadline()
        if deadline is None:
            break
        scrolls += machine.tick(deadline)
    return scrolls


class StartingAGlideTest(unittest.TestCase):
    def setUp(self):
        self.machine = MomentumMachine()

    def test_scrolling_itself_produces_nothing(self):
        _, actions = scroll(self.machine, 0.0, 200.0)
        self.assertEqual(actions, [])
        self.assertIsNone(self.machine.next_deadline())

    def test_flick_starts_a_glide(self):
        t, _ = scroll(self.machine, 0.0, 200.0)
        self.assertEqual(self.machine.update(t + STEP, 0, 0.0, 0.0), [])
        self.assertIsNotNone(self.machine.next_deadline())

    def test_slow_lift_does_not_glide(self):
        flick(self.machine, 0.0, m.GLIDE_MIN_MM_S / 2)
        self.assertIsNone(self.machine.next_deadline())
        self.assertEqual(self.machine.tick(10.0), [])

    def test_pausing_before_the_lift_does_not_glide(self):
        t, _ = scroll(self.machine, 0.0, 200.0)
        self.machine.update(t + 0.3, 0, 0.0, 0.0)
        self.assertIsNone(self.machine.next_deadline())

    def test_fingers_lifting_one_frame_apart_still_glide(self):
        t, _ = scroll(self.machine, 0.0, 200.0)
        self.machine.update(t + STEP, 1, 50.0, 40.0)
        self.machine.update(t + 2 * STEP, 0, 0.0, 0.0)
        self.assertIsNotNone(self.machine.next_deadline())

    def test_touch_that_ever_had_three_fingers_does_not_glide(self):
        t, _ = scroll(self.machine, 0.0, 200.0, count=3)
        t, _ = scroll(self.machine, 0.0, 200.0, start=t + STEP)
        self.machine.update(t + STEP, 0, 0.0, 0.0)
        self.assertIsNone(self.machine.next_deadline())

    def test_gliding_works_again_after_a_three_finger_touch(self):
        t, _ = scroll(self.machine, 0.0, 200.0, count=3)
        self.machine.update(t + STEP, 0, 0.0, 0.0)
        flick(self.machine, 0.0, 200.0, start=t + 1.0)
        self.assertIsNotNone(self.machine.next_deadline())

    def test_one_finger_does_not_glide(self):
        t, _ = scroll(self.machine, 0.0, 200.0, count=1)
        self.machine.update(t + STEP, 0, 0.0, 0.0)
        self.assertIsNone(self.machine.next_deadline())

    def test_centroid_jump_from_regrouping_is_not_speed(self):
        self.machine.update(0.000, 2, 50.0, 25.0)
        self.machine.update(0.030, 2, 50.0, 25.0)
        self.machine.update(0.037, 2, 80.0, 25.0, regrouped=True)
        self.machine.update(0.044, 2, 80.0, 25.0)
        self.machine.update(0.051, 0, 0.0, 0.0)
        self.assertIsNone(self.machine.next_deadline())

    def test_second_finger_landing_is_not_speed(self):
        # One finger at x=20, then a second lands: the centroid jumps to 50.
        self.machine.update(0.000, 1, 20.0, 25.0)
        self.machine.update(0.030, 1, 20.0, 25.0)
        self.machine.update(0.037, 2, 50.0, 25.0)
        self.machine.update(0.044, 2, 50.0, 25.0)
        self.machine.update(0.051, 0, 0.0, 0.0)
        self.assertIsNone(self.machine.next_deadline())


class NotAScrollTest(unittest.TestCase):
    """Two contacts whose centroid moves fast, without libinput scrolling."""

    def setUp(self):
        self.machine = MomentumMachine()

    def test_two_fingers_moving_together_glide(self):
        t = touch(self.machine, [line((40.0, 10.0), (0.0, 200.0)),
                                 line((60.0, 12.0), (0.0, 200.0))])
        lift(self.machine, t)
        self.assertIsNotNone(self.machine.next_deadline())

    def test_fingers_at_somewhat_different_speeds_still_glide(self):
        t = touch(self.machine, [line((40.0, 10.0), (0.0, 200.0)),
                                 line((60.0, 12.0), (10.0, 150.0))])
        lift(self.machine, t)
        self.assertIsNotNone(self.machine.next_deadline())

    def test_resting_thumb_and_moving_finger_do_not_glide(self):
        t = touch(self.machine, [line((30.0, 45.0), (0.0, 0.0)),
                                 line((60.0, 10.0), (0.0, 100.0))])
        lift(self.machine, t)
        self.assertIsNone(self.machine.next_deadline())

    def test_uneven_pinch_does_not_glide(self):
        t = touch(self.machine, [line((45.0, 25.0), (-60.0, 0.0)),
                                 line((55.0, 25.0), (160.0, 0.0))])
        lift(self.machine, t)
        self.assertIsNone(self.machine.next_deadline())

    def test_sloppy_two_finger_tap_does_not_glide(self):
        for i in range(5):
            self.machine.update(i * STEP, 2, 50.0, 25.0 + 0.3 * i)
        self.machine.update(5 * STEP, 0, 0.0, 0.0)
        self.assertIsNone(self.machine.next_deadline())

    def test_touch_with_the_pad_clicked_does_not_glide(self):
        paths = [line((40.0, 10.0), (0.0, 200.0)),
                 line((60.0, 12.0), (0.0, 200.0))]
        t = touch(self.machine, [p[:5] for p in paths], pressed=True)
        t = touch(self.machine, [p[5:] for p in paths], start=t + STEP)
        lift(self.machine, t)
        self.assertIsNone(self.machine.next_deadline())

    def test_flick_straight_after_a_drag_does_not_glide(self):
        t, _ = scroll(self.machine, 0.0, 200.0, count=3)
        dropped = lift(self.machine, t)
        flick(self.machine, 0.0, 200.0, start=dropped + 0.05)
        self.assertIsNone(self.machine.next_deadline())

    def test_flick_well_after_a_drag_glides(self):
        t, _ = scroll(self.machine, 0.0, 200.0, count=3)
        dropped = lift(self.machine, t)
        flick(self.machine, 0.0, 200.0, start=dropped + m.SPOIL_LINGER_S + 0.05)
        self.assertIsNotNone(self.machine.next_deadline())


class InterruptTest(unittest.TestCase):
    """The daemon interrupts the machines when the kernel dropped events."""

    def setUp(self):
        self.machine = MomentumMachine()

    def test_interrupt_mid_scroll_does_not_glide(self):
        t, _ = scroll(self.machine, 0.0, 200.0)
        self.assertEqual(self.machine.interrupt(t + STEP), [])
        self.assertIsNone(self.machine.next_deadline())
        self.machine.update(t + 2 * STEP, 0, 0.0, 0.0)
        self.assertIsNone(self.machine.next_deadline())

    def test_interrupt_ends_a_glide(self):
        lifted = flick(self.machine, 0.0, 200.0)
        self.machine.interrupt(lifted + 0.05)
        self.assertIsNone(self.machine.next_deadline())
        self.assertEqual(self.machine.tick(lifted + 0.1), [])

    def test_gliding_works_again_after_an_interrupt(self):
        t, _ = scroll(self.machine, 0.0, 200.0, count=3)
        self.machine.interrupt(t + STEP)
        flick(self.machine, 0.0, 200.0, start=t + 1.0)
        self.assertIsNotNone(self.machine.next_deadline())


class GlidingTest(unittest.TestCase):
    def setUp(self):
        self.machine = MomentumMachine()

    def test_tick_before_the_deadline_produces_nothing(self):
        lifted = flick(self.machine, 0.0, 200.0)
        self.assertEqual(self.machine.tick(lifted), [])

    def test_distance_is_starting_speed_times_the_time_constant(self):
        flick(self.machine, 0.0, 200.0)
        travelled = sum(s.dy for s in glide(self.machine))
        start = 200.0 * m.SCROLL_UNITS_PER_MM
        expected = (start - m.GLIDE_STOP_UNITS_S) * m.GLIDE_TAU_S
        self.assertAlmostEqual(travelled, expected, delta=expected * 0.03)

    def test_harder_flick_travels_further(self):
        flick(self.machine, 0.0, 100.0)
        gentle = sum(s.dy for s in glide(self.machine))
        other = MomentumMachine()
        flick(other, 0.0, 300.0)
        hard = sum(s.dy for s in glide(other))
        self.assertGreater(hard, 3.0 * gentle)
        self.assertLess(hard, 3.5 * gentle)

    def test_glide_slows_down_all_the_way(self):
        flick(self.machine, 0.0, 200.0)
        steps = [s.dy for s in glide(self.machine)]
        self.assertGreater(len(steps), 50)
        self.assertEqual(steps, sorted(steps, reverse=True))
        self.assertGreater(steps[-1], 0.0)

    def test_glide_is_over_within_two_seconds(self):
        lifted = flick(self.machine, 0.0, 200.0)
        last = lifted
        while self.machine.next_deadline() is not None:
            last = self.machine.next_deadline()
            self.machine.tick(last)
        self.assertGreater(last - lifted, 1.0)
        self.assertLess(last - lifted, 2.0)

    def test_glide_never_crawls(self):
        flick(self.machine, 0.0, 200.0)
        slowest = glide(self.machine)[-1].dy / m.GLIDE_FRAME_S
        self.assertGreaterEqual(slowest, m.GLIDE_STOP_UNITS_S)
        self.assertGreaterEqual(m.GLIDE_STOP_UNITS_S, 3 * 120)

    def test_late_tick_does_not_deliver_a_lump(self):
        lifted = flick(self.machine, 0.0, 200.0)
        (scroll_step,) = self.machine.tick(lifted + 0.15)
        start = 200.0 * m.SCROLL_UNITS_PER_MM
        self.assertLessEqual(scroll_step.dy, start * 3 * m.GLIDE_FRAME_S)
        self.assertGreater(scroll_step.dy, 0.0)

    def test_absurd_speed_is_capped(self):
        flick(self.machine, 0.0, 5000.0)
        travelled = sum(s.dy for s in glide(self.machine))
        start = m.GLIDE_MAX_MM_S * m.SCROLL_UNITS_PER_MM
        expected = (start - m.GLIDE_STOP_UNITS_S) * m.GLIDE_TAU_S
        self.assertAlmostEqual(travelled, expected, delta=expected * 0.03)

    def test_glide_ends(self):
        flick(self.machine, 0.0, 200.0)
        glide(self.machine)
        self.assertIsNone(self.machine.next_deadline())
        self.assertEqual(self.machine.tick(100.0), [])

    def test_any_touch_stops_the_glide(self):
        lifted = flick(self.machine, 0.0, 200.0)
        self.assertNotEqual(self.machine.tick(self.machine.next_deadline()), [])
        self.assertEqual(self.machine.update(lifted + 0.1, 1, 10.0, 10.0), [])
        self.assertIsNone(self.machine.next_deadline())
        self.assertEqual(self.machine.tick(lifted + 0.2), [])

    def test_stopping_a_glide_does_not_start_another(self):
        lifted = flick(self.machine, 0.0, 200.0)
        self.machine.update(lifted + 0.05, 1, 10.0, 10.0)
        self.machine.update(lifted + 0.06, 0, 0.0, 0.0)
        self.assertIsNone(self.machine.next_deadline())


class DirectionTest(unittest.TestCase):
    """Natural scrolling: the page follows the fingers."""

    def glide_after(self, vx, vy):
        machine = MomentumMachine()
        flick(machine, vx, vy)
        scrolls = glide(machine)
        return sum(s.dx for s in scrolls), sum(s.dy for s in scrolls)

    def test_fingers_moving_down_turn_the_wheel_up(self):
        dx, dy = self.glide_after(0.0, 200.0)
        self.assertGreater(dy, 0.0)
        self.assertEqual(dx, 0.0)

    def test_fingers_moving_up_turn_the_wheel_down(self):
        dx, dy = self.glide_after(0.0, -200.0)
        self.assertLess(dy, 0.0)
        self.assertEqual(dx, 0.0)

    def test_fingers_moving_right_scroll_left(self):
        dx, dy = self.glide_after(200.0, 0.0)
        self.assertLess(dx, 0.0)
        self.assertEqual(dy, 0.0)

    def test_fingers_moving_left_scroll_right(self):
        dx, dy = self.glide_after(-200.0, 0.0)
        self.assertGreater(dx, 0.0)
        self.assertEqual(dy, 0.0)

    def test_mostly_vertical_flick_glides_straight(self):
        dx, dy = self.glide_after(30.0, 200.0)
        self.assertEqual(dx, 0.0)
        self.assertGreater(dy, 0.0)

    def test_diagonal_flick_glides_along_the_stronger_axis(self):
        dx, dy = self.glide_after(150.0, 200.0)
        self.assertEqual(dx, 0.0)
        self.assertGreater(dy, 0.0)
        dx, dy = self.glide_after(200.0, 150.0)
        self.assertLess(dx, 0.0)
        self.assertEqual(dy, 0.0)

    def test_glide_keeps_the_speed_along_its_axis_not_the_diagonal_speed(self):
        _, straight = self.glide_after(0.0, 200.0)
        _, diagonal = self.glide_after(150.0, 200.0)
        self.assertAlmostEqual(diagonal, straight, delta=straight * 0.01)


class ScrollTest(unittest.TestCase):
    def test_scroll_is_a_value(self):
        self.assertEqual(Scroll(1.0, 2.0), Scroll(1.0, 2.0))


if __name__ == "__main__":
    unittest.main()
