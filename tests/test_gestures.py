import math
import unittest

from gnome_x11_touchpad_gestures import gestures as g
from gnome_x11_touchpad_gestures.gestures import (
    ButtonDown, ButtonUp, Direction, GestureMachine, Move, Overview, Snap,
    State, SwipeBegin, SwipeCancel, SwipeEnd, SwipeMove, SwitchWorkspace)


SWIPE = (SwipeBegin, SwipeMove, SwipeEnd, SwipeCancel)


def snapped(machine, actions):
    """What a desktop that cannot follow the fingers makes of these actions.

    A sideways swipe is reported as it goes, for a shell that moves the
    workspace along with it. Without such a shell the swipe is turned into
    one switch of workspace, which is what most tests here ask about.
    """
    snap = machine.__dict__.setdefault("snap_for_tests", Snap())
    seen = []
    for action in actions:
        seen += snap.feed(action) if isinstance(action, SWIPE) else [action]
    return seen


def raw(machine, frames):
    """Feed (t, count, cx, cy) frames; return every action exactly as made."""
    actions = []
    for t, count, cx, cy in frames:
        actions += machine.update(t, count, cx, cy)
    return actions


def feed(machine, frames):
    """Feed (t, count, cx, cy) frames; return every action in order."""
    return snapped(machine, raw(machine, frames))


def start_drag(machine):
    """Three fingers land at (50, 25) mm and travel 2.5 mm right."""
    return feed(machine, [
        (0.00, 3, 50.0, 25.0),
        (0.03, 3, 51.0, 25.0),
        (0.06, 3, 52.5, 25.0),
    ])


def line(origin, velocity, frames=12, step=0.01):
    """One finger's positions, a frame apart, moving at `velocity` mm/s."""
    return [(origin[0] + velocity[0] * i * step, origin[1] + velocity[1] * i * step)
            for i in range(frames)]


def touch(machine, paths, start=0.0, step=0.01):
    """Several fingers, each following its own path. Returns every action."""
    actions = []
    for i, positions in enumerate(zip(*paths)):
        cx = sum(p[0] for p in positions) / len(positions)
        cy = sum(p[1] for p in positions) / len(positions)
        actions += machine.update(start + i * step, len(positions), cx, cy,
                                  fingers=tuple(positions))
    return snapped(machine, actions)


def row(y, velocity, count=4, **kwargs):
    """Fingers side by side, 15 mm apart, all moving alike."""
    return [line((20.0 + 15.0 * i, y), velocity, **kwargs) for i in range(count)]


