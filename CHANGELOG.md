# Changelog

What changed in each version, newest first. Versions are three numbers:
the first changes when something you rely on stops working the same way, the
second when something is added, the third when something is fixed.

## 0.2.0 (unreleased)

Added
- Version numbers. `--version` prints the version, the service logs it when
  it starts, and this file records what each one changed.

## 0.1.0 (2026-09-28)

First release.

- Three-finger drag.
- Four-finger swipe sideways to switch workspace.
- Four-finger swipe up and down to open and close the overview.
- Momentum scrolling after a two-finger flick, faster with repeated flicks,
  and held back while the overview is open.
- Installer and uninstaller. The service runs an installed copy, so the
  repository can live anywhere.
