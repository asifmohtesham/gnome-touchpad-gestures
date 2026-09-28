"""Drives the real extension, run outside a shell, with the daemon's own client.

Run by tests/test_extension_runs.py inside `dbus-run-session`, so on a bus of
its own. Prints what happened as JSON.
"""
import json
import pathlib
import subprocess
import sys

import dbus

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from gnome_x11_touchpad_gestures.shell import Shell  # noqa: E402


def main(directory, real_bus):
    bus = dbus.SessionBus()
    # The runner takes the shell's name. On the desktop's own bus that name
    # belongs to the real shell, and nothing here may go near it.
    address = bus.get_object("org.freedesktop.DBus", "/").GetId(
        dbus_interface="org.freedesktop.DBus")
    if real_bus and str(address) == real_bus:
        sys.exit("refusing to run on the desktop's own bus")
    if bus.name_has_owner("org.gnome.Shell"):
        sys.exit("refusing to run where a shell is already answering")

    runner = subprocess.Popen(
        ["gjs", "-m", str(REPO / "tests/js/run_extension.js"), directory],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready = runner.stdout.readline().strip()
        if ready != "READY":
            sys.exit(f"runner said {ready!r}: {runner.stderr.read()}")

        def control(method, *args, signature=""):
            return bus.call_blocking(
                "org.gnome.Shell", "/test/Control", "test.Control", method,
                signature, args, timeout=5)

        shell = Shell()
        seen = {"version": shell.extension_version(), "over": {}}
        for what in ("window", "popup", "panel", "stage", "nothing"):
            control("Pick", what, signature="s")
            seen["over"][what] = shell.pointer_over_window()
        control("Pick", "window", signature="s")
        control("Overview", True, signature="b")
        seen["over"]["window, overview open"] = shell.pointer_over_window()
        seen["may glide, overview open"] = shell.may_glide()
        control("Overview", False, signature="b")
        seen["may glide"] = shell.may_glide()

        seen["begin"] = shell.swipe_begin(12.5)
        shell.swipe_update(12.507, 0.25)
        shell.swipe_update(12.514, -0.5)
        shell.swipe_end(12.6)
        seen["begin again"] = shell.swipe_begin(20.0)
        shell.swipe_cancel()

        control("Mode", dbus.UInt32(128), signature="u")
        seen["begin, menu open"] = shell.swipe_begin(30.0)
        control("Mode", dbus.UInt32(1), signature="u")
        control("TrackerEnabled", False, signature="b")
        seen["begin, tracker off"] = shell.swipe_begin(31.0)
        control("TrackerEnabled", True, signature="b")

        # Left open on purpose: switching the extension off must put it back.
        seen["begin, then switched off"] = Shell().swipe_begin(40.0)
        control("Disable")
        seen.update(json.loads(str(control("Seen"))))
        gone = Shell()
        seen["version once off"] = gone.extension_version()
        seen["over window once off"] = gone.pointer_over_window()
        control("Enable")
        seen["version once on again"] = Shell().extension_version()
        control("Quit")
        runner.wait(timeout=10)
        seen["runner exit"] = runner.returncode
        seen["runner complaints"] = runner.stderr.read()
    finally:
        if runner.poll() is None:
            runner.kill()
    print(json.dumps(seen))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
