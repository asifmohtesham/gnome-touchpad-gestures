"""Runs the real extension.js, which no other test does.

It cannot be imported outside a shell as it stands, because it imports from
the shell. Here those imports are pointed at stand-ins, in a copy, and
the copy is run with gjs on a message bus of its own. The daemon's own client
then talks to it, so that both ends of the conversation are the real ones.
"""
import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

import gnome_x11_touchpad_gestures

REPO = pathlib.Path(__file__).resolve().parent.parent
UUID = "gnome-x11-touchpad-gestures@asifmohtesham.github.io"
IMPORTS = {
    "'gi://Clutter'": "'./stand_in_clutter.js'",
    "'gi://Meta'": "'./stand_in_meta.js'",
    "'resource:///org/gnome/shell/extensions/extension.js'": "'./stand_in_extension.js'",
    "'resource:///org/gnome/shell/ui/main.js'": "'./stand_in_main.js'",
}
NEEDED = ("gjs", "dbus-run-session")


def copy_with_stand_ins(directory):
    for file in (REPO / "extension" / UUID).iterdir():
        shutil.copy(file, directory)
    source = (pathlib.Path(directory) / "extension.js").read_text()
    for real, stand_in in IMPORTS.items():
        assert source.count(real) == 1, real
        source = source.replace(real, stand_in)
    assert "resource:///org/gnome/shell" not in source, "an import without a stand-in"
    assert "gi://Clutter" not in source, "an import without a stand-in"
    assert "gi://Meta" not in source, "an import without a stand-in"
    (pathlib.Path(directory) / "extension.js").write_text(source)
    for name in ("clutter", "extension", "main", "meta"):
        shutil.copy(REPO / "tests/js/stand_ins" / f"{name}.js",
                    pathlib.Path(directory) / f"stand_in_{name}.js")


def run_extension(mode):
    """Runs the extension in a session of the kind named. What happened, or why not."""
    with tempfile.TemporaryDirectory(prefix="gnome-x11-touchpad-gestures-test-") as directory:
        copy_with_stand_ins(directory)
        environment = {key: value for key, value in os.environ.items()
                       if key not in ("DBUS_SESSION_BUS_ADDRESS", "DISPLAY")}
        # Keeps gjs from starting the desktop's file services on a bus
        # that is about to go away.
        environment["GIO_USE_VFS"] = "local"
        result = subprocess.run(
            ["dbus-run-session", "--", sys.executable,
             str(REPO / "tests/helpers/drive_extension.py"), directory,
             os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""), mode],
            capture_output=True, text=True, timeout=90, env=environment)
    if result.returncode != 0:
        return {}, f"exit {result.returncode}\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout.splitlines()[-1]), None


class Ran(unittest.TestCase):
    MODE = None

    @classmethod
    def setUpClass(cls):
        cls.seen, cls.failure = run_extension(cls.MODE)

    def setUp(self):
        if self.failure:
            self.fail(self.failure)


@unittest.skipUnless(all(shutil.which(tool) for tool in NEEDED),
                     "needs gjs and dbus-run-session")
class ExtensionRunsTest(Ran):
    MODE = "x11"

    def test_it_runs_and_stops_without_complaint(self):
        self.assertEqual(self.seen["runner exit"], 0)
        self.assertEqual(self.seen["runner complaints"], "")

    def test_it_tells_its_version(self):
        self.assertEqual(self.seen["version"], gnome_x11_touchpad_gestures.__version__)

    def test_pointer_over_a_window_or_an_application_s_popup(self):
        self.assertIs(self.seen["over"]["window"], True)
        self.assertIs(self.seen["over"]["popup"], True)

    def test_pointer_where_nothing_of_the_shell_s_reacts(self):
        self.assertIs(self.seen["over"]["stage"], True)

    def test_pointer_over_the_top_bar(self):
        self.assertIs(self.seen["over"]["panel"], False)
        self.assertIs(self.seen["over"]["nothing"], False)

    def test_nothing_is_a_window_while_the_overview_is_open(self):
        self.assertIs(self.seen["over"]["window, overview open"], False)
        self.assertIs(self.seen["may glide, overview open"], False)
        self.assertIs(self.seen["may glide"], True)

    def test_picking_is_among_what_reacts_to_the_pointer_where_it_is(self):
        reactive = 1  # Clutter.PickMode.REACTIVE
        self.assertGreater(len(self.seen["picks"]), 0)
        for mode, x, y in self.seen["picks"]:
            self.assertEqual((mode, x, y), (reactive, 640, 360))

    def test_swipe_reaches_the_shell_s_tracker_as_the_daemon_sent_it(self):
        self.assertIs(self.seen["begin"], True)
        self.assertEqual(self.seen["calls"][:4], [
            ["begin", 12500, 640, 360],
            ["update", 12507, 100, 400],
            ["update", 12514, -200, 400],
            ["end", 12600, 400],
        ])

    def test_cancelled_swipe_is_put_back(self):
        self.assertIs(self.seen["begin again"], True)
        self.assertEqual(self.seen["calls"][4:6],
                         [["begin", 20000, 640, 360], ["interrupt"]])

    def test_swipe_is_declined_where_the_shell_s_own_gesture_would_be(self):
        self.assertIs(self.seen["begin, menu open"], False)
        self.assertIs(self.seen["begin, tracker off"], False)
        begun = [call[1] for call in self.seen["calls"] if call[0] == "begin"]
        self.assertNotIn(30000, begun)
        self.assertNotIn(31000, begun)

    def test_switching_the_extension_off_puts_a_swipe_under_way_back(self):
        self.assertIs(self.seen["begin, then switched off"], True)
        self.assertEqual(self.seen["calls"][-2:],
                         [["begin", 40000, 640, 360], ["interrupt"]])

    def test_switched_off_it_no_longer_answers(self):
        self.assertIsNone(self.seen["version once off"])
        self.assertIsNone(self.seen["over window once off"])

    def test_switched_on_again_it_answers_again(self):
        self.assertEqual(self.seen["version once on again"],
                         gnome_x11_touchpad_gestures.__version__)

    def test_on_x11_three_fingers_are_not_freed_and_no_swipe_is_swallowed(self):
        self.assertIs(self.seen["three fingers free"], False)
        self.assertEqual(self.seen["handlers"], 0)
        self.assertEqual(self.seen["three fingers"], [False, False, False])


