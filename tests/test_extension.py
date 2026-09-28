"""The shell extension: its own tests, and that it agrees with the daemon."""
import json
import pathlib
import re
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ElementTree

import gnome_x11_touchpad_gestures
from gnome_x11_touchpad_gestures import shell

REPO = pathlib.Path(__file__).resolve().parent.parent
UUID = "gnome-x11-touchpad-gestures@asifmohtesham.github.io"
EXTENSION = REPO / "extension" / UUID
GJS = shutil.which("gjs")


def gjs(*args):
    return subprocess.run([GJS, "-m", *map(str, args)], cwd=REPO,
                          capture_output=True, text=True, timeout=60)


@unittest.skipUnless(GJS, "gjs is not installed")
class JavaScriptTest(unittest.TestCase):
    def test_logic_passes_its_own_tests(self):
        result = gjs(REPO / "tests/js/test_extension.js")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"\d+ tests, 0 failed")

    def test_every_file_of_the_extension_parses(self):
        files = sorted(EXTENSION.glob("*.js"))
        self.assertEqual([f.name for f in files], ["extension.js", "gestures.js"])
        for file in files:
            with self.subTest(file=file.name):
                result = gjs(REPO / "tests/js/syntax.js", file)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_syntax_check_does_notice_a_mistake(self):
        result = gjs(REPO / "tests/js/syntax.js", REPO / "tests/js/broken.js.txt")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("SyntaxError", result.stdout)

    def test_logic_imports_nothing_from_the_shell(self):
        # That is what lets it be tested outside one.
        source = (EXTENSION / "gestures.js").read_text()
        self.assertEqual(re.findall(r"^\s*import\b.*$", source, re.MULTILINE), [])


class MetadataTest(unittest.TestCase):
    def setUp(self):
        self.metadata = json.loads((EXTENSION / "metadata.json").read_text())

    def test_directory_is_named_after_the_uuid(self):
        self.assertEqual(self.metadata["uuid"], UUID)
        self.assertEqual(shell.EXTENSION_UUID, UUID)

    def test_version_is_the_project_s(self):
        self.assertEqual(self.metadata["version-name"],
                         gnome_x11_touchpad_gestures.__version__)

    def test_shell_versions_are_named(self):
        versions = self.metadata["shell-version"]
        self.assertGreater(len(versions), 0)
        for version in versions:
            self.assertRegex(version, r"^\d+$")


class InterfaceTest(unittest.TestCase):
    """The daemon and the extension are written apart and must agree."""

    def setUp(self):
        source = (EXTENSION / "extension.js").read_text()
        (xml,) = re.findall(r"const INTERFACE = `(.*?)`;", source, re.DOTALL)
        (self.interface,) = ElementTree.fromstring(xml).findall("interface")
        (self.path,) = re.findall(r"const OBJECT_PATH = '(.*?)';", source)

    def test_same_name_and_place(self):
        self.assertEqual(self.interface.get("name"), shell.EXTENSION_INTERFACE)
        self.assertEqual(self.path, shell.EXTENSION_PATH)

    def test_same_methods_taking_the_same_things(self):
        offered = {
            method.get("name"): "".join(
                arg.get("type") for arg in method.findall("arg")
                if arg.get("direction") == "in")
            for method in self.interface.findall("method")}
        self.assertEqual(offered, shell.EXTENSION_CALLS)

    def test_every_method_offered_is_written(self):
        source = (EXTENSION / "extension.js").read_text()
        for name in shell.EXTENSION_CALLS:
            with self.subTest(method=name):
                self.assertRegex(source, rf"\n    {name}\(")


if __name__ == "__main__":
    unittest.main()
