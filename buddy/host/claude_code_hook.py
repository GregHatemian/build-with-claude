#!/usr/bin/env python3
"""Claude Code Stop hook → JSONL appender.

Reads the hook payload from stdin, extracts the assistant's last
usage block (if any), and appends a single JSON line to
~/.buddyd/events.jsonl. Always exits 0 even on malformed input — a
hook must never block Claude Code.

Registered in ~/.claude/settings.json under .hooks.Stop. The path to
this script is what install_hook.sh writes.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys


DEFAULT_EVENTS_PATH = os.path.expanduser("~/.buddyd/events.jsonl")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _extract_usage(payload: dict):
    transcript = payload.get("transcript") or []
    for msg in reversed(transcript):
        if msg.get("role") != "assistant":
            continue
        return msg.get("usage")
    return None


def main(events_path: str = DEFAULT_EVENTS_PATH, now=_now) -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    record = {
        "ts": now(),
        "usage": _extract_usage(payload),
        "msg": payload.get("msg", "Stop"),
    }
    try:
        os.makedirs(os.path.dirname(events_path), exist_ok=True)
        with open(events_path, "a") as f:
            f.write(json.dumps(record))
            f.write("\n")
    except OSError:
        # Filesystem error → exit silently. Better to lose a stats event
        # than to make Claude Code feel slow or broken.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
