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
EXTENSION = "gnome-x11-touchpad-gestures@asifmohtesham.github.io"
EXTENSIONS = ".local/share/gnome-shell/extensions"


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
    for name in ("sudo", "systemctl", "udevadm", "gnome-extensions", "gsettings")})
ENVIRONMENT = sealed(SANDBOX.name, STUBS)

# What the stand-in udevadm reports: one touchpad.
FAKE_SYSFS = "/sys/devices/fake/input/input9/event9"
FAKE_NODE = "/dev/input/event9"


@contextlib.contextmanager
def recording_sandbox(status_exit=0, enabling_works=True,
                      enabled="['ubuntu-dock@ubuntu.com']", disabled="@as []"):
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
            "gnome-extensions": (f'echo "gnome-extensions $*" >> "{log}"\n'
                                 f'exit {0 if enabling_works else 1}\n'),
            "gsettings": (f'echo "gsettings $*" >> "{log}"\n'
                          f'case "$1 $3" in\n'
                          f'  "get enabled-extensions") echo "{enabled}";;\n'
                          f'  "get disabled-extensions") echo "{disabled}";;\n'
                          f'esac\n'),
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
                f"gnome-extensions enable {EXTENSION}",
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


class ExtensionIsInstalledTest(unittest.TestCase):
    def files(self, directory):
        return {path.name: path.read_text()
                for path in pathlib.Path(directory).iterdir() if path.is_file()}

    def test_extension_is_copied_whole(self):
        with recording_sandbox() as (home, run, _):
            result = run("install.sh", "install_service")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.files(home / EXTENSIONS / EXTENSION),
                             self.files(REPO / "extension" / EXTENSION))
            self.assertEqual(
                sorted(self.files(home / EXTENSIONS / EXTENSION)),
                ["extension.js", "gestures.js", "metadata.json"])

    def test_nothing_else_is_left_among_the_extensions(self):
        with recording_sandbox() as (home, run, _):
            run("install.sh", "install_service")
            run("install.sh", "install_service")
            self.assertEqual(
                sorted(path.name for path in (home / EXTENSIONS).iterdir()),
                [EXTENSION])

    def test_other_extensions_are_left_alone(self):
        with recording_sandbox() as (home, run, _):
            other = home / EXTENSIONS / "someone@else.example"
            other.mkdir(parents=True)
            (other / "extension.js").write_text("// theirs")
            run("install.sh", "install_service")
            self.assertEqual((other / "extension.js").read_text(), "// theirs")

    # Copying the extension, and nothing else, fails.
    FAILING_COPY = ('cp() { case "$*" in *@asifmohtesham*) return 1;; esac; '
                    'command cp "$@"; }; ')
    # The new copy is ready and cannot be moved to where it goes.
    FAILING_SWAP = ('mv() { case "$1" in *.new) return 1;; esac; '
                    'command mv "$@"; }; ')

    def settings_written(self, commands):
        return [c for c in commands() if c.startswith("gsettings set")]

    @contextlib.contextmanager
    def copy_of_the_repository(self):
        """Somewhere to leave things lying about without touching the real one."""
        with tempfile.TemporaryDirectory(prefix="gnome-x11-touchpad-gestures-test-") as copy:
            for part in ("extension", PACKAGE):
                shutil.copytree(REPO / part, pathlib.Path(copy) / part,
                                ignore=shutil.ignore_patterns("__pycache__"))
            yield pathlib.Path(copy)

    def test_failed_copy_leaves_the_extension_that_was_there(self):
        with recording_sandbox() as (home, run, _):
            run("install.sh", "install_service")
            working = self.files(home / EXTENSIONS / EXTENSION)
            result = run("install.sh", self.FAILING_COPY + "install_service")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.files(home / EXTENSIONS / EXTENSION), working)

    def test_failed_copy_leaves_nothing_for_the_shell_to_trip_on(self):
        # The shell takes every directory among the extensions for one.
        with recording_sandbox() as (home, run, _):
            result = run("install.sh", self.FAILING_COPY + "install_service")
            self.assertNotEqual(result.returncode, 0)
            among = home / EXTENSIONS
            self.assertEqual(
                sorted(p.name for p in among.iterdir()) if among.exists() else [], [])

    def test_failed_copy_over_a_working_extension_leaves_only_that(self):
        with recording_sandbox() as (home, run, _):
            run("install.sh", "install_service")
            run("install.sh", self.FAILING_COPY + "install_service")
            self.assertEqual(
                sorted(p.name for p in (home / EXTENSIONS).iterdir()), [EXTENSION])

    def test_copy_that_cannot_be_moved_into_place_leaves_the_old_one(self):
        with recording_sandbox() as (home, run, _):
            run("install.sh", "install_service")
            working = self.files(home / EXTENSIONS / EXTENSION)
            result = run("install.sh", self.FAILING_SWAP + "install_extension")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.files(home / EXTENSIONS / EXTENSION), working)
            self.assertEqual(
                sorted(p.name for p in (home / EXTENSIONS).iterdir()), [EXTENSION])
            self.assertEqual(
                [p for p in home.rglob("*") if p.suffix in (".new", ".old")], [])

    def test_what_a_failed_install_left_behind_is_not_installed(self):
        with recording_sandbox() as (home, run, _):
            run("install.sh", self.FAILING_COPY + "install_service")
            for left in home.rglob("*.new"):
                (left / "stale.js").write_text("// from a copy that failed")
            leftover = home / PROGRAM / "extension.new"
            leftover.mkdir(parents=True, exist_ok=True)
            (leftover / "stale.js").write_text("// from a copy that failed")
            result = run("install.sh", "install_service")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                sorted(self.files(home / EXTENSIONS / EXTENSION)),
                ["extension.js", "gestures.js", "metadata.json"])

    def test_only_the_extension_s_own_files_are_installed(self):
        with self.copy_of_the_repository() as copy:
            lying_about = copy / "extension" / EXTENSION
            (lying_about / "extension.js~").write_text("// an editor's backup")
            (lying_about / "notes.txt").write_text("to do")
            (lying_about / "scratch").mkdir()
            (lying_about / "scratch" / "try.js").write_text("// an experiment")
            with recording_sandbox() as (home, run, _):
                result = run("install.sh", 'repo="$1"; install_service', str(copy))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    sorted(p.name for p in (home / EXTENSIONS / EXTENSION).iterdir()),
                    ["extension.js", "gestures.js", "metadata.json"])

    def test_extension_missing_a_file_is_not_installed(self):
        with self.copy_of_the_repository() as copy:
            (copy / "extension" / EXTENSION / "gestures.js").unlink()
            with recording_sandbox() as (home, run, _):
                result = run("install.sh", 'repo="$1"; install_service', str(copy))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((home / EXTENSIONS / EXTENSION).exists())

    def test_shell_that_knows_it_switches_it_on_itself(self):
        with recording_sandbox() as (_, run, commands):
            run("install.sh", "install_service")
            self.assertIn(f"gnome-extensions enable {EXTENSION}", commands())
            self.assertEqual(self.settings_written(commands), [])

    def test_extension_switched_off_before_is_switched_on_again(self):
        # Switching it off writes it down among those switched off, and
        # that list wins over the list of those switched on.
        off = f"['{EXTENSION}', 'someone@else.example']"
        with recording_sandbox(enabling_works=False, disabled=off) as (_, run, commands):
            result = run("install.sh", "install_service")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.settings_written(commands), [
                "gsettings set org.gnome.shell enabled-extensions "
                f"['ubuntu-dock@ubuntu.com', '{EXTENSION}']",
                "gsettings set org.gnome.shell disabled-extensions "
                "['someone@else.example']"])

    def test_it_may_be_the_only_one_switched_off(self):
        off = f"['{EXTENSION}']"
        with recording_sandbox(enabling_works=False, disabled=off) as (_, run, commands):
            run("install.sh", "install_service")
            self.assertIn("gsettings set org.gnome.shell disabled-extensions []",
                          commands())

    def test_others_switched_off_stay_so(self):
        off = "['someone@else.example']"
        with recording_sandbox(enabling_works=False, disabled=off) as (_, run, commands):
            run("install.sh", "install_service")
            self.assertEqual(
                [c for c in self.settings_written(commands) if "disabled" in c], [])

    def test_list_of_names_that_are_not_names_is_not_overwritten(self):
        with recording_sandbox(enabling_works=False, enabled="[1, 2]",
                               disabled="{'a': 1}") as (_, run, commands):
            result = run("install.sh", "install_service")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.settings_written(commands), [])

    def test_shell_that_does_not_know_it_yet_has_it_switched_on_in_settings(self):
        with recording_sandbox(enabling_works=False) as (_, run, commands):
            result = run("install.sh", "install_service")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "gsettings set org.gnome.shell enabled-extensions "
                f"['ubuntu-dock@ubuntu.com', '{EXTENSION}']", commands())

    def test_it_is_not_switched_on_twice(self):
        already = f"['ubuntu-dock@ubuntu.com', '{EXTENSION}']"
        with recording_sandbox(enabling_works=False, enabled=already) as (_, run, commands):
            run("install.sh", "install_service")
            self.assertEqual(
                [c for c in commands() if c.startswith("gsettings set")], [])

    def test_empty_list_of_extensions_is_understood(self):
        with recording_sandbox(enabling_works=False, enabled="@as []") as (_, run, commands):
            run("install.sh", "install_service")
            self.assertIn("gsettings set org.gnome.shell enabled-extensions "
                          f"['{EXTENSION}']", commands())

    def test_list_that_cannot_be_read_is_not_overwritten(self):
        with recording_sandbox(enabling_works=False, enabled="nonsense(") as (_, run, commands):
            result = run("install.sh", "install_service")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                [c for c in commands() if c.startswith("gsettings set")], [])

    def test_uninstall_switches_it_off_and_removes_it(self):
        with recording_sandbox() as (home, run, commands):
            run("install.sh", "install_service")
            result = run("uninstall.sh", "main_as 1000")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((home / EXTENSIONS / EXTENSION).exists())
            self.assertIn(f"gnome-extensions disable {EXTENSION}", commands())

    def uninstalled(self, **settings):
        with recording_sandbox(**settings) as (_, run, commands):
            run("install.sh", "install_service")
            before = len(commands())
            result = run("uninstall.sh", "main_as 1000")
            self.assertEqual(result.returncode, 0, result.stderr)
            return [c for c in commands()[before:] if c.startswith("gsettings set")]

    def test_uninstall_leaves_no_trace_of_it_in_the_settings(self):
        # Switching it off through the shell writes it down among those
        # switched off, and a shell that has not loaded it cannot be asked.
        for shell_knows_it in (True, False):
            with self.subTest(shell_knows_it=shell_knows_it):
                self.assertEqual(
                    self.uninstalled(
                        enabling_works=shell_knows_it,
                        enabled=f"['ubuntu-dock@ubuntu.com', '{EXTENSION}']",
                        disabled=f"['{EXTENSION}']"),
                    ["gsettings set org.gnome.shell enabled-extensions "
                     "['ubuntu-dock@ubuntu.com']",
                     "gsettings set org.gnome.shell disabled-extensions []"])

    def test_uninstall_writes_no_settings_that_do_not_name_it(self):
        self.assertEqual(
            self.uninstalled(enabled="['ubuntu-dock@ubuntu.com']",
                             disabled="['someone@else.example']"), [])

    def test_uninstall_does_not_overwrite_a_list_it_cannot_read(self):
        self.assertEqual(
            self.uninstalled(enabling_works=False, enabled="nonsense(",
                             disabled="nonsense("), [])

    def test_uninstall_does_not_overwrite_a_list_of_more_than_names(self):
        self.assertEqual(
            self.uninstalled(enabling_works=False,
                             enabled=f"[1, '{EXTENSION}']",
                             disabled=f"[('a', 'b'), '{EXTENSION}']"), [])

    def test_uninstall_asks_the_shell_before_it_edits_the_settings(self):
        # The shell switches it off at once; the settings are what is left.
        with recording_sandbox(
                enabled=f"['{EXTENSION}']") as (_, run, commands):
            run("uninstall.sh", "main_as 1000")
            said = commands()
            self.assertLess(
                said.index(f"gnome-extensions disable {EXTENSION}"),
                said.index("gsettings set org.gnome.shell enabled-extensions []"))

    def test_uninstall_leaves_nothing_of_a_failed_install(self):
        with recording_sandbox() as (home, run, _):
            run("install.sh", self.FAILING_COPY + "install_service")
            run("uninstall.sh", "main_as 1000")
            self.assertEqual(list(home.rglob("*.new")), [])
            self.assertFalse((home / PROGRAM).exists())


