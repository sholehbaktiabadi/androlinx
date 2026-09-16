"""Parsing what adb prints.

Every sample here is real output shape, including the noise adb mixes into
stdout and the awkward two-word `no permissions` state whose trailing URL has
broken more than one parser.
"""

from __future__ import annotations

import unittest

from support import IsolatedConfig  # noqa: F401  (keeps sys.path set up)

from androlinx.adb import (
    TrackStream,
    parse_devices,
    parse_ip,
    parse_mdns,
    parse_probe,
)
from androlinx.errors import AdbError, TrackFormatError


def frame(body: bytes) -> bytes:
    """Wrap a payload the way the adb host protocol does."""
    return f"{len(body):04x}".encode() + body


class TestParseDevices(unittest.TestCase):
    def test_header_only(self):
        self.assertEqual(parse_devices("List of devices attached\n"), [])

    def test_empty(self):
        self.assertEqual(parse_devices(""), [])

    def test_daemon_noise_is_skipped(self):
        text = (
            "* daemon not running; starting now at tcp:5037\n"
            "* daemon started successfully\n"
            "List of devices attached\n"
            "R58M123456\tdevice\n"
        )
        devices = parse_devices(text)
        self.assertEqual([d.serial for d in devices], ["R58M123456"])

    def test_long_form_attributes(self):
        text = (
            "List of devices attached\n"
            "R58M123456   device product:a53x model:SM_A536E device:a53x transport_id:3\n"
        )
        device = parse_devices(text)[0]
        self.assertEqual(device.state, "device")
        self.assertEqual(device.model, "SM_A536E")
        self.assertEqual(device.product, "a53x")
        self.assertEqual(device.codename, "a53x")
        self.assertEqual(device.transport_id, "3")

    def test_no_permissions_url_is_not_mistaken_for_attributes(self):
        text = (
            "0123456789  no permissions; see "
            "[http://developer.android.com/tools/device.html]  usb:1-3 transport_id:11\n"
        )
        device = parse_devices(text)[0]
        self.assertEqual(device.state, "no permissions")
        self.assertEqual(device.transport_id, "11")
        # The `http:` in the URL must not become an attribute.
        self.assertIsNone(device.product)
        self.assertIsNone(device.model)

    def test_unauthorized_and_offline(self):
        text = "aaa\tunauthorized\nbbb\toffline\n"
        self.assertEqual([d.state for d in parse_devices(text)], ["unauthorized", "offline"])

    def test_tcpip_and_usb_are_distinguished(self):
        text = "192.168.1.5:5555\tdevice\nR58M123456\tdevice\n"
        first, second = parse_devices(text)
        self.assertTrue(first.over_tcpip)
        self.assertFalse(second.over_tcpip)

    def test_mdns_serial_counts_as_network(self):
        device = parse_devices("adb-R58M99-xyz._adb-tls-connect._tcp\tdevice\n")[0]
        self.assertTrue(device.over_tcpip)
        self.assertEqual(device.transport_label, "Wi-Fi")

    def test_emulator(self):
        device = parse_devices("emulator-5554\tdevice\n")[0]
        self.assertTrue(device.is_emulator)
        self.assertEqual(device.transport_label, "Emulator")

    def test_crlf(self):
        devices = parse_devices("List of devices attached\r\nABC\tdevice\r\n")
        self.assertEqual([d.serial for d in devices], ["ABC"])

    def test_unknown_state_is_kept_not_dropped(self):
        device = parse_devices("ABC\tsomethingnew\n")[0]
        self.assertEqual(device.state, "somethingnew")
        self.assertFalse(device.ready)


class TestParseProbe(unittest.TestCase):
    SAMPLE = (
        "@P\nGoogle\nPixel 9\n15\n35\n"
        "@S\nPhysical size: 1080x2424\n"
        "@D\nPhysical density: 420\n"
        "@B\n  level: 87\n  status: 2\n"
        "@E\n"
    )

    def test_full(self):
        data = parse_probe(self.SAMPLE)
        self.assertEqual(data["manufacturer"], "Google")
        self.assertEqual(data["model"], "Pixel 9")
        self.assertEqual(data["android_release"], "15")
        self.assertEqual(data["sdk"], 35)
        self.assertEqual((data["width"], data["height"]), (1080, 2424))
        self.assertEqual(data["density"], 420)
        self.assertEqual(data["battery"], 87)

    def test_override_size_wins(self):
        text = self.SAMPLE.replace(
            "Physical size: 1080x2424",
            "Physical size: 1080x2424\nOverride size: 1080x2340",
        )
        data = parse_probe(text)
        # scrcpy captures the override, so that is the size worth reporting.
        self.assertEqual((data["width"], data["height"]), (1080, 2340))

    def test_missing_property_keeps_the_positions(self):
        # getprop prints a blank line for a property that is not set.
        data = parse_probe("@P\nGoogle\nPixel 9\n\n35\n@S\n@D\n@B\n@E\n")
        self.assertIsNone(data["android_release"])
        self.assertEqual(data["sdk"], 35)

    def test_missing_dumpsys_is_not_an_error(self):
        data = parse_probe(
            "@P\nXiaomi\nRedmi\n14\n34\n@S\n@D\n@B\n"
            "/system/bin/sh: dumpsys: inaccessible or not found\n@E\n"
        )
        self.assertIsNone(data["battery"])
        self.assertEqual(data["manufacturer"], "Xiaomi")

    def test_truncated_output_raises(self):
        # No @E means the phone went away mid-probe; half an answer is worse
        # than none, because it would be cached as though it were complete.
        with self.assertRaises(AdbError):
            parse_probe("@P\nGoogle\nPixel 9\n15\n35\n@S\nPhysical size: 10")

    def test_crlf(self):
        data = parse_probe(self.SAMPLE.replace("\n", "\r\n"))
        self.assertEqual(data["manufacturer"], "Google")

    def test_non_numeric_sdk(self):
        data = parse_probe("@P\nGoogle\nPixel\n15\nbanana\n@S\n@D\n@B\n@E\n")
        self.assertIsNone(data["sdk"])


