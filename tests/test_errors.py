"""Turning tool output into advice.

Each raw string here is the shape adb or scrcpy actually prints. The ordering
tests matter as much as the matches: a loose rule placed above a specific one
would swallow it silently.
"""

from __future__ import annotations

import re
import unittest

from support import IsolatedConfig  # noqa: F401  (keeps sys.path set up)

from androlinx.errors import (
    ACTION_AUTHORISE,
    ACTION_OPEN_FOLDER,
    ACTION_PAIR_AGAIN,
    ACTION_RECONNECT,
    ACTION_RESTART_ADB,
    ACTION_RETRY,
    ACTION_UDEV_HELP,
    ACTIONS,
    SOURCE_ADB,
    SOURCE_SCRCPY,
    _COMPILED,
    map_error,
)


class TestRuleTable(unittest.TestCase):
    def test_every_pattern_compiles(self):
        for pattern, *_ in _COMPILED:
            self.assertIsInstance(pattern, re.Pattern)

    def test_every_action_is_declared(self):
        for _pattern, _title, _detail, action in _COMPILED:
            if action is not None:
                self.assertIn(action, ACTIONS)

    def test_every_rule_has_a_title(self):
        for _pattern, title, *_ in _COMPILED:
            self.assertTrue(title.strip())


class TestAdbMessages(unittest.TestCase):
    def assert_action(self, raw: str, action: str | None):
        self.assertEqual(map_error(raw).action, action, raw)

    def test_unauthorized(self):
        self.assert_action(
            "error: device unauthorized.\nThis adb server's $ADB_VENDOR_KEYS is not set",
            ACTION_AUTHORISE,
        )

    def test_offline_variants(self):
        for raw in (
            "error: device offline",
            "error: device offline (transport offline)",
            "error: device offline (no transport)",
        ):
            self.assert_action(raw, ACTION_RECONNECT)

    def test_no_permissions(self):
        self.assert_action(
            "0123456789\tno permissions; see "
            "[http://developer.android.com/tools/device.html]",
            ACTION_UDEV_HELP,
        )

    def test_server_version_mismatch(self):
        self.assert_action(
            "adb server version (41) doesn't match this client (39); killing...",
            ACTION_RESTART_ADB,
        )

    def test_port_in_use(self):
        self.assert_action(
            "cannot bind listener: Address already in use", ACTION_RESTART_ADB
        )

    def test_daemon_will_not_start(self):
        self.assert_action(
            "cannot connect to daemon at tcp:5037: Connection refused",
            ACTION_RESTART_ADB,
        )

    def test_nothing_connected(self):
        self.assert_action("error: no devices/emulators found", ACTION_RETRY)

    def test_more_than_one_device(self):
        self.assert_action("error: more than one device/emulator", None)

    def test_connection_refused(self):
        self.assert_action(
            "failed to connect to '192.168.1.5:5555': Connection refused",
            ACTION_PAIR_AGAIN,
        )

    def test_unreachable(self):
        self.assert_action(
            "failed to connect to '10.1.1.1:5555': No route to host", ACTION_RETRY
        )

    def test_wrong_pairing_code(self):
        self.assert_action(
            "Failed: Wrong password or connection was dropped.", ACTION_PAIR_AGAIN
        )

    def test_pairing_client_failure(self):
        self.assert_action("Failed: Unable to start pairing client.", ACTION_PAIR_AGAIN)

    def test_protocol_fault(self):
        self.assert_action(
            "protocol fault (couldn't read status message): Success",
            ACTION_RESTART_ADB,
        )


class TestSuccessesOnStderr(unittest.TestCase):
    def test_already_connected_is_not_a_failure(self):
        diagnosis = map_error("already connected to 192.168.1.5:5555")
        self.assertTrue(diagnosis.ok)
        self.assertEqual(diagnosis.title, "Already connected")

    def test_paired_is_not_a_failure(self):
        diagnosis = map_error("Successfully paired to 192.168.1.5:37999")
        self.assertTrue(diagnosis.ok)

    def test_ordinary_failures_are_not_ok(self):
        self.assertFalse(map_error("error: device offline").ok)


class TestScrcpyMessages(unittest.TestCase):
    def test_no_device(self):
        diagnosis = map_error("ERROR: Could not find any ADB device", source=SOURCE_SCRCPY)
        self.assertEqual(diagnosis.action, ACTION_RETRY)

    def test_server_push_failure(self):
        diagnosis = map_error(
            "ERROR: Failed to push server to device", source=SOURCE_SCRCPY
        )
        self.assertIn("helper", diagnosis.title)

    def test_tunnel_failure_mentions_the_vpn(self):
        diagnosis = map_error("ERROR: Server connection failed", source=SOURCE_SCRCPY)
        self.assertIn("VPN", diagnosis.detail)

    def test_codec_failure_suggests_h264(self):
        diagnosis = map_error("ERROR: Could not open codec", source=SOURCE_SCRCPY)
        self.assertIn("H.264", diagnosis.detail)

    def test_recording_failure_offers_the_folder(self):
        diagnosis = map_error("ERROR: Recording failed to /x/y.mp4", source=SOURCE_SCRCPY)
        self.assertEqual(diagnosis.action, ACTION_OPEN_FOLDER)

    def test_incomplete_recording(self):
        diagnosis = map_error(
            "ERROR: Failed to write trailer to /x/y.mp4", source=SOURCE_SCRCPY
        )
        self.assertIn("incomplete", diagnosis.title)


class TestFallback(unittest.TestCase):
    def test_the_informative_line_wins_over_the_noise(self):
        raw = "\n".join(
            ["INFO: scrcpy 3.3.4", "INFO: Renderer: opengl", "DEBUG: whatever"]
            + ["ERROR: something entirely unheard of"]
        )
        self.assertEqual(
            map_error(raw, source=SOURCE_SCRCPY).title, "something entirely unheard of"
        )

    def test_error_prefix_is_stripped(self):
        self.assertEqual(map_error("error: mystery failure").title, "mystery failure")

    def test_empty_input_does_not_raise(self):
        diagnosis = map_error("")
        self.assertTrue(diagnosis.title)
        self.assertEqual(diagnosis.action, ACTION_RETRY)

    def test_only_noise(self):
        diagnosis = map_error("INFO: a\nINFO: b", source=SOURCE_SCRCPY)
        self.assertTrue(diagnosis.title)

    def test_raw_is_always_kept_for_copying(self):
        raw = "error: device offline"
        self.assertEqual(map_error(raw).raw, raw)

    def test_long_lines_are_trimmed(self):
        self.assertLessEqual(len(map_error("error: " + "x" * 500).title), 120)

    def test_source_is_named_when_nothing_matches(self):
        # Unmatched output says which tool complained, so a bug report has a
        # starting point even when AndroLinx has no advice to offer.
        self.assertIn("adb", map_error("some unheard-of failure",
                                       source=SOURCE_ADB).detail)
        self.assertIn("scrcpy", map_error("some unheard-of failure",
                                          source=SOURCE_SCRCPY).detail)


if __name__ == "__main__":
    unittest.main()
