"""Talks to GNOME Shell, and to this project's extension in it, over the session bus."""
from __future__ import annotations

import sys
import time

import dbus
import dbus.lowlevel

BUS_NAME = "org.gnome.Shell"
OBJECT_PATH = "/org/gnome/Shell"
INTERFACE = "org.gnome.Shell"
PROPERTIES = "org.freedesktop.DBus.Properties"
# The request runs on the daemon's only thread, so a shell that does not
# answer must not hold up the other gestures for long.
OVERVIEW_TIMEOUT_S = 0.5
# A shell that did not answer a question is not asked another for this
# long. Every unanswered question holds the daemon up for the whole timeout.
SHELL_RETRY_S = 5.0

# The extension, which runs inside the shell and answers on its connection.
# What it offers is written out in its extension.js; a test holds the two
# descriptions to each other.
EXTENSION_UUID = "gnome-x11-touchpad-gestures@asifmohtesham.github.io"
EXTENSION_PATH = "/io/github/asifmohtesham/Gestures"
EXTENSION_INTERFACE = "io.github.asifmohtesham.Gestures"
EXTENSION_CALLS = {
    "PointerOverWindow": "",
    "SwipeBegin": "u",
    "SwipeUpdate": "ud",
    "SwipeEnd": "u",
    "SwipeCancel": "",
}
PREFIX = "gnome-x11-touchpad-gestures: "


def milliseconds(t: float) -> dbus.UInt32:
    """Seconds as the whole milliseconds the shell counts in, which wrap."""
    return dbus.UInt32(int(t * 1000) % 2 ** 32)


class Asked:
    """One thing that is asked over and over, and may stop answering.

    Keeps a question that went unanswered from being put again at once, and
    its failure from being logged at every gesture.
    """

    def __init__(self, clock, complaint: str) -> None:
        self._clock = clock
        self._complaint = complaint
        self._again_at = 0.0
        self._answering = True

    def resting(self) -> bool:
        return self._clock() < self._again_at

    def answered(self) -> None:
        self._answering = True

    def failed(self, error: Exception) -> None:
        if self._answering:
            print(f"{PREFIX}{self._complaint}: {error}", file=sys.stderr, flush=True)
        self._answering = False
        self._again_at = self._clock() + SHELL_RETRY_S


class Shell:
    def __init__(self, connect=dbus.SessionBus, clock=time.monotonic) -> None:
        self._connect = connect
        self._bus = None
        self._overview = Asked(
            clock, "could not ask whether the overview is open")
        self._extension = Asked(
            clock, "the shell extension is not answering, so workspaces will "
                   "not follow the fingers and glides are held back only in "
                   "the overview")

    def _connected(self):
        if self._bus is None:
            self._bus = self._connect()
        return self._bus

    # What the shell offers by itself.

    def overview_is_open(self) -> bool:
        """Whether the overview is showing. A shell that cannot say counts as no."""
        if self._overview.resting():
            return False
        try:
            answer = self._connected().call_blocking(
                BUS_NAME, OBJECT_PATH, PROPERTIES, "Get", "ss",
                (INTERFACE, "OverviewActive"), timeout=OVERVIEW_TIMEOUT_S)
        except Exception as error:
            # Asked at every glide, so a desktop without this shell would
            # fill the log. Said once, and again only if it had recovered.
            self._overview.failed(error)
            return False
        self._overview.answered()
        return bool(answer)

    def show_overview(self, show: bool) -> None:
        try:
            # Sent to the shell by name, without a proxy object. A proxy
            # first asks the shell to describe itself, with a wait of its own
            # that the timeout below does not cover, and it stays bound to
            # the shell it met, so it misses a shell that was restarted.
            self._connected().call_blocking(
                BUS_NAME, OBJECT_PATH, PROPERTIES, "Set", "ssv",
                (INTERFACE, "OverviewActive", dbus.Boolean(show)),
                timeout=OVERVIEW_TIMEOUT_S)
        except Exception as error:
            # Whatever went wrong, the overview is a convenience. It must
            # never take the drag handling down with it.
            verb = "open" if show else "close"
            print(f"{PREFIX}could not {verb} the overview: {error}",
                  file=sys.stderr, flush=True)

    # What the extension adds. It may not be installed, or not loaded yet,
    # and everything here has an answer for that.

    def _ask(self, method: str, *args):
        """The extension's answer, or None if it gave none."""
        if self._extension.resting():
            return None
        try:
            answer = self._connected().call_blocking(
                BUS_NAME, EXTENSION_PATH, EXTENSION_INTERFACE, method,
                EXTENSION_CALLS[method], args, timeout=OVERVIEW_TIMEOUT_S)
        except Exception as error:
            self._extension.failed(error)
            return None
        self._extension.answered()
        return answer

    def _tell(self, method: str, *args) -> None:
        """Sent without waiting for an answer.

        A swipe reports every few milliseconds. Waiting on each report would
        tie the fingers to how fast the shell answers.
        """
        try:
            message = dbus.lowlevel.MethodCallMessage(
                BUS_NAME, EXTENSION_PATH, EXTENSION_INTERFACE, method)
            if args:
                message.append(*args, signature=EXTENSION_CALLS[method])
            message.set_no_reply(True)
            bus = self._connected()
            bus.send_message(message)
            bus.flush()
        except Exception:
            # The swipe is already under way. The extension puts a swipe it
            # stops hearing about back where it was.
            pass

    def extension_version(self) -> str | None:
        """The version of the extension the shell has loaded, if it has one."""
        try:
            return str(self._connected().call_blocking(
                BUS_NAME, EXTENSION_PATH, PROPERTIES, "Get", "ss",
                (EXTENSION_INTERFACE, "Version"), timeout=OVERVIEW_TIMEOUT_S))
        except Exception:
            return None

    def pointer_over_window(self) -> bool | None:
        """Whether the pointer is over a window. None if nobody can say."""
        answer = self._ask("PointerOverWindow")
        return None if answer is None else bool(answer)

    def may_glide(self) -> bool:
        """Whether momentum is wanted where the pointer is.

        Over a window, yes. Over what the shell draws itself, no: there a
        turn of the wheel steps through workspaces or runs the volume up.
        """
        over_window = self.pointer_over_window()
        if over_window is None:
            return not self.overview_is_open()
        return over_window

    def swipe_begin(self, t: float) -> bool:
        """Whether the shell took the swipe up, and will follow the fingers."""
        return bool(self._ask("SwipeBegin", milliseconds(t)))

    def swipe_update(self, t: float, fraction: float) -> None:
        self._tell("SwipeUpdate", milliseconds(t), dbus.Double(fraction))

    def swipe_end(self, t: float) -> None:
        self._tell("SwipeEnd", milliseconds(t))

    def swipe_cancel(self) -> None:
        self._tell("SwipeCancel")