class FollowingTest(unittest.TestCase):
    """A sideways swipe is reported as it goes, so that the workspace can move
    under the fingers."""

    def setUp(self):
        self.machine = GestureMachine()

    def swipe(self, *xs, y=25.0, start=0.0):
        return raw(self.machine, [
            (start + i * 0.01, 4, x, y) for i, x in enumerate(xs)])

    def test_nothing_is_said_until_the_swipe_is_under_way(self):
        self.assertEqual(self.swipe(60.0, 58.0, 57.0), [])
        self.assertIs(self.machine.state, State.SWIPE_TRACKING)

    def test_swipe_begins_with_the_travel_made_so_far(self):
        actions = self.swipe(60.0, 58.0, 55.0)
        self.assertEqual(actions, [SwipeBegin(0.02), SwipeMove(0.02, -5.0)])
        self.assertIs(self.machine.state, State.SWIPE_FOLLOWING)

    def test_every_frame_after_reports_how_far_the_fingers_went(self):
        self.swipe(60.0, 55.0)
        self.assertEqual(self.machine.update(0.02, 4, 52.5, 25.0),
                         [SwipeMove(0.02, -2.5)])
        self.assertEqual(self.machine.update(0.03, 4, 51.0, 25.5),
                         [SwipeMove(0.03, -1.5)])

    def test_fingers_that_did_not_move_report_nothing(self):
        self.swipe(60.0, 55.0)
        self.assertEqual(self.machine.update(0.02, 4, 55.0, 25.0), [])

    def test_fingers_may_turn_back(self):
        self.swipe(60.0, 50.0)
        self.assertEqual(self.machine.update(0.02, 4, 56.0, 25.0),
                         [SwipeMove(0.02, 6.0)])

    def test_nothing_of_the_travel_is_lost(self):
        actions = self.swipe(80.0, 79.0, 77.0, 72.0, 64.0, 50.0, 41.0, 40.5)
        moved = sum(a.dx for a in actions if isinstance(a, SwipeMove))
        self.assertAlmostEqual(moved, 40.5 - 80.0)

    def test_lifting_every_finger_ends_the_swipe(self):
        self.swipe(60.0, 50.0)
        self.assertEqual(self.machine.update(0.5, 0, 0.0, 0.0), [SwipeEnd(0.5)])
        self.assertIs(self.machine.state, State.IDLE)

    def test_swipe_is_begun_and_ended_once_each(self):
        actions = self.swipe(60.0, 50.0, 40.0, 30.0)
        actions += self.machine.update(0.5, 0, 0.0, 0.0)
        actions += self.machine.update(0.6, 0, 0.0, 0.0)
        kinds = [type(a).__name__ for a in actions]
        self.assertEqual(kinds.count("SwipeBegin"), 1)
        self.assertEqual(kinds.count("SwipeEnd"), 1)
        self.assertEqual(kinds[0], "SwipeBegin")
        self.assertEqual(kinds[-1], "SwipeEnd")

    def test_fingers_lifting_one_after_another_end_it_when_all_are_up(self):
        self.swipe(60.0, 50.0)
        self.assertEqual(self.machine.update(0.02, 3, 45.0, 25.0), [])
        self.assertEqual(self.machine.update(0.03, 2, 30.0, 25.0), [])
        self.assertEqual(self.machine.update(0.04, 0, 0.0, 0.0), [SwipeEnd(0.04)])

    def test_finger_that_slips_off_and_returns_does_not_jolt_the_workspace(self):
        self.swipe(60.0, 50.0)
        self.machine.update(0.02, 3, 20.0, 25.0)
        self.assertEqual(self.machine.update(0.03, 4, 49.0, 25.0), [])
        self.assertEqual(self.machine.update(0.04, 4, 47.0, 25.0),
                         [SwipeMove(0.04, -2.0)])

    def test_lost_finger_state_puts_the_workspace_back(self):
        self.swipe(60.0, 50.0)
        self.assertEqual(self.machine.interrupt(0.5), [SwipeCancel()])
        self.assertIs(self.machine.state, State.IDLE)

    def test_swipe_up_is_not_followed(self):
        actions = raw(self.machine, [
            (0.00, 4, 60.0, 40.0), (0.01, 4, 60.0, 30.0), (0.02, 4, 60.0, 20.0)])
        self.assertEqual(actions, [Overview(show=True)])

    def test_swipe_that_set_off_sideways_cannot_open_the_overview(self):
        actions = self.swipe(60.0, 50.0)
        actions += raw(self.machine, [(0.02, 4, 50.0, 5.0)])
        self.assertNotIn(Overview(show=True), actions)
        self.assertNotIn(Overview(show=False), actions)

    def test_new_swipe_begins_afresh(self):
        self.swipe(60.0, 50.0)
        self.machine.update(0.5, 0, 0.0, 0.0)
        actions = self.swipe(30.0, 36.0, start=1.0)
        self.assertEqual(actions, [SwipeBegin(1.01), SwipeMove(1.01, 6.0)])


class SnapTest(unittest.TestCase):
    """One switch of workspace out of a swipe, where nothing can follow it."""

    def setUp(self):
        self.snap = Snap()

    def feed(self, *actions):
        return [made for action in actions for made in self.snap.feed(action)]

    def test_swipe_far_enough_to_the_left_is_the_next_workspace(self):
        self.assertEqual(
            self.feed(SwipeBegin(0.0), SwipeMove(0.0, -8.0), SwipeMove(0.1, -8.0)),
            [SwitchWorkspace(Direction.NEXT)])

    def test_swipe_far_enough_to_the_right_is_the_previous_one(self):
        self.assertEqual(
            self.feed(SwipeBegin(0.0), SwipeMove(0.0, 16.0)),
            [SwitchWorkspace(Direction.PREVIOUS)])

    def test_short_swipe_is_nothing(self):
        self.assertEqual(
            self.feed(SwipeBegin(0.0), SwipeMove(0.0, -14.0), SwipeEnd(0.2)), [])

    def test_only_one_switch_to_a_swipe(self):
        self.assertEqual(
            self.feed(SwipeBegin(0.0), SwipeMove(0.0, -20.0), SwipeMove(0.1, -20.0),
                      SwipeMove(0.2, 60.0)),
            [SwitchWorkspace(Direction.NEXT)])

    def test_each_swipe_is_counted_from_nothing(self):
        self.feed(SwipeBegin(0.0), SwipeMove(0.0, -14.0), SwipeEnd(0.2))
        self.assertEqual(self.feed(SwipeBegin(1.0), SwipeMove(1.0, -14.0)), [])

    def test_moves_without_a_swipe_are_nothing(self):
        self.assertEqual(self.feed(SwipeMove(0.0, -40.0)), [])


