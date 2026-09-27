import contextlib
import io
import unittest

from gnome_x11_touchpad_gestures.shell import OVERVIEW_TIMEOUT_S, Shell


class FakeBus:
    def __init__(self):
        self.calls = []
        self.error = None

    def call_blocking(self, bus_name, object_path, interface, method,
                      signature, args, timeout=None):
        self.calls.append((bus_name, object_path, interface, method,
                           signature, tuple(args), timeout))
        if self.error:
            raise self.error


def request(show):
    return ("org.gnome.Shell", "/org/gnome/Shell",
            "org.freedesktop.DBus.Properties", "Set", "ssv",
            ("org.gnome.Shell", "OverviewActive", show), OVERVIEW_TIMEOUT_S)


class ShellTest(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.connections = 0
        self.connect_error = None
        self.shell = Shell(connect=self.connect)

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
