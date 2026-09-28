# Changelog

What changed in each version, newest first. Versions are three numbers:
the first changes when something you rely on stops working the same way, the
second when something is added, the third when something is fixed.

## 0.2.0 (unreleased)

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
