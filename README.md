# gnome-touchpad-gestures

macOS-style touchpad gestures for GNOME: three-finger drag everywhere, and
on X11 the four-finger swipes and momentum scrolling that GNOME lacks there.

One small unprivileged Python daemon reads the touchpad and writes to
virtual input devices. It never grabs the touchpad, so normal pointing,
scrolling and tapping are untouched. A small GNOME Shell extension helps it.

What it does depends on the session, because GNOME does more for itself on
Wayland:

**On Wayland** (GNOME Shell 50)

| Gesture | Result |
|---|---|
| Three fingers, moving | Drags with the left button held |
| Four fingers, swipe sideways or up and down | GNOME's own workspace switch and overview. They need four fingers while this runs, where GNOME alone takes three |

**On X11** (GNOME Shell 46)

| Gesture | Result |
|---|---|
| Three fingers, moving | Drags with the left button held |
| Four fingers, swipe left | The next workspace slides in under your fingers |
| Four fingers, swipe right | The previous workspace, likewise |
| Four fingers, swipe up | Open the Activities overview |
| Four fingers, swipe down | Close the Activities overview |
| Two fingers, flick and lift | The page keeps gliding and slows to a stop |
| Flick again while it glides | The page glides faster, a little more each time |
| Any touch during a glide | The glide stops at once |
| Flick over the dock, the top bar, a menu or the overview | No glide. A wheel steps through things there |

Design: `docs/superpowers/specs/2026-09-27-gnome-touchpad-gestures-design.md`
for the X11 mode, `docs/superpowers/specs/2026-10-02-wayland-drag-mode-design.md`
for the Wayland one.

Earlier versions were called `finger-drag`. Installing this one removes
what they installed.

## Status

A personal project, written with Claude Code and used daily on one laptop.
It has a test suite and has been through six independent code reviews, but
it has only ever run on the hardware below. Expect to tune it for yours.

| | Tested on |
|---|---|
| Wayland mode | Ubuntu 26.04, GNOME Shell 50, libinput 1.31 |
| X11 mode | Ubuntu 24.04, GNOME Shell 46 on X11, libinput 1.25 |
| Touchpad | Synaptics `SYNA8017:00 06CB:CEB2`, a clickpad reporting up to five fingers |

The machine it is written on has moved to Wayland. The X11 mode is no
longer tried by hand there; it rests on the test suite.

## Requirements

- GNOME Shell 46 on **X11**, or GNOME Shell 50 on **Wayland**. The
  extension declares those two versions and the shell will not load it on
  another; without it there is no drag on Wayland. On Wayland with GNOME
  Shell 46 the extension loads but frees nothing, and there is no drag:
  that pairing was never tried.
- A touchpad that reports each finger separately (multitouch protocol B).
  Nearly all laptops from the last ten years do. The four-finger gestures
  need one that tracks at least four fingers at once.
- `python3-evdev` and `python3-dbus`:
  `sudo apt install python3-evdev python3-dbus`. The installer checks for
  both before it changes anything.
- On X11: `Ctrl+Alt+Left` and `Ctrl+Alt+Right` bound to switching
  workspace, which is GNOME's default.

## Install

Clone it wherever you keep code, then run the installer from there:

```bash
git clone https://github.com/asifmohtesham/gnome-touchpad-gestures.git
cd gnome-touchpad-gestures
./install/install.sh
```

The installer copies the program to `~/.local/share/gnome-touchpad-gestures`, and the
service runs that copy. Two things follow:

- **The repository can live anywhere.** Move it, rename it or delete it
  afterwards; the service keeps running. Keep a copy if you can, though:
  uninstalling and the checks under Troubleshooting are run from it.
- **Changing the code here does not change what runs** until you install
  again. After editing a file or pulling an update, run
  `./install/install.sh`. It asks for your password only when there is
  something for it to do: the first time, when the udev rule itself has
  changed, or when access to the devices is missing.

The commands below are all run from the repository directory.

Run it as your normal user, not from a root shell and not with `sudo`. It
installs a service for your own desktop session, and root has no session to
install it into. The script asks for your password itself for the one step
that needs it, and refuses to run as root.

