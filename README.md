# gnome-x11-touchpad-gestures

macOS-style touchpad gestures for GNOME on X11: three-finger drag,
four-finger swipes and momentum scrolling.

GNOME has touchpad gestures on Wayland but none on X11. This fills the gap
with one small unprivileged Python daemon, called `finger-drag`. It reads
the touchpad and writes to three virtual input devices. It never grabs the
touchpad, so normal pointing, scrolling and tapping are untouched.

| Gesture | Result |
|---|---|
| Three fingers, moving | Drags with the left button held |
| Four fingers, swipe left | Next workspace (`Ctrl+Alt+Right`) |
| Four fingers, swipe right | Previous workspace (`Ctrl+Alt+Left`) |
| Four fingers, swipe up | Open the Activities overview |
| Four fingers, swipe down | Close the Activities overview |
| Two fingers, flick and lift | The page keeps gliding and slows to a stop |
| Any touch during a glide | The glide stops at once |

Design: `docs/superpowers/specs/2026-09-27-finger-drag-design.md`

## Status

A personal project, written with Claude Code and used daily on one laptop.
It has a test suite and has been through three independent code reviews, but
it has only ever run on the hardware below. Expect to tune it for yours.

| | Tested on |
|---|---|
| Distribution | Ubuntu 24.04 |
| Desktop | GNOME Shell 46 on X11 |
| libinput | 1.25 |
| Touchpad | Synaptics `SYNA8017:00 06CB:CEB2`, a clickpad reporting up to five fingers |

## Requirements

- GNOME on **X11**. On Wayland GNOME has its own gestures, and they would
  conflict with these.
- A touchpad that reports each finger separately (multitouch protocol B).
  Nearly all laptops from the last ten years do. The four-finger gestures
  need one that tracks at least four fingers at once.
- `python3-evdev` and `python3-dbus`:
  `sudo apt install python3-evdev python3-dbus`. The installer checks for
  both before it changes anything.
- `Ctrl+Alt+Left` and `Ctrl+Alt+Right` bound to switching workspace, which is
  GNOME's default.

## Install

Clone it wherever you keep code, then run the installer from there:

```bash
git clone https://github.com/asifmohtesham/gnome-x11-touchpad-gestures.git
cd gnome-x11-touchpad-gestures
./install/install.sh
```

The service runs the code straight from that directory, and the installer
records where it is. Two things follow:

- **If you move or rename the directory, run `./install/install.sh` again
  from its new place.** Until you do, the service points at the old location
  and will not start.
- **Whatever is checked out there is what runs.** Switching branch or pulling
  changes what the service does the next time it starts.

The commands below are all run from the repository directory.

Run it as your normal user, not from a root shell and not with `sudo`. It
installs a service for your own desktop session, and root has no session to
install it into. The script asks for your password itself for the one step
that needs it, and refuses to run as root.

If it says a re-login is needed, log out and in, then run it again.

### What the installer changes

- **One udev rule**, `/etc/udev/rules.d/71-finger-drag.rules`. It lets the
  user sitting at the machine read its touchpads and create virtual input
  devices, without joining the `input` group, which would expose the
  keyboard too.
- **One user service**, `~/.config/systemd/user/finger-drag.service`. It
  starts with your graphical session, and only if that session is X11. It
  holds the path of the directory you installed from.

Be aware of what the rule allows: any program you run can then create input
devices, and so type and click as you. On X11 any program can already do
that through XTest, so this adds little there.

If the machine has more than one touchpad, the daemon uses the first it
finds.

## Uninstall

```bash
./install/uninstall.sh
```

This stops and removes the service, removes the udev rule, makes udev look
at the two devices afresh, and takes back the access to the touchpad and
`/dev/uinput` that the rule had granted. Removing the rule alone would leave
that access in place until the next reboot. The repository itself is left
where it is.

Two limits. A reboot completes the removal: until then the login manager may
grant access to `/dev/uinput` again at your next login. And if another
package also grants access to `/dev/uinput`, as `steam-devices` does, the
uninstaller takes that away too until the next reboot.

## Tuning

Edit the constants at the top of `finger_drag/gestures.py`, then run
`systemctl --user restart finger-drag`.

