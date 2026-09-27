"""Reads the touchpad, runs the gesture machine, writes to virtual devices."""
from __future__ import annotations

import argparse
import contextlib
import os
import select
import signal
import sys
import time

import evdev
from evdev import ecodes as e

from gnome_x11_touchpad_gestures.gestures import GestureMachine
from gnome_x11_touchpad_gestures.momentum import MomentumMachine
from gnome_x11_touchpad_gestures.output import WORKSPACE_KEYS, Output
from gnome_x11_touchpad_gestures.shell import Shell
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


def pump(events, read_state, tracker, machine, output, now: float) -> None:
    for event in events:
        if event.type == e.EV_SYN and event.code == e.SYN_DROPPED:
            # The kernel dropped events, so slot state is stale. End whatever
            # gesture was in progress; fingers must touch again to start one.
            tracker.begin_resync()
            output.emit(machine.interrupt(now))
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
                now, frame.count, frame.cx, frame.cy, frame.regrouped,
                frame.fingers, frame.pressed))


def run(device, tracker, machine, output, clock=time.monotonic) -> None:
    while True:
        deadline = machine.next_deadline()
        timeout = None if deadline is None else max(0.0, deadline - clock())
        ready, _, _ = select.select([device.fd], [], [], timeout)
        if ready:
            pump(device.read(), lambda: snapshot(device), tracker, machine,
                 output, clock())
        else:
            output.emit(machine.tick(clock()))


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
    return 0


def _terminate(signum, frame):
    raise SystemExit(0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="gnome-x11-touchpad-gestures",
        description="Three-finger drag, four-finger workspace switch "
                    "and momentum scrolling.")
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

        pointer = create(
            {e.EV_REL: [e.REL_X, e.REL_Y], e.EV_KEY: [e.BTN_LEFT]},
            POINTER_NAME)
        keyboard = create(
            {e.EV_KEY: list(WORKSPACE_KEYS)}, KEYBOARD_NAME)
        # The motion axes and buttons are never used; they are what makes
        # libinput accept the device as a mouse and listen to its wheel.
        wheel = create(
            {e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL,
                        e.REL_WHEEL_HI_RES, e.REL_HWHEEL_HI_RES],
             e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE]},
            WHEEL_NAME)
        output = Output(pointer, keyboard, wheel, Shell())
        # A crash must never leave a drag or a modifier stuck.
        cleanup.callback(output.release_all)
        print(f"gnome-x11-touchpad-gestures: listening on {device.path} ({device.name})",
              flush=True)
        try:
            run(device, make_tracker(device),
                Machines(GestureMachine(), MomentumMachine()), output)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
