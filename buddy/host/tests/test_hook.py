"""Unit tests for the Stop-hook script.

We exercise the hook's main() with a synthetic stdin + a temp-dir
events file, then read back the JSONL and assert one record was
appended with the expected shape.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from buddy.host import claude_code_hook as hook


class TestStopHook(unittest.TestCase):
    def test_appends_record_with_usage_block(self):
        payload = {
            "transcript": [{"role": "user", "content": "hi"}, {
                "role": "assistant",
                "content": "hello",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 20,
                },
            }],
            "msg": "Stop",
        }
        with tempfile.TemporaryDirectory() as d:
            events_path = os.path.join(d, "events.jsonl")
            with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
                exit_code = hook.main(events_path=events_path, now=lambda: "2026-05-25T14:00:00+00:00")
            self.assertEqual(exit_code, 0)
            with open(events_path) as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 1)
            rec = json.loads(lines[0])
            self.assertEqual(rec["ts"], "2026-05-25T14:00:00+00:00")
            self.assertEqual(rec["usage"]["input_tokens"], 100)

    def test_no_assistant_message_writes_null_usage(self):
        payload = {"transcript": [{"role": "user", "content": "hi"}], "msg": "Stop"}
        with tempfile.TemporaryDirectory() as d:
            events_path = os.path.join(d, "events.jsonl")
            with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
                hook.main(events_path=events_path, now=lambda: "2026-05-25T14:00:00+00:00")
            with open(events_path) as f:
                rec = json.loads(f.readline())
            self.assertIsNone(rec["usage"])

    def test_malformed_payload_returns_nonzero_but_does_not_raise(self):
        # Empty stdin → silent skip, exit 0 (the hook must NEVER block CC).
        with tempfile.TemporaryDirectory() as d:
            events_path = os.path.join(d, "events.jsonl")
            with mock.patch.object(sys, "stdin", io.StringIO("")):
                exit_code = hook.main(events_path=events_path, now=lambda: "2026-05-25T14:00:00+00:00")
            self.assertEqual(exit_code, 0)
            # File may not even exist — that's fine.

    def test_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as d:
            events_path = os.path.join(d, "nested", "subdir", "events.jsonl")
            payload = {"transcript": [], "msg": "Stop"}
            with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
                hook.main(events_path=events_path, now=lambda: "2026-05-25T14:00:00+00:00")
            self.assertTrue(os.path.exists(events_path))


if __name__ == "__main__":
    unittest.main()
