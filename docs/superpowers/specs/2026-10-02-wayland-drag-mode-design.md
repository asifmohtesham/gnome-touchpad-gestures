# Three-finger drag on Wayland: design

Date: 2026-10-02. Target version: 0.3.0.

This adds a second mode to gnome-touchpad-gestures. The first design,
`2026-09-27-gnome-touchpad-gestures-design.md`, still describes the X11
mode, which this leaves as it is.

## Why

The machine this was written on moved to Ubuntu 26.04: GNOME Shell 50, and
no X11 session for GNOME any more. On Wayland GNOME has its own touchpad
gestures and its toolkits their own momentum, so most of what this project
does is no longer needed there. One thing is missing: three-finger drag.
GNOME 50 has no setting for it.

## Goal

On GNOME on Wayland, three fingers moving on the touchpad drag with the
left button held, as they do in the X11 mode. Nothing else is added there.

Success is:

- A window is dragged by its title bar, and text is selected, with three
  fingers, without the workspace moving or the overview opening.
- A four-finger swipe still switches workspace and opens the overview.
- With the daemon stopped, crashed or uninstalled, a three-finger swipe
  does what GNOME intends again. It is never left doing nothing.
- The X11 mode behaves exactly as in 0.2.1.

## What was tried first (2026-10-01)

Three routes, on this machine, by hand.

| Route | Result |
|---|---|
| A. libinput does the drag itself | Not possible. libinput 1.31 has three-finger drag, but mutter 50 never switches it on and has no setting for it. libinput's plugins could have done it, but Ubuntu builds libinput without them: the library holds no Lua and links to none |
| B. The daemon drags; an extension keeps GNOME's swipes off three fingers | Works. Drag, text selection and four-finger swipes were all confirmed |
| C. An extension alone turns the swipe into a drag, with a virtual pointer made inside the shell | Failed. The button went down, the window did not follow and the button never came up; clicks stopped working until logout |

Without the extension of route B, the daemon's drag works for about a
second and then GNOME's swipe cuts in: the shell acts on any swipe of three
fingers or more (`GESTURE_FINGER_COUNT = 3` in its `swipeTracker.js`) once
it has gone 16 units.

Route C most likely failed because the press starts a window drag, and
the compositor's grab then keeps the rest of the gesture from the handler
that should have moved the pointer and released the button. That was not
proven. It is why this design keeps one rule: **nothing inside the shell
ever presses a button or moves the pointer.** The daemon's pointer is an
input device like any other, and does not depend on how the shell hands
events round.

## The two modes

The daemon picks its mode when it starts, from `XDG_SESSION_TYPE`:

| `XDG_SESSION_TYPE` | Mode | What the daemon does |
|---|---|---|
| `wayland` | drag | Three-finger drag, and nothing else |
| anything else | full | Everything, as in 0.2.1 |

"Anything else" is the full mode because that is what the daemon has done
until now when run by hand outside a session. The service itself starts
only in an X11 or a Wayland session.

## The daemon in drag mode

Unchanged: finding the touchpad, the slot tracker, the frame times, and
the gesture machine with its constants. A drag starts, moves and is let go
exactly as in the X11 mode.

Different in drag mode:

- **Machines.** Only the gesture machine runs. The momentum machine is not
  made.
- **Devices.** Only the virtual pointer is made, not the keyboard or the
  wheel.
- **Actions.** The gesture machine still makes swipe and overview actions
  when four fingers move. They are dropped: GNOME does those itself. Only
  `ButtonDown`, `Move` and `ButtonUp` are carried out.
- **A name on the session bus.** The daemon takes the name
  `io.github.asifmohtesham.Gestures.Daemon`, on the connection it already
  keeps for talking to the shell. The bus takes the name back by itself
  when the daemon stops for any reason. If the name cannot be had, that is
  logged once and the daemon carries on; it then never drags, by the next
  rule.
- **A drag needs the extension's word.** At every `ButtonDown` the daemon
  asks the extension `ThreeFingersFree`. Unless the answer is yes, that
  whole drag is dropped: its press, its moves and its release. Without
  this, a daemon running before the extension is loaded would drag for a
  second and then have GNOME's swipe land on top of it, with the button
  held.

