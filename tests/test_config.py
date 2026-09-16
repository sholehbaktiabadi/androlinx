"""The settings file."""

from __future__ import annotations

import json
import unittest

from support import IsolatedConfig

from androlinx import config as store
from androlinx.const import config_path


class TestRoundTrip(IsolatedConfig):
    def test_defaults_when_absent(self):
        config = store.load()
        self.assertEqual(config.on_quit, store.ON_QUIT_ASK)
        self.assertEqual(config.preview_interval, 2.0)
        self.assertFalse(config.read_only)

    def test_round_trip(self):
        config = store.load()
        config.on_quit = store.ON_QUIT_STOP
        config.preview_interval = 1.0
        config.force_x11 = True
        store.save(config)

        loaded = store.load()
        self.assertEqual(loaded.on_quit, store.ON_QUIT_STOP)
        self.assertEqual(loaded.preview_interval, 1.0)
        self.assertTrue(loaded.force_x11)

    def test_no_temporary_file_is_left_behind(self):
        store.save(store.load())
        leftovers = [p.name for p in config_path().parent.iterdir()
                     if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestRecentAddresses(unittest.TestCase):
    def test_newest_first_without_duplicates(self):
        config = store.Config()
        config.remember_address("a")
        config.remember_address("b")
        config.remember_address("a")
        self.assertEqual(config.recent_addresses, ["a", "b"])

    def test_bounded(self):
        config = store.Config()
        for n in range(20):
            config.remember_address(f"host{n}")
        self.assertEqual(len(config.recent_addresses), 8)
        self.assertEqual(config.recent_addresses[0], "host19")


class TestDamagedAndFutureFiles(IsolatedConfig):
    def _write(self, text: str) -> None:
        config_path().parent.mkdir(parents=True, exist_ok=True)
        config_path().write_text(text)

    def test_broken_file_falls_back_and_is_left_alone(self):
        self._write("{ not json")
        config = store.load()
        self.assertEqual(config.on_quit, store.ON_QUIT_ASK)
        self.assertTrue(config.read_only)

        config.on_quit = store.ON_QUIT_STOP
        store.save(config)
        self.assertEqual(config_path().read_text(), "{ not json")

    def test_a_newer_file_is_read_but_not_written(self):
        self._write(json.dumps({"version": 99, "on_quit": "keep", "new_thing": 1}))
        config = store.load()
        self.assertTrue(config.read_only)
        self.assertEqual(config.on_quit, "keep")
        self.assertEqual(config.extra, {"new_thing": 1})

        config.on_quit = store.ON_QUIT_STOP
        store.save(config)
        self.assertEqual(json.loads(config_path().read_text())["on_quit"], "keep")

    def test_unknown_keys_survive_a_round_trip(self):
        self._write(json.dumps({"version": 1, "on_quit": "keep", "future": [1, 2]}))
        config = store.load()
        self.assertEqual(config.extra, {"future": [1, 2]})
        store.save(config)
        self.assertEqual(json.loads(config_path().read_text())["future"], [1, 2])

    def test_invalid_values_fall_back(self):
        self._write(json.dumps({
            "version": 1,
            "on_quit": "explode",
            "preview_interval": "fast",
            "recent_addresses": ["ok", 5, None],
        }))
        config = store.load()
        self.assertEqual(config.on_quit, store.ON_QUIT_ASK)
        self.assertEqual(config.preview_interval, 2.0)
        self.assertEqual(config.recent_addresses, ["ok"])

    def test_an_interval_we_do_not_offer_falls_back(self):
        self._write(json.dumps({"version": 1, "preview_interval": 0.05}))
        self.assertEqual(store.load().preview_interval, 2.0)

    def test_a_list_instead_of_an_object(self):
        self._write("[1, 2, 3]")
        config = store.load()
        self.assertEqual(config.on_quit, store.ON_QUIT_ASK)
        self.assertTrue(config.read_only)


if __name__ == "__main__":
    unittest.main()
