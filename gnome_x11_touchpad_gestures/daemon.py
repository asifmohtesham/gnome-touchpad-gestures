"""Reads the touchpad, runs the gesture machine, writes to virtual devices."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
import select
import signal
import struct
import sys
import time

import evdev
from evdev import ecodes as e

from gnome_x11_touchpad_gestures import __version__
from gnome_x11_touchpad_gestures.gestures import GestureMachine
from gnome_x11_touchpad_gestures.momentum import MomentumMachine
from gnome_x11_touchpad_gestures.output import (
    WORKSPACE_KEYS, DragOnly, NoDevice, Output)
from gnome_x11_touchpad_gestures.shell import (
    DRAG_COMPLAINT, OVERVIEW_TIMEOUT_S, Shell)
from gnome_x11_touchpad_gestures.slots import (
    FALLBACK_HEIGHT_MM, FALLBACK_WIDTH_MM, SlotTracker, units_per_mm)

UINPUT_PATH = "/dev/uinput"
# Not the project's full name, on purpose. GNOME's window manager sorts input
# devices by words in their names, and "touchpad" in a name gets a device the
# touchpad's settings: natural scrolling would turn every glide round, and
# the pointer would take the touchpad's speed.
POINTER_NAME = "gnome-x11-gestures pointer"
KEYBOARD_NAME = "gnome-x11-gestures keyboard"
WHEEL_NAME = "gnome-x11-gestures wheel"
# Distinct from a crash (1) or a usage error (2) so that install.sh can tell
# "log out and back in" apart from every other failure.
EXIT_NO_ACCESS = 3
# A touchpad is readable but reports one position, not one per finger.
EXIT_UNSUPPORTED = 4
# A touchpad plugged in or paired later arrives on one of these buses.
EXTERNAL_BUSES = (0x03, 0x05)  # USB, Bluetooth
# Sets the clock the kernel stamps this reader's events by. From
# linux/input.h: _IOW('E', 0xa0, int).
EVIOCSCLOCKID = 0x400445A0
# A frame said to be older than this when it is read is not believed. What
# holds the daemon up is the shell, for two timeouts at the very most.
STAMP_MAX_AGE_S = 4 * OVERVIEW_TIMEOUT_S
# What the daemon does, which depends on what the desktop does itself.
MODE_SAYS = {
    "full": "every gesture",
    "drag": "three-finger drag only",
}
NO_TOUCHPAD = (
    "gnome-x11-touchpad-gestures: no accessible touchpad found. Is "
    "/etc/udev/rules.d/71-gnome-x11-touchpad-gestures.rules installed? "
    "Log out and back in after installing it.")
UNSUPPORTED = (
    "gnome-x11-touchpad-gestures: a touchpad was found, but it does not report "
    "each finger separately, so gestures cannot be read from it.")
NO_UINPUT = (
    f"gnome-x11-touchpad-gestures: cannot write to {UINPUT_PATH}. Is "
    "/etc/udev/rules.d/71-gnome-x11-touchpad-gestures.rules installed? "
    "Log out and back in after installing it.")


def session_mode(environ=None) -> str:
    """ "drag" where the desktop does the other gestures itself, else "full".

    GNOME on Wayland has its own swipes and its toolkits their own momentum.
    What it lacks is three-finger drag.
    """
    environ = os.environ if environ is None else environ
    return "drag" if environ.get("XDG_SESSION_TYPE") == "wayland" else "full"


class Machines:
    """Runs several gesture machines side by side as if they were one."""

    def __init__(self, *machines) -> None:
        self._machines = machines

    def update(self, t: float, count: int, cx: float, cy: float,
               regrouped: bool = False, fingers: tuple = (),
               pressed: bool = False) -> list:
        return [action for machine in self._machines
                for action in machine.update(
                    t, count, cx, cy, regrouped, fingers, pressed)]

    def tick(self, t: float) -> list:
        return [action for machine in self._machines
                for action in machine.tick(t)]

    def interrupt(self, t: float) -> list:
        return [action for machine in self._machines
                for action in machine.interrupt(t)]

    def next_deadline(self) -> float | None:
        deadlines = [machine.next_deadline() for machine in self._machines]
        return min((d for d in deadlines if d is not None), default=None)


def is_touchpad(capabilities: dict, props: list) -> bool:
    abs_codes = capabilities.get(e.EV_ABS, [])
    return (e.INPUT_PROP_POINTER in props
            and e.ABS_MT_SLOT in abs_codes
            and e.ABS_MT_POSITION_X in abs_codes)


def is_single_touch_pad(capabilities: dict, props: list) -> bool:
    """A touchpad that reports where a touch is, but not each finger."""
    return (e.INPUT_PROP_POINTER in props
            and e.ABS_X in capabilities.get(e.EV_ABS, [])
            and e.BTN_TOOL_FINGER in capabilities.get(e.EV_KEY, [])
            and not is_touchpad(capabilities, props))


def preference(path: str, bustype: int) -> tuple:
    """Sorts the touchpad to use first: built in, then lowest event number."""
    digits = "".join(ch for ch in os.path.basename(path) if ch.isdigit())
    return (bustype in EXTERNAL_BUSES, int(digits or 0), path)


def readable(matches) -> list:
    """Every readable device that `matches`, opened. The caller closes them."""
    found = []
    for path in evdev.list_devices():
        try:
            device = evdev.InputDevice(path)
        except OSError:
            continue
        if matches(device.capabilities(absinfo=False), device.input_props()):
            found.append(device)
        else:
            device.close()
    return found


def find_touchpad():
    found = readable(is_touchpad)
    if not found:
        return None
    best = min(found, key=lambda d: preference(d.path, d.info.bustype))
    for device in found:
        if device is not best:
            device.close()
    return best


def no_touchpad() -> int:
    """Says why there is no touchpad to use, and returns the exit status."""
    unusable = readable(is_single_touch_pad)
    for device in unusable:
        device.close()
    if unusable:
        print(UNSUPPORTED, file=sys.stderr)
        return EXIT_UNSUPPORTED
    print(NO_TOUCHPAD, file=sys.stderr)
    return EXIT_NO_ACCESS


def make_tracker(device) -> SlotTracker:
    x = device.absinfo(e.ABS_MT_POSITION_X)
    y = device.absinfo(e.ABS_MT_POSITION_Y)
    return SlotTracker(
        units_per_mm(x.min, x.max, x.resolution, FALLBACK_WIDTH_MM),
        units_per_mm(y.min, y.max, y.resolution, FALLBACK_HEIGHT_MM),
        *snapshot(device),
    )


def snapshot(device) -> tuple[int, bool]:
    """The device's current slot, and whether any finger is on the pad."""
    return (device.absinfo(e.ABS_MT_SLOT).value,
            e.BTN_TOUCH in device.active_keys())