The question is put through the existing `Asked` logic: half a second at
most, and an extension that does not answer is left alone for a while and
complained of once. That while is one second here (`DRAG_RETRY_S`), not
the five of the X11 mode: while it lasts three fingers do nothing at all,
since the extension goes on swallowing and the daemon does not drag, and
the question is only put once for each drag. A daemon that could not take
its name does not ask and does not drag: if another program holds the
name, the extension would say yes to both. (Both from the review of the
branch, 2026-10-02.) The complaint names the mode's own consequence,
that three-finger drag is off.

One known rough edge: the first drag in the moment after the daemon starts
may be dropped, because the extension learns of the daemon's name a moment
after the daemon has it.

### Where this goes in the code

- `daemon.py`: `session_mode(environ)` returns `"drag"` or `"full"`.
  `main` builds the devices, machines and output for the mode.
- `output.py`: a class `DragOnly` wraps an `Output`. Its `emit` keeps the
  three drag actions and applies the rule above; `release_all` passes
  through.
- `shell.py`: `announce()` takes the name; `three_fingers_free()` asks the
  extension. The complaint text is given to `Shell` by the mode.

## The extension

One extension, as now, with one more behaviour. Which one it takes is
decided when it is enabled, by `Meta.is_wayland_compositor()` where the
shell has that call. GNOME 50 dropped it together with the X11 session, so
a shell that cannot be asked is taken to run Wayland. (Found at the first
real load, 2026-10-02: the extension failed on that call, which the test
stand-in had offered and the real shell did not.)

| | On X11 | On Wayland |
|---|---|---|
| `Version` | answered | answered |
| `PointerOverWindow`, `SwipeBegin` and the rest | as in 0.2.1 | answer false, or do nothing |
| `ThreeFingersFree` | false | true while the daemon's name is on the bus |
| Touches the shell's private swipe tracker | yes | never |
| Swallows three-finger swipes | never | while the daemon's name is on the bus |

### Swallowing a swipe

The extension puts an action on the stage in the capture phase, which
runs before the shell's own swipe handling, and has it handle the events
to be swallowed. (At first it connected a handler to
`captured-event::touchpad`; see the change in 0.3.1 below.) Only the public
event calls are used:
`type()`, `get_touchpad_gesture_finger_count()`, `get_gesture_phase()`.

(Changed in 0.3.1.) The handler on the stage was the wrong tool. While a
button is held, Clutter sends every event of that pointer down a chain
fixed at the press, and an event that a handler on an actor stops makes it
cancel every gesture in that chain (`clutter_sprite_remove_all_actions_from_chain`).
The daemon holds the button all through a drag, so each swallowed swipe
event cancelled the shell's own drag of a window in the overview: it
worked only when the drag got going before the first swipe event came, or
when the fingers went down two and then one, which makes no swipe. The
swallowing is now done by an action on the stage in the capture phase
(a `Clutter.Action` with `vfunc_handle_event`). An event that an action
handles ends there and cancels nothing. Windows on the desktop were never
affected: their drag is the compositor's, not a gesture of the shell's.

A swipe is a run of events: a begin, updates, an end or a cancel. It is
swallowed whole or not at all. The decision is made at its begin: swallow
if it has exactly three fingers and the daemon is there. Every later event
of that swipe follows the decision made at its begin, whatever has
happened to the daemon since. Otherwise a daemon that started or stopped
in the middle of a swipe would leave the shell with half of one: a begin
without an end, and a workspace stuck part way across.

(Changed in 0.4.1.) A swipe whose begin was not seen is decided where it
is first seen, by the same rule. At first it was let through. But while
another actor holds a grab, the shell's own drag of a window in the
overview for one, events do not pass the stage; a swipe that began then
could reach the shell from the middle, and the shell starts a swipe of its
own from any update. Found by the seventh review; not seen in use.

(Also 0.4.1.) `enable()` exports the object on the bus before it sets
anything else up. The shell does not call `disable()` on an extension whose
`enable()` threw, and the export is the step that can throw: an action
already on the stage would have gone on swallowing.

A swipe of four fingers or more is never swallowed. Pinch and hold
gestures are not swipes and are never looked at.

### Knowing the daemon is there

`Gio.bus_watch_name_on_connection` on the session bus, for the daemon's
name. The watch is set up in `enable()` and taken down in `disable()`,
along with the action on the stage.

