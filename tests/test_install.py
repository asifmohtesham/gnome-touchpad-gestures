"""Exercises the shell functions in install/ without running the installers."""
import contextlib
import os
import pathlib
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

from gnome_x11_touchpad_gestures import daemon

INSTALL = pathlib.Path(__file__).resolve().parent.parent / "install"
REPO = INSTALL.parent
PROGRAM = ".local/share/gnome-x11-touchpad-gestures"
PACKAGE = "gnome_x11_touchpad_gestures"
UNIT = "gnome-x11-touchpad-gestures.service"
RULE = "71-gnome-x11-touchpad-gestures.rules"


# The only programs a script under test can reach. Anything else it tries to
# run is not found, so a command added to a script later cannot get out to
# the real system through a test.
# Found here, in the test's own environment, and run by its full path: the
# scripts themselves get a PATH that does not include it.
BASH = shutil.which("bash")
ALLOWED = ("dirname", "cmp", "python3", "mkdir", "cp", "mv", "rm", "install",
           "chmod", "sleep", "id", "tr", "wc")


def write_stub(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def make_bin(directory, stubs):
    """A directory to be the whole PATH: stand-ins, and the allowed programs."""
    directory.mkdir()
    for name in ALLOWED:
        (directory / name).symlink_to(shutil.which(name))
    for name, body in stubs.items():
        write_stub(directory / name, body)
    return directory


def sealed(home, bin_directory, **extra):
    """An environment that carries nothing of the real session with it."""
    return dict(HOME=str(home), USER="tester", LANG="C.UTF-8",
                PATH=str(bin_directory), **extra)


# If a script ever did run when sourced, these keep it from touching the
# system: privileged and service commands become failing stubs, and HOME
# points at an empty directory.
SANDBOX = tempfile.TemporaryDirectory(prefix="gnome-x11-touchpad-gestures-test-")
STUBS = make_bin(pathlib.Path(SANDBOX.name) / "bin", {
    name: f'echo "stub: {name} was called" >&2\nexit 97\n'
    for name in ("sudo", "systemctl", "udevadm")})
ENVIRONMENT = sealed(SANDBOX.name, STUBS)

# What the stand-in udevadm reports: one touchpad.
FAKE_SYSFS = "/sys/devices/fake/input/input9/event9"
FAKE_NODE = "/dev/input/event9"


@contextlib.contextmanager
def recording_sandbox(status_exit=0):
    """An empty home where system commands succeed and are written to a log.

    Yields the home, a function that sources a script and runs a snippet of
    shell after it, and a function that returns the commands logged so far.
    """
    with tempfile.TemporaryDirectory(prefix="gnome-x11-touchpad-gestures-test-") as sandbox:
        home = pathlib.Path(sandbox) / "home"
        log = pathlib.Path(sandbox) / "log"
        home.mkdir()
        log.touch()
        stubs = make_bin(pathlib.Path(sandbox) / "bin", {
            "sudo": f'echo "sudo $*" >> "{log}"\n',
            "systemctl": (f'echo "systemctl $*" >> "{log}"\n'
                          f'case "$*" in *status*) exit {status_exit};; esac\n'),
            "udevadm": (f'echo "udevadm $*" >> "{log}"\n'
                        f'case "$*" in\n'
                        f'  *--dry-run*) echo "{FAKE_SYSFS}";;\n'
                        f'  info*) echo "input/event9";;\n'
                        f'esac\n'),
        })
        environment = sealed(home, stubs, RECORDING_LOG=str(log))

        def run(script, snippet, *args):
            # The script must not be $0, or it would believe it was run directly.
            return subprocess.run(
                [BASH, "-c", 'source "$1"; shift; ' + snippet, "bash",
                 str(INSTALL / script), *args],
                capture_output=True, text=True, timeout=30, env=environment)

        yield home, run, lambda: log.read_text().splitlines()


def record(script, function, *args):
    """Like call(), but system commands succeed and are written to a log."""
    with recording_sandbox() as (_, run, commands):
        result = run(script, '"$@"', function, *args)
        return result, commands()


def call(script, function, *args):
    """Source a script, which must not run anything by itself, and call one function."""
    # The script must not be $0, or it would believe it was run directly.
    command = 'source "$1"; shift; "$@"'
    return subprocess.run(
        [BASH, "-c", command, "bash", str(INSTALL / script), function, *args],
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
        unit = (INSTALL / "gnome-x11-touchpad-gestures.service").read_text()
        self.assertIn("WorkingDirectory=%h/" + PROGRAM + "\n", unit)
        self.assertNotIn("@REPO@", unit)

    def test_installing_copies_the_program_and_the_unit(self):
        with recording_sandbox() as (home, run, commands):
            result = run("install.sh", "install_service")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.modules(home / PROGRAM / "gnome_x11_touchpad_gestures"),
                             self.modules(REPO / "gnome_x11_touchpad_gestures"))
            installed = (home / ".config/systemd/user/gnome-x11-touchpad-gestures.service")
            self.assertEqual(installed.read_text(),
                             (INSTALL / "gnome-x11-touchpad-gestures.service").read_text())
            self.assertEqual(commands(), [
                "systemctl --user daemon-reload",
                "systemctl --user enable gnome-x11-touchpad-gestures.service",
                "systemctl --user restart gnome-x11-touchpad-gestures.service",
            ])

    def test_nothing_installed_mentions_where_the_repository_is(self):
        with recording_sandbox() as (home, run, _):
            result = run("install.sh", "install_service")
            self.assertEqual(result.returncode, 0, result.stderr)
            installed = [path for path in home.rglob("*") if path.is_file()]
            self.assertGreater(len(installed), 5)
            for path in installed:
                self.assertNotIn(str(REPO), path.read_text(), path)

    def test_failed_copy_leaves_the_working_program_in_place(self):
        # The third file cannot be written, as when the disk fills up.
        failing = ('cp() { local last="${@: -1}"; command cp "$1" "$2" "$last"; '
                   'echo "cp: No space left on device" >&2; return 1; }; ')
        with recording_sandbox() as (home, run, commands):
            run("install.sh", "install_service")
            working = self.modules(home / PROGRAM / PACKAGE)
            before = len(commands())
            result = run("install.sh", failing + "install_service")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.modules(home / PROGRAM / PACKAGE), working)
            self.assertEqual(commands()[before:], [])

    def test_failed_first_install_leaves_no_half_program(self):
        failing = ('cp() { local last="${@: -1}"; command cp "$1" "$2" "$last"; '
                   'return 1; }; ')
        with recording_sandbox() as (home, run, _):
            result = run("install.sh", failing + "install_service")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((home / PROGRAM / PACKAGE).exists())

    def test_install_after_a_failed_one_succeeds(self):
        failing = 'cp() { return 1; }; '
        with recording_sandbox() as (home, run, _):
            run("install.sh", failing + "install_service")
            result = run("install.sh", "install_service")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.modules(home / PROGRAM / PACKAGE),
                             self.modules(REPO / PACKAGE))
            self.assertEqual(
                sorted(path.name for path in (home / PROGRAM).iterdir()),
                [PACKAGE])

    def test_program_is_in_place_before_the_service_is_restarted(self):
        noting = ('eval "real_$(declare -f install_program)"; '
                  'install_program() { real_install_program; '
                  'echo "program copied" >> "$RECORDING_LOG"; }; ')
        with recording_sandbox() as (_, run, commands):
            result = run("install.sh", noting + "install_service")
            self.assertEqual(result.returncode, 0, result.stderr)
            log = commands()
            self.assertLess(log.index("program copied"),
                            log.index(f"systemctl --user restart {UNIT}"))

    def test_unit_finds_the_program_even_when_python_ignores_the_directory(self):
        unit = (INSTALL / UNIT).read_text()
        self.assertIn("Environment=PYTHONPATH=%h/" + PROGRAM + "\n", unit)
        with recording_sandbox() as (home, run, _):
            run("install.sh", "install_service")
            helped = subprocess.run(
                ["python3", "-m", f"{PACKAGE}.daemon", "--help"],
                cwd=home / PROGRAM, capture_output=True, text=True, timeout=30,
                env=dict(os.environ, PYTHONSAFEPATH="1",
                         PYTHONPATH=str(home / PROGRAM)))
            self.assertEqual(helped.returncode, 0, helped.stderr)

    def test_installed_program_runs_without_the_repository(self):
        with recording_sandbox() as (home, run, _):
            run("install.sh", "install_service")
            environment = {key: value for key, value in os.environ.items()
                           if key != "PYTHONPATH"}
            where = subprocess.run(
                ["python3", "-c",
                 f"import {PACKAGE}.daemon as d; print(d.__file__)"],
                cwd=home / PROGRAM, env=environment,
                capture_output=True, text=True, timeout=30)
            helped = subprocess.run(
                ["python3", "-m", "gnome_x11_touchpad_gestures.daemon", "--help"],
                cwd=home / PROGRAM, env=environment,
                capture_output=True, text=True, timeout=30)
            self.assertEqual(where.returncode, 0, where.stderr)
            self.assertTrue(where.stdout.startswith(str(home)), where.stdout)
            self.assertEqual(helped.returncode, 0, helped.stderr)

    def test_installing_again_removes_a_module_that_no_longer_exists(self):
        with recording_sandbox() as (home, run, _):
            run("install.sh", "install_service")
            stale = home / PROGRAM / "gnome_x11_touchpad_gestures" / "removed_upstream.py"
            stale.write_text("raise SystemExit('stale')\n")
            run("install.sh", "install_service")
            self.assertFalse(stale.exists())
            self.assertEqual(self.modules(home / PROGRAM / "gnome_x11_touchpad_gestures"),
                             self.modules(REPO / "gnome_x11_touchpad_gestures"))

    def test_installing_twice_leaves_only_the_program(self):
        with recording_sandbox() as (home, run, _):
            run("install.sh", "install_service")
            result = run("install.sh", "install_service")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                sorted(path.name for path in (home / PROGRAM).iterdir()),
                [PACKAGE])

    def test_stray_file_where_the_program_goes_is_replaced(self):
        with recording_sandbox() as (home, run, _):
            (home / PROGRAM).mkdir(parents=True)
            (home / PROGRAM / PACKAGE).write_text("not a directory")
            result = run("install.sh", "install_service")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.modules(home / PROGRAM / PACKAGE),
                             self.modules(REPO / PACKAGE))

    def test_installer_does_not_insist_on_one_location(self):
        script = (INSTALL / "install.sh").read_text()
        self.assertNotIn('"$repo" !=', script)
        self.assertNotIn("@REPO@", script)

    def test_uninstalling_removes_the_program(self):
        with recording_sandbox() as (home, run, _):
            run("install.sh", "install_service")
            self.assertTrue((home / PROGRAM).is_dir())
            result = run("uninstall.sh", "main_as 1000")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((home / PROGRAM).exists())
            self.assertFalse(
                (home / ".config/systemd/user/gnome-x11-touchpad-gestures.service").exists())


