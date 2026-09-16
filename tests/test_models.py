"""The Device, Profile and Session records."""

from __future__ import annotations

import unittest

from support import IsolatedConfig  # noqa: F401  (keeps sys.path set up)

from androlinx.models import Device, Profile, Session


class TestDisplayName(unittest.TestCase):
    def test_underscores_become_spaces(self):
        self.assertEqual(Device(serial="x", model="Pixel_9").display_name, "Pixel 9")

    def test_manufacturer_is_prefixed(self):
        device = Device(serial="x", model="SM-A536E", manufacturer="samsung")
        self.assertEqual(device.display_name, "Samsung SM-A536E")

    def test_manufacturer_capitalisation_keeps_inner_capitals(self):
        device = Device(serial="x", model="CPH2449", manufacturer="OnePlus")
        self.assertEqual(device.display_name, "OnePlus CPH2449")

    def test_redundant_manufacturer_is_not_repeated(self):
        device = Device(serial="x", model="Pixel_9", manufacturer="Google")
        self.assertEqual(device.display_name, "Google Pixel 9")
        device = Device(serial="x", model="Google_Pixel_9", manufacturer="Google")
        self.assertEqual(device.display_name, "Google Pixel 9")

    def test_falls_back_to_the_serial(self):
        self.assertEqual(Device(serial="R58M123456").display_name, "R58M123456")

    def test_blank_model_falls_back(self):
        self.assertEqual(Device(serial="abc", model="   ").display_name, "abc")


class TestTransport(unittest.TestCase):
    def test_usb(self):
        self.assertFalse(Device(serial="R58M123456").over_tcpip)

    def test_host_and_port(self):
        self.assertTrue(Device(serial="192.168.1.5:5555").over_tcpip)

    def test_ipv6(self):
        self.assertTrue(Device(serial="[fe80::1]:5555").over_tcpip)

    def test_mdns_serial_has_no_port(self):
        self.assertTrue(Device(serial="adb-R58M-x._adb-tls-connect._tcp").over_tcpip)

    def test_emulator(self):
        device = Device(serial="emulator-5554")
        self.assertTrue(device.is_emulator)
        self.assertEqual(device.transport_label, "Emulator")

    def test_a_colon_alone_is_not_a_network_device(self):
        self.assertFalse(Device(serial="weird:serial").over_tcpip)


class TestSummary(unittest.TestCase):
    def test_full(self):
        device = Device(
            serial="x", state="device", android_release="15", battery=87,
            width=1080, height=2424,
        )
        self.assertEqual(device.summary(), "USB · Android 15 · 87%")
        self.assertEqual(device.resolution, "1080×2424")

    def test_bare(self):
        self.assertEqual(Device(serial="x").summary(), "USB")
        self.assertIsNone(Device(serial="x").resolution)

    def test_battery_zero_is_shown(self):
        self.assertIn("0%", Device(serial="x", battery=0).summary())


class TestReadiness(unittest.TestCase):
    def test_device_is_ready(self):
        self.assertTrue(Device(serial="x", state="device").ready)

    def test_others_are_not(self):
        for state in ("unauthorized", "offline", "no permissions", "bootloader"):
            self.assertFalse(Device(serial="x", state=state).ready, state)


class TestMerge(unittest.TestCase):
    def test_enrichment_is_carried_over_a_plain_listing(self):
        rich = Device(serial="x", android_release="15", battery=90, enriched=True)
        plain = Device(serial="x", model="Pixel_9")
        merged = rich.merged_with(plain)
        self.assertEqual(merged.android_release, "15")
        self.assertEqual(merged.battery, 90)
        self.assertEqual(merged.model, "Pixel_9")

    def test_a_fresh_probe_wins(self):
        old = Device(serial="x", battery=10, enriched=True)
        new = Device(serial="x", battery=99, enriched=True)
        self.assertEqual(old.merged_with(new).battery, 99)

    def test_different_devices_are_not_merged(self):
        rich = Device(serial="a", battery=90, enriched=True)
        other = Device(serial="b")
        self.assertIsNone(rich.merged_with(other).battery)


class TestProfileAndSession(unittest.TestCase):
    def test_copy_as_clears_builtin(self):
        original = Profile(name="Balanced", builtin=True, max_size=720)
        copy = original.copy_as("Mine")
        self.assertFalse(copy.builtin)
        self.assertEqual(copy.max_size, 720)
        self.assertTrue(original.builtin)

    def test_session_recording_flag(self):
        from pathlib import Path

        plain = Session(serial="x", profile_name="p", pid=1, started_at=0.0)
        self.assertFalse(plain.recording)
        recording = Session(
            serial="x", profile_name="p", pid=1, started_at=0.0,
            record_path=Path("/tmp/a.mp4"),
        )
        self.assertTrue(recording.recording)


if __name__ == "__main__":
    unittest.main()