### Where this goes in the code

- `gestures.js`: a class `SwipeFilter`, pure, with
  `handle(phase, fingers, daemonPresent)` returning whether to
  swallow. Tested with gjs like the rest of that file.
- `extension.js`: the glue. It imports `gi://Meta` as well.
- `metadata.json`: `"shell-version": ["46", "50"]`. The Wayland behaviour
  uses nothing private, but it has been seen to work on 50 only, and the
  X11 behaviour on 46 only. Versions between are not named.
- Shell 46 has a Wayland session too, and there the extension would load.
  It frees three fingers only where the shell's own version
  (`Config.PACKAGE_VERSION`) is 50; elsewhere on Wayland it connects
  nothing and answers `ThreeFingersFree` with no, so the daemon does not
  drag. An older shell is believed to handle a swipe before an extension
  can see it, which would let its swipe land on a held drag. (From the
  review of the branch, 2026-10-02.)

## The service

```
ConditionEnvironment=|XDG_SESSION_TYPE=x11
ConditionEnvironment=|XDG_SESSION_TYPE=wayland
```

The `|` makes each a condition of which one is enough. The description
line no longer lists gestures the Wayland mode does not have.

On a Wayland session that is not GNOME the service starts, the extension
is not there, and no drag is ever made. That is harmless and is not
treated specially.

## Installing

The steps do not change: the udev rule, the program, the extension, the
service. Three things do:

- **How to load the extension.** On Wayland the shell cannot be restarted
  in place. Where the installer and `--check` say to restart the shell,
  they say on Wayland to log out and back in.
- **`--check`** names the mode it would run in.
- **The uninstaller** needs no change: it already stops the service and
  clears the extension from the shell's settings. With the service stopped
  the name leaves the bus, and three-finger swipes are GNOME's again even
  before the logout that unloads the extension.

The udev rule stays as it is. The drag mode needs the same access as the
full mode: to read the touchpad and to make a virtual device.

## If it goes wrong

The extension holds no button and moves nothing, so the worst it can do
is swallow three-finger swipes when it should not. Either of these puts
GNOME's swipes back at once, without a logout:

```
systemctl --user stop gnome-touchpad-gestures
gnome-extensions disable gnome-touchpad-gestures@asifmohtesham.github.io
```

The daemon releases the button on its way out, as it always has.

## Testing

By the suite:

- `session_mode` for each value of the variable, and unset.
- `DragOnly`: keeps drag actions, drops the others; a drag the extension
  did not agree to is dropped whole; the extension is asked once per drag;
  `release_all` reaches the pointer.
- `Shell.announce` and `three_fingers_free` against the fake bus,
  including a bus that refuses the name and an extension that is silent.
- `main` in drag mode: one device made, no momentum, name announced.
- `SwipeFilter` with gjs: three and four fingers, daemon there and not,
  and a daemon that comes or goes in the middle of a swipe.
- The extension run on a bus of its own, as now, in both modes. The
  stand-ins gain `Meta` and a stage that events can be put through.
- The unit's conditions, the installer's advice per session, and the
  metadata.
- Every new line is checked by undoing it and seeing a test fail.

By hand, on Wayland, after logging out and in:

- Drag a window; select text; lift and re-place fingers during a drag.
- Three-finger tap still pastes.
- Four-finger swipes switch workspace and open the overview.
- Stop the service: a three-finger swipe is GNOME's again. Start it: the
  drag is back.
- Disable the extension with the service running: no drag, and the swipe
  is GNOME's.

Not by hand: the X11 mode. There is no X11 session for GNOME on this
machine any more, so it rests on the suite, which is unchanged for it.

## Known limits

- While the daemon runs, GNOME's workspace and overview swipes need four
  fingers. Three no longer do them.
- An application that listens for three-finger swipes itself may no longer
  receive them. Whether it does was not looked into.
- The Wayland mode is written against GNOME Shell 50 and tried on one
  laptop.

## Out of scope

- Momentum, workspace swipes or the overview on Wayland. GNOME has them.
- GNOME Shell 47 to 49, and other compositors.
- A setting for which finger count drags.
- Renaming the project. It keeps "x11" in its name for now.
