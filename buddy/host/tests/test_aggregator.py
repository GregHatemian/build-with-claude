"""Unit tests for the per-day token + cache aggregator.

The aggregator consumes hook events shaped like:
    {"ts": "2026-05-25T14:32:11+02:00", "usage": {
        "input_tokens": 1200, "output_tokens": 340,
        "cache_creation_input_tokens": 800, "cache_read_input_tokens": 4200}}
and produces a heartbeat dict per the device protocol.
"""

import datetime as dt
import unittest

from buddy.host.aggregator import DailyAggregator


class TestDailyAggregator(unittest.TestCase):
    def test_first_event_initializes_day(self):
        agg = DailyAggregator(local_tz=dt.timezone.utc)
        hb = agg.consume({
            "ts": "2026-05-25T08:00:00+00:00",
            "usage": {
                "input_tokens": 100, "output_tokens": 50,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            },
        })
        self.assertEqual(hb["source"], "CC")
        self.assertEqual(hb["daily_total"], 150)
        self.assertEqual(hb["cache_read"], 0)
        self.assertEqual(hb["cache_create"], 0)
        self.assertEqual(hb["cache_uncached"], 100)

    def test_subsequent_event_accumulates(self):
        agg = DailyAggregator(local_tz=dt.timezone.utc)
        agg.consume({"ts": "2026-05-25T08:00:00+00:00", "usage": {
            "input_tokens": 100, "output_tokens": 50,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}})
        hb = agg.consume({"ts": "2026-05-25T09:00:00+00:00", "usage": {
            "input_tokens": 200, "output_tokens": 100,
            "cache_creation_input_tokens": 500, "cache_read_input_tokens": 1000}})
        # Day total: 100+50+200+100 = 450 (output and cache_create count too)
        self.assertEqual(hb["daily_total"], 450)
        self.assertEqual(hb["cache_read"], 1000)
        self.assertEqual(hb["cache_create"], 500)
        # Uncached: input minus cache_read minus cache_create, accumulated
        # = (100-0-0) + (200-1000-500) — but the second event has more
        # cache_read than input_tokens, so uncached for that event is 0.
        # Total uncached = 100 + 0 = 100.
        self.assertEqual(hb["cache_uncached"], 100)

    def test_date_rollover_resets_aggregates(self):
        agg = DailyAggregator(local_tz=dt.timezone.utc)
        agg.consume({"ts": "2026-05-25T23:30:00+00:00", "usage": {
            "input_tokens": 500, "output_tokens": 200,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}})
        hb = agg.consume({"ts": "2026-05-26T00:30:00+00:00", "usage": {
            "input_tokens": 50, "output_tokens": 25,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}})
        # New day: only the second event counts.
        self.assertEqual(hb["daily_total"], 75)

    def test_missing_usage_field_is_ignored(self):
        agg = DailyAggregator(local_tz=dt.timezone.utc)
        # Some hook payloads may not have a usage block (e.g., very short
        # assistant turn with no token info). Aggregator should not crash
        # and should return None to signal 'no update'.
        hb = agg.consume({"ts": "2026-05-25T08:00:00+00:00", "usage": None})
        self.assertIsNone(hb)
        # State should not advance.
        hb2 = agg.consume({"ts": "2026-05-25T09:00:00+00:00", "usage": {
            "input_tokens": 100, "output_tokens": 50,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}})
        self.assertEqual(hb2["daily_total"], 150)

    def test_uncached_never_negative(self):
        # If cache_read > input_tokens for some reason, uncached for that
        # event clamps to 0 (defensive — the API shouldn't produce this
        # but a bad hook payload shouldn't break aggregation).
        agg = DailyAggregator(local_tz=dt.timezone.utc)
        hb = agg.consume({"ts": "2026-05-25T08:00:00+00:00", "usage": {
            "input_tokens": 100, "output_tokens": 50,
            "cache_creation_input_tokens": 50, "cache_read_input_tokens": 200}})
        self.assertEqual(hb["cache_uncached"], 0)


if __name__ == "__main__":
    unittest.main()
