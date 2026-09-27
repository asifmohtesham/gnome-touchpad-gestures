"""Exercises the shell functions in install/ without running the installers."""
import os
import pathlib
import re
import stat
import subprocess
import tempfile
import unittest

from finger_drag import daemon

INSTALL = pathlib.Path(__file__).resolve().parent.parent / "install"


# If a script ever did run when sourced, these keep it from touching the
# system: privileged and service commands become failing stubs, and HOME
# points at an empty directory.
SANDBOX = tempfile.TemporaryDirectory(prefix="finger-drag-test-")
STUBS = pathlib.Path(SANDBOX.name) / "bin"
STUBS.mkdir()
for name in ("sudo", "systemctl"):
    stub = STUBS / name
    stub.write_text(f'#!/bin/sh\necho "stub: {name} was called" >&2\nexit 97\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
ENVIRONMENT = dict(
    os.environ, HOME=SANDBOX.name, PATH=f"{STUBS}{os.pathsep}{os.environ['PATH']}")


def call(script, function, *args):
    """Source a script, which must not run anything by itself, and call one function."""
    # The script must not be $0, or it would believe it was run directly.
    command = 'source "$1"; shift; "$@"'
    return subprocess.run(
        ["bash", "-c", command, "bash", str(INSTALL / script), function, *args],
        capture_output=True, text=True, timeout=30, env=ENVIRONMENT)


class InstallScriptTest(unittest.TestCase):
    def test_missing_access_is_explained_as_a_re_login(self):
        result = call("install.sh", "explain_check_failure",
                      str(daemon.EXIT_NO_ACCESS))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Log out, log back in", result.stderr)

    def test_any_other_failure_is_not_blamed_on_the_login_session(self):
        result = call("install.sh", "explain_check_failure", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Log out, log back in", result.stderr)
        self.assertIn("exit status 1", result.stderr)

    def test_sourcing_the_installer_runs_nothing(self):
        result = call("install.sh", "true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


class UninstallScriptTest(unittest.TestCase):
    def test_sourcing_the_uninstaller_runs_nothing(self):
        result = call("uninstall.sh", "true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_touchpad_nodes_lists_device_nodes(self):
        result = call("uninstall.sh", "touchpad_nodes")
        self.assertEqual(result.returncode, 0, result.stderr)
        nodes = result.stdout.split()
        if not nodes:
            self.skipTest("this machine has no touchpad")
        for node in nodes:
            self.assertRegex(node, re.compile(r"^/dev/input/event\d+$"))
            self.assertTrue(pathlib.Path(node).exists())


if __name__ == "__main__":
    unittest.main()
