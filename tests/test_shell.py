import contextlib
import io
import unittest

import dbus

from gnome_x11_touchpad_gestures.shell import (
    OVERVIEW_TIMEOUT_S, SHELL_RETRY_S, Shell)


class FakeBus:
    def __init__(self):
        self.calls = []
        self.error = None
        self.answer = None

    def call_blocking(self, bus_name, object_path, interface, method,
                      signature, args, timeout=None):
        self.calls.append((bus_name, object_path, interface, method,
                           signature, tuple(args), timeout))
        if self.error:
            raise self.error
        return self.answer


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


if __name__ == "__main__":
    unittest.main()
