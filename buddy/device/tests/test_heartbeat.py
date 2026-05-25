"""Unit tests for the extended heartbeat schema.

The protocol module accepts new optional fields (source, daily_total,
cache_read/create/uncached). These tests pin the parse + acceptance
contract on a CPython interpreter without needing a device.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import buddy_protocol  # type: ignore


HB_FIELDS = ("total", "running", "waiting", "tokens", "tokens_today", "entries")


class TestHeartbeatDetection(unittest.TestCase):
    def test_detects_legacy_heartbeat_shape(self):
        hb = {"running": 1, "waiting": 0, "tokens_today": 1000}
        self.assertTrue(buddy_protocol._looks_like_heartbeat(hb))

    def test_detects_new_daily_total_field(self):
        hb = {"daily_total": 64200, "source": "CC"}
        self.assertTrue(buddy_protocol._looks_like_heartbeat(hb))

    def test_rejects_command_message(self):
        hb = {"cmd": "status"}
        self.assertFalse(buddy_protocol._looks_like_heartbeat(hb))

    def test_rejects_ack_message(self):
        hb = {"ack": "status", "name": "Foo"}
        self.assertFalse(buddy_protocol._looks_like_heartbeat(hb))


class TestSourceValidation(unittest.TestCase):
    def test_missing_source_defaults_to_cd(self):
        hb = {"tokens_today": 1000}  # legacy: no source
        self.assertEqual(buddy_protocol._heartbeat_source(hb), "CD")

    def test_explicit_cc_source(self):
        hb = {"daily_total": 1000, "source": "CC"}
        self.assertEqual(buddy_protocol._heartbeat_source(hb), "CC")

    def test_explicit_cd_source(self):
        hb = {"tokens_today": 1000, "source": "CD"}
        self.assertEqual(buddy_protocol._heartbeat_source(hb), "CD")

    def test_invalid_source_treated_as_cd(self):
        hb = {"daily_total": 1000, "source": "XX"}
        self.assertEqual(buddy_protocol._heartbeat_source(hb), "CD")


class FakeUI:
    def __init__(self):
        self.last_hb = None

    def update_heartbeat(self, hb):
        self.last_hb = hb

    def update_footer(self, *a, **k):
        pass

    def update_identity(self, *a, **k):
        pass

    def show_unpair_prompt(self, *a, **k):
        pass

    def clear_unpair_prompt(self, *a, **k):
        pass

    def flash_decision(self, *a, **k):
        pass


class FakeChars:
    pass


class FakeBLE:
    pairing_supported = False
    advertised_name = "Claude_test"

    def disconnect(self):
        pass


class TestHeartbeatApplication(unittest.TestCase):
    def setUp(self):
        # Import lazily here so the import error in earlier tests doesn't
        # mask this section.
        import buddy_state  # type: ignore

        self.state = buddy_state.BuddyState()
        self.ui = FakeUI()
        self.chars = FakeChars()
        self.ble = FakeBLE()
        self.proto = buddy_protocol.BuddyProtocol(
            state=self.state,
            ui=self.ui,
            chars=self.chars,
            ble=self.ble,
            battery_reader=lambda: {"pct": 100},
        )

    def test_cc_heartbeat_applied_when_tab_is_cc(self):
        self.state.set_source_tab("CC")
        self.proto.on_line(json.dumps({
            "source": "CC",
            "daily_total": 64200,
            "cache_read": 1_200_000,
            "cache_create": 180_000,
            "cache_uncached": 320_000,
            "tokens_today": 64200,
        }).encode("utf-8"))
        # The UI got the heartbeat dict so the renderer can pull what it needs.
        self.assertIsNotNone(self.ui.last_hb)
        self.assertEqual(self.ui.last_hb["daily_total"], 64200)
        self.assertEqual(self.ui.last_hb["cache_read"], 1_200_000)

    def test_cd_heartbeat_rejected_when_tab_is_cc(self):
        self.state.set_source_tab("CC")
        # Vanilla Claude Desktop heartbeat: no source field → treated as CD.
        self.proto.on_line(json.dumps({"tokens_today": 5000, "running": 1}).encode("utf-8"))
        self.assertIsNone(self.ui.last_hb)

    def test_cc_heartbeat_rejected_when_tab_is_cd(self):
        self.state.set_source_tab("CD")
        self.proto.on_line(json.dumps({"source": "CC", "daily_total": 1000}).encode("utf-8"))
        self.assertIsNone(self.ui.last_hb)

    def test_cc_heartbeat_records_graph_sample(self):
        self.state.set_source_tab("CC")
        self.assertEqual(len(self.state.graph_samples), 0)
        self.proto.handle_line(json.dumps({
            "source": "CC", "daily_total": 64200,
        }))
        self.assertEqual(len(self.state.graph_samples), 1)
        # Subsequent CC heartbeat appends another sample.
        self.proto.handle_line(json.dumps({
            "source": "CC", "daily_total": 70000,
        }))
        self.assertEqual(len(self.state.graph_samples), 2)
        # CD-shaped heartbeat (no source field, no daily_total) shouldn't
        # touch graph_samples on CC tab (it's filtered out as wrong source).
        self.proto.handle_line(json.dumps({"tokens_today": 5000}))
        self.assertEqual(len(self.state.graph_samples), 2)


if __name__ == "__main__":
    unittest.main()
