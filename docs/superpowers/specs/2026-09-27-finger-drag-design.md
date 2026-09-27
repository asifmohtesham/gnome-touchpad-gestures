# finger-drag: three-finger drag and four-finger workspace switch

Date: 2026-09-27
Status: design approved in conversation, awaiting spec review

## Goal

Give this laptop two macOS-style touchpad gestures:

1. **Three-finger drag**: moving three fingers on the touchpad drags whatever
   is under the pointer, as if the left button were held.
2. **Four-finger workspace switch**: swiping four fingers sideways moves to
   the adjacent GNOME workspace.

Success means both gestures work in the current session after login with no
manual start, and existing touchpad behaviour (pointing, two-finger scroll,
tap-to-click, three-finger tap as middle click) is unchanged.

## Environment

| Item | Value |
|---|---|
| OS | Ubuntu 24.04.4 |
| Desktop | GNOME Shell 46 on **X11** |
| Wayland | Disabled in `/etc/gdm3/custom.conf`; stays disabled (RustDesk compatibility) |
| Touchpad | `SYNA8017:00 06CB:CEB2 Touchpad`, 106 x 52 mm, multitouch protocol B, up to 5 fingers |
| libinput | 1.25 (no native three-finger drag; that arrived in 1.28) |
| Python | 3.12 with `python3-evdev` 1.7.0 already installed |
| Workspace keys | `Ctrl+Alt+Left` / `Ctrl+Alt+Right` already bound |

## Constraints

- Stay on X11.
- Install no new packages and add no third-party repositories.
- Do not add the user to the `input` group.
- One `sudo` step at install time is acceptable; the daemon itself runs
  unprivileged.

## Approach

A single unprivileged Python daemon reads raw multitouch events from the
touchpad through evdev and writes synthesised events to virtual devices
through uinput. It listens passively and never grabs the touchpad, so
libinput and the X server keep receiving every event.

This works because, with three or more fingers down, libinput classifies the
motion as a swipe gesture and stops moving the pointer, and GNOME on X11
ignores those gesture events. The daemon fills that gap.

Rejected alternatives:

- **touchegg 2.x + X11 Gestures extension**: needs a PPA (apt only carries
  1.1.1) and does not provide a hold-and-drag action.
- **Existing three-finger-drag tools plus a separate swipe tool**: needs a
  Rust toolchain, and two programs would interpret the same touchpad with
  independent thresholds.

## Layout

```
~/finger-drag/
  finger_drag/
    __init__.py
    gestures.py        pure state machine, no device access
    daemon.py          device discovery, event loop, uinput output
  tests/
    test_gestures.py   stdlib unittest
  install/
    71-finger-drag.rules
    finger-drag.service
    install.sh
  README.md
```

## Component: `gestures.py`

Pure logic. It imports nothing from evdev and performs no I/O, so it can be
tested with synthetic input.

### Input

The daemon calls the state machine once per touchpad frame (one `SYN_REPORT`):

```python
machine.update(t: float, count: int, cx: float, cy: float) -> list[Action]
machine.tick(t: float) -> list[Action]
machine.next_deadline() -> float | None
```

- `t`: monotonic time in seconds.
- `count`: number of fingers currently touching.
- `cx`, `cy`: centroid of the touching fingers in **millimetres**. The daemon
  converts device units to millimetres using the axis resolution the device
  reports, so thresholds are independent of the hardware.
- `tick` lets time-based transitions fire when no touchpad events arrive.
- `next_deadline` tells the daemon how long it may sleep.

Whenever `count` changes, the centroid jumps because a different set of
fingers is being averaged. The machine resets its reference point on every
count change and produces no motion for that frame.

### Output

```python
ButtonDown()              # press virtual left button
ButtonUp()                # release virtual left button
Move(dx: float, dy: float)  # relative pointer motion, in output counts
SwitchWorkspace(direction)  # Direction.NEXT or Direction.PREVIOUS
```

### States