If it says a re-login is needed, log out and in, then run it again.

**Then make the shell load the extension.** It loads an extension when it
starts and goes on running what it loaded, so the same is needed after an
update that changes the extension. The installer says at the end whether it
is: it asks the shell which version it has loaded. `--check`, under
Troubleshooting, says the same at any time.

- On X11, restart the shell: press Alt+F2, type `r`, press Enter. Your
  windows stay open.
- On Wayland the shell cannot be restarted in place: log out and log back
  in.

### On X11, with and without the extension

Part of this runs inside GNOME Shell, as an extension, because only the
shell can move a workspace gradually or say what the pointer is over.
Everything works without it, less well:

| | With the extension | Without |
|---|---|---|
| Four-finger swipe sideways | The workspace follows your fingers, and springs back if you let go early | One switch, after 15 mm of travel |
| Momentum | Only with the pointer over a window | Everywhere except in the overview |

Even with the extension, some swipes are the one on the right. The shell
follows a swipe only where its own gesture would: not in the overview, not
with a menu open, not while a switch of workspace is still sliding, and
not with the pointer on a monitor that has no workspaces of its own, which
by default is every monitor but the primary one. The daemon asks at the
start of every swipe and takes no for an answer.

On X11 the extension uses parts of GNOME Shell that are not meant for
extensions and can change from one version to the next. That side of it is
written for GNOME Shell 46. Should it misbehave, switch
it off:

```bash
gnome-extensions disable gnome-touchpad-gestures@asifmohtesham.github.io
```

That takes effect at once, without restarting the shell, and the gestures
then behave as in the right-hand column. `gnome-extensions enable` with the
same name switches it back on, and so does running the installer.

### On Wayland

GNOME makes a swipe of any three fingers or more. Left alone, a
three-finger drag would work for a second and then the workspace would
start to move under it. So the extension keeps swipes of exactly three
fingers from the shell, for as long as the daemon is running, and the
daemon asks it before every drag whether it is doing so. Without the
extension there is no drag at all, which is better than a drag with a
workspace switch on top.

What this costs: GNOME's own workspace and overview swipes need four
fingers while the daemon runs. Stop the daemon and three do them again, at
once and without logging out:

```bash
systemctl --user stop gnome-touchpad-gestures
```

The extension never presses a button or moves the pointer. If it should
misbehave, the worst it can do is swallow three-finger swipes, and either
the command above or
`gnome-extensions disable gnome-touchpad-gestures@asifmohtesham.github.io`
puts them back.

### What the installer changes

- **One udev rule**,
  `/etc/udev/rules.d/71-gnome-touchpad-gestures.rules`. It lets the user
  sitting at the machine read its touchpads and create virtual input
  devices, without joining the `input` group, which would expose the
  keyboard too. A device that is a keyboard as well as a touchpad is left
  alone for the same reason.
- **One user service**,
  `~/.config/systemd/user/gnome-touchpad-gestures.service`. It starts
  with your graphical session, and only if that session is X11 or Wayland.
- **The program**, copied to `~/.local/share/gnome-touchpad-gestures`.
- **The shell extension**, copied to
  `~/.local/share/gnome-shell/extensions/gnome-touchpad-gestures@asifmohtesham.github.io`
  and switched on. If the shell cannot be asked to, its name is written
  into the shell's settings, `enabled-extensions`, and taken out of
  `disabled-extensions`.

Be aware of what the rule allows: any program you run can then create input
devices, and so type and click as you. On X11 any program can already do
that through XTest, so this adds little there. On Wayland no program can,
unless you grant it, so there the rule gives every program you run
something it did not have. That is the price of the drag on Wayland.

If the machine has more than one touchpad, the daemon uses the built-in
one. A touchpad on USB or Bluetooth is used only when there is no other.
Among equals it takes the lowest event number. The choice is made when the
service starts.

## Uninstall

```bash
./install/uninstall.sh
```