def stamp_by_our_clock(device) -> bool:
    """Asks the kernel to stamp events by the clock the daemon reads.

    It stamps them by the clock on the wall otherwise, which is another
    clock and one that is put right now and then. Only this reader of the
    touchpad is affected. False if the kernel would not.
    """
    try:
        fcntl.ioctl(device.fd, EVIOCSCLOCKID,
                    struct.pack("i", time.CLOCK_MONOTONIC))
    except (OSError, AttributeError, TypeError, ValueError):
        return False
    return True


class FrameTimes:
    """When each frame happened.

    Frames are read in batches, and after the daemon was held up a batch
    holds many. Taken to have happened when they were read, they happened
    all at once, and a swipe was faster than it was. The kernel stamps
    every event as it happens; that is used where it can be had.
    """

    def __init__(self, stamped: bool) -> None:
        self._stamped = stamped
        self._latest = float("-inf")

    def of(self, event, now: float) -> float:
        """When the frame that `event` closes happened."""
        at = now
        if self._stamped:
            stamp = event.timestamp()
            if now - STAMP_MAX_AGE_S <= stamp <= now:
                at = stamp
        return self.passed(at)

    def passed(self, t: float) -> float:
        """Takes note of a time handed to the machines, which never runs back."""
        self._latest = max(self._latest, t)
        return self._latest


def pump(events, read_state, tracker, machine, output, now: float,
         times: FrameTimes | None = None) -> None:
    times = times or FrameTimes(stamped=False)
    for event in events:
        if event.type == e.EV_SYN and event.code == e.SYN_DROPPED:
            # The kernel dropped events, so slot state is stale. End whatever
            # gesture was in progress; fingers must touch again to start one.
            tracker.begin_resync()
            output.emit(machine.interrupt(times.passed(now)))
            continue
        if tracker.resyncing:
            # The rest of the interrupted packet belongs to slots we can no
            # longer identify, so it is discarded up to the next report.
            if event.type == e.EV_SYN and event.code == e.SYN_REPORT:
                tracker.reset(*read_state())
            continue
        frame = tracker.feed(event.type, event.code, event.value)
        if frame is not None:
            output.emit(machine.update(
                times.of(event, now), frame.count, frame.cx, frame.cy,
                frame.regrouped, frame.fingers, frame.pressed))


def run(device, tracker, machine, output, clock=time.monotonic,
        times: FrameTimes | None = None) -> None:
    times = times or FrameTimes(stamped=False)
    while True:
        deadline = machine.next_deadline()
        timeout = None if deadline is None else max(0.0, deadline - clock())
        ready, _, _ = select.select([device.fd], [], [], timeout)
        if ready:
            pump(device.read(), lambda: snapshot(device), tracker, machine,
                 output, clock(), times)
        else:
            output.emit(machine.tick(times.passed(clock())))


