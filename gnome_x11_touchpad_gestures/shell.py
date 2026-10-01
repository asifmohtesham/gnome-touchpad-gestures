"""Talks to GNOME Shell, and to this project's extension in it, over the session bus."""
from __future__ import annotations

import sys
import time

import dbus
import dbus.bus
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
    "ThreeFingersFree": "",
}
# The name the daemon takes on the session bus where it only drags. The
# extension frees three fingers for it while the name is there, and the
# bus takes the name back by itself when the daemon stops for any reason.
DAEMON_NAME = "io.github.asifmohtesham.Gestures.Daemon"
NAME_IS_OURS = (dbus.bus.REQUEST_NAME_REPLY_PRIMARY_OWNER,
                dbus.bus.REQUEST_NAME_REPLY_ALREADY_OWNER)
# What a silent extension costs, which depends on what the daemon is doing.
FULL_COMPLAINT = (
    "the shell extension is not answering, so workspaces will not follow "
    "the fingers and glides are held back only in the overview")
DRAG_COMPLAINT = (
    "the shell extension is not answering, so three-finger drag is off")
PREFIX = "gnome-x11-touchpad-gestures: "
# What the bus calls a question that was given no answer in time.
UNANSWERED = (
    "org.freedesktop.DBus.Error.NoReply",
    "org.freedesktop.DBus.Error.Timeout",
    "org.freedesktop.DBus.Error.TimedOut",
)


def went_unanswered(error: Exception) -> bool:
    """Whether the shell said nothing, as opposed to saying no."""
    name = getattr(error, "get_dbus_name", None)
    return name is not None and name() in UNANSWERED


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
        self.rest()

    def rest(self) -> None:
        """Not to be asked for a while, for a reason found out elsewhere."""
        self._again_at = self._clock() + SHELL_RETRY_S


class Shell:
    def __init__(self, connect=dbus.SessionBus, clock=time.monotonic,
                 complaint=FULL_COMPLAINT) -> None:
        self._connect = connect
        self._bus = None
        self._overview = Asked(
            clock, "could not ask whether the overview is open")
        self._extension = Asked(clock, complaint)
        self._name = Asked(
            clock, f"could not take the name {DAEMON_NAME} on the session "
                   "bus, so three-finger drag is off")
        self._named = False

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
            self._spare(self._extension, error)
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
            self._spare(self._overview, error)
            return None
        self._extension.answered()
        return answer

    def _spare(self, other: Asked, error: Exception) -> None:
        """Leaves the other question unasked if the shell said nothing.

        The extension answers on the shell's own connection, so a question
        that went unanswered is word about both, and the other would hold
        the daemon up just as long for nothing.
        """
        if went_unanswered(error):
            other.rest()

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

    # Where the desktop does the other gestures itself.

    def announce(self) -> bool:
        """Takes the daemon's name on the bus. Whether it is ours."""
        if self._named:
            return True
        if self._name.resting():
            return False
        try:
            reply = self._connected().request_name(DAEMON_NAME)
        except Exception as error:
            self._name.failed(error)
            return False
        if reply not in NAME_IS_OURS:
            self._name.failed(RuntimeError("another program holds it"))
            return False
        self._name.answered()
        self._named = True
        return True

    def three_fingers_free(self) -> bool:
        """Whether the extension is keeping the shell's swipes off three fingers."""
        # Taken here too: a daemon started before the bus was there has no
        # name yet, and without one the extension frees nothing.
        self.announce()
        return bool(self._ask("ThreeFingersFree"))