This stops and removes the service and the installed program, including
anything an earlier version installed as `finger-drag`. It switches the
shell extension off, removes it, and takes its name out of the shell's
settings. It removes the udev rule, makes udev look at the devices afresh,
and takes back the access to the touchpad and `/dev/uinput` that the rule
had granted. Removing the rule alone would leave that access in place until
the next reboot. The repository itself is left where it is.

The shell keeps the extension's code in memory until it is next restarted.
Switched off, that code does nothing.

Two limits. A reboot completes the removal: until then the login manager may
grant access to `/dev/uinput` again at your next login. And if another
package also grants access to `/dev/uinput`, as `steam-devices` does, the
uninstaller takes that away too until the next reboot.

## Tuning

Edit the constants at the top of `gnome_touchpad_gestures/gestures.py`, then run
`./install/install.sh` to install the change. It restarts the service and,
with the udev rule already in place, does not ask for a password.
On Wayland only the three `DRAG_` constants and `POINTER_COUNTS_PER_MM` have
any effect.

| Constant | Default | Raise it to... |
|---|---|---|
| `DRAG_START_MM` | 2.0 | make accidental drags rarer |
| `DRAG_SETTLE_S` | 0.05 | stop a four-finger swipe from clicking as the fingers land |
| `DRAG_RELEASE_S` | 0.3 | get more time to reposition fingers mid-drag |
| `POINTER_COUNTS_PER_MM` | 12.0 | make the pointer faster while dragging |
| `SWIPE_MM` | 15.0 | require a longer swipe up or down, or sideways without the extension |
| `SWIPE_BEGIN_MM` | 4.0 | have the workspace wait longer before it starts to follow |
| `SWIPE_FULL_MM` | 40.0 | need more finger travel to move one whole workspace |
| `SWIPE_AXIS_RATIO` | 1.5 | require a straighter swipe |
| `SWIPE_TOGETHER_MM` | 2.0 | compare the fingers over a longer stretch before a swipe counts |

One more is in `gnome_touchpad_gestures/motion.py`. `TOGETHER_RATIO`
(0.5) is how much of the shared motion each finger must cover for a swipe or
a scroll to count. Lower it if four-finger swipes fail when one finger lags;
raise it if resting fingers are being taken for part of a gesture.

Momentum scrolling has its own constants at the top of
`gnome_touchpad_gestures/momentum.py`:

| Constant | Default | Raise it to... |
|---|---|---|
| `SCROLL_UNITS_PER_MM` | 84.0 | make the glide start faster. It is set to what libinput scrolls per millimetre, so the page keeps its speed at the moment you lift |
| `GLIDE_TAU_S` | 0.5 | make the glide last longer and travel further |
| `GLIDE_MIN_MM_S` | 40.0 | require a harder flick before anything glides |
| `GLIDE_STOP_UNITS_S` | 480.0 | end the glide sooner, while it is still moving briskly |
| `GLIDE_MIN_TRAVEL_MM` | 3.0 | require a longer scroll before a lift can glide |
| `STILL_S` | 0.03 | make flicks glide more reliably. Raise it if a flick sometimes fails to glide; lower it if a scroll that had stopped glides anyway |
| `FLICK_BOOST_STEP` | 0.3 | make repeated flicks build up speed faster. Each repeat adds this much of the normal speed; 0 turns the build-up off |
| `FLICK_BOOST_MAX` | 3.0 | let repeated flicks reach a higher top speed |
| `FLICK_CHAIN_S` | 0.3 | allow a longer pause between flicks before the speed starts again from normal |
| `FLICK_TOUCH_S` | 0.6 | let a longer stroke still count as a repeated flick |

Set `NATURAL_SCROLL = False` there if you turn natural scrolling off for
the touchpad. To change the shape of the slowdown itself, edit
`glide_speed()` in the same file, and to change how speed builds up over
repeated flicks, edit `flick_boost()`.

## Troubleshooting

```bash
systemctl --user status gnome-touchpad-gestures      # is it running?
journalctl --user -u gnome-touchpad-gestures -n 20   # what did it say?
python3 -m gnome_touchpad_gestures.daemon --check
```

`--check` tests device access with the code in the repository, not the
installed copy. Its exit status says what it found:

| Status | Meaning |
|---|---|
| 0 | The touchpad and `/dev/uinput` are both accessible |
| 3 | No touchpad could be read. Usually access is missing: run the installer, and if it has been run, log out and in. A machine with no touchpad at all gives the same status |
| 4 | The touchpad reports one position, not each finger, and cannot be used |
| anything else | A different problem, such as a missing Python package or an error in edited code. Logging out will not fix it |

## Versions

```bash
python3 -m gnome_touchpad_gestures.daemon --version       # the repository's
journalctl --user -u gnome-touchpad-gestures -n 5          # the one running
```

The service logs its version when it starts. `CHANGELOG.md` says what each
version changed. If the two commands above disagree, the repository is
newer than what is installed: run `./install/install.sh`.

## Tests

```bash
python3 -m unittest discover -s tests
```

The tests of the extension need `gjs`, and those that run it need
`dbus-run-session` as well, from the package `dbus-daemon`, and GNOME
Shell's own Clutter library. All are there on a GNOME desktop. Without them those tests are skipped. The extension is
run on a message bus of its own, never on the desktop's.

## Manual checklist

Run after installing or changing a tunable.

On Wayland, after logging out and in:

- [ ] Drag a window by its title bar with three fingers. The workspace
      does not move and the overview does not open.
- [ ] Select text with three fingers.
- [ ] Lift and re-place fingers mid-drag; the drag continues.
- [ ] Three-finger tap still pastes (middle click).
- [ ] A four-finger swipe switches workspace and opens the overview.
- [ ] `systemctl --user stop gnome-touchpad-gestures`: a three-finger
      swipe is GNOME's again. Start it: the drag is back.
- [ ] `gnome-extensions disable` with the service running: no drag, and a
      three-finger swipe is GNOME's.

On X11:

- [ ] Drag a window by its title bar with three fingers.
- [ ] Select text with three fingers.
- [ ] Lift and re-place fingers mid-drag; the drag continues.
- [ ] Three-finger tap still pastes (middle click).
- [ ] Two-finger scroll and one-finger pointing are unchanged.
- [ ] Four-finger swipe left and right moves the workspace under the
      fingers. Let go past half way, or with a flick, and it changes;
      let go early and it springs back.
- [ ] Four fingers that drift a few millimetres sideways and lift do not
      change the workspace.
- [ ] A swipe held still half way for a few seconds stays where it is, and
      carries on when the fingers do.
- [ ] A four-finger swipe sideways with a menu open changes the workspace
      in one step, or does nothing. It does not leave the workspace half
      way across.
- [ ] A two-finger flick with the pointer over the dock or the top bar
      does not glide.
- [ ] Suspend and resume; gestures work again within a few seconds.
- [ ] `systemctl --user stop gnome-touchpad-gestures` mid-drag releases the button.
- [ ] Four-finger swipe up opens the overview; swiping up again leaves it
      open.
- [ ] Four-finger swipe down closes the overview.
- [ ] A four-finger swipe sideways inside the overview still switches
      workspace, in one step after 15 mm.
- [ ] A two-finger flick keeps gliding in the same direction, up, down
      and sideways.
- [ ] The page does not lurch faster or slower at the moment of lifting.
- [ ] Touching the pad stops a glide.
- [ ] Flicking again while the page glides makes it glide faster, a little
      more each time.
- [ ] A flick the other way, or after a pause, glides at normal speed.
- [ ] A flick inside the overview does not race through the workspaces.
- [ ] A slow scroll that ends in a stop does not glide.
- [ ] No app travels much too far after a flick (it may be adding its own
      glide on top).

## Known behaviour

- On Wayland, while the daemon runs, GNOME's workspace and overview swipes
  need four fingers.
- On Wayland the very first drag after the daemon starts may do nothing:
  the extension learns that the daemon is there a moment after it starts.
- On Wayland an application that listens for three-finger swipes itself may
  no longer receive them.
- On Wayland the swipes of three fingers are kept from GNOME on every
  touchpad, but the daemon reads only one. Three fingers on a second
  touchpad do nothing while the daemon runs.
