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
from finger_drag.output import WORKSPACE_KEYS, Output
from finger_drag.slots import (
    FALLBACK_HEIGHT_MM, FALLBACK_WIDTH_MM, SlotTracker, units_per_mm)

UINPUT_PATH = "/dev/uinput"
NO_TOUCHPAD = (
    "finger-drag: no accessible touchpad found. Is "
    "/etc/udev/rules.d/71-finger-drag.rules installed? "
    "Log out and back in after installing it.")
NO_UINPUT = (
    f"finger-drag: cannot write to {UINPUT_PATH}. Is "
    "/etc/udev/rules.d/71-finger-drag.rules installed? "
    "Log out and back in after installing it.")


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
        current_slot=device.absinfo(e.ABS_MT_SLOT).value,
    )


def pump(events, current_slot, tracker, machine, output, now: float) -> None:
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
                tracker.reset(current_slot())
            continue
        frame = tracker.feed(event.type, event.code, event.value)
        if frame is not None:
            output.emit(machine.update(now, frame.count, frame.cx, frame.cy))


def run(device, tracker, machine, output, clock=time.monotonic) -> None:
    def current_slot() -> int:
        return device.absinfo(e.ABS_MT_SLOT).value

    while True:
        deadline = machine.next_deadline()
        timeout = None if deadline is None else max(0.0, deadline - clock())
        ready, _, _ = select.select([device.fd], [], [], timeout)
        if ready:
            pump(device.read(), current_slot, tracker, machine, output, clock())
        else:
            output.emit(machine.tick(clock()))


def check() -> int:
    device = find_touchpad()
    if device is None:
        print(NO_TOUCHPAD, file=sys.stderr)
        return 1
    print(f"touchpad: {device.path} ({device.name})")
    device.close()
    if not os.access(UINPUT_PATH, os.W_OK):
        print(NO_UINPUT, file=sys.stderr)
        return 1
    print(f"uinput: {UINPUT_PATH} writable")
    return 0


def _terminate(signum, frame):
    raise SystemExit(0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="finger-drag",
        description="Three-finger drag and four-finger workspace switch.")
    parser.add_argument(
        "--check", action="store_true",
        help="verify access to the touchpad and uinput, then exit")
    args = parser.parse_args(argv)
    if args.check:
        return check()

    device = find_touchpad()
    if device is None:
        print(NO_TOUCHPAD, file=sys.stderr)
        return 1
    if not os.access(UINPUT_PATH, os.W_OK):
        print(NO_UINPUT, file=sys.stderr)
        return 1

    signal.signal(signal.SIGTERM, _terminate)
    pointer = evdev.UInput(
        {e.EV_REL: [e.REL_X, e.REL_Y], e.EV_KEY: [e.BTN_LEFT]},
        name="finger-drag pointer")
    keyboard = evdev.UInput(
        {e.EV_KEY: list(WORKSPACE_KEYS)}, name="finger-drag keyboard")
    output = Output(pointer, keyboard)
    print(f"finger-drag: listening on {device.path} ({device.name})", flush=True)
    try:
        run(device, make_tracker(device), GestureMachine(), output)
    except KeyboardInterrupt:
        pass
    finally:
        # A crash must never leave a drag or a modifier stuck.
        output.release_all()
        pointer.close()
        keyboard.close()
        device.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