class TestTrackStream(unittest.TestCase):
    BODY = b"R58M123456\tdevice product:a53x model:SM_A536E transport_id:3\n"

    def test_empty_device_list(self):
        self.assertEqual(TrackStream().feed(b"0000"), [[]])

    def test_single_frame(self):
        snapshots = TrackStream().feed(frame(self.BODY))
        self.assertEqual([d.serial for d in snapshots[0]], ["R58M123456"])

    def test_split_inside_the_header(self):
        data = frame(self.BODY)
        stream = TrackStream()
        out = []
        for piece in (data[:2], data[2:6], data[6:30], data[30:]):
            out += stream.feed(piece)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0].model, "SM_A536E")

    def test_two_frames_in_one_read(self):
        out = TrackStream().feed(frame(self.BODY) + frame(b"ABC\tunauthorized\n"))
        self.assertEqual([[d.state for d in snap] for snap in out],
                         [["device"], ["unauthorized"]])

    def test_multibyte_character_split_across_reads(self):
        body = "XYZ\tdevice model:Café_Phone\n".encode("utf-8")
        data = frame(body)
        cut = data.index(b"\xc3")  # split inside the two-byte 'e-acute'
        stream = TrackStream()
        out = stream.feed(data[:cut + 1]) + stream.feed(data[cut + 1:])
        self.assertEqual(out[0][0].model, "Café_Phone")

    def test_incomplete_frame_yields_nothing_yet(self):
        self.assertEqual(TrackStream().feed(frame(self.BODY)[:10]), [])

    def test_non_hexadecimal_header(self):
        with self.assertRaises(TrackFormatError):
            TrackStream().feed(b"ZZZZrubbish")

    def test_implausible_length(self):
        # Must be reachable: four hex digits top out at 0xFFFF, so the bound has
        # to sit below that to mean anything.
        with self.assertRaises(TrackFormatError):
            TrackStream().feed(b"f000" + b"x" * 8)

    def test_large_but_sane_list_is_accepted(self):
        body = b"".join(
            f"SERIAL{i:04d}\tdevice model:Phone_{i}\n".encode() for i in range(100)
        )
        snapshots = TrackStream().feed(frame(body))
        self.assertEqual(len(snapshots[0]), 100)


class TestOtherParsers(unittest.TestCase):
    def test_mdns_distinguishes_pairing_from_connect(self):
        text = (
            "List of discovered mdns services\n"
            "adb-R58M-abc\t_adb-tls-pairing._tcp\t192.168.1.5:41234\n"
            "adb-R58M-abc\t_adb-tls-connect._tcp\t192.168.1.5:37999\n"
        )
        services = parse_mdns(text)
        self.assertEqual(len(services), 2)
        self.assertEqual(services[0][1], "_adb-tls-pairing._tcp")
        self.assertEqual(services[1][2], "192.168.1.5:37999")

    def test_mdns_empty(self):
        self.assertEqual(parse_mdns("List of discovered mdns services\n"), [])

    def test_ip_from_ip_route(self):
        self.assertEqual(
            parse_ip("1.0.0.0 via 192.168.1.1 dev wlan0 table 0 src 192.168.1.42 uid 0"),
            "192.168.1.42",
        )

    def test_ip_from_ifconfig(self):
        self.assertEqual(parse_ip("inet addr:10.0.0.7  Bcast:10.0.0.255"), "10.0.0.7")

    def test_ip_ignores_loopback(self):
        self.assertIsNone(parse_ip("inet addr:127.0.0.1  Mask:255.0.0.0"))

    def test_ip_absent(self):
        self.assertIsNone(parse_ip(""))


if __name__ == "__main__":
    unittest.main()
