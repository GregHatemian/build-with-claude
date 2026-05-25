"""bleak-based BLE central for the Cardputer.

Responsibilities:
- Continuously scan for an advertised name starting with `ClaudeCC_`.
- When seen, connect to the buddy GATT service.
- Maintain the connection while the device keeps advertising CC.
- Expose a single coroutine `push(heartbeat_dict)` that writes a JSON
  line to the RX characteristic.
- Reconnect on disconnect with exponential backoff (1s, 2s, 4s, cap 30s).

UUIDs come from the Nordic UART Service the device exposes — see
buddy/device/buddy_ble.py lines 78-84.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from bleak import BleakClient, BleakScanner

# Nordic UART Service. The device's RX characteristic is the one we
# (the central) write to; the device reads via gatts_read on its end.
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

_logger = logging.getLogger("buddyd.ble")


class CCBleClient:
    def __init__(self, name_prefix: str = "ClaudeCC_"):
        self._name_prefix = name_prefix
        self._client: Optional[BleakClient] = None
        self._connected = asyncio.Event()
        self._backoff_s = 1.0

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def run(self) -> None:
        """Main connect/reconnect loop. Runs until cancelled."""
        while True:
            try:
                addr = await self._scan_for_device()
                _logger.info("connecting to %s", addr)
                async with BleakClient(addr, disconnected_callback=self._on_disconnect) as client:
                    self._client = client
                    self._connected.set()
                    self._backoff_s = 1.0
                    _logger.info("connected")
                    while client.is_connected:
                        await asyncio.sleep(1.0)
                    _logger.info("disconnected")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                _logger.warning("ble loop error: %s; backoff %.1fs", e, self._backoff_s)
            finally:
                self._client = None
                self._connected.clear()
            await asyncio.sleep(self._backoff_s)
            self._backoff_s = min(self._backoff_s * 2, 30.0)

    async def _scan_for_device(self) -> str:
        while True:
            _logger.debug("scanning for %s*", self._name_prefix)
            devices = await BleakScanner.discover(timeout=5.0)
            for d in devices:
                if d.name and d.name.startswith(self._name_prefix):
                    _logger.info("found %s (%s)", d.name, d.address)
                    return d.address
            _logger.info("no %s* device seen; retrying", self._name_prefix)
            await asyncio.sleep(2.0)

    def _on_disconnect(self, _client) -> None:
        self._connected.clear()

    async def push(self, heartbeat: dict) -> bool:
        """Write one JSON-line heartbeat. Returns True on success."""
        if not self.is_connected:
            return False
        try:
            line = (json.dumps(heartbeat) + "\n").encode("utf-8")
            # Default ATT MTU is 23 bytes (20 payload). bleak will use a
            # higher MTU if the peripheral negotiates one, but we can't
            # introspect that portably across backends, so we chunk at
            # 20 bytes to be safe. The device's _rx_buf accumulates until
            # it sees '\n' (see buddy_ble.py), so multi-chunk writes
            # reassemble correctly.
            CHUNK = 20
            for i in range(0, len(line), CHUNK):
                await self._client.write_gatt_char(NUS_RX_CHAR_UUID, line[i:i + CHUNK], response=False)
            return True
        except Exception as e:
            _logger.warning("write failed: %s", e)
            return False
