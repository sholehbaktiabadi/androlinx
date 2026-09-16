"""Shared test helpers."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Tests run against the working tree, not an installed copy.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class IsolatedConfig(unittest.TestCase):
    """Point XDG_CONFIG_HOME at a temporary directory for the whole test case.

    Without this the tests would read and overwrite the developer's own
    profiles, which is a rude thing for a test suite to do.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._dir.name
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._saved is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._saved
        self._dir.cleanup()

    @property
    def config_dir(self) -> Path:
        return Path(self._dir.name)
