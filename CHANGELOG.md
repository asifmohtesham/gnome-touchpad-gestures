# Changelog

What changed in each version, newest first. Versions are three numbers:
the first changes when something you rely on stops working the same way, the
second when something is added, the third when something is fixed.

## 0.4.0 (unreleased)

Changed
- The project is now called `gnome-touchpad-gestures`. It was
  `gnome-x11-touchpad-gestures`, which stopped being true when it gained a
  mode for Wayland. The service, the installed program, the udev rule, the
  extension and the Python package all take the new name, and the virtual
  devices are now `gnome-gestures pointer`, `keyboard` and `wheel`.
- Installing this version removes what the former name installed. The
  installer asks for your password once, to put the udev rule under its
  new name. On Wayland, log out and back in afterwards; on X11, restart
  the shell.

The gestures themselves have not changed.

## 0.3.1 (2026-10-02)

Fixed
- On Wayland, dragging a window with three fingers inside the Activities
  overview mostly did nothing. The extension kept GNOME's swipes from it
  in a way that, while the button was held, made GNOME cancel its own drag
  of the window. It now keeps them from it in a way that cancels nothing.

## 0.3.0 (2026-10-02)

Added
- Three-finger drag on GNOME on Wayland. GNOME has its own workspace
  swipes and overview there, and its toolkits their own momentum; the one
  thing it lacks is the drag. On Wayland the daemon does that and nothing
  else.
- The extension keeps GNOME's own swipes off three fingers while the daemon
  is running, so that the two do not land on top of each other. With the
  daemon stopped they are GNOME's again at once.
- `--check` names the mode it would run in.

Changed
- The service starts in a Wayland session as well as an X11 one.
- On Wayland, while the daemon runs, GNOME's workspace and overview swipes
  need four fingers. Three no longer do them.
- The extension declares GNOME Shell 46 and 50. On Wayland the shell cannot
  be restarted in place, so the installer and `--check` say to log out and
  back in where they used to say to restart the shell.

On X11 the gestures are as they were. What is said has changed a little:
the service's startup line and `--check` name the mode, and the extension
is listed as "Touchpad gestures".

## 0.2.1 (2026-09-29)

Fixed
- The installer advised restarting the shell at the end of every install,
  and said that workspaces would snap across until then, whether or not
  that was so. It now asks the shell what it has loaded, and says that no
  restart is needed when the extension is loaded and has not changed.

This version changes nothing in the extension but its version number. The
shell still has to be restarted once to load it under the new number.

## 0.2.0 (2026-09-28)

Added
- Workspaces follow the fingers on a four-finger swipe sideways, and spring
  back if the swipe is let go early.
- Momentum only with the pointer over a window. The dock, the top bar and
  menus no longer receive glides.
- A GNOME Shell extension, which the two above need. The installer puts it
  in place; the shell has to be restarted to load it. Without it a sideways
  swipe is one switch of workspace as in 0.1.0, and momentum is held back
  only in the overview.
- Version numbers. `--version` prints the version, the service logs it when
  it starts, and this file records what each one changed.
- `--check` says whether the shell has the extension loaded.

Changed
- A sideways swipe is taken up after 4 mm and reported as it goes. Without
  the extension it still becomes one switch after 15 mm. Which way a swipe
  goes, sideways or up and down, is now settled at 4 mm and not at 15: a
  swipe that set off sideways can no longer open the overview.
- Each frame bears the time the kernel says it happened, not the time it
  was read, so that a swipe read late is not taken for a quick one.
- A shell that has stopped answering holds a glide up for half a second,
  not a whole one.

Fixed
- Uninstalling left the extension's name in the shell's settings, and an
  install after that could leave the extension switched off for good.

## 0.1.0 (2026-09-28)

First release.

- Three-finger drag.
- Four-finger swipe sideways to switch workspace.
- Four-finger swipe up and down to open and close the overview.
- Momentum scrolling after a two-finger flick, faster with repeated flicks,
  and held back while the overview is open.
- Installer and uninstaller. The service runs an installed copy, so the
  repository can live anywhere.
