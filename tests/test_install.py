"""Exercises the shell functions in install/ without running the installers."""
import contextlib
import os
import pathlib
import re
import stat
import subprocess
import tempfile
import unittest

from finger_drag import daemon

INSTALL = pathlib.Path(__file__).resolve().parent.parent / "install"
REPO = INSTALL.parent
PROGRAM = ".local/share/finger-drag"


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


@contextlib.contextmanager
def recording_sandbox():
    """An empty home where sudo and systemctl succeed and are written to a log.

    Yields the home, a function that sources a script and runs a snippet of
    shell after it, and a function that returns the commands logged so far.
    """
    with tempfile.TemporaryDirectory(prefix="finger-drag-test-") as sandbox:
        home = pathlib.Path(sandbox) / "home"
        stubs = pathlib.Path(sandbox) / "bin"
        log = pathlib.Path(sandbox) / "log"
        home.mkdir()
        stubs.mkdir()
        log.touch()
        for name in ("sudo", "systemctl"):
            stub = stubs / name
            stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        environment = dict(
            os.environ, HOME=str(home),
            PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}")

        def run(script, snippet, *args):
            # The script must not be $0, or it would believe it was run directly.
            return subprocess.run(
                ["bash", "-c", 'source "$1"; shift; ' + snippet, "bash",
                 str(INSTALL / script), *args],
                capture_output=True, text=True, timeout=30, env=environment)

        yield home, run, lambda: log.read_text().splitlines()


def record(script, function, *args):
    """Like call(), but sudo and systemctl succeed and are written to a log."""
    with recording_sandbox() as (_, run, commands):
        result = run(script, '"$@"', function, *args)
        return result, commands()


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


class ProgramIsCopiedTest(unittest.TestCase):
    """The service runs an installed copy, so the repository may be anywhere."""

    def modules(self, directory):
        return {path.name: path.read_text()
                for path in pathlib.Path(directory).glob("*.py")}

    def test_unit_names_no_directory_chosen_by_the_user(self):
        unit = (INSTALL / "finger-drag.service").read_text()
        self.assertIn("WorkingDirectory=%h/" + PROGRAM + "\n", unit)
        self.assertNotIn("@REPO@", unit)

    def test_installing_copies_the_program_and_the_unit(self):
        with recording_sandbox() as (home, run, commands):
            result = run("install.sh", "install_service")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.modules(home / PROGRAM / "finger_drag"),
                             self.modules(REPO / "finger_drag"))
            installed = (home / ".config/systemd/user/finger-drag.service")
            self.assertEqual(installed.read_text(),
                             (INSTALL / "finger-drag.service").read_text())
            self.assertEqual(commands(), [
                "systemctl --user daemon-reload",
                "systemctl --user enable finger-drag.service",
                "systemctl --user restart finger-drag.service",
            ])

    def test_nothing_installed_mentions_where_the_repository_is(self):
        with recording_sandbox() as (home, run, _):
            run("install.sh", "install_service")
            for path in home.rglob("*"):
                if path.is_file():
                    self.assertNotIn(str(REPO), path.read_text(), path)

    def test_installed_program_runs_without_the_repository(self):
        with recording_sandbox() as (home, run, _):
            run("install.sh", "install_service")
            environment = {key: value for key, value in os.environ.items()
                           if key != "PYTHONPATH"}
            where = subprocess.run(
                ["python3", "-c", "import finger_drag.daemon as d; print(d.__file__)"],
                cwd=home / PROGRAM, env=environment,
                capture_output=True, text=True, timeout=30)
            helped = subprocess.run(
                ["python3", "-m", "finger_drag.daemon", "--help"],
                cwd=home / PROGRAM, env=environment,
                capture_output=True, text=True, timeout=30)
            self.assertEqual(where.returncode, 0, where.stderr)
            self.assertTrue(where.stdout.startswith(str(home)), where.stdout)
            self.assertEqual(helped.returncode, 0, helped.stderr)

    def test_installing_again_removes_a_module_that_no_longer_exists(self):
        with recording_sandbox() as (home, run, _):
            run("install.sh", "install_service")
            stale = home / PROGRAM / "finger_drag" / "removed_upstream.py"
            stale.write_text("raise SystemExit('stale')\n")
            run("install.sh", "install_service")
            self.assertFalse(stale.exists())
            self.assertEqual(self.modules(home / PROGRAM / "finger_drag"),
                             self.modules(REPO / "finger_drag"))

    def test_installer_does_not_insist_on_one_location(self):
        script = (INSTALL / "install.sh").read_text()
        self.assertNotIn("must live at", script)
        self.assertNotIn("$HOME/finger-drag", script)
        self.assertNotIn("render_unit", script)

    def test_uninstalling_removes_the_program(self):
        with recording_sandbox() as (home, run, _):
            run("install.sh", "install_service")
            self.assertTrue((home / PROGRAM).is_dir())
            result = run("uninstall.sh", "main_as 1000")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((home / PROGRAM).exists())
            self.assertFalse(
                (home / ".config/systemd/user/finger-drag.service").exists())


