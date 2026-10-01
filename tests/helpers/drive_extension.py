"""Drives the real extension, run outside a shell, with the daemon's own client.

Run by tests/test_extension_runs.py inside `dbus-run-session`, so on a bus of
its own. Prints what happened as JSON.
"""
import json
import pathlib
import subprocess
import sys
import time

import dbus

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from gnome_x11_touchpad_gestures.shell import (  # noqa: E402
    DAEMON_NAME, EXTENSION_INTERFACE, EXTENSION_PATH, Shell)


def as_on_x11(bus, control, seen):
    shell = Shell()
    seen["version"] = shell.extension_version()
    seen["over"] = {}
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

    # Nothing of the Wayland behaviour is to happen here.
    seen["three fingers free"] = Shell().three_fingers_free()
    seen["handlers"] = int(control("Handlers"))
    seen["watches"] = int(control("Watches"))
    seen["three fingers"] = [
        bool(control("Swipe", phase, dbus.UInt32(3), signature="su"))
        for phase in ("begin", "update", "end")]

    # Left open on purpose: switching the extension off must put it back.
    seen["begin, then switched off"] = Shell().swipe_begin(40.0)
    control("Disable")
    seen.update(json.loads(str(control("Seen"))))
    gone = Shell()
    seen["version once off"] = gone.extension_version()
    seen["over window once off"] = gone.pointer_over_window()
    control("Enable")
    seen["version once on again"] = Shell().extension_version()


def as_on_wayland(bus, control, seen):
    def free():
        """Asked without taking the name, which asking as the daemon would."""
        return bool(bus.call_blocking(
            "org.gnome.Shell", EXTENSION_PATH, EXTENSION_INTERFACE,
            "ThreeFingersFree", "", (), timeout=5))

    def learns(wanted):
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if free() is wanted:
                return True
            time.sleep(0.02)
        return False

    def one(phase, fingers=3):
        return bool(control("Swipe", phase, dbus.UInt32(fingers), signature="su"))

    def swipe(fingers=3):
        return [one(phase, fingers) for phase in ("begin", "update", "end")]

    seen["version"] = Shell().extension_version()
    seen["handlers"] = int(control("Handlers"))
    seen["watches"] = int(control("Watches"))
    seen["free before the daemon"] = free()
    seen["swipe before the daemon"] = swipe()

    seen["name taken"] = Shell().announce()
    seen["learned of the daemon"] = learns(True)
    seen["free with the daemon"] = Shell().three_fingers_free()
    seen["three fingers"] = swipe(3)
    seen["four fingers"] = swipe(4)
    seen["two fingers"] = swipe(2)
    seen["pinch"] = bool(control("Pinch"))
    seen["cancelled"] = [one("begin"), one("cancel"), one("update")]
    seen["unknown phase"] = [one("begin"), one("unknown"), one("end")]
    seen["broken event"] = bool(control("Broken"))
    seen["swipe after a broken event"] = swipe()

    began = one("begin")
    bus.release_name(DAEMON_NAME)
    seen["learned the daemon went"] = learns(False)
    seen["swipe the daemon left during"] = [began, one("update"), one("end")]
    seen["swipe after it left"] = swipe()

    began = one("begin")
    Shell().announce()
    seen["learned it came back"] = learns(True)
    seen["swipe the daemon came during"] = [began, one("update"), one("end")]
    seen["swipe after it came back"] = swipe()

    # What the X11 behaviour offers must answer, and do nothing.
    asked = Shell()
    seen["over window"] = asked.pointer_over_window()
    seen["may glide"] = asked.may_glide()
    seen["begin"] = asked.swipe_begin(12.5)
    asked.swipe_update(12.507, 0.25)
    asked.swipe_end(12.6)
    asked.swipe_cancel()

    control("Disable")
    seen["handlers once off"] = int(control("Handlers"))
    seen["watches once off"] = int(control("Watches"))
    seen["swipe once off"] = swipe()
    seen.update(json.loads(str(control("Seen"))))
    seen["version once off"] = Shell().extension_version()
    control("Enable")
    seen["version once on again"] = Shell().extension_version()
    seen["handlers once on again"] = int(control("Handlers"))
    seen["watches once on again"] = int(control("Watches"))
    seen["free once on again"] = learns(True)
    seen["swipe once on again"] = swipe()


def as_on_wayland_in_another_shell(bus, control, seen):
    def free():
        return bool(bus.call_blocking(
            "org.gnome.Shell", EXTENSION_PATH, EXTENSION_INTERFACE,
            "ThreeFingersFree", "", (), timeout=5))

    seen["version"] = Shell().extension_version()
    seen["handlers"] = int(control("Handlers"))
    seen["watches"] = int(control("Watches"))
    daemon = Shell()
    seen["name taken"] = daemon.announce()
    seen["free with the daemon"] = daemon.three_fingers_free()
    # Long enough for a watch on the bus, were there one, to have fired.
    time.sleep(0.5)
    seen["free a moment later"] = free()
    seen["three fingers"] = [
        bool(control("Swipe", phase, dbus.UInt32(3), signature="su"))
        for phase in ("begin", "update", "end")]
    asked = Shell()
    seen["over window"] = asked.pointer_over_window()
    seen["begin"] = asked.swipe_begin(12.5)
    seen.update(json.loads(str(control("Seen"))))


def main(directory, real_bus, mode, shell_version):
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
        ["gjs", "-m", str(REPO / "tests/js/run_extension.js"), directory,
         "x11" if mode == "x11" else "wayland", shell_version],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready = runner.stdout.readline().strip()
        if ready != "READY":
            sys.exit(f"runner said {ready!r}: {runner.stderr.read()}")

        def control(method, *args, signature=""):
            return bus.call_blocking(
                "org.gnome.Shell", "/test/Control", "test.Control", method,
                signature, args, timeout=5)

        seen = {}
        {"x11": as_on_x11, "wayland": as_on_wayland,
         "wayland-elsewhere": as_on_wayland_in_another_shell}[mode](
            bus, control, seen)
        control("Quit")
        runner.wait(timeout=10)
        seen["runner exit"] = runner.returncode
        seen["runner complaints"] = runner.stderr.read()
    finally:
        if runner.poll() is None:
            runner.kill()
    print(json.dumps(seen))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
