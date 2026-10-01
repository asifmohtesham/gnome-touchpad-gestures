import contextlib
import io
import pathlib
import re
import unittest

import gnome_touchpad_gestures
from gnome_touchpad_gestures import daemon

REPO = pathlib.Path(__file__).resolve().parent.parent
VERSION = gnome_touchpad_gestures.__version__


class VersionTest(unittest.TestCase):
    def test_version_is_three_numbers(self):
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+$")

    def test_daemon_tells_its_version_and_does_nothing_else(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(SystemExit) as raised:
                daemon.main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(stdout.getvalue().strip(),
                         f"gnome-touchpad-gestures {VERSION}")

    def test_newest_entry_in_the_changelog_is_this_version(self):
        changelog = (REPO / "CHANGELOG.md").read_text()
        entries = re.findall(r"^## (\d+\.\d+\.\d+)", changelog, re.MULTILINE)
        self.assertGreater(len(entries), 0)
        self.assertEqual(entries[0], VERSION)

    def test_changelog_entries_run_from_newest_to_oldest(self):
        changelog = (REPO / "CHANGELOG.md").read_text()
        entries = [tuple(int(n) for n in entry.split("."))
                   for entry in re.findall(r"^## (\d+\.\d+\.\d+)", changelog,
                                           re.MULTILINE)]
        self.assertEqual(entries, sorted(entries, reverse=True))
        self.assertEqual(len(entries), len(set(entries)))

    def test_every_changelog_entry_is_dated_or_marked_unreleased(self):
        changelog = (REPO / "CHANGELOG.md").read_text()
        for heading in re.findall(r"^## \d+\.\d+\.\d+.*$", changelog, re.MULTILINE):
            with self.subTest(heading=heading):
                self.assertRegex(
                    heading, r"^## \d+\.\d+\.\d+ \((\d{4}-\d{2}-\d{2}|unreleased)\)$")

    def test_readme_does_not_name_a_version_of_its_own(self):
        # One place to change at a release, not several.
        readme = (REPO / "README.md").read_text()
        self.assertEqual(re.findall(r"\bv?\d+\.\d+\.\d+\b", readme), [])


if __name__ == "__main__":
    unittest.main()