| Constant | Default | Raise it to... |
|---|---|---|
| `DRAG_START_MM` | 2.0 | make accidental drags rarer |
| `DRAG_SETTLE_S` | 0.05 | stop a four-finger swipe from clicking as the fingers land |
| `DRAG_RELEASE_S` | 0.3 | get more time to reposition fingers mid-drag |
| `POINTER_COUNTS_PER_MM` | 12.0 | make the pointer faster while dragging |
| `SWIPE_MM` | 15.0 | require a longer swipe, sideways or up and down |
| `SWIPE_AXIS_RATIO` | 1.5 | require a straighter swipe |

Momentum scrolling has its own constants at the top of
`finger_drag/momentum.py`:

| Constant | Default | Raise it to... |
|---|---|---|
| `SCROLL_UNITS_PER_MM` | 84.0 | make the glide start faster. It is set to what libinput scrolls per millimetre, so the page keeps its speed at the moment you lift |
| `GLIDE_TAU_S` | 0.5 | make the glide last longer and travel further |
| `GLIDE_MIN_MM_S` | 40.0 | require a harder flick before anything glides |
| `GLIDE_STOP_UNITS_S` | 480.0 | end the glide sooner, while it is still moving briskly |
| `GLIDE_MIN_TRAVEL_MM` | 3.0 | require a longer scroll before a lift can glide |
| `STILL_S` | 0.03 | make flicks glide more reliably. Raise it if a flick sometimes fails to glide; lower it if a scroll that had stopped glides anyway |

Set `NATURAL_SCROLL = False` there if you turn natural scrolling off for
the touchpad. To change the shape of the slowdown itself, edit
`glide_speed()` in the same file.

## Troubleshooting

```bash
systemctl --user status finger-drag      # is it running?
journalctl --user -u finger-drag -n 20   # what did it say?
python3 -m finger_drag.daemon --check
```

`--check` exits 0 when both devices are accessible and 3 when access is
missing. Any other status is a different problem, such as a missing
`python3-evdev`, and logging out will not fix it.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Manual checklist

Run after installing or changing a tunable.

- [ ] Drag a window by its title bar with three fingers.
- [ ] Select text with three fingers.
- [ ] Lift and re-place fingers mid-drag; the drag continues.
- [ ] Three-finger tap still pastes (middle click).
- [ ] Two-finger scroll and one-finger pointing are unchanged.
- [ ] Four-finger swipe left and right switches workspace, once per swipe.
- [ ] Suspend and resume; gestures work again within a few seconds.
- [ ] `systemctl --user stop finger-drag` mid-drag releases the button.
- [ ] Four-finger swipe up opens the overview; swiping up again leaves it
      open.
- [ ] Four-finger swipe down closes the overview.
- [ ] A four-finger swipe sideways inside the overview still switches
      workspace.
- [ ] A two-finger flick keeps gliding in the same direction, up, down
      and sideways.
- [ ] The page does not lurch faster or slower at the moment of lifting.
- [ ] Touching the pad stops a glide.
- [ ] A slow scroll that ends in a stop does not glide.
- [ ] No app travels much too far after a flick (it may be adding its own
      glide on top).

## Known behaviour

- For a moment after lifting from a drag the button is still held, so a
  finger left on the pad can nudge the dragged item.
- Workspace changes snap once per swipe; they do not follow the fingers.
- Holding a physical modifier key while swiping changes what the key chord
  means to GNOME.
- The overview is opened and closed by asking GNOME Shell directly, so that
  part works on GNOME only. GNOME ignores the request on the lock screen and
  while a menu is open, as it does the Super key.
- While the daemon waits for GNOME to answer, no gesture is handled. It waits
  half a second at most, then logs it and carries on. GNOME may still act on
  the request when it wakes.
- A four-finger swipe needs all the fingers to move. Contacts that rest on
  the pad while others move are not a swipe.
- For a moment after a three-finger drag, a two-finger flick does not glide,
  because the drag still holds its button.
- A glide lasts up to about two seconds. During that time it goes to
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
- GNOME treats the virtual wheel as a mouse. Glide direction assumes
  natural scrolling is on for the touchpad and off for mice. If yours
  differ, set `NATURAL_SCROLL` in `finger_drag/momentum.py`.
- X applies its mouse acceleration to the virtual pointer, so drag speed
  also depends on the mouse speed set in GNOME Settings.
  `POINTER_COUNTS_PER_MM` is tuned with that in place.
- If fingers are on the pad when the service starts, gestures begin working
  once the pad has been completely empty for a moment.

## Licence

MIT. See `LICENSE`.
