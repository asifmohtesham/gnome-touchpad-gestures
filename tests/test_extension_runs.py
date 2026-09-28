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
    (pathlib.Path(directory) / "extension.js").write_text(source)
    for name in ("clutter", "extension", "main"):
        shutil.copy(REPO / "tests/js/stand_ins" / f"{name}.js",
                    pathlib.Path(directory) / f"stand_in_{name}.js")


@unittest.skipUnless(all(shutil.which(tool) for tool in NEEDED),
                     "needs gjs and dbus-run-session")
class ExtensionRunsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
                 os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")],
                capture_output=True, text=True, timeout=90, env=environment)
        cls.failure = None if result.returncode == 0 else (
            f"exit {result.returncode}\n{result.stdout}\n{result.stderr}")
        cls.seen = json.loads(result.stdout.splitlines()[-1]) if not cls.failure else {}

    def setUp(self):
        if self.failure:
            self.fail(self.failure)

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


class StandInsTest(unittest.TestCase):
    def test_every_import_from_the_shell_has_a_stand_in(self):
        with tempfile.TemporaryDirectory() as directory:
            copy_with_stand_ins(directory)
            files = sorted(p.name for p in pathlib.Path(directory).iterdir())
        self.assertEqual(files, ["extension.js", "gestures.js", "metadata.json",
                                 "stand_in_clutter.js", "stand_in_extension.js",
                                 "stand_in_main.js"])


if __name__ == "__main__":
    unittest.main()
