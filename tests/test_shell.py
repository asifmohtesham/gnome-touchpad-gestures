import contextlib
import io
import unittest

import dbus

from gnome_x11_touchpad_gestures.shell import (
    EXTENSION_INTERFACE, EXTENSION_PATH, OVERVIEW_TIMEOUT_S, SHELL_RETRY_S,
    Shell)


class FakeBus:
    def __init__(self):
        self.calls = []
        self.error = None
        self.answer = None
        self.sent = []
        self.flushed = 0
        self.extension_error = None
        self.extension_answers = {}

    def call_blocking(self, bus_name, object_path, interface, method,
                      signature, args, timeout=None):
        self.calls.append((bus_name, object_path, interface, method,
                           signature, tuple(args), timeout))
        if self.error:
            raise self.error
        asks_the_extension = (
            interface == EXTENSION_INTERFACE
            or (object_path == EXTENSION_PATH and method == "Get"))
        if asks_the_extension:
            if self.extension_error:
                raise self.extension_error
            return self.extension_answers.get(method)
        return self.answer

    def send_message(self, message):
        if self.extension_error:
            raise self.extension_error
        self.sent.append(message)

    def flush(self):
        self.flushed += 1


def request(show):
    return ("org.gnome.Shell", "/org/gnome/Shell",
            "org.freedesktop.DBus.Properties", "Set", "ssv",
            ("org.gnome.Shell", "OverviewActive", show), OVERVIEW_TIMEOUT_S)