@unittest.skipUnless(all(shutil.which(tool) for tool in NEEDED),
                     "needs gjs and dbus-run-session")
class OnWaylandTest(Ran):
    """Where the daemon only drags, the extension keeps the shell's swipes
    off three fingers for as long as the daemon is there."""

    MODE = "wayland"
    WHOLE = [True, True, True]
    NONE = [False, False, False]

    def test_it_runs_and_stops_without_complaint(self):
        self.assertEqual(self.seen["runner exit"], 0)
        self.assertEqual(self.seen["runner complaints"], "")

    def test_it_tells_its_version(self):
        self.assertEqual(self.seen["version"], gnome_x11_touchpad_gestures.__version__)

    def test_it_listens_on_the_stage_once(self):
        self.assertEqual(self.seen["handlers"], 1)

    def test_nothing_is_freed_or_swallowed_before_the_daemon_is_there(self):
        self.assertIs(self.seen["free before the daemon"], False)
        self.assertEqual(self.seen["swipe before the daemon"], self.NONE)

    def test_it_learns_of_the_daemon_by_its_name_on_the_bus(self):
        self.assertIs(self.seen["name taken"], True)
        self.assertIs(self.seen["learned of the daemon"], True)
        self.assertIs(self.seen["free with the daemon"], True)

    def test_swipe_of_three_fingers_is_swallowed_whole(self):
        self.assertEqual(self.seen["three fingers"], self.WHOLE)

    def test_swipes_of_other_counts_are_the_shell_s(self):
        self.assertEqual(self.seen["four fingers"], self.NONE)
        self.assertEqual(self.seen["two fingers"], self.NONE)

    def test_gesture_that_is_not_a_swipe_is_the_shell_s(self):
        self.assertIs(self.seen["pinch"], False)

    def test_cancelled_swipe_is_over(self):
        self.assertEqual(self.seen["cancelled"], [True, True, False])

    def test_phase_it_does_not_know_follows_the_swipe_it_is_in(self):
        self.assertEqual(self.seen["unknown phase"], self.WHOLE)

    def test_event_it_cannot_read_is_let_through_and_survived(self):
        self.assertIs(self.seen["broken event"], False)
        self.assertEqual(self.seen["swipe after a broken event"], self.WHOLE)

    def test_daemon_leaving_during_a_swipe_does_not_leave_half_of_one(self):
        self.assertIs(self.seen["learned the daemon went"], True)
        self.assertEqual(self.seen["swipe the daemon left during"], self.WHOLE)
        self.assertEqual(self.seen["swipe after it left"], self.NONE)

    def test_daemon_coming_during_a_swipe_does_not_cut_it_short(self):
        self.assertIs(self.seen["learned it came back"], True)
        self.assertEqual(self.seen["swipe the daemon came during"], self.NONE)
        self.assertEqual(self.seen["swipe after it came back"], self.WHOLE)

    def test_what_is_for_x11_answers_no_and_touches_nothing(self):
        self.assertIs(self.seen["over window"], False)
        self.assertIs(self.seen["may glide"], False)
        self.assertIs(self.seen["begin"], False)
        self.assertEqual(self.seen["calls"], [])
        self.assertEqual(self.seen["picks"], [])

    def test_switched_off_it_listens_to_nothing_and_swallows_nothing(self):
        self.assertEqual(self.seen["handlers once off"], 0)
        self.assertEqual(self.seen["swipe once off"], self.NONE)
        self.assertIsNone(self.seen["version once off"])

    def test_switched_on_again_it_finds_the_daemon_still_there(self):
        self.assertEqual(self.seen["version once on again"],
                         gnome_x11_touchpad_gestures.__version__)
        self.assertEqual(self.seen["handlers once on again"], 1)
        self.assertIs(self.seen["free once on again"], True)
        self.assertEqual(self.seen["swipe once on again"], self.WHOLE)


class StandInsTest(unittest.TestCase):
    def test_every_import_from_the_shell_has_a_stand_in(self):
        with tempfile.TemporaryDirectory() as directory:
            copy_with_stand_ins(directory)
            files = sorted(p.name for p in pathlib.Path(directory).iterdir())
        self.assertEqual(files, ["extension.js", "gestures.js", "metadata.json",
                                 "stand_in_clutter.js", "stand_in_extension.js",
                                 "stand_in_main.js", "stand_in_meta.js"])


if __name__ == "__main__":
    unittest.main()
