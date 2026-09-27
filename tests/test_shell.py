import contextlib
import io
import unittest

from finger_drag.shell import OVERVIEW_TIMEOUT_S, Shell


class FakeProperties:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def Set(self, interface, name, value, timeout=None):
        self.calls.append((interface, name, value, timeout))
        if self.error:
            raise self.error


class ShellTest(unittest.TestCase):
    def setUp(self):
        self.properties = FakeProperties()
        self.connections = 0
        self.connect_error = None
        self.shell = Shell(connect=self.connect)

    def connect(self):
        self.connections += 1
        if self.connect_error:
            raise self.connect_error
        return self.properties

    def quietly(self, show):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.shell.show_overview(show)
        return stderr.getvalue()

    def test_nothing_is_contacted_until_needed(self):
        self.assertEqual(self.connections, 0)

    def test_open(self):
        self.assertEqual(self.quietly(True), "")
        self.assertEqual(self.properties.calls, [
            ("org.gnome.Shell", "OverviewActive", True, OVERVIEW_TIMEOUT_S)])

    def test_close(self):
        self.quietly(False)
        self.assertEqual(self.properties.calls, [
            ("org.gnome.Shell", "OverviewActive", False, OVERVIEW_TIMEOUT_S)])

    def test_value_is_a_real_boolean_for_the_bus(self):
        self.quietly(True)
        value = self.properties.calls[0][2]
        self.assertEqual(type(value).__name__, "Boolean")

    def test_connection_is_reused(self):
        self.quietly(True)
        self.quietly(False)
        self.assertEqual(self.connections, 1)

    def test_failed_request_is_reported_and_survived(self):
        self.properties.error = RuntimeError("shell is busy")
        message = self.quietly(True)
        self.assertIn("could not open the overview", message)
        self.assertIn("shell is busy", message)

    def test_failed_connection_is_reported_and_survived(self):
        self.connect_error = RuntimeError("no session bus")
        message = self.quietly(False)
        self.assertIn("could not close the overview", message)
        self.assertIn("no session bus", message)

    def test_reconnects_after_a_failure(self):
        self.properties.error = RuntimeError("shell restarted")
        self.quietly(True)
        self.properties.error = None
        self.assertEqual(self.quietly(True), "")
        self.assertEqual(self.connections, 2)


if __name__ == "__main__":
    unittest.main()