```
IDLE
  count == 3 for at least DRAG_SETTLE_S
    and travel since touchdown >= DRAG_START_MM           -> ButtonDown, DRAGGING
  count >= 4                                              -> SWIPE_TRACKING

DRAGGING
  count == 3   -> Move(delta * POINTER_COUNTS_PER_MM)
  count >= 4   -> ButtonUp, SWIPE_TRACKING
  count < 3    -> RELEASE_WAIT, deadline = t + DRAG_RELEASE_S

RELEASE_WAIT
  count == 3 before deadline  -> DRAGGING (button stays down)
  count >= 4                  -> ButtonUp, SWIPE_TRACKING
  deadline reached            -> ButtonUp, IDLE

SWIPE_TRACKING
  |dx| >= SWIPE_MM and |dx| >= SWIPE_AXIS_RATIO * |dy|
                              -> SwitchWorkspace, SWIPE_DONE
  count == 0                  -> IDLE

SWIPE_DONE
  count == 0                  -> IDLE
  anything else               -> ignored
```

`SWIPE_DONE` guarantees one switch per swipe: the fingers must fully lift
before another switch can fire.

Fingers rarely land in the same frame. If three are down and moving when a
fourth arrives a few frames later, the drag threshold could be crossed in
between, producing a click on whatever is under the pointer. `DRAG_SETTLE_S`
prevents that: the count must have been exactly three for that long before
the button goes down. Travel still accumulates during the wait. (Added after
the whole-branch review, 2026-09-27.)

In `SWIPE_TRACKING`, travel accumulates only over frames where `count >= 4`.
Frames with one to three fingers are ignored and leave the state unchanged,
so fingers lifting unevenly at the end of a swipe cannot start a drag.

### Tunables

Module-level constants at the top of `gestures.py`:

| Constant | Initial value | Meaning |
|---|---|---|
| `DRAG_START_MM` | 2.0 | Travel needed before a three-finger touch becomes a drag. Keeps three-finger tap working. |
| `DRAG_SETTLE_S` | 0.05 | How long the count must stay at three before a drag can start. Keeps a four-finger swipe from clicking as the fingers land. |
| `DRAG_RELEASE_S` | 0.3 | How long the button stays held after fingers lift. |
| `POINTER_COUNTS_PER_MM` | 12.0 | Pointer speed during a drag. |
| `SWIPE_MM` | 15.0 | Sideways travel needed to switch workspace. |
| `SWIPE_AXIS_RATIO` | 1.5 | How much horizontal travel must exceed vertical. |

### Swipe direction

Fingers moving **left** produce `Direction.NEXT`; fingers moving **right**
produce `Direction.PREVIOUS`. Content follows the fingers, consistent with
the natural scrolling already enabled on this machine.

## Component: `daemon.py`

### Device discovery

Enumerate `/dev/input/event*` and pick the first device that has
`INPUT_PROP_POINTER`, `ABS_MT_SLOT` and `ABS_MT_POSITION_X`. Do not hard-code
`event8`; event numbers can change between boots. If no device matches, exit
non-zero with a message naming the likely cause (udev rule not installed).

### Reading

Track per-slot state from `ABS_MT_SLOT`, `ABS_MT_TRACKING_ID`,
`ABS_MT_POSITION_X` and `ABS_MT_POSITION_Y`. A slot is active while its
tracking id is not -1. On each `SYN_REPORT`, compute `count` and the centroid
of active slots and call `machine.update`.

A slot whose `ABS_MT_TOOL_TYPE` is `MT_TOOL_PALM` is not counted, matching
what libinput does with contacts the firmware classifies as palms.