class SwipeNeedsEveryFingerTest(unittest.TestCase):
    def setUp(self):
        self.machine = GestureMachine()

    def test_four_fingers_moving_up_together_open_the_overview(self):
        self.assertEqual(touch(self.machine, row(40.0, (0.0, -200.0))),
                         [Overview(show=True)])

    def test_four_fingers_moving_left_together_switch_workspace(self):
        self.assertEqual(touch(self.machine, row(25.0, (-200.0, 0.0))),
                         [SwitchWorkspace(Direction.NEXT)])

    def test_one_finger_moving_among_three_resting_does_nothing(self):
        paths = row(45.0, (0.0, 0.0), count=3, frames=40)
        paths.append(line((80.0, 45.0), (0.0, -200.0), frames=40))
        self.assertEqual(touch(self.machine, paths), [])
        self.assertIs(self.machine.state, State.SWIPE_TRACKING)

    def test_two_fingers_scrolling_beside_two_resting_does_nothing(self):
        paths = row(45.0, (0.0, 0.0), count=2, frames=30)
        paths += [line((60.0, 40.0), (0.0, -200.0), frames=30),
                  line((75.0, 40.0), (0.0, -200.0), frames=30)]
        self.assertEqual(touch(self.machine, paths), [])

    def test_one_finger_moving_sideways_among_three_resting_does_nothing(self):
        paths = row(45.0, (0.0, 0.0), count=3, frames=40)
        paths.append(line((90.0, 20.0), (-200.0, 0.0), frames=40))
        self.assertEqual(touch(self.machine, paths), [])

    def test_stray_fourth_contact_during_a_drag_does_not_open_the_overview(self):
        drag = row(45.0, (0.0, -200.0), count=3, frames=10, step=0.01)
        actions = touch(self.machine, drag)
        self.assertEqual(actions[0], ButtonDown())
        carried_on = [line(path[-1], (0.0, -200.0), frames=15) for path in drag]
        carried_on.append(line((95.0, 48.0), (0.0, 0.0), frames=15))
        actions = touch(self.machine, carried_on, start=0.1)
        self.assertEqual(actions, [ButtonUp()])

    def test_fifth_finger_landing_mid_swipe_still_switches_once(self):
        first = row(25.0, (-200.0, 0.0), frames=5)
        actions = touch(self.machine, first)
        second = [line(path[-1], (-200.0, 0.0), frames=8) for path in first]
        second.append(line((95.0, 25.0), (-200.0, 0.0), frames=8))
        actions += touch(self.machine, second, start=0.05)
        self.assertEqual(actions, [SwitchWorkspace(Direction.NEXT)])

    def test_travel_made_beside_resting_fingers_never_counts(self):
        # Two fingers scroll 28 mm beside two resting ones; one resting finger
        # lifts and lands again; then all four move 3.5 mm together.
        beside = row(45.0, (0.0, 0.0), count=2, frames=15)
        beside += [line((60.0, 40.0), (0.0, -200.0), frames=15),
                   line((75.0, 40.0), (0.0, -200.0), frames=15)]
        actions = touch(self.machine, beside)
        ends = [path[-1] for path in beside]
        self.machine.update(0.15, 3, 50.0, 30.0, fingers=tuple(ends[1:]))
        together = [line(end, (0.0, -350.0), frames=2) for end in ends]
        actions += touch(self.machine, together, start=0.16)
        self.assertEqual(actions, [])

    def test_contact_that_comes_and_goes_does_not_stop_a_swipe(self):
        # A fifth contact appears and vanishes every 21 ms while four fingers
        # travel 59 mm. Only the stretches without it count, which is half.
        actions = []
        t = 0.0
        x = 80.0
        for burst in range(14):
            fingers = [(x - 15.0 * i, 25.0) for i in range(4)]
            if burst % 2:
                fingers.append((95.0, 45.0))
            for _ in range(3):
                centre = (sum(f[0] for f in fingers) / len(fingers),
                          sum(f[1] for f in fingers) / len(fingers))
                actions += snapped(self.machine, self.machine.update(
                    t, len(fingers), *centre, fingers=tuple(fingers)))
                t += 0.007
                x -= 1.4
                fingers = [(fx - 1.4, fy) for fx, fy in fingers[:4]] + fingers[4:]
        self.assertEqual(actions, [SwitchWorkspace(Direction.NEXT)])

    def test_finger_swapped_for_a_resting_one_stops_counting(self):
        moving = row(25.0, (-200.0, 0.0), frames=2)
        actions = touch(self.machine, moving)
        ends = [path[-1] for path in moving]
        # Same count, different fingers: the last one is replaced by a
        # contact that then rests while the other three carry on. It lands
        # ahead of the swipe, so measured from where the old finger was it
        # would look as if it had moved a long way with the others.
        paths = [line(end, (-200.0, 0.0), frames=20) for end in ends[:3]]
        paths.append(line((5.0, 45.0), (0.0, 0.0), frames=20))
        for i, positions in enumerate(zip(*paths)):
            cx = sum(p[0] for p in positions) / 4
            cy = sum(p[1] for p in positions) / 4
            actions += snapped(self.machine, self.machine.update(
                0.02 + i * 0.01, 4, cx, cy, regrouped=(i == 0),
                fingers=tuple(positions)))
        self.assertEqual(actions, [])

    def test_swipe_fires_at_the_threshold_and_not_before(self):
        paths = row(25.0, (-250.0, 0.0), frames=7)        # 2.5 mm a frame
        self.assertEqual(touch(self.machine, [p[:6] for p in paths]), [])
        last = [p[6:] for p in paths]
        self.assertEqual(touch(self.machine, last, start=0.06),
                         [SwitchWorkspace(Direction.NEXT)])

    def test_new_set_of_contacts_drops_travel_not_yet_shared(self):
        # 14 mm of the centroid made by two fingers beside two resting ones,
        # then a contact is replaced, then all four move 3 mm together.
        beside = row(45.0, (0.0, 0.0), count=2, frames=15)
        beside += [line((60.0, 40.0), (0.0, -200.0), frames=15),
                   line((75.0, 40.0), (0.0, -200.0), frames=15)]
        actions = touch(self.machine, beside)
        together = [line(path[-1], (0.0, -300.0), frames=2) for path in beside]
        for i, positions in enumerate(zip(*together)):
            cx = sum(p[0] for p in positions) / 4
            cy = sum(p[1] for p in positions) / 4
            actions += snapped(self.machine, self.machine.update(
                0.15 + i * 0.01, 4, cx, cy, regrouped=(i == 0),
                fingers=tuple(positions)))
        self.assertEqual(actions, [])

    def test_fingers_fanning_slightly_still_swipe(self):
        paths = [line((20.0, 40.0), (-30.0, -200.0)),
                 line((35.0, 40.0), (-10.0, -220.0)),
                 line((50.0, 40.0), (10.0, -180.0)),
                 line((65.0, 40.0), (30.0, -160.0))]
        self.assertEqual(touch(self.machine, paths), [Overview(show=True)])


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
        self.assertIs(self.machine.state, State.SWIPE_FOLLOWING)

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
        self.assertEqual(snapped(self.machine, self.machine.update(
            0.02, 4, 30.0, 25.0, regrouped=True)), [])
        self.assertEqual(snapped(self.machine, self.machine.update(
            0.03, 4, 22.0, 25.0)), [SwitchWorkspace(Direction.NEXT)])

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
        self.assertIs(self.machine.state, State.SWIPE_FOLLOWING)

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