class SudoOnlyWhenNeededTest(unittest.TestCase):
    """Reinstalling after a change to the code should not ask for a password."""

    ACCESS = 'check_access() { return 0; }; '
    NO_ACCESS = 'check_access() { return 3; }; '

    def rules(self, home, content):
        directory = home / "rules"
        directory.mkdir()
        if content is not None:
            (directory / "71-finger-drag.rules").write_text(content)
        return str(directory)

    def current_rule(self):
        return (INSTALL / "71-finger-drag.rules").read_text()

    def needs_sudo(self, content, access):
        with recording_sandbox() as (home, run, _):
            result = run("install.sh",
                         access + 'rules_dir="$1"; needs_sudo && echo yes || echo no',
                         self.rules(home, content))
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()

    def test_not_needed_when_the_rule_is_in_place_and_access_is_granted(self):
        self.assertEqual(self.needs_sudo(self.current_rule(), self.ACCESS), "no")

    def test_needed_when_the_rule_is_missing(self):
        self.assertEqual(self.needs_sudo(None, self.ACCESS), "yes")

    def test_needed_when_the_rule_is_an_older_one(self):
        self.assertEqual(self.needs_sudo("# an older rule\n", self.ACCESS), "yes")

    def test_needed_when_access_was_never_granted(self):
        self.assertEqual(self.needs_sudo(self.current_rule(), self.NO_ACCESS), "yes")

    def test_reinstalling_with_everything_in_place_never_calls_sudo(self):
        with recording_sandbox() as (home, run, commands):
            result = run("install.sh",
                         self.ACCESS + 'rules_dir="$1"; main_as 1000',
                         self.rules(home, self.current_rule()))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([c for c in commands() if c.startswith("sudo")], [])
            self.assertIn("systemctl --user restart finger-drag.service", commands())
            self.assertTrue((home / PROGRAM / "finger_drag" / "daemon.py").exists())

    def test_first_install_installs_the_rule_with_sudo(self):
        with recording_sandbox() as (home, run, commands):
            rules = self.rules(home, None)
            result = run("install.sh",
                         self.ACCESS + 'rules_dir="$1"; main_as 1000', rules)
            self.assertEqual(result.returncode, 0, result.stderr)
            sudo = [c for c in commands() if c.startswith("sudo")]
            self.assertIn(f"{rules}/71-finger-drag.rules", sudo[0])
            self.assertTrue(sudo[0].startswith("sudo install "))
            self.assertIn("sudo udevadm control --reload", sudo)
            self.assertIn("sudo udevadm settle", sudo)

    def test_no_access_after_installing_stops_before_the_service(self):
        with recording_sandbox() as (home, run, commands):
            result = run("install.sh",
                         self.NO_ACCESS + 'rules_dir="$1"; main_as 1000',
                         self.rules(home, self.current_rule()))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual([c for c in commands() if c.startswith("systemctl")], [])
            self.assertFalse((home / PROGRAM).exists())


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