On `SYN_DROPPED` (the kernel's event buffer overran) slot state is stale. The
daemon ends any gesture in progress, discards events up to and including the
next `SYN_REPORT`, then starts again from an empty slot table and the
device's current slot. Fingers must touch again to start a gesture.

The event loop waits on the device with a timeout taken from
`machine.next_deadline()` and calls `machine.tick` when the timeout expires.

### Writing

Two virtual devices, so that libinput classifies each one cleanly:

| Device | Capabilities |
|---|---|
| `finger-drag pointer` | `REL_X`, `REL_Y`, `BTN_LEFT` |
| `finger-drag keyboard` | `KEY_LEFTCTRL`, `KEY_LEFTALT`, `KEY_LEFT`, `KEY_RIGHT` |

| Action | Events |
|---|---|
| `ButtonDown` / `ButtonUp` | `BTN_LEFT` 1 / 0 |
| `Move` | `REL_X`, `REL_Y`, with fractional remainders carried to the next frame |
| `SwitchWorkspace(NEXT)` | press Ctrl, Alt, Right; release in reverse order |
| `SwitchWorkspace(PREVIOUS)` | press Ctrl, Alt, Left; release in reverse order |

### Failure handling

- The daemon does not reconnect. If the touchpad disappears (suspend, driver
  reload) the read fails, the daemon exits non-zero and systemd restarts it.
- On every exit path, including `SIGTERM` and unhandled exceptions, the
  daemon releases `BTN_LEFT` and all keys before closing the virtual devices,
  so a crash cannot leave a drag or a modifier stuck.

## Permissions

`install/71-finger-drag.rules`:

```
ACTION!="remove", SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="SYNA8017:00 06CB:CEB2 Touchpad", TAG+="uaccess"
KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
```

`uaccess` makes logind grant an ACL to the user at the active seat and remove
it at logout. The file name must sort before `73-seat-late.rules`, which is
where the tag is applied.

Trade-off accepted: write access to `/dev/uinput` lets this user's processes
inject input. On X11 any client can already do that through XTest, so this
adds no meaningful exposure in the current session.

## Service

`install/finger-drag.service`, installed to `~/.config/systemd/user/`:

```ini
[Unit]
Description=Three-finger drag and four-finger workspace switch
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=/usr/bin/python3 -m finger_drag.daemon
WorkingDirectory=%h/finger-drag
Restart=on-failure
RestartSec=2

[Install]
WantedBy=graphical-session.target
```

## Installation

`install/install.sh` does the following and is safe to run repeatedly:

1. Copy the udev rule to `/etc/udev/rules.d/` (the only step needing `sudo`).
2. Reload udev rules and trigger the touchpad and uinput devices.
3. Verify the current user can open both devices; if not, say that logging
   out and back in is required and stop.
4. Copy the unit to `~/.config/systemd/user/`, reload, enable and start it.

The README documents uninstalling: disable the service, remove the unit and
the rule, reload udev.

## Testing

### Automated (`tests/test_gestures.py`, stdlib `unittest`)

| Case | Expected |
|---|---|
| Three fingers down and up with travel under `DRAG_START_MM` | no actions |
| Three fingers travel past `DRAG_START_MM` | `ButtonDown`, then `Move` per frame |
| Frame where finger count changes | no `Move` for that frame |
| Fingers lift, deadline passes | `ButtonUp` from `tick` |
| Fingers lift, three return before deadline | no `ButtonUp`; `Move` resumes |
| Four fingers travel left past `SWIPE_MM` | one `SwitchWorkspace(NEXT)` |
| Four fingers travel right past `SWIPE_MM` | one `SwitchWorkspace(PREVIOUS)` |
| Four fingers keep moving after a switch | no second switch until full lift |
| Four fingers travel vertically | no actions |
| Fourth finger lands during a drag | `ButtonUp`, then swipe tracking |
| One and two fingers, any motion | no actions |
| Three fingers cross `DRAG_START_MM` before `DRAG_SETTLE_S` has passed | no `ButtonDown` until it has |
| Fourth finger lands 21 ms after three that are already moving | one `SwitchWorkspace`, no button events |
| Contact flagged as a palm | not counted as a finger |
| `SYN_DROPPED` mid-drag | drag ends, button released after `DRAG_RELEASE_S` |
| Events between `SYN_DROPPED` and the next `SYN_REPORT` | discarded |
| `SIGTERM`, or the touchpad vanishing, mid-drag | button and keys released, devices closed |

Run with `python3 -m unittest discover -s tests`.

### Manual checklist (in README)

- Drag a window by its title bar with three fingers.
- Select text with three fingers.
- Lift and re-place fingers mid-drag; the drag continues.
- Three-finger tap still pastes (middle click).
- Two-finger scroll and one-finger pointing are unchanged.
- Four-finger swipe left and right switches workspace, once per swipe.
- Suspend and resume; gestures work again within a few seconds.
- `systemctl --user stop finger-drag` mid-drag releases the button.

## Known behaviour

- While the button is held in `RELEASE_WAIT`, libinput still moves the
  pointer for any one finger left on the pad, so the dragged item can move
  slightly. This matches macOS and is accepted.
- Workspace changes snap once per swipe. They do not follow the fingers.
- X applies its mouse acceleration profile to the virtual pointer.
  `POINTER_COUNTS_PER_MM` is tuned by feel with that in place.

## Out of scope

Configuration file, vertical four-finger gestures, animated workspace
transitions, GUI or tray icon, multiple touchpads, external trackpads,
Wayland-specific handling.
