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


class TestBuddyStateGraph(unittest.TestCase):
    def test_default_daily_budget(self):
        s = buddy_state.BuddyState()
        self.assertEqual(s.daily_budget_tokens, 500_000)

    def test_set_daily_budget_round_trips_in_memory(self):
        s = buddy_state.BuddyState()
        s.set_daily_budget_tokens(1_000_000)
        self.assertEqual(s.daily_budget_tokens, 1_000_000)

    def test_percentage_returns_none_when_budget_is_zero(self):
        s = buddy_state.BuddyState()
        s.set_daily_budget_tokens(0)
        # tokens_today not yet set; defaults to 0
        self.assertIsNone(s.budget_percentage(50_000))

    def test_percentage_clamps_to_999(self):
        # Over-budget: don't show 1200% (UI line would wrap). Clamp to 999.
        s = buddy_state.BuddyState()
        s.set_daily_budget_tokens(1000)
        self.assertEqual(s.budget_percentage(15000), 999)

    def test_percentage_basic(self):
        s = buddy_state.BuddyState()
        s.set_daily_budget_tokens(200_000)
        self.assertEqual(s.budget_percentage(64200), 32)

    def test_graph_samples_start_empty(self):
        s = buddy_state.BuddyState()
        self.assertEqual(list(s.graph_samples), [])

    def test_record_graph_sample_appends(self):
        s = buddy_state.BuddyState()
        s.record_graph_sample(timestamp_ms=1000, cum_tokens=100)
        s.record_graph_sample(timestamp_ms=2000, cum_tokens=250)
        self.assertEqual(list(s.graph_samples), [(1000, 100), (2000, 250)])

    def test_reset_graph_clears_samples(self):
        s = buddy_state.BuddyState()
        s.record_graph_sample(timestamp_ms=1000, cum_tokens=100)
        s.reset_graph()
        self.assertEqual(list(s.graph_samples), [])


class TestSourceTabPersistence(unittest.TestCase):
    def test_default_source_tab_is_cd(self):
        s = buddy_state.BuddyState()
        self.assertEqual(s.source_tab, "CD")

    def test_set_source_tab_round_trips_in_memory(self):
        s = buddy_state.BuddyState()
        s.set_source_tab("CC")
        self.assertEqual(s.source_tab, "CC")

    def test_invalid_source_tab_is_rejected(self):
        s = buddy_state.BuddyState()
        s.set_source_tab("XX")  # invalid — silently ignored
        self.assertEqual(s.source_tab, "CD")


if __name__ == "__main__":
    unittest.main()
