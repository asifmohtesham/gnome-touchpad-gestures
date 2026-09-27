"""Talks to GNOME Shell over the session bus."""
from __future__ import annotations

import sys

import dbus

BUS_NAME = "org.gnome.Shell"
OBJECT_PATH = "/org/gnome/Shell"
INTERFACE = "org.gnome.Shell"
# The request runs on the daemon's only thread, so a shell that does not
# answer must not hold up a drag for long.
OVERVIEW_TIMEOUT_S = 0.5


def connect_to_shell():
    shell = dbus.SessionBus().get_object(BUS_NAME, OBJECT_PATH)
    return dbus.Interface(shell, "org.freedesktop.DBus.Properties")


class Shell:
    def __init__(self, connect=connect_to_shell) -> None:
        self._connect = connect
        self._properties = None

    def show_overview(self, show: bool) -> None:
        try:
            if self._properties is None:
                self._properties = self._connect()
            self._properties.Set(
                INTERFACE, "OverviewActive", dbus.Boolean(show),
                timeout=OVERVIEW_TIMEOUT_S)
        except Exception as error:
            # Whatever went wrong, the overview is a convenience. It must
            # never take the drag handling down with it.
            self._properties = None
            verb = "open" if show else "close"
            print(f"finger-drag: could not {verb} the overview: {error}",
                  file=sys.stderr, flush=True)
