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
machine.update(t: float, count: int, cx: float, cy: float,
               regrouped: bool = False) -> list[Action]
machine.tick(t: float) -> list[Action]
machine.next_deadline() -> float | None
```

- `t`: monotonic time in seconds.
- `count`: number of fingers currently touching.
- `cx`, `cy`: centroid of the touching fingers in **millimetres**. The daemon
  converts device units to millimetres using the axis resolution the device
  reports, so thresholds are independent of the hardware.
- `regrouped`: true when a different set of fingers is being averaged than
  in the previous frame, even if `count` is the same.
- `tick` lets time-based transitions fire when no touchpad events arrive.
- `next_deadline` tells the daemon how long it may sleep.

Whenever `count` changes, the centroid jumps because a different set of
fingers is being averaged. The machine resets its reference point on every
count change and produces no motion for that frame.

The same jump happens when one finger lifts and another lands within a
single frame: the count is unchanged but the fingers are not. The slot
tracker compares each frame's set of tracking ids with the previous frame's
and reports `regrouped`; the machine treats it exactly like a count change.

### Output

```python
ButtonDown()              # press virtual left button
ButtonUp()                # release virtual left button
Move(dx: float, dy: float)  # relative pointer motion, in output counts
SwitchWorkspace(direction)  # Direction.NEXT or Direction.PREVIOUS
Overview(show: bool)        # open or close the Activities overview
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
  |dy| >= SWIPE_MM and |dy| >= SWIPE_AXIS_RATIO * |dx|
                              -> Overview, SWIPE_DONE
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
| `SWIPE_MM` | 15.0 | Travel needed for a four-finger swipe, sideways or vertical. |
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
with status 3 and a message naming the likely cause (udev rule not
installed). Status 3 means "device access is missing" and nothing else, so
the installer can tell it apart from a crash or a missing dependency.

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

Fingers that are already on the pad, at startup or after a `SYN_DROPPED`,
have no touch event left to send, so they cannot be counted. The daemon reads
`BTN_TOUCH` from the device at those two moments. If it is set, the tracker
reports no fingers until `BTN_TOUCH` goes to 0, which means the pad is empty.
That event also clears any finger whose lift was never seen.

The event loop waits on the device with a timeout taken from
`machine.next_deadline()` and calls `machine.tick` when the timeout expires.

### Writing

Three virtual devices, so that libinput classifies each one cleanly:

| Device | Capabilities |
|---|---|
| `finger-drag pointer` | `REL_X`, `REL_Y`, `BTN_LEFT` |
| `finger-drag keyboard` | `KEY_LEFTCTRL`, `KEY_LEFTALT`, `KEY_LEFT`, `KEY_RIGHT` |
| `finger-drag wheel` | high-resolution and notch wheel axes, both directions; see the momentum section |

| Action | Events |
|---|---|
| `ButtonDown` / `ButtonUp` | `BTN_LEFT` 1 / 0 |
| `Move` | `REL_X`, `REL_Y`, with fractional remainders carried to the next frame |
| `SwitchWorkspace(NEXT)` | press Ctrl, Alt, Right; release in reverse order |
| `SwitchWorkspace(PREVIOUS)` | press Ctrl, Alt, Left; release in reverse order |
| `Overview(show)` | no device; sets GNOME Shell's `OverviewActive` over D-Bus |

### Failure handling

- The daemon does not reconnect. If the touchpad disappears (suspend, driver
  reload) the read fails, the daemon exits non-zero and systemd restarts it.
- On every exit path, including `SIGTERM` and unhandled exceptions, the
  daemon releases `BTN_LEFT` and all keys before closing the virtual devices,
  so a crash cannot leave a drag or a modifier stuck.

## Component: `momentum.py` (added 2026-09-27)

Goal: mimic macOS momentum scrolling. After a two-finger flick the page keeps
gliding and slows to a stop, in every app, and any touch stops it.

libinput leaves momentum to each toolkit, so on X11 some apps glide and
others do not. This component adds one system-wide glide. It is pure logic
with the same interface as the gesture machine (`update`, `tick`,
`next_deadline`, `interrupt`), and the daemon runs the two side by side.

While fingers are on the pad libinput does the scrolling as before. The
component only measures the two-finger centroid's velocity over the last
`SPEED_WINDOW_S`. When the pad becomes empty it starts a glide if all of
these hold:

- the last speed measurement is at most `LIFT_GRACE_S` old, so fingers may
  leave a frame or two apart but a pause before lifting cancels the glide
- the speed is at least `GLIDE_MIN_MM_S`
- the touch never had three or more fingers, so drags and swipes never glide
- the two fingers travelled at least `GLIDE_MIN_TRAVEL_MM`, further than a
  tap ever does
- both fingers took part: each covered at least `TOGETHER_RATIO` of the
  motion they share, so a resting thumb or a pinch does not count
- the pad itself was not clicked during the touch
- no drag was still holding its button, which it does for
  `SPOIL_LINGER_S` after its fingers lift

A glide emits `Scroll(dx, dy)` every `GLIDE_FRAME_S`, in high-resolution
wheel units (120 per notch). Its speed is the starting speed times
`glide_speed(elapsed)`, an exponential decay with time constant
`GLIDE_TAU_S`, so the distance travelled is starting speed times
`GLIDE_TAU_S`. It ends below `GLIDE_STOP_UNITS_S`, or at once on any touch.
A glide that is served late, after a stall, skips the distance it missed
instead of delivering it as one jump. Starting speed is capped at
`GLIDE_MAX_MM_S`.

A glide always runs along the stronger axis only. (The first version let
nearly diagonal flicks glide on both axes; see below for why that changed.)