class SealTest(unittest.TestCase):
    """The tests above are only safe if a script cannot reach the real system."""

    def test_program_that_is_not_allowed_is_not_found(self):
        for program in ("gsettings", "loginctl", "setfacl", "xinput", "apt"):
            with self.subTest(program=program):
                result = call("install.sh", "command", "-v", program)
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_real_sudo_systemctl_and_udevadm_are_out_of_reach(self):
        for program in ("sudo", "systemctl", "udevadm"):
            with self.subTest(program=program):
                result = call("install.sh", "command", "-v", program)
                self.assertTrue(result.stdout.startswith(str(STUBS)), result.stdout)

    def test_nothing_of_the_session_is_passed_on(self):
        result = call("install.sh", "export", "-p")
        self.assertIn("HOME=", result.stdout)
        for name in ("DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "XDG_RUNTIME_DIR",
                     "XAUTHORITY", "SSH_AUTH_SOCK"):
            with self.subTest(name=name):
                self.assertNotIn(name, result.stdout)


class UpgradeTest(unittest.TestCase):
    """The project was once installed under another name. An upgrade clears it."""

    FORMER = "finger-drag"
    ACCESS = 'check_access() { return 0; }; '

    def plant(self, home):
        unit = home / ".config/systemd/user" / f"{self.FORMER}.service"
        program = home / ".local/share" / self.FORMER / "finger_drag"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\n")
        program.mkdir(parents=True)
        (program / "daemon.py").write_text("")
        return unit, program.parent

    def rules(self, home, *names):
        directory = home / "rules"
        directory.mkdir()
        for name in names:
            (directory / name).write_text((INSTALL / RULE).read_text())
        return str(directory)

    def test_install_removes_the_former_service_and_program(self):
        with recording_sandbox() as (home, run, commands):
            unit, program = self.plant(home)
            result = run("install.sh", "install_service")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(unit.exists())
            self.assertFalse(program.exists())
            log = commands()
            stopped = log.index(f"systemctl --user disable --now {self.FORMER}.service")
            self.assertLess(stopped, log.index(f"systemctl --user restart {UNIT}"))

    def test_former_rule_is_a_reason_to_ask_for_a_password(self):
        with recording_sandbox() as (home, run, _):
            rules = self.rules(home, RULE, f"71-{self.FORMER}.rules")
            result = run("install.sh", self.ACCESS +
                         'rules_dir="$1"; needs_sudo && echo yes || echo no', rules)
            self.assertEqual(result.stdout.strip(), "yes")

    def test_install_removes_the_former_rule_before_udev_looks_again(self):
        with recording_sandbox() as (home, run, commands):
            rules = self.rules(home, RULE, f"71-{self.FORMER}.rules")
            result = run("install.sh", self.ACCESS + 'rules_dir="$1"; main_as 1000', rules)
            self.assertEqual(result.returncode, 0, result.stderr)
            log = commands()
            removed = log.index(f"sudo rm -f {rules}/71-{self.FORMER}.rules")
            self.assertLess(removed, log.index("sudo udevadm control --reload"))

    def test_uninstall_removes_what_the_former_version_installed(self):
        with recording_sandbox() as (home, run, commands):
            unit, program = self.plant(home)
            rules = self.rules(home, RULE, f"71-{self.FORMER}.rules")
            result = run("uninstall.sh", 'rules_dir="$1"; main_as 1000', rules)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(unit.exists())
            self.assertFalse(program.exists())
            log = commands()
            self.assertIn(f"sudo rm -f {rules}/71-{self.FORMER}.rules", log)
            self.assertIn(f"sudo rm -f {rules}/{RULE}", log)
            self.assertIn(f"systemctl --user disable --now {self.FORMER}.service", log)


