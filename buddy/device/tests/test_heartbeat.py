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


if __name__ == "__main__":
    unittest.main()