`interrupt(t)` is what the daemon calls on `SYN_DROPPED`. For the gesture
machine it is the same as every finger lifting. For momentum it is not a
lift: the glide ends and nothing new may start.

| Constant | Initial value | Meaning |
|---|---|---|
| `GLIDE_TAU_S` | 0.5 | Slowdown time constant |
| `GLIDE_MIN_MM_S` | 40.0 | Slowest flick that glides |
| `GLIDE_STOP_UNITS_S` | 480.0 | Speed at which a glide ends. Was 40, which left a three-second crawl |
| `GLIDE_MAX_MM_S` | 600.0 | Cap on the starting speed |
| `GLIDE_MIN_TRAVEL_MM` | 3.0 | Least two-finger travel that counts as a scroll |
| `TOGETHER_RATIO` | 0.5 | Share of the common motion each finger must cover |
| `SPOIL_LINGER_S` | 0.3 | How long after a drag nothing may glide |
| `GLIDE_FRAME_S` | 0.008 | Time between glide steps |
| `SCROLL_UNITS_PER_MM` | 84.0 | Wheel units per millimetre of finger travel: 39.37 units per mm at 1000 dpi, times libinput's unaccelerated touchpad factor 0.9 x 0.2968, times 8 wheel units each. Was 94, which missed the 0.9 |
| `NATURAL_SCROLL` | True | The page follows the fingers |
| `SPEED_WINDOW_S` | 0.06 | Span over which speed is measured |
| `SPEED_MIN_SPAN_S` | 0.02 | Shortest span that gives a usable speed |
| `LIFT_GRACE_S` | 0.10 | How far apart fingers may lift |

Output goes to a third virtual device, `finger-drag wheel`, as
`REL_WHEEL_HI_RES` and `REL_HWHEEL_HI_RES`, with a `REL_WHEEL` or
`REL_HWHEEL` notch for every 120 units for programs that predate
high-resolution scrolling. The device also declares motion axes and buttons
it never uses, because that is what makes libinput accept it as a mouse.

Not mimicked: the rubber-band bounce at the end of a page, which each app
draws itself, and a glide staying with its original window, since X11 sends
scrolling to the window under the pointer.

Double momentum: GTK3, GTK4 and Firefox start their own glide when the
fingers lift, and cancel it on the next scroll event from any device. The
daemon's first wheel event therefore replaces their glide instead of adding
to it. This was established by reading their source, not by measurement. If
an app is seen to overshoot, the follow-up is a skip list keyed on the window
under the pointer.

A glide runs on one axis because libinput keeps a single scroll direction
per device: a wheel reporting both axes at once makes it hold events back
until half a notch has built up, which shows as stutter.

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
3. Verify the current user can open both devices. If the check reports
   missing access (status 3), say that logging out and back in is required
   and stop. If it fails any other way, say so and do not suggest a re-login.
4. Copy the unit to `~/.config/systemd/user/`, reload, enable and start it.

Before step 1 it confirms `python3-evdev` can be imported, so a missing
dependency is reported before anything on the system is changed.

`install/uninstall.sh` disables and removes the service, removes the rule,
reloads udev, and strips this user's ACL entry from `/dev/uinput` and the
touchpad node. The last step is needed because removing the rule does not
take back access that was already granted.

Both scripts run only when executed. Sourcing them defines their functions
and does nothing else, which is how the tests reach them.

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
| Four fingers travel up past `SWIPE_MM` | one `Overview(show=True)` |
| Four fingers travel down past `SWIPE_MM` | one `Overview(show=False)` |
| Four fingers travel diagonally | no actions |
| GNOME Shell does not answer, or the bus is unreachable | logged, daemon carries on, reconnects next time |
| Fourth finger lands during a drag | `ButtonUp`, then swipe tracking |
| One and two fingers, any motion | no actions |
| Three fingers cross `DRAG_START_MM` before `DRAG_SETTLE_S` has passed | no `ButtonDown` until it has |
| Fourth finger lands 21 ms after three that are already moving | one `SwitchWorkspace`, no button events |
| Contact flagged as a palm | not counted as a finger |
| `SYN_DROPPED` mid-drag | drag ends, button released after `DRAG_RELEASE_S` |
| Events between `SYN_DROPPED` and the next `SYN_REPORT` | discarded |
| `SIGTERM`, or the touchpad vanishing, mid-drag | button and keys released, devices closed |
| One finger lifts and another lands in the same frame | no `Move` for that frame |
| Fingers already down at startup or after `SYN_DROPPED` | nothing reported until the pad has been empty |
| `--check` without device access | exit status 3 |
| Installer's check fails with a status other than 3 | message does not suggest logging out |
| Sourcing `install.sh` or `uninstall.sh` | nothing runs |

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

## Overview gesture (added 2026-09-27)

A four-finger swipe up opens GNOME's Activities overview, the view that shows
the windows and the workspace strip. A swipe down closes it. This was listed
as out of scope in the first version of this spec.

The only key that opens the overview is a tap of `Super`, and a key can only
toggle: swiping up with the overview already open would close it. So the
daemon sets the state it wants instead, through the `OverviewActive`
property of `org.gnome.Shell` on the session bus. Each direction then has one
meaning, and repeating a swipe changes nothing.

`finger_drag/shell.py` holds that one call. It connects on first use, passes
a timeout of `OVERVIEW_TIMEOUT_S` (0.5 s) because the call runs on the
daemon's only thread, and treats every failure the same way: log it, drop
the connection so the next request reconnects, and carry on. The overview is
a convenience and must never take the drag handling down.

This is the one action that does not go through a virtual device, and the
one part that works on GNOME only. It uses `python3-dbus`, which is already
installed.

## Out of scope

Configuration file, animated workspace
transitions, GUI or tray icon, multiple touchpads, external trackpads,
Wayland-specific handling.
