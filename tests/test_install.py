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


def record(script, function, *args):
    """Like call(), but sudo and systemctl succeed and are written to a log."""
    with tempfile.TemporaryDirectory(prefix="finger-drag-test-") as sandbox:
        stubs = pathlib.Path(sandbox) / "bin"
        stubs.mkdir()
        log = pathlib.Path(sandbox) / "log"
        log.touch()
        for name in ("sudo", "systemctl"):
            stub = stubs / name
            stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        environment = dict(
            os.environ, HOME=sandbox,
            PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}")
        command = 'source "$1"; shift; "$@"'
        result = subprocess.run(
            ["bash", "-c", command, "bash", str(INSTALL / script), function, *args],
            capture_output=True, text=True, timeout=30, env=environment)
        return result, log.read_text().splitlines()


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
                      str(daemon.EXIT_NO_ACCESS), "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Log out, log back in", result.stderr)

    def test_any_other_failure_is_not_blamed_on_the_login_session(self):
        result = call("install.sh", "explain_check_failure", "1", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Log out, log back in", result.stderr)
        self.assertIn("exit status 1", result.stderr)

    def test_installer_refuses_to_run_as_root(self):
        result = call("install.sh", "refuse_root", "0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not as root", result.stderr)
        self.assertNotIn("stub:", result.stderr)

    def test_installer_accepts_a_normal_user(self):
        result = call("install.sh", "refuse_root", "1000")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_root_is_refused_before_anything_else_happens(self):
        # main must reach the guard before it looks at paths or calls sudo.
        result = call("install.sh", "main_as", "0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not as root", result.stderr)
        self.assertNotIn("stub:", result.stderr)
        self.assertNotIn("must live at", result.stderr)

    def test_sourcing_the_installer_runs_nothing(self):
        result = call("install.sh", "true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


class InstalledFilesTest(unittest.TestCase):
    def test_rule_matches_any_touchpad_so_nobody_has_to_edit_it(self):
        rule = (INSTALL / "71-finger-drag.rules").read_text()
        active = [line for line in rule.splitlines()
                  if line and not line.startswith("#")]
        self.assertEqual(len(active), 2)
        self.assertIn('ENV{ID_INPUT_TOUCHPAD}=="1"', active[0])
        self.assertNotIn("ATTRS{name}", rule)

    def test_service_runs_in_an_x11_session_only(self):
        unit = (INSTALL / "finger-drag.service").read_text()
        self.assertIn("ConditionEnvironment=XDG_SESSION_TYPE=x11", unit)

    def test_installer_names_each_missing_python_package(self):
        result = call("install.sh", "missing_packages", "evdev", "dbus",
                      "no_such_module_a", "no_such_module_b")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(),
                         ["python3-no_such_module_a", "python3-no_such_module_b"])

    def test_installer_finds_nothing_missing_here(self):
        result = call("install.sh", "missing_packages", "evdev", "dbus")
        self.assertEqual(result.stdout, "")

    def test_no_touchpad_is_explained_as_such(self):
        result = call("install.sh", "explain_check_failure",
                      str(daemon.EXIT_NO_ACCESS), "0")
        self.assertIn("no touchpad", result.stderr.lower())
        self.assertNotIn("Log out, log back in", result.stderr)


class UninstallScriptTest(unittest.TestCase):
    def test_uninstaller_refuses_to_run_as_root(self):
        result = call("uninstall.sh", "main_as", "0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not as root", result.stderr)
        self.assertNotIn("stub:", result.stderr)

    def position(self, log, *words):
        for index, line in enumerate(log):
            if all(word in line for word in words):
                return index
        self.fail(f"no command with {words} in {log}")

    def test_uninstall_makes_udev_forget_the_devices_before_revoking_access(self):
        result, log = record("uninstall.sh", "main_as", "1000")
        self.assertEqual(result.returncode, 0, result.stderr)

        removed = self.position(log, "sudo rm -f", "71-finger-drag.rules")
        reloaded = self.position(log, "sudo udevadm control --reload")
        uinput = self.position(log, "sudo udevadm trigger", "uinput")
        touchpad = self.position(log, "sudo udevadm trigger", "ID_INPUT_TOUCHPAD=1")
        settled = self.position(log, "sudo udevadm settle")
        revoked = self.position(log, "sudo setfacl -x", "/dev/uinput")

        self.assertLess(removed, reloaded)
        self.assertLess(reloaded, min(uinput, touchpad))
        self.assertLess(max(uinput, touchpad), settled)
        self.assertLess(settled, revoked)

    def test_uninstall_touches_only_the_touchpad_and_uinput(self):
        _, log = record("uninstall.sh", "main_as", "1000")
        triggers = [line for line in log if "udevadm trigger" in line]
        self.assertEqual(len(triggers), 2)
        for line in triggers:
            self.assertTrue(
                "--sysname-match=uinput" in line
                or "--property-match=ID_INPUT_TOUCHPAD=1" in line, line)

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
