#!/usr/bin/env python3
"""buddyd — daemon that tails Claude Code stop-hook events and pushes
heartbeats over BLE to the Cardputer.

Pipeline:
  ~/.buddyd/events.jsonl
        │  (watchdog or 2s polling fallback)
        ▼
  DailyAggregator (per-local-date accumulators)
        │  (one heartbeat per event)
        ▼
  CCBleClient (connected only when the device advertises ClaudeCC_*)

Run with `python -m buddy.host.buddyd`. Ctrl-C to stop.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path

from buddy.host.aggregator import DailyAggregator
from buddy.host.ble_client import CCBleClient

EVENTS_PATH = Path(os.path.expanduser("~/.buddyd/events.jsonl"))
POLL_INTERVAL_S = 2.0

_logger = logging.getLogger("buddyd")


async def _tail(path: Path, queue: asyncio.Queue) -> None:
    """Polling tail. Watchdog/inotify integration is a future improvement;
    2 s polling is fine for this scale (we expect < 1 event/sec)."""
    last_size = 0
    pos = 0
    while True:
        try:
            if path.exists():
                size = path.stat().st_size
                if size < last_size:
                    # File was rotated / truncated. Start over.
                    pos = 0
                if size > pos:
                    with path.open("r") as f:
                        f.seek(pos)
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                event = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            await queue.put(event)
                        pos = f.tell()
                last_size = size
        except OSError as e:
            _logger.warning("tail error: %s", e)
        await asyncio.sleep(POLL_INTERVAL_S)


async def _consumer(queue: asyncio.Queue, agg: DailyAggregator, ble: CCBleClient) -> None:
    while True:
        event = await queue.get()
        heartbeat = agg.consume(event)
        if heartbeat is None:
            continue
        ok = await ble.push(heartbeat)
        if not ok:
            _logger.debug("not connected; heartbeat dropped (will be replaced by next event)")


async def amain() -> int:
    logging.basicConfig(
        level=os.environ.get("BUDDYD_LOG", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVENTS_PATH.touch(exist_ok=True)
    local_tz = dt.datetime.now().astimezone().tzinfo
    agg = DailyAggregator(local_tz=local_tz)
    ble = CCBleClient()
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    tasks = [
        asyncio.create_task(ble.run()),
        asyncio.create_task(_tail(EVENTS_PATH, queue)),
        asyncio.create_task(_consumer(queue, agg, ble)),
    ]
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
