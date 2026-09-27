"""Reads the touchpad, runs the gesture machine, writes to virtual devices."""
from __future__ import annotations

import argparse
import os
import select
import signal
import sys
import time

import evdev
from evdev import ecodes as e

from finger_drag.gestures import GestureMachine
from finger_drag.momentum import MomentumMachine
from finger_drag.output import WORKSPACE_KEYS, Output
from finger_drag.slots import (
    FALLBACK_HEIGHT_MM, FALLBACK_WIDTH_MM, SlotTracker, units_per_mm)

UINPUT_PATH = "/dev/uinput"
# Distinct from a crash (1) or a usage error (2) so that install.sh can tell
# "log out and back in" apart from every other failure.
EXIT_NO_ACCESS = 3
NO_TOUCHPAD = (
    "finger-drag: no accessible touchpad found. Is "
    "/etc/udev/rules.d/71-finger-drag.rules installed? "
    "Log out and back in after installing it.")
NO_UINPUT = (
    f"finger-drag: cannot write to {UINPUT_PATH}. Is "
    "/etc/udev/rules.d/71-finger-drag.rules installed? "
    "Log out and back in after installing it.")


class Machines:
    """Runs several gesture machines side by side as if they were one."""

    def __init__(self, *machines) -> None:
        self._machines = machines

    def update(self, t: float, count: int, cx: float, cy: float,
               regrouped: bool = False) -> list:
        return [action for machine in self._machines
                for action in machine.update(t, count, cx, cy, regrouped)]

    def tick(self, t: float) -> list:
        return [action for machine in self._machines
                for action in machine.tick(t)]

    def next_deadline(self) -> float | None:
        deadlines = [machine.next_deadline() for machine in self._machines]
        return min((d for d in deadlines if d is not None), default=None)


def is_touchpad(capabilities: dict, props: list) -> bool:
    abs_codes = capabilities.get(e.EV_ABS, [])
    return (e.INPUT_PROP_POINTER in props
            and e.ABS_MT_SLOT in abs_codes
            and e.ABS_MT_POSITION_X in abs_codes)


def find_touchpad():
    for path in sorted(evdev.list_devices()):
        try:
            device = evdev.InputDevice(path)
        except OSError:
            continue
        if is_touchpad(device.capabilities(absinfo=False), device.input_props()):
            return device
        device.close()
    return None


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
            output.emit(machine.update(now, 0, 0.0, 0.0))
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
                now, frame.count, frame.cx, frame.cy, frame.regrouped))


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
        print(NO_TOUCHPAD, file=sys.stderr)
        return EXIT_NO_ACCESS
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
        prog="finger-drag",
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
        print(NO_TOUCHPAD, file=sys.stderr)
        return EXIT_NO_ACCESS
    if not os.access(UINPUT_PATH, os.W_OK):
        print(NO_UINPUT, file=sys.stderr)
        return EXIT_NO_ACCESS

    signal.signal(signal.SIGTERM, _terminate)
    pointer = evdev.UInput(
        {e.EV_REL: [e.REL_X, e.REL_Y], e.EV_KEY: [e.BTN_LEFT]},
        name="finger-drag pointer")
    keyboard = evdev.UInput(
        {e.EV_KEY: list(WORKSPACE_KEYS)}, name="finger-drag keyboard")
    # The motion axes and buttons are never used; they are what makes
    # libinput accept the device as a mouse and listen to its wheel.
    wheel = evdev.UInput(
        {e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL,
                    e.REL_WHEEL_HI_RES, e.REL_HWHEEL_HI_RES],
         e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE]},
        name="finger-drag wheel")
    output = Output(pointer, keyboard, wheel)
    print(f"finger-drag: listening on {device.path} ({device.name})", flush=True)
    try:
        run(device, make_tracker(device),
            Machines(GestureMachine(), MomentumMachine()), output)
    except KeyboardInterrupt:
        pass
    finally:
        # A crash must never leave a drag or a modifier stuck.
        output.release_all()
        pointer.close()
        keyboard.close()
        wheel.close()
        device.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
