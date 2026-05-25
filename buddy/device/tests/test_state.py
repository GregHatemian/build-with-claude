"""Pure-CPython unit tests for buddy_state.

buddy_state.py degrades to _NVS = None on non-MicroPython builds, so
these tests exercise the in-memory paths without a device.
"""

import os
import sys
import unittest

# Make sure we import buddy_state from buddy/device/, not anywhere else.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import buddy_state  # type: ignore


class TestU32Helpers(unittest.TestCase):
    def test_get_u32_returns_default_when_nvs_absent(self):
        # On CPython, _NVS is None — the helper should return the default.
        self.assertEqual(buddy_state._get_u32("nonexistent_key", 42), 42)

    def test_set_u32_is_noop_when_nvs_absent(self):
        # Should not raise. We're not asserting anything was stored —
        # only that the code path is safe on a dev machine.
        buddy_state._set_u32("test_key", 1234)


if __name__ == "__main__":
    unittest.main()
