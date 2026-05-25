"""Persistent state for the buddy: name, owner, lifetime counters.

Kept in ESP32 NVS under a dedicated "buddy" namespace so that a
device reflash of UIFlow doesn't clobber settings — the firmware
partition and NVS partition are separate.

Counters tracked (names chosen to match the desktop `stats` ack shape):
  appr  total permission approvals over device lifetime
  deny  total denials
  vel   exponential moving average of permissions/minute
  nap   number of idle periods (no activity > 5 min) — vanity metric
  lvl   derived level = int(sqrt(appr + deny)), a toy tamagotchi gauge

vel/nap/lvl are best-effort — the desktop uses them to render a
personality badge, so mild imprecision is fine. What matters is
monotonicity of appr/deny so the user's approval history is stable
across reboots.
"""

import time


# Stub MicroPython time functions for CPython testing.
if not hasattr(time, 'ticks_ms'):
    _ticks_ms_counter = 0
    def _ticks_ms():
        global _ticks_ms_counter
        _ticks_ms_counter += 1000
        return _ticks_ms_counter
    time.ticks_ms = _ticks_ms
    time.ticks_diff = lambda a, b: a - b


try:
    import esp32

    _NVS = esp32.NVS("buddy")
except ImportError:
    _NVS = None  # dev machine stub


def _get_str(key: str, default: str = "") -> str:
    if _NVS is None:
        return default
    try:
        buf = bytearray(128)
        n = _NVS.get_blob(key, buf)
        return bytes(buf[:n]).decode("utf-8", errors="replace")
    except Exception:
        return default


def _set_str(key: str, value: str) -> None:
    if _NVS is None:
        return
    _NVS.set_blob(key, value.encode("utf-8"))
    _NVS.commit()


def _get_int(key: str, default: int = 0) -> int:
    if _NVS is None:
        return default
    try:
        return _NVS.get_i32(key)
    except Exception:
        return default


def _set_int(key: str, value: int) -> None:
    if _NVS is None:
        return
    _NVS.set_i32(key, value)
    _NVS.commit()


def _get_u32(key: str, default: int = 0) -> int:
    if _NVS is None:
        return default
    try:
        return _NVS.get_u32(key)
    except Exception:
        return default


def _set_u32(key: str, value: int) -> None:
    if _NVS is None:
        return
    _NVS.set_u32(key, value)
    _NVS.commit()


def _erase(key: str) -> None:
    if _NVS is None:
        return
    try:
        _NVS.erase_key(key)
    except Exception:
        pass


class RingBuffer:
    """Fixed-capacity FIFO. push() evicts oldest when full.

    Used for the cumul-graph sample series: (timestamp_ms, cum_tokens).
    iter() yields samples in chronological order regardless of internal
    rotation state.
    """

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._buf = [None] * capacity
        self._head = 0
        self._size = 0

    def push(self, item) -> None:
        idx = (self._head + self._size) % self._capacity
        self._buf[idx] = item
        if self._size < self._capacity:
            self._size += 1
        else:
            self._head = (self._head + 1) % self._capacity

    def clear(self) -> None:
        for i in range(self._capacity):
            self._buf[i] = None
        self._head = 0
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def __iter__(self):
        for i in range(self._size):
            yield self._buf[(self._head + i) % self._capacity]


class BuddyState:
    """In-memory state mirror, write-through to NVS on changes."""

    def __init__(self):
        self.name = _get_str("name", "Buddy")
        self.owner = _get_str("owner", "")
        self.appr = _get_int("appr", 0)
        self.deny = _get_int("deny", 0)
        self._vel = 0.0  # per-minute EWMA, not persisted across reboots
        self._nap_count = _get_int("nap", 0)
        self._last_action_ms = time.ticks_ms()
        self._nap_window_ms = 5 * 60 * 1000
        # Daily budget + cumul graph.
        self.daily_budget_tokens = _get_u32("daily_budget_tokens", 500_000)
        # 60 samples × 15 min = 15 h horizon. Not persisted across reboots.
        self.graph_samples = RingBuffer(capacity=60)

    def set_name(self, name: str) -> None:
        self.name = name[:32]
        _set_str("name", self.name)

    def set_owner(self, owner: str) -> None:
        self.owner = owner[:64]
        _set_str("owner", self.owner)

    def record_decision(self, decision: str) -> None:
        """Called on a permission decision (once|deny)."""
        now = time.ticks_ms()
        if decision == "once":
            self.appr += 1
            _set_int("appr", self.appr)
        elif decision == "deny":
            self.deny += 1
            _set_int("deny", self.deny)
        else:
            return

        dt_s = max(0.5, time.ticks_diff(now, self._last_action_ms) / 1000.0)
        inst_per_min = 60.0 / dt_s
        # Smoothing factor 0.3 — fast enough to feel live, slow enough
        # that a single button-mash doesn't pin vel at some huge value.
        self._vel = self._vel * 0.7 + inst_per_min * 0.3
        self._last_action_ms = now

    def tick_nap(self) -> None:
        """Call periodically; counts stretches of no activity."""
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_action_ms) > self._nap_window_ms:
            self._nap_count += 1
            _set_int("nap", self._nap_count)
            self._last_action_ms = now  # reset window so we don't double-count

    def stats(self) -> dict:
        total = self.appr + self.deny
        # Triangular progression: each level costs one more action
        # than the last. Hit points: level 1 at 1 action, level 10
        # at 55 (=1+2+...+10), level 100 at 5050. The previous
        # comment mis-described this as a sqrt curve (level 10 at
        # 100, level 100 at 10000); the loop below is and always
        # was triangular — only the comment was wrong.
        lvl = 0
        t = total
        while t > 0:
            lvl += 1
            t -= lvl
        return {
            "appr": self.appr,
            "deny": self.deny,
            "vel": round(self._vel, 2),
            "nap": self._nap_count,
            "lvl": lvl,
        }

    def set_daily_budget_tokens(self, n: int) -> None:
        # Clamp to u32 range; negative is rejected silently (UI shouldn't
        # allow it to begin with, but defensive).
        if n < 0:
            return
        n = min(n, 0xFFFFFFFF)
        self.daily_budget_tokens = n
        _set_u32("daily_budget_tokens", n)

    def budget_percentage(self, tokens_today: int):
        # Return None when the budget is zero — caller must render "--%"
        # rather than divide by zero. Clamp the upper bound at 999 so a
        # user with a 1k-token budget who blows past it doesn't break
        # the 3-char layout slot.
        if self.daily_budget_tokens <= 0:
            return None
        pct = int(tokens_today * 100 // self.daily_budget_tokens)
        if pct > 999:
            pct = 999
        return pct

    def record_graph_sample(self, timestamp_ms: int, cum_tokens: int) -> None:
        self.graph_samples.push((timestamp_ms, cum_tokens))

    def reset_graph(self) -> None:
        self.graph_samples.clear()

    def reset_all(self) -> None:
        """Called on unpair. Wipes name/owner/counters but not firmware."""
        self.name = "Buddy"
        self.owner = ""
        self.appr = 0
        self.deny = 0
        self._vel = 0.0
        self._nap_count = 0
        self.daily_budget_tokens = 500_000
        self.graph_samples.clear()
        for k in ("name", "owner", "appr", "deny", "nap", "daily_budget_tokens"):
            _erase(k)