def glides(shell) -> MomentumMachine:
    """Momentum that holds off wherever a wheel steps instead of scrolling."""
    return MomentumMachine(may_glide=shell.may_glide)


RESTART_THE_SHELL = "press Alt+F2, type r, press Enter"
LOG_OUT = "log out and back in"


def extension_state(loaded: str | None, mode: str = "full") -> str:
    """What to tell someone about the shell extension."""
    if mode == "drag":
        # A Wayland shell cannot be restarted in place.
        if loaded is None:
            return ("not answering, so three-finger drag is off. If it is "
                    f"installed, {LOG_OUT} to load it")
        if loaded != __version__:
            return (f"answering, but the shell still runs version {loaded} "
                    f"and this is {__version__}. To load it, {LOG_OUT}")
        return f"answering, version {loaded}"
    if loaded is None:
        return ("not answering, so workspaces snap across and glides are held "
                "back only in the overview. If it is installed, restart the "
                f"shell to load it: {RESTART_THE_SHELL}")
    if loaded != __version__:
        return (f"answering, but the shell still runs version {loaded} and "
                f"this is {__version__}. Restart the shell: {RESTART_THE_SHELL}")
    return f"answering, version {loaded}"


def check() -> int:
    device = find_touchpad()
    if device is None:
        return no_touchpad()
    print(f"touchpad: {device.path} ({device.name})")
    device.close()
    if not os.access(UINPUT_PATH, os.W_OK):
        print(NO_UINPUT, file=sys.stderr)
        return EXIT_NO_ACCESS
    print(f"uinput: {UINPUT_PATH} writable")
    mode = session_mode()
    session = os.environ.get("XDG_SESSION_TYPE") or "unknown"
    print(f"session: {session}, so {MODE_SAYS[mode]}")
    print(f"extension: {extension_state(Shell().extension_version(), mode)}")
    return 0


def _terminate(signum, frame):
    raise SystemExit(0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="gnome-x11-touchpad-gestures",
        description="Three-finger drag, four-finger workspace switch "
                    "and momentum scrolling.")
    parser.add_argument(
        "--version", action="version",
        version=f"gnome-x11-touchpad-gestures {__version__}")
    parser.add_argument(
        "--check", action="store_true",
        help="verify access to the touchpad and uinput, then exit")
    args = parser.parse_args(argv)
    if args.check:
        return check()

    device = find_touchpad()
    if device is None:
        return no_touchpad()
    if not os.access(UINPUT_PATH, os.W_OK):
        print(NO_UINPUT, file=sys.stderr)
        return EXIT_NO_ACCESS

    signal.signal(signal.SIGTERM, _terminate)
    # Everything registered here is undone on the way out, last first, and
    # each step runs even if an earlier one failed.
    with contextlib.ExitStack() as cleanup:
        cleanup.callback(device.close)

        def create(capabilities, name):
            virtual = evdev.UInput(capabilities, name=name)
            cleanup.callback(virtual.close)
            return virtual

        mode = session_mode()
        pointer = create(
            {e.EV_REL: [e.REL_X, e.REL_Y], e.EV_KEY: [e.BTN_LEFT]},
            POINTER_NAME)
        if mode == "drag":
            # The desktop does the other gestures, so their devices are
            # not made and their actions not carried out.
            shell = Shell(complaint=DRAG_COMPLAINT)
            output = DragOnly(
                Output(pointer, NoDevice(), NoDevice(), shell),
                shell.three_fingers_free)
            machine = GestureMachine()
            shell.announce()
        else:
            keyboard = create(
                {e.EV_KEY: list(WORKSPACE_KEYS)}, KEYBOARD_NAME)
            # The motion axes and buttons are never used; they are what makes
            # libinput accept the device as a mouse and listen to its wheel.
            wheel = create(
                {e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL,
                            e.REL_WHEEL_HI_RES, e.REL_HWHEEL_HI_RES],
                 e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE]},
                WHEEL_NAME)
            shell = Shell()
            output = Output(pointer, keyboard, wheel, shell)
            machine = Machines(GestureMachine(), glides(shell))
        # A crash must never leave a drag or a modifier stuck.
        cleanup.callback(output.release_all)
        print(f"gnome-x11-touchpad-gestures {__version__}: "
              f"{MODE_SAYS[mode]}, "
              f"listening on {device.path} ({device.name})", flush=True)
        try:
            run(device, make_tracker(device), machine, output,
                times=FrameTimes(stamped=stamp_by_our_clock(device)))
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
