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
        self._answering = True   # the last question put to the shell was answered

    def overview_is_open(self) -> bool:
        """Whether the overview is showing. A shell that cannot say counts as no."""
        try:
            if self._bus is None:
                self._bus = self._connect()
            answer = self._bus.call_blocking(
                BUS_NAME, OBJECT_PATH, PROPERTIES, "Get", "ss",
                (INTERFACE, "OverviewActive"), timeout=OVERVIEW_TIMEOUT_S)
        except Exception as error:
            # Asked at every glide, so a desktop without this shell would
            # fill the log. Said once, and again only if it had recovered.
            if self._answering:
                print("gnome-x11-touchpad-gestures: could not ask whether the "
                      f"overview is open: {error}", file=sys.stderr, flush=True)
            self._answering = False
            return False
        self._answering = True
        return bool(answer)

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