class SudoOnlyWhenNeededTest(unittest.TestCase):
    """Reinstalling after a change to the code should not ask for a password."""

    ACCESS = 'check_access() { return 0; }; '
    NO_ACCESS = 'check_access() { return 3; }; '

    def rules(self, home, content):
        directory = home / "rules"
        directory.mkdir()
        if content is not None:
            (directory / "71-gnome-x11-touchpad-gestures.rules").write_text(content)
        return str(directory)

    def current_rule(self):
        return (INSTALL / "71-gnome-x11-touchpad-gestures.rules").read_text()

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

    def test_not_needed_when_the_check_fails_for_a_reason_sudo_cannot_fix(self):
        broken = 'check_access() { return 1; }; '
        self.assertEqual(self.needs_sudo(self.current_rule(), broken), "no")

    def test_broken_code_is_reported_without_asking_for_a_password(self):
        broken = 'check_access() { echo "SyntaxError" >&2; return 1; }; '
        with recording_sandbox() as (home, run, commands):
            result = run("install.sh", broken + 'rules_dir="$1"; main_as 1000',
                         self.rules(home, self.current_rule()))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual([c for c in commands() if c.startswith("sudo")], [])
            self.assertIn("exit status 1", result.stderr)
            self.assertNotIn("access is granted", result.stdout)
            self.assertFalse((home / PROGRAM).exists())

    def test_install_finishes_when_the_service_is_not_started_yet(self):
        # systemctl status exits 3 for a unit that is not running, which is
        # what a session other than X11 gives.
        with recording_sandbox(status_exit=3) as (home, run, commands):
            result = run("install.sh", self.ACCESS + 'rules_dir="$1"; main_as 1000',
                         self.rules(home, self.current_rule()))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Installed to", result.stdout)
            self.assertIn("X11", result.stdout)

    def test_needed_when_access_was_never_granted(self):
        self.assertEqual(self.needs_sudo(self.current_rule(), self.NO_ACCESS), "yes")

    def test_reinstalling_with_everything_in_place_never_calls_sudo(self):
        with recording_sandbox() as (home, run, commands):
            result = run("install.sh",
                         self.ACCESS + 'rules_dir="$1"; main_as 1000',
                         self.rules(home, self.current_rule()))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([c for c in commands() if c.startswith("sudo")], [])
            self.assertIn(f"systemctl --user restart {UNIT}", commands())
            self.assertTrue((home / PROGRAM / PACKAGE / "daemon.py").exists())

    def test_first_install_installs_the_rule_with_sudo(self):
        with recording_sandbox() as (home, run, commands):
            rules = self.rules(home, None)
            result = run("install.sh",
                         self.ACCESS + 'rules_dir="$1"; main_as 1000', rules)
            self.assertEqual(result.returncode, 0, result.stderr)
            sudo = [c for c in commands() if c.startswith("sudo")]
            self.assertIn(f"{rules}/71-gnome-x11-touchpad-gestures.rules", sudo[0])
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
    def test_rule_leaves_anything_that_is_also_a_keyboard_alone(self):
        rule = (INSTALL / RULE).read_text()
        touchpad = [line for line in rule.splitlines()
                    if "ID_INPUT_TOUCHPAD" in line and not line.startswith("#")]
        self.assertEqual(len(touchpad), 1)
        self.assertIn('ENV{ID_INPUT_KEYBOARD}!="1"', touchpad[0])

    def test_unsupported_touchpad_is_explained_as_such(self):
        result = call("install.sh", "explain_check_failure",
                      str(daemon.EXIT_UNSUPPORTED), "1")
        self.assertIn("each finger", result.stderr)
        self.assertNotIn("Log out, log back in", result.stderr)

    def test_rule_matches_any_touchpad_so_nobody_has_to_edit_it(self):
        rule = (INSTALL / "71-gnome-x11-touchpad-gestures.rules").read_text()
        active = [line for line in rule.splitlines()
                  if line and not line.startswith("#")]
        self.assertEqual(len(active), 2)
        self.assertIn('ENV{ID_INPUT_TOUCHPAD}=="1"', active[0])
        self.assertNotIn("ATTRS{name}", rule)

    def test_service_runs_in_an_x11_session_only(self):
        unit = (INSTALL / "gnome-x11-touchpad-gestures.service").read_text()
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

        removed = self.position(log, "sudo rm -f", "71-gnome-x11-touchpad-gestures.rules")
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
        triggers = [line for line in log
                    if line.startswith("sudo udevadm trigger")]
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

    def test_touchpad_nodes_turns_what_udev_reports_into_device_nodes(self):
        with recording_sandbox() as (_, run, _commands):
            result = run("uninstall.sh", "touchpad_nodes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), [FAKE_NODE])

    def test_uninstall_revokes_access_to_uinput_and_each_touchpad(self):
        _, log = record("uninstall.sh", "main_as", "1000")
        revoked = [line for line in log if line.startswith("sudo setfacl")]
        self.assertEqual(revoked, [
            "sudo setfacl -x u:tester /dev/uinput",
            f"sudo setfacl -x u:tester {FAKE_NODE}",
        ])

    def test_touchpad_nodes_on_this_machine(self):
        """The one test here that asks the real udev, and changes nothing."""
        result = subprocess.run(
            [BASH, "-c", 'source "$1"; touchpad_nodes', "bash",
             str(INSTALL / "uninstall.sh")],
            capture_output=True, text=True, timeout=30,
            env=dict(ENVIRONMENT, PATH="/usr/bin:/bin"))
        if result.returncode != 0 or not result.stdout.split():
            self.skipTest("no touchpad, or no udev, on this machine")
        for node in result.stdout.split():
            self.assertRegex(node, re.compile(r"^/dev/input/event\d+$"))


if __name__ == "__main__":
    unittest.main()
