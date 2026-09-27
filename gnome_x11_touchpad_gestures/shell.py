"""Talks to GNOME Shell over the session bus."""
from __future__ import annotations

import sys

import dbus

BUS_NAME = "org.gnome.Shell"
OBJECT_PATH = "/org/gnome/Shell"
INTERFACE = "org.gnome.Shell"
PROPERTIES = "org.freedesktop.DBus.Properties"
# The request runs on the daemon's only thread, so a shell that does not
# answer must not hold up the other gestures for long.
OVERVIEW_TIMEOUT_S = 0.5


class Shell:
    def __init__(self, connect=dbus.SessionBus) -> None:
        self._connect = connect
        self._bus = None

    def show_overview(self, show: bool) -> None:
        try:
            if self._bus is None:
                self._bus = self._connect()
            # Sent to the shell by name, without a proxy object. A proxy
            # first asks the shell to describe itself, with a wait of its own
            # that the timeout below does not cover, and it stays bound to
            # the shell it met, so it misses a shell that was restarted.
            self._bus.call_blocking(
                BUS_NAME, OBJECT_PATH, PROPERTIES, "Set", "ssv",
                (INTERFACE, "OverviewActive", dbus.Boolean(show)),
                timeout=OVERVIEW_TIMEOUT_S)
        except Exception as error:
            # Whatever went wrong, the overview is a convenience. It must
            # never take the drag handling down with it.
            verb = "open" if show else "close"
            print(f"gnome-x11-touchpad-gestures: could not {verb} the overview: {error}",
                  file=sys.stderr, flush=True)
