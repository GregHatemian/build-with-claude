"""Per-day token + cache aggregator.

Pure logic — no I/O. Tests run on CPython without a device or daemon.

The aggregator owns one piece of mutable state: the current day's
cumulative counters. consume() takes one event dict and returns a
heartbeat dict ready to write to BLE (or None if the event was
ignored — e.g., missing usage info).
"""

from __future__ import annotations

import datetime as dt
from typing import Optional


class DailyAggregator:
    def __init__(self, local_tz: dt.tzinfo):
        self._tz = local_tz
        self._current_day: Optional[dt.date] = None
        self._daily_total = 0
        self._cache_read = 0
        self._cache_create = 0
        self._cache_uncached = 0
        self._last_msg = ""

    def consume(self, event: dict) -> Optional[dict]:
        usage = event.get("usage")
        if not usage:
            return None
        ts = event.get("ts")
        if not ts:
            return None
        when = dt.datetime.fromisoformat(ts).astimezone(self._tz).date()
        if when != self._current_day:
            self._current_day = when
            self._daily_total = 0
            self._cache_read = 0
            self._cache_create = 0
            self._cache_uncached = 0
        input_tokens = usage.get("input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0) or 0
        cache_create = usage.get("cache_creation_input_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        uncached = input_tokens - cache_create - cache_read
        if uncached < 0:
            uncached = 0
        self._daily_total += input_tokens + output_tokens
        self._cache_read += cache_read
        self._cache_create += cache_create
        self._cache_uncached += uncached
        return {
            "source": "CC",
            "daily_total": self._daily_total,
            "tokens_today": self._daily_total,  # keep legacy field in sync
            "cache_read": self._cache_read,
            "cache_create": self._cache_create,
            "cache_uncached": self._cache_uncached,
            "msg": event.get("msg", "") or self._last_msg,
        }
