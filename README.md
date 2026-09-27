# finger-drag

Three-finger drag and four-finger workspace switching for a GNOME-on-X11
laptop. One unprivileged Python daemon reads the touchpad and writes to two
virtual input devices. It never grabs the touchpad, so normal pointing,
scrolling and tapping are untouched.

| Gesture | Result |
|---|---|
| Three fingers, moving | Drags with the left button held |
| Four fingers, swipe left | Next workspace (`Ctrl+Alt+Right`) |
| Four fingers, swipe right | Previous workspace (`Ctrl+Alt+Left`) |

Design: `docs/superpowers/specs/2026-09-27-finger-drag-design.md`

## Install

Requires `python3-evdev` (already present on this machine). The repo must
live at `~/finger-drag`.

```bash
~/finger-drag/install/install.sh
```

The script asks for your password once to install a udev rule. If it says a
re-login is needed, log out and in, then run it again.

## Uninstall

```bash
systemctl --user disable --now finger-drag.service
rm ~/.config/systemd/user/finger-drag.service
systemctl --user daemon-reload
sudo rm /etc/udev/rules.d/71-finger-drag.rules
sudo udevadm control --reload
```

## Tuning

Edit the constants at the top of `finger_drag/gestures.py`, then run
`systemctl --user restart finger-drag`.

| Constant | Default | Raise it to... |
|---|---|---|
| `DRAG_START_MM` | 2.0 | make accidental drags rarer |
| `DRAG_SETTLE_S` | 0.05 | stop a four-finger swipe from clicking as the fingers land |
| `DRAG_RELEASE_S` | 0.3 | get more time to reposition fingers mid-drag |
| `POINTER_COUNTS_PER_MM` | 12.0 | make the pointer faster while dragging |
| `SWIPE_MM` | 15.0 | require a longer swipe to switch workspace |
| `SWIPE_AXIS_RATIO` | 1.5 | require a straighter swipe |

## Troubleshooting

```bash
systemctl --user status finger-drag      # is it running?
journalctl --user -u finger-drag -n 20   # what did it say?
cd ~/finger-drag && python3 -m finger_drag.daemon --check
```

## Tests

```bash
cd ~/finger-drag && python3 -m unittest discover -s tests
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

## Known behaviour

- For a moment after lifting from a drag the button is still held, so a
  finger left on the pad can nudge the dragged item.
- Workspace changes snap once per swipe; they do not follow the fingers.
- Holding a physical modifier key while swiping changes what the key chord
  means to GNOME.
