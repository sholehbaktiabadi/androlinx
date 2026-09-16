"""Loading, saving and repairing the profile store."""

from __future__ import annotations

import json
import unittest

from support import IsolatedConfig

from androlinx import profiles as store
from androlinx.const import profiles_path
from androlinx.models import Device, Profile
from androlinx.scrcpy import Capabilities, build_command

CAPS = Capabilities(version=(3, 3, 4), window_placement=False)
DEVICE = Device(serial="ABC")


class TestBuiltins(unittest.TestCase):
    def test_names_are_unique(self):
        names = [p.name for p in store.BUILTINS]
        self.assertEqual(len(names), len(set(names)))

    def test_all_marked_builtin(self):
        self.assertTrue(all(p.builtin for p in store.BUILTINS))

    def test_default_exists(self):
        self.assertIn(store.DEFAULT_PROFILE, [p.name for p in store.BUILTINS])

    def test_codecs_are_valid(self):
        for profile in store.BUILTINS:
            self.assertIn(profile.video_codec, store.VIDEO_CODECS)
            self.assertIn(profile.audio_codec, store.AUDIO_CODECS)


class TestRoundTrip(IsolatedConfig):
    def test_fresh_install_gets_the_builtins(self):
        profiles, read_only = store.load()
        self.assertEqual([p.name for p in profiles], [p.name for p in store.BUILTINS])
        self.assertFalse(read_only)
        self.assertFalse(profiles_path().exists())

    def test_save_and_load_a_user_profile(self):
        profiles, _ = store.load()
        mine = Profile(name="My desk", max_size=1440, video_bit_rate=12)
        store.save([*profiles, mine])

        loaded, _ = store.load()
        self.assertEqual(loaded[-1].name, "My desk")
        self.assertEqual(loaded[-1].max_size, 1440)
        self.assertEqual(loaded[-1].video_bit_rate, 12)
        self.assertFalse(loaded[-1].builtin)

    def test_builtins_are_not_written_to_disk(self):
        profiles, _ = store.load()
        store.save(profiles)
        data = json.loads(profiles_path().read_text())
        self.assertEqual(data["profiles"], [])

    def test_no_temporary_file_is_left_behind(self):
        store.save([*store.BUILTINS, Profile(name="x")])
        leftovers = [p.name for p in profiles_path().parent.iterdir()
                     if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestForwardCompatibility(IsolatedConfig):
    def _write(self, payload: dict) -> None:
        profiles_path().parent.mkdir(parents=True, exist_ok=True)
        profiles_path().write_text(json.dumps(payload))

    def test_unknown_keys_survive_a_round_trip(self):
        self._write({
            "version": 1,
            "profiles": [{"name": "Mine", "max_size": 720, "future_option": "keep me"}],
        })
        profiles, read_only = store.load()
        self.assertEqual(profiles[-1].extra, {"future_option": "keep me"})

        store.save(profiles, read_only=read_only)
        written = json.loads(profiles_path().read_text())
        self.assertEqual(written["profiles"][0]["future_option"], "keep me")

    def test_a_newer_file_is_never_written_back(self):
        self._write({"version": 99, "profiles": [{"name": "Mine"}]})
        profiles, read_only = store.load()
        self.assertTrue(read_only)

        store.save([*profiles, Profile(name="New")], read_only=read_only)
        # Unchanged: a newer format is left exactly as it was found.
        self.assertEqual(json.loads(profiles_path().read_text())["version"], 99)

    def test_broken_json_falls_back_without_destroying_the_file(self):
        profiles_path().parent.mkdir(parents=True, exist_ok=True)
        profiles_path().write_text("{ not json at all")
        profiles, read_only = store.load()
        self.assertEqual([p.name for p in profiles], [p.name for p in store.BUILTINS])
        self.assertTrue(read_only)
        self.assertEqual(profiles_path().read_text(), "{ not json at all")

    def test_one_broken_entry_does_not_lose_the_others(self):
        self._write({
            "version": 1,
            "profiles": [
                {"name": "Good one", "max_size": 720},
                {"name": ""},
                "not even an object",
                {"name": "Good two", "max_size": 480},
            ],
        })
        profiles, _ = store.load()
        names = [p.name for p in profiles if not p.builtin]
        self.assertEqual(names, ["Good one", "Good two"])

    def test_a_user_profile_cannot_shadow_a_builtin(self):
        self._write({"version": 1, "profiles": [{"name": "Balanced", "max_size": 240}]})
        profiles, _ = store.load()
        balanced = [p for p in profiles if p.name == "Balanced"]
        self.assertEqual(len(balanced), 1)
        self.assertTrue(balanced[0].builtin)


class TestClamping(IsolatedConfig):
    def _one(self, payload: dict) -> Profile:
        profiles_path().parent.mkdir(parents=True, exist_ok=True)
        profiles_path().write_text(
            json.dumps({"version": 1, "profiles": [{"name": "T", **payload}]})
        )
        return store.load()[0][-1]

    def test_unknown_codec_falls_back(self):
        self.assertEqual(self._one({"video_codec": "vp9"}).video_codec, "h264")

    def test_oversized_resolution_is_clamped(self):
        self.assertEqual(self._one({"max_size": 99999}).max_size, 4096)

    def test_zero_resolution_means_native(self):
        self.assertIsNone(self._one({"max_size": 0}).max_size)

    def test_non_numeric_fps_falls_back(self):
        self.assertEqual(self._one({"max_fps": "fast"}).max_fps, 60)

    def test_bad_orientation_falls_back(self):
        self.assertEqual(self._one({"orientation": "sideways"}).orientation, "")

    def test_clamped_values_still_build_a_command(self):
        profile = self._one({"video_codec": "vp9", "max_size": 99999, "max_fps": "fast"})
        argv = build_command(profile, DEVICE, caps=CAPS)
        self.assertIn("--max-size=4096", argv)
        self.assertNotIn("--video-codec=vp9", argv)


class TestNaming(unittest.TestCase):
    def setUp(self):
        self.profiles = list(store.BUILTINS)

    def test_free_name_is_kept(self):
        self.assertEqual(store.unique_name(self.profiles, "Desk"), "Desk")

    def test_taken_name_is_numbered(self):
        self.assertEqual(store.unique_name(self.profiles, "Balanced"), "Balanced 2")

    def test_comparison_ignores_case(self):
        self.assertEqual(store.unique_name(self.profiles, "balanced"), "balanced 2")

    def test_blank_name_gets_a_default(self):
        self.assertEqual(store.unique_name(self.profiles, "   "), "Custom")

    def test_find_falls_back_to_the_first(self):
        self.assertEqual(store.find(self.profiles, "nope").name, self.profiles[0].name)

    def test_copy_as_is_not_builtin(self):
        copy = store.BUILTINS[0].copy_as("Mine")
        self.assertFalse(copy.builtin)
        self.assertEqual(copy.name, "Mine")
        self.assertEqual(copy.max_size, store.BUILTINS[0].max_size)


if __name__ == "__main__":
    unittest.main()
