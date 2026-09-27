import unittest

from evdev import ecodes as e

from finger_drag.slots import MT_TOOL_PALM, Frame, SlotTracker, units_per_mm


def touch(tracker, slot, tracking_id, x=None, y=None):
    tracker.feed(e.EV_ABS, e.ABS_MT_SLOT, slot)
    tracker.feed(e.EV_ABS, e.ABS_MT_TRACKING_ID, tracking_id)
    if x is not None:
        tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_X, x)
    if y is not None:
        tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_Y, y)


def report(tracker):
    return tracker.feed(e.EV_SYN, e.SYN_REPORT, 0)


class SlotTrackerTest(unittest.TestCase):
    def setUp(self):
        self.tracker = SlotTracker(10.0, 20.0)

    def test_no_fingers(self):
        self.assertEqual(report(self.tracker), Frame(0, 0.0, 0.0))

    def test_abs_events_return_nothing_until_report(self):
        self.assertIsNone(self.tracker.feed(e.EV_ABS, e.ABS_MT_TRACKING_ID, 1))
        self.assertIsNone(self.tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_X, 100))

    def test_one_finger_in_millimetres(self):
        touch(self.tracker, 0, 1, 100, 200)
        self.assertEqual(report(self.tracker), Frame(1, 10.0, 10.0))

    def test_three_finger_centroid(self):
        touch(self.tracker, 0, 1, 100, 200)
        touch(self.tracker, 1, 2, 200, 400)
        touch(self.tracker, 2, 3, 300, 600)
        self.assertEqual(report(self.tracker), Frame(3, 20.0, 20.0))

    def test_motion_updates_only_the_selected_slot(self):
        touch(self.tracker, 0, 1, 100, 200)
        touch(self.tracker, 1, 2, 300, 200)
        report(self.tracker)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_SLOT, 0)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_X, 200)
        self.assertEqual(report(self.tracker), Frame(2, 25.0, 10.0))

    def test_lift_removes_finger(self):
        touch(self.tracker, 0, 1, 100, 200)
        touch(self.tracker, 1, 2, 300, 600)
        report(self.tracker)
        touch(self.tracker, 0, -1)
        self.assertEqual(report(self.tracker), Frame(1, 30.0, 30.0))

    def test_new_touch_reuses_retained_position(self):
        touch(self.tracker, 0, 1, 100, 200)
        report(self.tracker)
        touch(self.tracker, 0, -1)
        self.assertEqual(report(self.tracker), Frame(0, 0.0, 0.0))
        touch(self.tracker, 0, 2)
        self.assertEqual(report(self.tracker), Frame(1, 10.0, 10.0))

    def test_finger_with_unknown_position_is_not_counted(self):
        touch(self.tracker, 0, 5)
        self.assertEqual(report(self.tracker).count, 0)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_X, 100)
        self.assertEqual(report(self.tracker).count, 0)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_Y, 200)
        self.assertEqual(report(self.tracker), Frame(1, 10.0, 10.0))

    def test_starts_from_the_given_current_slot(self):
        tracker = SlotTracker(10.0, 20.0, current_slot=2)
        tracker.feed(e.EV_ABS, e.ABS_MT_TRACKING_ID, 7)
        tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_X, 100)
        tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_Y, 200)
        touch(tracker, 0, 8, 300, 600)
        self.assertEqual(report(tracker), Frame(2, 20.0, 20.0))

    def test_reset_forgets_fingers_and_sets_slot(self):
        touch(self.tracker, 0, 1, 100, 200)
        report(self.tracker)
        self.tracker.reset(1)
        self.assertEqual(report(self.tracker), Frame(0, 0.0, 0.0))
        self.tracker.feed(e.EV_ABS, e.ABS_MT_TRACKING_ID, 9)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_X, 300)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_POSITION_Y, 600)
        touch(self.tracker, 0, 10, 100, 200)
        self.assertEqual(report(self.tracker), Frame(2, 20.0, 20.0))

    def test_palm_contact_is_not_counted(self):
        touch(self.tracker, 0, 1, 100, 200)
        touch(self.tracker, 1, 2, 300, 600)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_TOOL_TYPE, MT_TOOL_PALM)
        self.assertEqual(report(self.tracker), Frame(1, 10.0, 10.0))

    def test_finger_reclassified_as_palm_and_back(self):
        touch(self.tracker, 0, 1, 100, 200)
        self.assertEqual(report(self.tracker).count, 1)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_TOOL_TYPE, MT_TOOL_PALM)
        self.assertEqual(report(self.tracker).count, 0)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_TOOL_TYPE, 0)
        self.assertEqual(report(self.tracker), Frame(1, 10.0, 10.0))

    def test_finger_in_a_slot_that_held_a_palm_is_counted(self):
        touch(self.tracker, 0, 1, 100, 200)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_TOOL_TYPE, MT_TOOL_PALM)
        touch(self.tracker, 0, -1)
        report(self.tracker)
        touch(self.tracker, 0, 2)
        self.tracker.feed(e.EV_ABS, e.ABS_MT_TOOL_TYPE, 0)
        self.assertEqual(report(self.tracker), Frame(1, 10.0, 10.0))

    def test_begin_resync_forgets_fingers_until_reset(self):
        touch(self.tracker, 0, 1, 100, 200)
        self.tracker.begin_resync()
        self.assertTrue(self.tracker.resyncing)
        self.assertEqual(report(self.tracker), Frame(0, 0.0, 0.0))
        self.tracker.reset(0)
        self.assertFalse(self.tracker.resyncing)

    def test_unrelated_events_are_ignored(self):
        self.assertIsNone(self.tracker.feed(e.EV_KEY, e.BTN_LEFT, 1))
        self.assertIsNone(self.tracker.feed(e.EV_ABS, e.ABS_X, 500))
        self.assertIsNone(self.tracker.feed(e.EV_SYN, e.SYN_DROPPED, 0))
        self.assertEqual(report(self.tracker), Frame(0, 0.0, 0.0))


class UnitsPerMmTest(unittest.TestCase):
    def test_uses_reported_resolution(self):
        self.assertEqual(units_per_mm(0, 1300, 12, 100.0), 12.0)

    def test_zero_resolution_falls_back_to_assumed_size(self):
        self.assertEqual(units_per_mm(0, 1300, 0, 100.0), 13.0)

    def test_fallback_honours_non_zero_minimum(self):
        self.assertEqual(units_per_mm(100, 1100, 0, 100.0), 10.0)

    def test_no_resolution_and_no_range_is_an_error(self):
        with self.assertRaises(ValueError):
            units_per_mm(0, 0, 0, 100.0)


if __name__ == "__main__":
    unittest.main()