- On Wayland, if GNOME Shell does not answer the daemon within half a
  second as a drag starts, that drag does nothing, and nor does any other
  for the next second.
- Everything below is about the X11 mode, except the lines on dragging.
- For a moment after lifting from a drag the button is still held, so a
  finger left on the pad can nudge the dragged item.
- Workspaces follow the fingers only with the extension loaded, and only
  sideways. The overview still opens and closes in one step.
- The workspace starts to follow once the fingers have gone 4 mm, and
  follows from where they are then. Those first millimetres decide which
  way the swipe goes and move nothing.
- With GNOME's animations switched off, the workspace follows the fingers
  but settles at once when you let go, without sliding into place.
- Holding a physical modifier key while swiping changes what the key chord
  means to GNOME.
- The overview is opened and closed by asking GNOME Shell directly, so that
  part works on GNOME only. GNOME ignores the request on the lock screen and
  while a menu is open, as it does the Super key.
- While the daemon waits for GNOME to answer, no gesture is handled. It waits
  half a second at most, then logs it, carries on, and asks GNOME nothing
  more for five seconds. GNOME may still act on the request when it wakes.
- A four-finger swipe needs all the fingers to move. Contacts that rest on
  the pad while others move are not a swipe.
- For about a third of a second after any touch with three or more fingers,
  a two-finger flick does not glide. A drag still holds its button for that
  long, and the daemon does not tell a drag from a swipe or a tap here.
- There is a glide only with the pointer over a window or the desktop
  background. Over what GNOME Shell draws itself (the dock, the top bar, a
  menu, the overview) a notch of the wheel steps through something,
  workspaces or the volume, and a glide would race through it. Scrolling
  with your fingers on the pad works there as before.
- Without the extension only the overview is known about, and a glide over
  the dock or the top bar does happen.
- Repeated flicks build up speed only while they follow one another: each
  must land while the page still glides, or within a third of a second of
  it stopping, be a quick touch of no more than 0.6 s, and go the same way.
  A flick the other way, a pause, fingers left resting on the pad, a long
  scroll, a slow scroll or any other touch in between starts again from
  normal speed.
- A boosted glide starts faster than your fingers were moving, so the page
  speeds up at the moment you lift. That is the point of it.
- A glide lasts two to three seconds, the longer the faster it started.
  During that time it goes to
  whatever window is under the pointer, so it follows the pointer if an
  external mouse moves it, and pressing Ctrl zooms in apps that zoom on
  Ctrl+scroll. The daemon cannot see the keyboard, by design.
- A glide runs along one axis, the stronger one. Diagonal flicks do not
  glide diagonally.
- A glide needs a real scroll first: both fingers moving together for at
  least 3 mm, without the pad being clicked. A resting thumb, a tap or a
  pinch does not glide. Nor does a scroll that came to rest before the
  fingers left.
- A glide does not know where the page ends. In GTK apps the glow or
  bounce at the end of a page can stay until the glide is over. Touch the
  pad to end it sooner.
- The virtual devices are called `gnome-gestures pointer`, `keyboard`
  and `wheel`, without the word "touchpad". GNOME decides what kind of device
  something is from words in its name, and would give a "touchpad" wheel the
  touchpad's settings.
- **Firefox: turn on "Use smooth scrolling"** in Settings, under Browsing.
  A glide reaches Firefox as mouse-wheel input, and without smooth scrolling
  Firefox does not render it well. Firefox turns the option off by default
  when the desktop has animations switched off (GNOME Settings,
  Accessibility, Reduce Animation), so it can be off without you having
  chosen that.
- GNOME treats the virtual wheel as a mouse. Glide direction assumes
  natural scrolling is on for the touchpad and off for mice. If yours
  differ, set `NATURAL_SCROLL` in `gnome_touchpad_gestures/momentum.py`.
- X applies its mouse acceleration to the virtual pointer, so drag speed
  also depends on the mouse speed set in GNOME Settings.
  `POINTER_COUNTS_PER_MM` is tuned with that in place.
- If fingers are on the pad when the service starts, gestures begin working
  once the pad has been completely empty for a moment.

## Licence

MIT. See `LICENSE`.