class SealTest(unittest.TestCase):
    """The tests above are only safe if a script cannot reach the real system."""

    def test_program_that_is_not_allowed_is_not_found(self):
        for program in ("dconf", "loginctl", "setfacl", "xinput", "apt"):
            with self.subTest(program=program):
                result = call("install.sh", "command", "-v", program)
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_real_sudo_systemctl_and_udevadm_are_out_of_reach(self):
        for program in ("sudo", "systemctl", "udevadm", "gnome-extensions",
                        "gsettings"):
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


class RestartAdviceTest(unittest.TestCase):
    """The shell loads the extension when it starts. Whether it has to be
    restarted depends on what it has loaded, and is not said otherwise."""

    RESTART = "Alt+F2"
    # The installer waits a second for the service; the tests need not.
    ACCESS = ('check_access() { return 0; }; needs_sudo() { return 1; }; '
              'sleep() { :; }; ')

    def advice(self, loaded, version, changed):
        result = call("install.sh", "explain_extension", loaded, version, changed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_extension_loaded_as_installed_needs_no_restart(self):
        said = self.advice("0.2.1", "0.2.1", "no")
        self.assertNotIn(self.RESTART, said)
        self.assertNotIn("snap", said)
        self.assertIn("0.2.1", said)
        self.assertIn("No restart is needed", said)

    def test_extension_not_loaded_needs_the_shell_restarted(self):
        said = self.advice("", "0.2.1", "yes")
        self.assertIn(self.RESTART, said)
        self.assertIn("has not loaded", said)
        self.assertIn("snap", said)
        self.assertNotIn("No restart", said)

    def test_it_needs_it_whether_or_not_the_files_changed(self):
        self.assertIn(self.RESTART, self.advice("", "0.2.1", "no"))

    def test_older_version_loaded_needs_the_shell_restarted(self):
        said = self.advice("0.2.0", "0.2.1", "no")
        self.assertIn(self.RESTART, said)
        self.assertIn("0.2.0", said)
        self.assertIn("0.2.1", said)
        self.assertNotIn("No restart", said)
        # It is loaded, and follows the fingers as it did.
        self.assertNotIn("snap", said)
        self.assertNotIn("has not loaded", said)

    def test_changed_extension_of_the_same_version_needs_it_too(self):
        # As after editing it, or an install from a checkout between releases.
        said = self.advice("0.2.1", "0.2.1", "yes")
        self.assertIn(self.RESTART, said)
        self.assertIn("changed", said)
        self.assertNotIn("No restart", said)
        self.assertNotIn("snap", said)

    def test_windows_are_said_to_stay_open_wherever_a_restart_is_advised(self):
        for loaded, changed in (("", "yes"), ("0.2.0", "no"), ("0.2.1", "yes")):
            with self.subTest(loaded=loaded, changed=changed):
                self.assertIn("windows stay open",
                              self.advice(loaded, "0.2.1", changed))

    def installed(self, loaded, again=False, edit=None):
        """What the whole installer says at the end, and the last advice in it."""
        shell = f'loaded_extension_version() {{ echo "{loaded}"; }}; '
        with contextlib.ExitStack() as stack:
            home, run, _ = stack.enter_context(recording_sandbox())
            snippet = self.ACCESS + shell + "main_as 1000"
            args = []
            if edit is not None:
                copy = pathlib.Path(stack.enter_context(tempfile.TemporaryDirectory(
                    prefix="gnome-x11-touchpad-gestures-test-")))
                for part in ("extension", PACKAGE):
                    shutil.copytree(REPO / part, copy / part,
                                    ignore=shutil.ignore_patterns("__pycache__"))
                snippet = 'repo="$1"; ' + snippet
                args = [str(copy)]
            result = run("install.sh", snippet, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            if edit is not None:
                edit(copy / "extension" / EXTENSION)
                again = True
            if again:
                result = run("install.sh", snippet, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

    def version(self):
        import gnome_x11_touchpad_gestures
        return gnome_x11_touchpad_gestures.__version__

    def test_first_install_advises_a_restart(self):
        said = self.installed(loaded="")
        self.assertIn(self.RESTART, said)
        self.assertIn("has not loaded", said)

    def test_install_over_a_loaded_extension_of_this_version_changed_nothing(self):
        said = self.installed(loaded=self.version(), again=True)
        self.assertNotIn(self.RESTART, said)
        self.assertIn("No restart is needed", said)
        self.assertNotIn("snap", said)

    def test_first_copy_of_the_files_counts_as_a_change(self):
        # The shell answers with this version, from somewhere else.
        said = self.installed(loaded=self.version())
        self.assertIn(self.RESTART, said)

    def test_install_of_an_edited_extension_advises_a_restart(self):
        def edit(extension):
            with (extension / "gestures.js").open("a") as file:
                file.write("// edited\n")

        said = self.installed(loaded=self.version(), edit=edit)
        self.assertIn(self.RESTART, said)
        self.assertIn("changed", said)

    def test_edit_to_any_of_its_files_counts(self):
        for name in ("extension.js", "gestures.js", "metadata.json"):
            with self.subTest(file=name):
                def edit(extension, name=name):
                    with (extension / name).open("a") as file:
                        file.write("\n")

                self.assertIn(self.RESTART,
                              self.installed(loaded=self.version(), edit=edit))

    def test_install_over_an_older_loaded_extension_advises_a_restart(self):
        said = self.installed(loaded="0.0.9", again=True)
        self.assertIn(self.RESTART, said)
        self.assertIn("0.0.9", said)
        self.assertIn(self.version(), said)

    def test_what_is_loaded_is_asked_after_the_extension_is_in_place(self):
        # Switching it on may be what makes the shell load it.
        shell = ('loaded_extension_version() { '
                 f'test -e "$HOME/{EXTENSIONS}/{EXTENSION}/metadata.json" '
                 '&& echo in-place; }; ')
        with recording_sandbox() as (_, run, _commands):
            result = run("install.sh", self.ACCESS + shell + "main_as 1000")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("in-place", result.stdout)

    def test_this_version_is_the_repository_s(self):
        result = call("install.sh", "this_version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.version())

    def test_shell_that_cannot_be_asked_has_loaded_nothing(self):
        # As here, where there is no session to ask.
        result = call("install.sh", "loaded_extension_version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_client_that_cannot_be_run_has_loaded_nothing_either(self):
        result = call("install.sh", "eval",
                      'repo=/nowhere/at/all; loaded_extension_version')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((result.stdout, result.stderr), ("", ""))

    def test_installer_that_cannot_ask_still_finishes(self):
        failing = 'loaded_extension_version() { return 1; }; '
        with recording_sandbox() as (_, run, _commands):
            result = run("install.sh", self.ACCESS + failing + "main_as 1000")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(self.RESTART, result.stdout)

    def test_the_shell_is_asked_by_the_daemon_s_own_client(self):
        script = (INSTALL / "install.sh").read_text()
        self.assertIn("extension_version()", script)

    LOG_OUT = "Log out and log back in"

    def advice_on_wayland(self, loaded, version, changed):
        result = call("install.sh", "explain_extension", loaded, version,
                      changed, "wayland")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_on_wayland_the_shell_cannot_be_restarted_so_logging_out_is_advised(self):
        for loaded, changed in (("", "yes"), ("0.2.0", "no"), ("0.2.1", "yes")):
            with self.subTest(loaded=loaded, changed=changed):
                said = self.advice_on_wayland(loaded, "0.2.1", changed)
                self.assertIn(self.LOG_OUT, said)
                self.assertNotIn(self.RESTART, said)
                self.assertNotIn("windows stay open", said)

    def test_on_wayland_an_extension_not_loaded_costs_the_drag(self):
        said = self.advice_on_wayland("", "0.2.1", "yes")
        self.assertIn("has not loaded", said)
        self.assertIn("three-finger drag", said)
        self.assertNotIn("snap", said)
        self.assertNotIn("glides", said)

    def test_on_wayland_too_an_extension_loaded_as_installed_needs_nothing(self):
        said = self.advice_on_wayland("0.2.1", "0.2.1", "no")
        self.assertIn("No restart is needed", said)
        self.assertNotIn(self.LOG_OUT, said)

    def test_every_other_session_is_advised_as_x11_was(self):
        for session in ("x11", "", "tty"):
            with self.subTest(session=session):
                result = call("install.sh", "explain_extension", "", "0.2.1",
                              "yes", session)
                self.assertIn(self.RESTART, result.stdout)
                self.assertIn("snap", result.stdout)

    def installed_in(self, session):
        said = 'export XDG_SESSION_TYPE="$1"; ' if session is not None else ""
        shell = 'loaded_extension_version() { echo ""; }; '
        with recording_sandbox() as (_, run, _commands):
            result = run("install.sh", said + self.ACCESS + shell + "main_as 1000",
                         *([session] if session is not None else []))
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout, result.stderr

    def test_install_on_wayland_advises_logging_out_and_warns_of_nothing(self):
        said, warned = self.installed_in("wayland")
        self.assertIn(self.LOG_OUT, said)
        self.assertNotIn(self.RESTART, said)
        self.assertNotIn("not x11", warned)
        self.assertEqual(warned, "")
        self.assertNotIn("has not started", said)

    def test_install_on_x11_warns_of_nothing_either(self):
        said, warned = self.installed_in("x11")
        self.assertIn(self.RESTART, said)
        self.assertEqual(warned, "")
        self.assertNotIn("has not started", said)

    def test_install_in_any_other_session_says_where_the_service_starts(self):
        for session in ("tty", None):
            with self.subTest(session=session):
                said, warned = self.installed_in(session)
                self.assertIn("X11", warned)
                self.assertIn("Wayland", warned)
                self.assertIn("has not started", said)
                self.assertIn("X11 or Wayland", said)


class WhatIsSaidTest(unittest.TestCase):
    """Statements that were true of X11 alone and are easy to leave behind."""

    def setUp(self):
        self.readme = " ".join((REPO / "README.md").read_text().split())

    def test_readme_says_the_service_starts_in_either_session(self):
        self.assertNotIn("only if that session is X11.", self.readme)
        self.assertIn("only if that session is X11 or Wayland.", self.readme)

    def test_readme_does_not_play_down_the_rule_where_it_is_news(self):
        # On X11 any program can type and click already. On Wayland it cannot.
        self.assertIn("On Wayland no program can", self.readme)

    def test_readme_says_what_happens_in_a_shell_the_drag_was_not_tried_in(self):
        self.assertIn("frees nothing", self.readme)

    def test_changelog_does_not_say_that_nothing_at_all_changed_on_x11(self):
        changelog = (REPO / "CHANGELOG.md").read_text()
        self.assertNotIn("On X11 nothing has changed.", changelog)

    def test_installer_does_not_say_every_gesture_works_without_the_extension(self):
        script = (INSTALL / "install.sh").read_text()
        self.assertNotIn("Without it the gestures still work", script)


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

    def test_service_starts_in_an_x11_or_a_wayland_session_and_no_other(self):
        unit = (INSTALL / "gnome-x11-touchpad-gestures.service").read_text()
        conditions = [line for line in unit.splitlines()
                      if line.startswith("ConditionEnvironment=")]
        # The bar makes each a condition of which one is enough.
        self.assertEqual(conditions, [
            "ConditionEnvironment=|XDG_SESSION_TYPE=x11",
            "ConditionEnvironment=|XDG_SESSION_TYPE=wayland"])

    def test_service_does_not_promise_what_one_mode_lacks(self):
        unit = (INSTALL / "gnome-x11-touchpad-gestures.service").read_text()
        (description,) = [line for line in unit.splitlines()
                          if line.startswith("Description=")]
        self.assertIn("three-finger drag", description)
        self.assertNotIn("momentum", description)
        self.assertNotIn("four-finger", description)

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
