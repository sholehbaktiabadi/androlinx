"""Building the scrcpy command line.

This is the one function whose output goes straight to a subprocess, and the
same function feeds the command preview in the window, so what it produces has
to be exactly right and stable enough to assert on.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from support import IsolatedConfig  # noqa: F401  (keeps sys.path set up)

from androlinx.models import Device, Profile
from androlinx.profiles import BUILTINS
from androlinx.scrcpy import Capabilities, build_command, parse_version

X11 = Capabilities(version=(3, 3, 4), window_placement=True)
WAYLAND = Capabilities(version=(3, 3, 4), window_placement=False)
DEVICE = Device(serial="R58M123456", model="Pixel_9")


class TestParseVersion(unittest.TestCase):
    def test_release(self):
        self.assertEqual(
            parse_version("scrcpy 3.3.4 <https://github.com/Genymobile/scrcpy>"),
            (3, 3, 4),
        )

    def test_two_components(self):
        self.assertEqual(parse_version("scrcpy 2.0"), (2, 0, 0))

    def test_git_build(self):
        self.assertEqual(parse_version("scrcpy 3.3.4-1-gabcdef"), (3, 3, 4))

    def test_version_on_a_later_line(self):
        self.assertEqual(parse_version("warning: blah\nscrcpy 3.1.0\n"), (3, 1, 0))

    def test_garbage(self):
        self.assertIsNone(parse_version("command not found"))

    def test_empty(self):
        self.assertIsNone(parse_version(""))


class TestCapabilities(unittest.TestCase):
    def test_supported_floor(self):
        self.assertTrue(Capabilities(version=(3, 0, 0)).supported)
        self.assertTrue(Capabilities(version=(3, 3, 4)).supported)

    def test_too_old(self):
        self.assertFalse(Capabilities(version=(2, 7, 0)).supported)
        self.assertFalse(Capabilities(version=None).supported)

    def test_version_text(self):
        self.assertEqual(Capabilities(version=(3, 3, 4)).version_text, "3.3.4")
        self.assertEqual(Capabilities(version=None).version_text, "unknown")


class TestBuildCommand(unittest.TestCase):
    def test_default_profile(self):
        argv = build_command(Profile(name="Balanced"), DEVICE, caps=WAYLAND)
        self.assertEqual(
            argv,
            [
                "scrcpy",
                "-s",
                "R58M123456",
                "--max-size=1080",
                "--video-bit-rate=8M",
                "--max-fps=60",
                "--stay-awake",
            ],
        )

    def test_serial_always_comes_first(self):
        argv = build_command(Profile(name="x"), DEVICE, caps=WAYLAND)
        self.assertEqual(argv[1:3], ["-s", "R58M123456"])

    def test_no_device_means_no_serial(self):
        argv = build_command(Profile(name="x"), None, caps=WAYLAND)
        self.assertNotIn("-s", argv)

    def test_everything_at_its_default_is_omitted(self):
        profile = Profile(
            name="bare",
            max_size=None,
            video_bit_rate=0,
            max_fps=None,
            stay_awake=False,
        )
        self.assertEqual(build_command(profile, DEVICE, caps=WAYLAND),
                         ["scrcpy", "-s", "R58M123456"])

    def test_always_on_top_is_dropped_under_wayland(self):
        profile = Profile(name="top", always_on_top=True)
        self.assertNotIn("--always-on-top", build_command(profile, DEVICE, caps=WAYLAND))

    def test_always_on_top_is_kept_on_x11(self):
        profile = Profile(name="top", always_on_top=True)
        self.assertIn("--always-on-top", build_command(profile, DEVICE, caps=X11))

    def test_recording(self):
        argv = build_command(
            Profile(name="rec"), DEVICE, caps=WAYLAND, record_to=Path("/tmp/a b.mp4")
        )
        self.assertIn("--record=/tmp/a b.mp4", argv)

    def test_window_title_stays_one_argument(self):
        argv = build_command(
            Profile(name="x"), DEVICE, caps=WAYLAND, window_title="Pixel 9 — AndroLinx"
        )
        self.assertIn("--window-title=Pixel 9 — AndroLinx", argv)

    def test_audio_off(self):
        argv = build_command(Profile(name="x", audio=False), DEVICE, caps=WAYLAND)
        self.assertIn("--no-audio", argv)
        self.assertFalse([a for a in argv if a.startswith("--audio-codec")])

    def test_non_default_audio_codec(self):
        argv = build_command(
            Profile(name="x", audio=True, audio_codec="aac"), DEVICE, caps=WAYLAND
        )
        self.assertIn("--audio-codec=aac", argv)

    def test_default_codecs_are_not_emitted(self):
        argv = build_command(Profile(name="x"), DEVICE, caps=WAYLAND)
        self.assertNotIn("--video-codec=h264", argv)
        self.assertNotIn("--audio-codec=opus", argv)

    def test_view_only(self):
        argv = build_command(Profile(name="x", control=False), DEVICE, caps=WAYLAND)
        self.assertIn("--no-control", argv)

    def test_orientation(self):
        argv = build_command(Profile(name="x", orientation="90"), DEVICE, caps=WAYLAND)
        self.assertIn("--orientation=90", argv)

    def test_every_element_is_a_string(self):
        profile = Profile(
            name="all",
            fullscreen=True,
            borderless=True,
            turn_screen_off=True,
            show_touches=True,
            power_off_on_close=True,
            orientation="180",
            video_codec="h265",
            audio_codec="aac",
            control=False,
        )
        argv = build_command(
            profile, DEVICE, caps=X11, record_to=Path("/tmp/x.mp4"), window_title="T"
        )
        for item in argv:
            self.assertIsInstance(item, str)

    def test_is_pure(self):
        # Calling it twice must give the same answer and change nothing.
        profile = Profile(name="x")
        first = build_command(profile, DEVICE, caps=WAYLAND)
        second = build_command(profile, DEVICE, caps=WAYLAND)
        self.assertEqual(first, second)
        self.assertEqual(profile, Profile(name="x"))

    def test_executable_can_be_overridden(self):
        argv = build_command(
            Profile(name="x"), DEVICE, caps=WAYLAND, executable="/opt/scrcpy"
        )
        self.assertEqual(argv[0], "/opt/scrcpy")

    def test_every_builtin_produces_a_usable_command(self):
        for profile in BUILTINS:
            with self.subTest(profile=profile.name):
                argv = build_command(profile, DEVICE, caps=WAYLAND)
                self.assertEqual(argv[0], "scrcpy")
                self.assertNotIn(None, argv)
                for item in argv:
                    self.assertIsInstance(item, str)
                    self.assertNotEqual(item, "")


if __name__ == "__main__":
    unittest.main()