class ShellTest(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.connections = 0
        self.connect_error = None
        self.now = 100.0
        self.shell = Shell(connect=self.connect, clock=lambda: self.now)

    def connect(self):
        self.connections += 1
        if self.connect_error:
            raise self.connect_error
        return self.bus

    def quietly(self, show):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.shell.show_overview(show)
        return stderr.getvalue()

    def test_nothing_is_contacted_until_needed(self):
        self.assertEqual(self.connections, 0)

    def test_open(self):
        self.assertEqual(self.quietly(True), "")
        self.assertEqual(self.bus.calls, [request(True)])

    def test_close(self):
        self.quietly(False)
        self.assertEqual(self.bus.calls, [request(False)])

    def test_shell_is_addressed_by_name_so_a_restarted_shell_is_found(self):
        self.quietly(True)
        self.assertEqual(self.bus.calls[0][0], "org.gnome.Shell")

    def test_every_request_carries_the_timeout(self):
        self.quietly(True)
        self.quietly(False)
        self.assertEqual([call[-1] for call in self.bus.calls],
                         [OVERVIEW_TIMEOUT_S, OVERVIEW_TIMEOUT_S])

    def test_value_is_a_real_boolean_for_the_bus(self):
        self.quietly(True)
        self.assertEqual(type(self.bus.calls[0][5][2]).__name__, "Boolean")

    def test_connection_is_reused(self):
        self.quietly(True)
        self.quietly(False)
        self.assertEqual(self.connections, 1)

    def test_failed_request_is_reported_and_survived(self):
        self.bus.error = RuntimeError("shell is busy")
        message = self.quietly(True)
        self.assertIn("could not open the overview", message)
        self.assertIn("shell is busy", message)

    def test_request_after_a_failed_one_goes_through(self):
        self.bus.error = RuntimeError("shell restarted")
        self.quietly(True)
        self.bus.error = None
        self.assertEqual(self.quietly(True), "")
        self.assertEqual(self.bus.calls, [request(True), request(True)])

    QUESTION = ("org.gnome.Shell", "/org/gnome/Shell",
                "org.freedesktop.DBus.Properties", "Get", "ss",
                ("org.gnome.Shell", "OverviewActive"), OVERVIEW_TIMEOUT_S)

    def asking(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            answer = self.shell.overview_is_open()
        return answer, stderr.getvalue()

    def test_overview_open(self):
        self.bus.answer = dbus.Boolean(True)
        self.assertEqual(self.asking(), (True, ""))
        self.assertEqual(self.bus.calls, [self.QUESTION])

    def test_overview_closed(self):
        self.bus.answer = dbus.Boolean(False)
        self.assertEqual(self.asking(), (False, ""))

    def test_answer_is_a_plain_truth_value(self):
        self.bus.answer = dbus.Boolean(True)
        self.assertIs(self.asking()[0], True)

    def test_shell_that_cannot_be_asked_counts_as_overview_closed(self):
        self.bus.error = RuntimeError("no such name")
        answer, message = self.asking()
        self.assertIs(answer, False)
        self.assertIn("overview", message)
        self.assertIn("no such name", message)

    def test_failing_question_is_reported_once_not_at_every_glide(self):
        self.bus.error = RuntimeError("no such name")
        self.asking()
        for _ in range(2):
            self.now += SHELL_RETRY_S
            self.assertEqual(self.asking(), (False, ""))

    def test_failure_is_reported_again_after_the_shell_has_answered(self):
        self.bus.error = RuntimeError("no such name")
        self.asking()
        self.bus.error = None
        self.bus.answer = dbus.Boolean(False)
        self.now += SHELL_RETRY_S
        self.asking()
        self.bus.error = RuntimeError("gone again")
        self.assertIn("gone again", self.asking()[1])

    def test_shell_that_did_not_answer_is_left_alone_for_a_while(self):
        self.bus.error = RuntimeError("timed out")
        self.asking()
        asked = len(self.bus.calls)
        for later in (0.1, 0.2, 1.0, SHELL_RETRY_S - 0.1):
            self.now = 100.0 + later
            self.assertEqual(self.asking(), (False, ""))
        self.assertEqual(len(self.bus.calls), asked)

    def test_shell_is_asked_again_after_that_while(self):
        self.bus.error = RuntimeError("timed out")
        self.asking()
        self.bus.error = None
        self.bus.answer = dbus.Boolean(True)
        self.now = 100.0 + SHELL_RETRY_S
        self.assertEqual(self.asking(), (True, ""))

    def test_shell_that_answers_is_asked_every_time(self):
        self.bus.answer = dbus.Boolean(False)
        for _ in range(4):
            self.asking()
        self.assertEqual(len(self.bus.calls), 4)

    def test_request_to_open_the_overview_is_always_tried(self):
        self.bus.error = RuntimeError("timed out")
        self.asking()
        asked = len(self.bus.calls)
        self.quietly(True)
        self.assertEqual(len(self.bus.calls), asked + 1)

    def test_failed_connection_is_reported_and_tried_again(self):
        self.connect_error = RuntimeError("no session bus")
        message = self.quietly(False)
        self.assertIn("could not close the overview", message)
        self.assertIn("no session bus", message)
        self.connect_error = None
        self.assertEqual(self.quietly(False), "")
        self.assertEqual(self.connections, 2)


class ExtensionTest(unittest.TestCase):
    """What the daemon asks of the extension, and how it copes without it."""

    MISSING = RuntimeError("No such interface")

    def setUp(self):
        self.bus = FakeBus()
        self.now = 100.0
        self.shell = Shell(connect=lambda: self.bus, clock=lambda: self.now)

    def quietly(self, call, *args):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            answer = call(*args)
        return answer, stderr.getvalue()

    def asked(self):
        return [call for call in self.bus.calls if call[2] == EXTENSION_INTERFACE]

    def sent(self):
        return [(m.get_destination(), m.get_path(), m.get_interface(),
                 m.get_member(), tuple(m.get_args_list()), m.get_no_reply())
                for m in self.bus.sent]

    # Where the pointer is.

    def test_pointer_over_a_window(self):
        self.bus.extension_answers["PointerOverWindow"] = dbus.Boolean(True)
        self.assertEqual(self.quietly(self.shell.pointer_over_window), (True, ""))
        self.assertEqual(self.asked(), [(
            "org.gnome.Shell", EXTENSION_PATH, EXTENSION_INTERFACE,
            "PointerOverWindow", "", (), OVERVIEW_TIMEOUT_S)])

    def test_pointer_over_the_dock(self):
        self.bus.extension_answers["PointerOverWindow"] = dbus.Boolean(False)
        self.assertIs(self.quietly(self.shell.pointer_over_window)[0], False)

    def test_without_the_extension_nothing_is_known(self):
        self.bus.extension_error = self.MISSING
        answer, message = self.quietly(self.shell.pointer_over_window)
        self.assertIsNone(answer)
        self.assertIn("extension", message)

    def test_glide_allowed_over_a_window(self):
        self.bus.extension_answers["PointerOverWindow"] = dbus.Boolean(True)
        self.assertIs(self.quietly(self.shell.may_glide)[0], True)

    def test_glide_refused_over_the_dock(self):
        self.bus.extension_answers["PointerOverWindow"] = dbus.Boolean(False)
        self.assertIs(self.quietly(self.shell.may_glide)[0], False)

    def test_without_the_extension_only_the_overview_refuses_a_glide(self):
        self.bus.extension_error = self.MISSING
        self.bus.answer = dbus.Boolean(False)
        self.assertIs(self.quietly(self.shell.may_glide)[0], True)
        self.bus.answer = dbus.Boolean(True)
        self.assertIs(self.quietly(self.shell.may_glide)[0], False)

    def test_extension_answering_means_the_overview_is_not_asked_about(self):
        self.bus.extension_answers["PointerOverWindow"] = dbus.Boolean(True)
        self.quietly(self.shell.may_glide)
        self.assertEqual(len(self.bus.calls), 1)

    # Swipes.

    def test_swipe_taken_up(self):
        self.bus.extension_answers["SwipeBegin"] = dbus.Boolean(True)
        self.assertEqual(self.quietly(self.shell.swipe_begin, 12.5), (True, ""))
        self.assertEqual(self.asked(), [(
            "org.gnome.Shell", EXTENSION_PATH, EXTENSION_INTERFACE,
            "SwipeBegin", "u", (12500,), OVERVIEW_TIMEOUT_S)])

    def test_swipe_declined(self):
        self.bus.extension_answers["SwipeBegin"] = dbus.Boolean(False)
        self.assertIs(self.quietly(self.shell.swipe_begin, 12.5)[0], False)

    def test_without_the_extension_no_swipe_is_taken_up(self):
        self.bus.extension_error = self.MISSING
        self.assertIs(self.quietly(self.shell.swipe_begin, 12.5)[0], False)

    def test_update_is_sent_without_waiting_for_an_answer(self):
        self.shell.swipe_update(12.507, 0.25)
        self.assertEqual(self.sent(), [(
            "org.gnome.Shell", EXTENSION_PATH, EXTENSION_INTERFACE,
            "SwipeUpdate", (12507, 0.25), True)])
        self.assertEqual(self.bus.calls, [])
        self.assertEqual(self.bus.flushed, 1)

    def test_end_is_sent_without_waiting_for_an_answer(self):
        self.shell.swipe_end(12.6)
        self.assertEqual(self.sent(), [(
            "org.gnome.Shell", EXTENSION_PATH, EXTENSION_INTERFACE,
            "SwipeEnd", (12600,), True)])

    def test_cancel_is_sent_without_waiting_for_an_answer(self):
        self.shell.swipe_cancel()
        self.assertEqual(self.sent(), [(
            "org.gnome.Shell", EXTENSION_PATH, EXTENSION_INTERFACE,
            "SwipeCancel", (), True)])

    def test_time_is_whole_milliseconds_that_wrap_like_the_shell_s(self):
        self.shell.swipe_end(2 ** 32 / 1000 + 0.005)
        self.assertEqual(self.sent()[0][4], (5,))

    def test_failure_to_send_is_survived(self):
        self.bus.extension_error = RuntimeError("bus went away")
        for call, args in ((self.shell.swipe_update, (1.0, 0.1)),
                           (self.shell.swipe_end, (1.0,)),
                           (self.shell.swipe_cancel, ())):
            with self.subTest(call=call.__name__):
                self.quietly(call, *args)

    def test_version_of_the_extension_that_is_loaded(self):
        self.bus.extension_answers["Get"] = dbus.String("0.2.0")
        self.bus.properties_of_the_extension = True
        self.assertEqual(self.quietly(self.shell.extension_version), ("0.2.0", ""))

    def test_no_version_without_the_extension(self):
        self.bus.extension_error = self.MISSING
        self.assertIsNone(self.quietly(self.shell.extension_version)[0])

    # A missing extension is the usual case, not a fault.

    def test_missing_extension_is_said_once(self):
        self.bus.extension_error = self.MISSING
        self.bus.answer = dbus.Boolean(False)
        _, first = self.quietly(self.shell.may_glide)
        self.assertIn("extension", first)
        for _ in range(3):
            self.now += SHELL_RETRY_S
            self.assertEqual(self.quietly(self.shell.may_glide)[1], "")
            self.assertEqual(self.quietly(self.shell.swipe_begin, self.now)[1], "")

    def test_missing_extension_is_left_alone_for_a_while(self):
        self.bus.extension_error = self.MISSING
        self.bus.answer = dbus.Boolean(False)
        self.quietly(self.shell.may_glide)
        asked = len(self.asked())
        self.now += SHELL_RETRY_S / 2
        self.quietly(self.shell.may_glide)
        self.quietly(self.shell.swipe_begin, self.now)
        self.assertEqual(len(self.asked()), asked)

    def test_extension_is_found_once_it_is_there(self):
        self.bus.extension_error = self.MISSING
        self.quietly(self.shell.swipe_begin, self.now)
        self.bus.extension_error = None
        self.bus.extension_answers["SwipeBegin"] = dbus.Boolean(True)
        self.now += SHELL_RETRY_S
        self.assertIs(self.quietly(self.shell.swipe_begin, self.now)[0], True)

    def test_missing_extension_does_not_silence_the_overview_question(self):
        self.bus.extension_error = self.MISSING
        self.bus.answer = dbus.Boolean(True)
        self.assertIs(self.quietly(self.shell.may_glide)[0], False)
        self.assertIs(self.quietly(self.shell.may_glide)[0], False)


if __name__ == "__main__":
    unittest.main()
