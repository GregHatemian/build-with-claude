# Cardputer Session Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Claude Code (CC) data source and a richer "stats" view to the existing `claude_buddy` MicroPython app, with the Cardputer's Tab key toggling which source (Claude Desktop vs Claude Code) is currently connected and displayed.

**Architecture:** Two BLE pairings (one per source, never simultaneous). The Cardputer changes its advertised name prefix on Tab to attract the right central. A new host-side Python daemon (`buddyd`) consumes Claude Code `Stop`-hook events from a JSONL sink, accumulates per-day token totals + cache split, and pushes them to the device over BLE whenever the device advertises `ClaudeCC_*`. Claude Desktop integration stays unchanged.

**Tech Stack:** MicroPython (UIFlow 2.0 build) on ESP32-S3 (Cardputer-Adv); CPython 3.11+ for host daemon, `bleak` for BLE central, `watchdog` for file events, `pytest`/`unittest` for tests.

**Spec:** `docs/superpowers/specs/2026-05-25-cardputer-session-stats-design.md`

---

## File Structure

**Device side (`buddy/device/`):**
- Modify `buddy_state.py` — add `_get_u32`/`_set_u32` helpers, `RingBuffer` class, daily-budget accessors, graph buffer + reset, percentage helper, daily aggregates fields, source tab persistence.
- Modify `buddy_protocol.py` — extend heartbeat schema, source validation.
- Modify `buddy_ble.py` — runtime-switchable `name_prefix`, teardown helper.
- Create `buddy_ui_stats.py` — CC stats renderer (header, big %, subtitle, cumul graph, cache split line, hint strip).
- Modify `buddy_ui_cp.py` — renderer dispatch based on `source_tab`.
- Modify `apps/claude_buddy.py` — `source_tab` state, `_switch_source()`, Tab key binding in the keyboard polling loop, source persistence load on app start.

**Device-side tests (`buddy/device/tests/`):**
- Create `test_ring_buffer.py` — pure-Python (no esp32 imports) unit tests, runs via `python -m unittest`.
- Create `test_heartbeat.py` — pure-Python unit tests for the new protocol fields.
- Create `test_state.py` — pure-Python unit tests for `BuddyState` graph/budget logic with `_NVS = None` stub path.
- Create `test_smoke_device.py` — REPL smoke test (drives a real device via `buddy/scripts/repl_run.py`).

**Host side (`buddy/host/`, new directory):**
- Create `buddy/host/__init__.py` — empty package marker.
- Create `buddy/host/claude_code_hook.py` — Stop hook entry point (stdin JSON → JSONL append).
- Create `buddy/host/aggregator.py` — pure-logic daily aggregator (date rollover, accumulation).
- Create `buddy/host/ble_client.py` — `bleak`-based BLE central that scans, connects, writes JSON lines.
- Create `buddy/host/buddyd.py` — daemon main loop (file watch → aggregator → BLE write).
- Create `buddy/host/install_hook.sh` — idempotent installer that writes the hook block into `~/.claude/settings.json`.
- Create `buddy/host/requirements.txt` — `bleak>=0.21`, `watchdog>=3.0`, `pytest>=7.0`.

**Host-side tests (`buddy/host/tests/`):**
- Create `buddy/host/tests/__init__.py`.
- Create `buddy/host/tests/test_aggregator.py` — date rollover, multi-event accumulation, null-usage handling.
- Create `buddy/host/tests/test_hook.py` — Stop-hook stdin parsing + atomic JSONL append.

---

## Task 1: Add `_get_u32`/`_set_u32` NVS helpers

**Files:**
- Modify: `buddy/device/buddy_state.py:23-72`
- Test: `buddy/device/tests/test_state.py` (create)

- [ ] **Step 1: Write the failing test**

Create `buddy/device/tests/__init__.py` (empty) and `buddy/device/tests/test_state.py`:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/gregh/projects/build-with-claude
python3 -m unittest buddy.device.tests.test_state -v 2>&1 | head -20
```

Expected: `AttributeError: module 'buddy_state' has no attribute '_get_u32'`.

- [ ] **Step 3: Implement the helpers**

In `buddy/device/buddy_state.py`, after the existing `_set_int` function and before `_erase`, add:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m unittest buddy.device.tests.test_state -v 2>&1 | tail -5
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
cd /home/gregh/projects/build-with-claude
git add buddy/device/buddy_state.py buddy/device/tests/__init__.py buddy/device/tests/test_state.py
git commit -m "Add u32 NVS helpers to buddy_state

Needed for daily_budget_tokens (uint32 with a reasonable max of 4.3
billion, comfortably above any plausible Claude budget). Matches the
existing _get_int / _set_int pattern but uses the u32-tagged NVS API
so the typed value round-trips cleanly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Add RingBuffer class for cumul graph samples

**Files:**
- Modify: `buddy/device/buddy_state.py` (add new class before `class BuddyState`)
- Test: `buddy/device/tests/test_ring_buffer.py` (create)

- [ ] **Step 1: Write the failing test**

Create `buddy/device/tests/test_ring_buffer.py`:

```python
"""Unit tests for the cumul-graph ring buffer."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import buddy_state  # type: ignore


class TestRingBuffer(unittest.TestCase):
    def test_empty_buffer_returns_no_samples(self):
        rb = buddy_state.RingBuffer(capacity=4)
        self.assertEqual(list(rb), [])
        self.assertEqual(len(rb), 0)

    def test_push_under_capacity_preserves_order(self):
        rb = buddy_state.RingBuffer(capacity=4)
        rb.push((1, 100))
        rb.push((2, 200))
        rb.push((3, 300))
        self.assertEqual(list(rb), [(1, 100), (2, 200), (3, 300)])

    def test_push_at_capacity_wraps_oldest_first(self):
        rb = buddy_state.RingBuffer(capacity=3)
        rb.push((1, 100))
        rb.push((2, 200))
        rb.push((3, 300))
        rb.push((4, 400))  # evicts (1, 100)
        rb.push((5, 500))  # evicts (2, 200)
        self.assertEqual(list(rb), [(3, 300), (4, 400), (5, 500)])
        self.assertEqual(len(rb), 3)

    def test_clear_empties_buffer(self):
        rb = buddy_state.RingBuffer(capacity=4)
        rb.push((1, 100))
        rb.push((2, 200))
        rb.clear()
        self.assertEqual(list(rb), [])
        self.assertEqual(len(rb), 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/gregh/projects/build-with-claude
python3 -m unittest buddy.device.tests.test_ring_buffer -v 2>&1 | head -10
```

Expected: `AttributeError: module 'buddy_state' has no attribute 'RingBuffer'`.

- [ ] **Step 3: Implement RingBuffer**

In `buddy/device/buddy_state.py`, after the `_erase` function and before `class BuddyState`, add:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m unittest buddy.device.tests.test_ring_buffer -v 2>&1 | tail -5
```

Expected: `OK` (4 tests).

- [ ] **Step 5: Commit**

```bash
git add buddy/device/buddy_state.py buddy/device/tests/test_ring_buffer.py
git commit -m "Add RingBuffer for cumul-graph samples

60-slot capacity at 15 min spacing gives 15 hours of horizon. Iter
yields chronological order regardless of internal rotation, so the
renderer can walk samples left-to-right without thinking about wrap.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Wire RingBuffer + daily budget into BuddyState

**Files:**
- Modify: `buddy/device/buddy_state.py` (extend `BuddyState.__init__`, add methods)
- Test: `buddy/device/tests/test_state.py` (add cases)

- [ ] **Step 1: Add failing tests**

Append to `buddy/device/tests/test_state.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m unittest buddy.device.tests.test_state -v 2>&1 | head -30
```

Expected: failures with `AttributeError: 'BuddyState' object has no attribute 'daily_budget_tokens'` (and similar for `set_daily_budget_tokens`, `budget_percentage`, `graph_samples`, `record_graph_sample`, `reset_graph`).

- [ ] **Step 3: Extend `BuddyState`**

In `buddy/device/buddy_state.py`, modify `BuddyState.__init__` to add the new fields at the end:

```python
        # Daily budget + cumul graph.
        self.daily_budget_tokens = _get_u32("daily_budget_tokens", 500_000)
        # 60 samples × 15 min = 15 h horizon. Not persisted across reboots.
        self.graph_samples = RingBuffer(capacity=60)
```

Add these methods on `BuddyState` (before `reset_all`):

```python
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
```

Also modify `reset_all` to also reset graph + budget-to-default:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m unittest buddy.device.tests.test_state -v 2>&1 | tail -10
```

Expected: `OK` (10 tests — including the two earlier u32 helper tests).

- [ ] **Step 5: Commit**

```bash
git add buddy/device/buddy_state.py buddy/device/tests/test_state.py
git commit -m "Wire RingBuffer and daily budget into BuddyState

Adds graph_samples (60-slot ring) and daily_budget_tokens (NVS-backed
uint32 with 500k default). budget_percentage returns None when the
budget is zero (caller renders '--%') and clamps at 999 so an
over-budget percentage stays in 3 chars.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Add source_tab persistence

**Files:**
- Modify: `buddy/device/buddy_state.py`
- Test: `buddy/device/tests/test_state.py`

- [ ] **Step 1: Add failing tests**

Append to `test_state.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m unittest buddy.device.tests.test_state.TestSourceTabPersistence -v 2>&1 | head -10
```

Expected: `AttributeError: 'BuddyState' object has no attribute 'source_tab'`.

- [ ] **Step 3: Add source_tab to BuddyState**

In `BuddyState.__init__`, append at the end:

```python
        # Active source tab: "CD" (Claude Desktop) or "CC" (Claude Code).
        # Persisted so reboot keeps the user's last choice.
        self.source_tab = _get_str("source_tab", "CD")
        if self.source_tab not in ("CD", "CC"):
            self.source_tab = "CD"
```

Add method:

```python
    def set_source_tab(self, tab: str) -> None:
        if tab not in ("CD", "CC"):
            return
        self.source_tab = tab
        _set_str("source_tab", tab)
```

Also extend `reset_all` to reset source_tab:

```python
        self.source_tab = "CD"
```

And add `"source_tab"` to the `_erase` loop in `reset_all`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m unittest buddy.device.tests.test_state -v 2>&1 | tail -5
```

Expected: `OK` (13 tests total).

- [ ] **Step 5: Commit**

```bash
git add buddy/device/buddy_state.py buddy/device/tests/test_state.py
git commit -m "Persist source_tab in BuddyState

Reboot keeps the user's last selected tab (CD/CC). Invalid values are
silently rejected to CD so a corrupted NVS entry can't brick the app
in some impossible state.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Extend heartbeat schema with new optional fields

**Files:**
- Modify: `buddy/device/buddy_protocol.py`
- Test: `buddy/device/tests/test_heartbeat.py` (create)

- [ ] **Step 1: Write the failing test**

Create `buddy/device/tests/test_heartbeat.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m unittest buddy.device.tests.test_heartbeat -v 2>&1 | head -20
```

Expected: most fail with `AttributeError`. The two `_looks_like_heartbeat` tests around legacy and command may already pass if a similar helper exists; if so, accept that and only the new-field test and source-helper tests should fail.

- [ ] **Step 3: Inspect existing detection logic**

Look at how `buddy_protocol.py` currently decides heartbeat-ness:

```bash
grep -n "_HEARTBEAT_FIELDS\|cmd.*not in\|ack.*not in" buddy/device/buddy_protocol.py
```

If there's already a heartbeat-detection function with a different name (e.g., a method on `BuddyProtocol`), refactor that out into a module-level `_looks_like_heartbeat` helper that the class method delegates to. Keep behavior identical.

- [ ] **Step 4: Implement the helpers**

In `buddy/device/buddy_protocol.py`, near the top with `_HEARTBEAT_FIELDS`:

```python
_HEARTBEAT_FIELDS = (
    "total", "running", "waiting", "tokens", "tokens_today",
    "entries", "daily_total", "cache_read", "cache_create",
    "cache_uncached",
)


def _looks_like_heartbeat(msg: dict) -> bool:
    """A heartbeat has no cmd/ack and at least one heartbeat-shape field."""
    if "cmd" in msg or "ack" in msg:
        return False
    return any(k in msg for k in _HEARTBEAT_FIELDS)


def _heartbeat_source(msg: dict) -> str:
    """Source tag from a heartbeat. Missing/invalid → 'CD' for backward compat."""
    src = msg.get("source", "CD")
    if src not in ("CD", "CC"):
        return "CD"
    return src
```

Refactor the existing in-class detection to call `_looks_like_heartbeat`. Concretely, in `BuddyProtocol.handle_line` (or whichever method dispatches), replace any inline `any(k in msg for k in _HEARTBEAT_FIELDS)` with `_looks_like_heartbeat(msg)`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
python3 -m unittest buddy.device.tests.test_heartbeat -v 2>&1 | tail -10
```

Expected: `OK` (8 tests).

- [ ] **Step 6: Commit**

```bash
git add buddy/device/buddy_protocol.py buddy/device/tests/test_heartbeat.py
git commit -m "Extend heartbeat schema with source + cache + daily_total

New optional fields: source ('CD'|'CC'), daily_total (authoritative
day cumulative), cache_read/create/uncached. Missing source defaults
to CD so legacy Claude Desktop heartbeats parse unchanged. Refactored
heartbeat detection into a module-level helper so unit tests can pin
the contract without instantiating BuddyProtocol.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Heartbeat consumer applies new fields to state

**Files:**
- Modify: `buddy/device/buddy_protocol.py` (heartbeat handler)
- Test: `buddy/device/tests/test_heartbeat.py` (add cases)

- [ ] **Step 1: Add failing tests**

Append to `test_heartbeat.py`:

```python
class FakeUI:
    def __init__(self):
        self.last_hb = None

    def set_heartbeat(self, hb):
        self.last_hb = hb

    def update_footer(self, *a, **k):
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
        self.proto.handle_line(json.dumps({
            "source": "CC",
            "daily_total": 64200,
            "cache_read": 1_200_000,
            "cache_create": 180_000,
            "cache_uncached": 320_000,
            "tokens_today": 64200,
        }))
        # The UI got the heartbeat dict so the renderer can pull what it needs.
        self.assertIsNotNone(self.ui.last_hb)
        self.assertEqual(self.ui.last_hb["daily_total"], 64200)
        self.assertEqual(self.ui.last_hb["cache_read"], 1_200_000)

    def test_cd_heartbeat_rejected_when_tab_is_cc(self):
        self.state.set_source_tab("CC")
        # Vanilla Claude Desktop heartbeat: no source field → treated as CD.
        self.proto.handle_line(json.dumps({"tokens_today": 5000, "running": 1}))
        self.assertIsNone(self.ui.last_hb)

    def test_cc_heartbeat_rejected_when_tab_is_cd(self):
        self.state.set_source_tab("CD")
        self.proto.handle_line(json.dumps({"source": "CC", "daily_total": 1000}))
        self.assertIsNone(self.ui.last_hb)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m unittest buddy.device.tests.test_heartbeat.TestHeartbeatApplication -v 2>&1 | head -20
```

Expected: failures — the handler currently has no source-filtering logic. Either the test crashes (no `set_heartbeat` on UI) or accepts CC into a CD tab.

- [ ] **Step 3: Read the existing handler**

```bash
grep -n "def handle_line\|set_heartbeat\|def _on_heartbeat\|class BuddyProtocol" buddy/device/buddy_protocol.py
```

Identify the method that processes incoming heartbeats. Add a source-filter at the top of that method:

In `BuddyProtocol._on_heartbeat` (or equivalent — the method called once a message is identified as a heartbeat), prepend:

```python
        # Reject heartbeats whose declared source doesn't match the
        # currently-active tab. Defensive against both daemons writing
        # simultaneously (which shouldn't happen by design — the device
        # only advertises for one of them at a time — but guards against
        # stale messages buffered through a slow BLE link).
        if _heartbeat_source(msg) != self.state.source_tab:
            return
```

If `BuddyProtocol` already calls `self.ui.set_heartbeat(...)` or similar, leave that line alone. If it doesn't, add it after the source-check:

```python
        # Forward the raw heartbeat dict to the renderer; per-field
        # rendering decisions live in buddy_ui_cp.py / buddy_ui_stats.py.
        if hasattr(self.ui, "set_heartbeat"):
            self.ui.set_heartbeat(msg)
```

Make sure `FakeUI.set_heartbeat` in the test reflects the same method name. If your existing UI uses a different method (e.g., `set_state`), match that.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m unittest buddy.device.tests.test_heartbeat -v 2>&1 | tail -10
```

Expected: `OK` (11 tests total).

- [ ] **Step 5: Commit**

```bash
git add buddy/device/buddy_protocol.py buddy/device/tests/test_heartbeat.py
git commit -m "Apply source-tab filter on heartbeat ingress

A heartbeat whose declared source doesn't match the active tab is
silently dropped. Defensive against a stale message buffered across
a Tab switch. The UI receives raw heartbeats via set_heartbeat so
renderers stay decoupled from protocol-level concerns.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Runtime-switchable BLE name prefix

**Files:**
- Modify: `buddy/device/buddy_ble.py`

- [ ] **Step 1: Read the current advertising setup**

```bash
grep -n "name_prefix\|gap_name=\|gap_advertise\|class BuddyBLE\|def __init__\|def stop\|disconnect" buddy/device/buddy_ble.py | head -30
```

Identify where the prefix is currently a constructor argument and where the actual `gap_advertise` happens.

- [ ] **Step 2: Add a runtime restart method**

In `buddy/device/buddy_ble.py`, on `class BuddyBLE`, add:

```python
    def restart_with_prefix(self, name_prefix: str) -> None:
        """Tear down current advertise + connections and re-init with a
        new name_prefix. Used by the source-tab switcher so the correct
        central re-pairs.

        Returns when the new advertise is active. Idempotent if the
        prefix is unchanged.
        """
        if getattr(self, "_name_prefix", None) == name_prefix:
            return
        try:
            # Disconnect any active central first so we're not advertising
            # a name change while bonded — some centrals get confused.
            self._ble.gap_advertise(None)
        except Exception:
            pass
        # Forget current connections without wiping pairing bonds —
        # bonds are keyed by peer MAC, not by gap name, so they survive.
        try:
            for conn_handle in list(getattr(self, "_conns", set())):
                self._ble.gap_disconnect(conn_handle)
        except Exception:
            pass
        # Re-run the same init path that the constructor used, just
        # with a different name_prefix. _ensure_stack is the canonical
        # entry point — see buddy_ble.py top-of-file comment about
        # ordering (active → config(mac) → config(gap_name) →
        # gatts_register_services → gap_advertise).
        self._name_prefix = name_prefix
        self._restart_advertise(name_prefix)
```

Add a helper `_restart_advertise` that re-runs the relevant subset of `_ensure_stack` for the new gap_name + advertise — extract from existing code without changing behavior for the constructor path.

If `_ensure_stack` is structured as one big function, refactor it to expose a `_set_name_and_advertise(name_prefix)` step that both the constructor and `restart_with_prefix` call. Constructor still calls `ble.active(True)` + register_services first; `restart_with_prefix` skips those (stack stays active).

- [ ] **Step 3: Add a device-side smoke check**

Create `buddy/device/tests/test_smoke_ble_restart.py`:

```python
"""On-device smoke check: BuddyBLE.restart_with_prefix() should change
the advertised gap_name without raising, and a subsequent connect
attempt should see the new name.

Run via repl_run.py against a connected device:

    python3 buddy/scripts/repl_run.py --script buddy/device/tests/test_smoke_ble_restart.py

Prints PASS / FAIL to the device REPL stdout.
"""

print("test_smoke_ble_restart: start")

import buddy_ble

ble = buddy_ble.BuddyBLE(on_line=lambda b: None, on_passkey=lambda p: None, on_state=lambda s: None)
n1 = ble.advertised_name
print("after init:", n1)
assert n1.startswith("Claude_"), "constructor name_prefix not Claude"

ble.restart_with_prefix("ClaudeCC")
n2 = ble.advertised_name
print("after restart:", n2)
assert n2.startswith("ClaudeCC_"), "restart_with_prefix didn't change prefix"
assert n2[-4:] == n1[-4:], "MAC suffix changed across restart (should be stable)"

ble.restart_with_prefix("Claude")
n3 = ble.advertised_name
print("after second restart:", n3)
assert n3.startswith("Claude_") and not n3.startswith("ClaudeCC_")

print("PASS test_smoke_ble_restart")
```

- [ ] **Step 4: Run the device smoke check**

With a flashed device connected (run `m5-onboard` first if needed):

```bash
cd /home/gregh/projects/build-with-claude
python3 buddy/scripts/repl_run.py --port COM5 --script buddy/device/tests/test_smoke_ble_restart.py 2>&1 | tail -15
```

(Replace `COM5` with whatever the device enumerates as — `detect.py` or the previous `m5-onboard` run reports this.)

Expected: `PASS test_smoke_ble_restart` in the output.

- [ ] **Step 5: Commit**

```bash
git add buddy/device/buddy_ble.py buddy/device/tests/test_smoke_ble_restart.py
git commit -m "Runtime-switchable BLE name prefix on BuddyBLE

restart_with_prefix() tears down the current advertise + central
connections, swaps gap_name, and re-advertises with the new prefix.
MAC suffix stays stable so paired bonds survive across switches.
Used by the upcoming Tab key handler to flip between Claude_ (CD)
and ClaudeCC_ (CC).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Create buddy_ui_stats.py skeleton + header

**Files:**
- Create: `buddy/device/buddy_ui_stats.py`

- [ ] **Step 1: Write the skeleton**

Create `buddy/device/buddy_ui_stats.py`:

```python
"""Renderer for the Claude Code (CC) source tab.

Layout at 240x135:
  y=0..17    source header line ("ClaudeCC_xxxx ● CC", right-aligned dot)
  y=18..50   big percentage number, size 4, color thresholded
  y=52..62   subtitle: "N'NNN / N'NNN tok"
  y=66..96   cumul graph (cyan line, dashed budget cap)
  y=98..110  cache split line ("cache r:NM c:NK u:NK")
  y=120..134 hint strip: "Tab=CD"

set_heartbeat(hb) stashes the latest heartbeat dict; paint() renders.
The dispatcher in buddy_ui_cp.py decides which renderer is active.
"""

from M5 import Lcd as _LCD

_W = 240
_H = 135

BLACK = 0x000000
WHITE = 0xFFFFFF
CYAN = 0x00FFFF
YELLOW = 0xFFFF00
RED = 0xFF0000
GRAY_MID = 0x808080
DIM = 0x404040
GREEN = 0x00C800
CREAM = 0xEEEED5


class StatsRenderer:
    def __init__(self, state):
        self._state = state
        self._hb = {}
        self._link_up = False

    def set_link(self, up: bool) -> None:
        self._link_up = up

    def set_heartbeat(self, hb: dict) -> None:
        self._hb = hb

    def paint(self) -> None:
        _LCD.fillScreen(BLACK)
        self._draw_header()
        self._draw_percentage()
        self._draw_subtitle()
        self._draw_graph()
        self._draw_cache_line()
        self._draw_hint_strip()

    def _draw_header(self) -> None:
        _LCD.setTextSize(1)
        _LCD.setTextColor(CYAN, BLACK)
        # advertised name comes from the BLE layer; for now show the tab
        # tag as a placeholder — the app passes the real name in via
        # set_heartbeat or a dedicated setter (added in a later task).
        _LCD.drawString("CC source", 6, 4)
        dot_color = GREEN if self._link_up else GRAY_MID
        _LCD.fillCircle(_W - 14, 8, 4, dot_color)
        _LCD.drawString("CC", _W - 28, 4)
        _LCD.drawFastHLine(0, 17, _W, DIM)

    def _draw_percentage(self) -> None:
        pass  # task 9

    def _draw_subtitle(self) -> None:
        pass  # task 10

    def _draw_graph(self) -> None:
        pass  # task 11

    def _draw_cache_line(self) -> None:
        pass  # task 12

    def _draw_hint_strip(self) -> None:
        pass  # task 13
```

- [ ] **Step 2: Verify it imports cleanly on the device**

```bash
python3 buddy/scripts/repl_run.py --port COM5 --snippet "import buddy_ui_stats; r = buddy_ui_stats.StatsRenderer(state=None); print('import ok')" 2>&1 | tail -5
```

(Skip this step if no device is connected — it'll be exercised in Task 14's smoke.)

Expected: `import ok`.

- [ ] **Step 3: Commit**

```bash
git add buddy/device/buddy_ui_stats.py
git commit -m "Skeleton for buddy_ui_stats CC renderer

Class scaffold with stubbed paint() — only the header (source tag +
link dot + hairline) is implemented. Subsequent tasks fill in the
percentage, subtitle, graph, cache line, and hint strip. Keeps the
diff per-task small enough to review meaningfully.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Big percentage number (color thresholded)

**Files:**
- Modify: `buddy/device/buddy_ui_stats.py:_draw_percentage`

- [ ] **Step 1: Implement `_draw_percentage`**

Replace the stub with:

```python
    def _draw_percentage(self) -> None:
        daily_total = self._hb.get("daily_total", self._hb.get("tokens_today", 0))
        pct = self._state.budget_percentage(daily_total) if self._state else None
        if pct is None:
            text = "--%"
            color = GRAY_MID
        else:
            text = "{}%".format(pct)
            if pct >= 90:
                color = RED
            elif pct >= 70:
                color = YELLOW
            else:
                color = CYAN
        _LCD.setTextSize(4)
        _LCD.setTextColor(color, BLACK)
        _LCD.drawString(text, 6, 18)
        _LCD.setTextSize(1)  # restore for subsequent draws
```

- [ ] **Step 2: Device smoke test**

Create `buddy/device/tests/test_smoke_stats_percent.py`:

```python
"""On-device smoke: paint StatsRenderer with three percentage scenarios
and verify each color band by reading the framebuffer pixel at a known
inside-the-glyph coordinate.

Drawn pixels are read via Lcd.readPixel(x, y). The exact glyph mask is
font-dependent, but at size 4 the top-left of the '%' character has
many filled pixels; we sample a small area and assert the modal color
matches the expected band.
"""

import buddy_state, buddy_ui_stats
from M5 import Lcd

def modal_color_in_box(x0, y0, w, h):
    counts = {}
    for x in range(x0, x0 + w):
        for y in range(y0, y0 + h):
            c = Lcd.readPixel(x, y)
            counts[c] = counts.get(c, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]

state = buddy_state.BuddyState()
state.set_daily_budget_tokens(200_000)
r = buddy_ui_stats.StatsRenderer(state=state)

# 32% → cyan
r.set_heartbeat({"daily_total": 64_000})
r.paint()
c = modal_color_in_box(20, 22, 30, 30)
print("32% modal color:", hex(c))
assert c in (buddy_ui_stats.CYAN, buddy_ui_stats.BLACK), "32% should be cyan-on-black"

# 75% → yellow
r.set_heartbeat({"daily_total": 150_000})
r.paint()
c = modal_color_in_box(20, 22, 30, 30)
print("75% modal color:", hex(c))
assert c in (buddy_ui_stats.YELLOW, buddy_ui_stats.BLACK)

# 95% → red
r.set_heartbeat({"daily_total": 190_000})
r.paint()
c = modal_color_in_box(20, 22, 30, 30)
print("95% modal color:", hex(c))
assert c in (buddy_ui_stats.RED, buddy_ui_stats.BLACK)

# budget=0 → '--%' in gray
state.set_daily_budget_tokens(0)
r.set_heartbeat({"daily_total": 5_000})
r.paint()
print("PASS test_smoke_stats_percent")
```

- [ ] **Step 2 (run): Execute against the device**

```bash
python3 buddy/scripts/repl_run.py --port COM5 --script buddy/device/tests/test_smoke_stats_percent.py 2>&1 | tail -15
```

Expected: `PASS test_smoke_stats_percent`. The LCD should also briefly flash through the four states; if a color band looks wrong, the modal-color assert catches it.

- [ ] **Step 3: Commit**

```bash
git add buddy/device/buddy_ui_stats.py buddy/device/tests/test_smoke_stats_percent.py
git commit -m "Render big percentage with cyan/yellow/red thresholds

<70% cyan, 70..89% yellow, >=90% red. None (budget=0) renders '--%'
in gray. The threshold values match common 'watch your budget'
intuition without being prescriptive; tune later if user feedback
suggests different cutoffs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Subtitle "N'NNN / N'NNN tok"

**Files:**
- Modify: `buddy/device/buddy_ui_stats.py:_draw_subtitle`

- [ ] **Step 1: Implement subtitle**

Replace the `_draw_subtitle` stub:

```python
    def _draw_subtitle(self) -> None:
        daily_total = self._hb.get("daily_total", self._hb.get("tokens_today", 0))
        budget = self._state.daily_budget_tokens if self._state else 0
        cur = "{:,}".format(daily_total).replace(",", "'")
        cap = "{:,}".format(budget).replace(",", "'") if budget else "--"
        line = "{} / {} tok".format(cur, cap)
        while _LCD.textWidth(line) > _W - 12 and len(line) > 1:
            line = line[:-1]
        _LCD.setTextSize(1)
        _LCD.setTextColor(GRAY_MID, BLACK)
        _LCD.drawString(line, 6, 52)
```

- [ ] **Step 2: Device smoke check**

Append to `buddy/device/tests/test_smoke_stats_percent.py`, just before the final `print("PASS ...")`:

```python
# Subtitle smoke: render with a known shape and confirm via readPixel
# that something non-black is drawn at the subtitle row.
state.set_daily_budget_tokens(200_000)
r.set_heartbeat({"daily_total": 64_200})
r.paint()
any_drawn = False
for x in range(6, 100):
    if Lcd.readPixel(x, 56) != buddy_ui_stats.BLACK:
        any_drawn = True
        break
assert any_drawn, "subtitle row appears empty"
```

- [ ] **Step 3: Run the smoke check**

```bash
python3 buddy/scripts/repl_run.py --port COM5 --script buddy/device/tests/test_smoke_stats_percent.py 2>&1 | tail -5
```

Expected: `PASS test_smoke_stats_percent`.

- [ ] **Step 4: Commit**

```bash
git add buddy/device/buddy_ui_stats.py buddy/device/tests/test_smoke_stats_percent.py
git commit -m "Render subtitle with cur/cap token counts

Apostrophe-grouped thousands like the existing tokens line in
buddy_ui_cp.py. Clips overflow with the same character-trim loop
pattern so a large budget can't push the line past screen edge.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Cumul graph rendering

**Files:**
- Modify: `buddy/device/buddy_ui_stats.py:_draw_graph`

- [ ] **Step 1: Implement `_draw_graph`**

Replace the stub:

```python
    def _draw_graph(self) -> None:
        # Plot region: x=6..234 (228 wide), y=66..96 (30 tall).
        x0, y0, w, h = 6, 66, _W - 12, 30
        # Frame: a faint floor line + dashed budget cap.
        _LCD.drawFastHLine(x0, y0 + h, w, DIM)
        budget = self._state.daily_budget_tokens if self._state else 0
        if budget <= 0 or not self._state or len(self._state.graph_samples) < 2:
            # Not enough data to draw the line or no budget to scale by.
            return
        # Y scale: 0 at bottom (y0+h), budget at top (y0). Anything over
        # 100% is clamped to top (we already render '999%' in the big
        # number for that case).
        def yv(cum):
            ratio = cum / budget
            if ratio > 1.0:
                ratio = 1.0
            return int(y0 + h - ratio * h)
        # X scale: oldest sample at x0, newest at x0+w.
        samples = list(self._state.graph_samples)
        n = len(samples)
        # Cap line: every 8 px draw a 4-px dash at y = y0 (top).
        for x in range(x0, x0 + w, 8):
            _LCD.drawFastHLine(x, y0, 4, GRAY_MID)
        # Line: connect successive samples.
        prev_x = x0
        prev_y = yv(samples[0][1])
        for i in range(1, n):
            cx = x0 + int(i * w / (n - 1))
            cy = yv(samples[i][1])
            _LCD.drawLine(prev_x, prev_y, cx, cy, CYAN)
            prev_x, prev_y = cx, cy
```

- [ ] **Step 2: Device smoke check**

Append to `test_smoke_stats_percent.py`:

```python
# Graph smoke: push 10 increasing samples and confirm some pixels in
# the plot region are cyan after paint.
state = buddy_state.BuddyState()
state.set_daily_budget_tokens(200_000)
for i in range(10):
    state.record_graph_sample(timestamp_ms=i * 1000, cum_tokens=i * 20_000)
r = buddy_ui_stats.StatsRenderer(state=state)
r.set_heartbeat({"daily_total": 180_000})
r.paint()
cyan_count = 0
for x in range(6, 234):
    for y in range(66, 97):
        if Lcd.readPixel(x, y) == buddy_ui_stats.CYAN:
            cyan_count += 1
print("graph cyan pixels:", cyan_count)
assert cyan_count > 30, "graph line appears not drawn"
```

- [ ] **Step 3: Run and commit**

```bash
python3 buddy/scripts/repl_run.py --port COM5 --script buddy/device/tests/test_smoke_stats_percent.py 2>&1 | tail -8
git add buddy/device/buddy_ui_stats.py buddy/device/tests/test_smoke_stats_percent.py
git commit -m "Render cumul graph with dashed budget cap line

Connects samples left-to-right with a cyan polyline; budget=100%
maps to the top of the plot region. Over-budget samples clamp to
the top edge (big-number renderer already handles the 999% case).
Dashed cap is faint gray-mid; not meant to compete with the line.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Cache split line (r/c/u with 3 colors)

**Files:**
- Modify: `buddy/device/buddy_ui_stats.py:_draw_cache_line`

- [ ] **Step 1: Implement `_draw_cache_line`**

```python
    def _draw_cache_line(self) -> None:
        cr = self._hb.get("cache_read")
        cc = self._hb.get("cache_create")
        cu = self._hb.get("cache_uncached")
        if cr is None and cc is None and cu is None:
            # Source didn't provide cache info — render a dim placeholder.
            _LCD.setTextSize(1)
            _LCD.setTextColor(GRAY_MID, BLACK)
            _LCD.drawString("cache: --", 6, 98)
            return
        _LCD.setTextSize(1)
        x = 6
        _LCD.setTextColor(GRAY_MID, BLACK)
        _LCD.drawString("cache", x, 98)
        x += _LCD.textWidth("cache ")
        for label, val, color in (
            ("r:", cr, GREEN),
            ("c:", cc, CYAN),
            ("u:", cu, GRAY_MID),
        ):
            if val is None:
                continue
            _LCD.setTextColor(color, BLACK)
            txt = label + _fmt_short(val)
            if x + _LCD.textWidth(txt) > _W - 6:
                break  # ran out of horizontal room
            _LCD.drawString(txt, x, 98)
            x += _LCD.textWidth(txt) + 4


def _fmt_short(n: int) -> str:
    """Compact integer: 1234 -> '1.2K', 1234567 -> '1.2M'."""
    if n is None:
        return "?"
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return "{:.1f}K".format(n / 1000.0)
    return "{:.1f}M".format(n / 1_000_000.0)
```

- [ ] **Step 2: Add unit test for `_fmt_short`**

This one IS pure-Python (no LCD). Append to `buddy/device/tests/test_state.py` — actually create a new tiny file since it tests a `buddy_ui_stats` helper:

```python
# buddy/device/tests/test_fmt_short.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

# Stub the M5 import so importing buddy_ui_stats doesn't blow up on CPython.
sys.modules["M5"] = type(sys)("M5")
sys.modules["M5"].Lcd = object()

import buddy_ui_stats

class TestFmtShort(unittest.TestCase):
    def test_under_thousand(self):
        self.assertEqual(buddy_ui_stats._fmt_short(0), "0")
        self.assertEqual(buddy_ui_stats._fmt_short(999), "999")
    def test_thousands(self):
        self.assertEqual(buddy_ui_stats._fmt_short(1234), "1.2K")
        self.assertEqual(buddy_ui_stats._fmt_short(999_999), "1000.0K")
    def test_millions(self):
        self.assertEqual(buddy_ui_stats._fmt_short(1_234_567), "1.2M")
    def test_none(self):
        self.assertEqual(buddy_ui_stats._fmt_short(None), "?")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run + commit**

```bash
python3 -m unittest buddy.device.tests.test_fmt_short -v 2>&1 | tail -5
git add buddy/device/buddy_ui_stats.py buddy/device/tests/test_fmt_short.py
git commit -m "Render cache split line (r:cache_read c:create u:uncached)

Three colors: green for cache_read (money saved), cyan for create
(cost-bearing input that built the cache), gray for uncached
(informational). Numbers formatted with K/M suffix so 1.2M fits in
~5 chars. Falls back to 'cache: --' when the source omits the
fields (i.e., on a CD-source heartbeat).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Hint strip "Tab=CD"

**Files:**
- Modify: `buddy/device/buddy_ui_stats.py:_draw_hint_strip`

- [ ] **Step 1: Implement hint strip**

```python
    def _draw_hint_strip(self) -> None:
        _LCD.drawFastHLine(0, 116, _W, DIM)
        _LCD.setTextSize(1)
        _LCD.setTextColor(GRAY_MID, BLACK)
        _LCD.drawString("Tab=CD", 6, 120)
```

- [ ] **Step 2: Smoke**

Append to `test_smoke_stats_percent.py`:

```python
# Hint strip smoke: any non-black pixel at y=120..130 on the left third.
hint_drawn = False
for x in range(6, 70):
    for y in range(120, 131):
        if Lcd.readPixel(x, y) != buddy_ui_stats.BLACK:
            hint_drawn = True
            break
    if hint_drawn:
        break
assert hint_drawn, "hint strip appears empty"
```

- [ ] **Step 3: Run + commit**

```bash
python3 buddy/scripts/repl_run.py --port COM5 --script buddy/device/tests/test_smoke_stats_percent.py 2>&1 | tail -5
git add buddy/device/buddy_ui_stats.py buddy/device/tests/test_smoke_stats_percent.py
git commit -m "Draw hint strip 'Tab=CD' on CC renderer

Mirror of the existing hint strip on the CD renderer. The text
indicates 'press Tab to go to CD' so the user always knows the other
tab is one keypress away.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Renderer dispatch in buddy_ui_cp.py

**Files:**
- Modify: `buddy/device/buddy_ui_cp.py`

- [ ] **Step 1: Read the existing UI class shape**

```bash
grep -n "class BuddyUI\|def __init__\|def paint\|def update_\|def set_" buddy/device/buddy_ui_cp.py | head -20
```

Identify how the app currently calls into the UI from `claude_buddy.py`. Likely it constructs one `BuddyUI(state)` and calls `update_footer`, `set_connection`, etc.

- [ ] **Step 2: Wrap with a dispatcher**

In `buddy/device/buddy_ui_cp.py`, at the bottom of the file add:

```python
import buddy_ui_stats


class _UIDispatcher:
    """Routes UI calls to the renderer for the active source_tab.

    BuddyUI (the existing CD renderer) is constructed once and lives
    forever. StatsRenderer is constructed once and lives forever. The
    dispatcher just picks which one to forward calls to.

    Methods present on only one renderer (e.g., CD-only set_connection)
    are silently dropped when the other tab is active — that's correct
    behavior because such events shouldn't fire on the other source
    (no permission prompts on CC, no cache split on CD).
    """

    def __init__(self, state):
        self._state = state
        self._cd = BuddyUI(state)
        self._cc = buddy_ui_stats.StatsRenderer(state)

    def _active(self):
        return self._cd if self._state.source_tab == "CD" else self._cc

    def set_heartbeat(self, hb):
        active = self._active()
        if hasattr(active, "set_heartbeat"):
            active.set_heartbeat(hb)

    def set_connection(self, state):
        # CD-only: permission UI doesn't apply to CC.
        if self._state.source_tab == "CD":
            self._cd.set_connection(state)

    def set_link(self, up):
        active = self._active()
        if hasattr(active, "set_link"):
            active.set_link(up)

    def show_passkey(self, pk):
        if self._state.source_tab == "CD":
            self._cd.show_passkey(pk)

    def show_prompt(self, prompt):
        if self._state.source_tab == "CD":
            self._cd.show_prompt(prompt)

    def hide_prompt(self):
        if self._state.source_tab == "CD":
            self._cd.hide_prompt()

    def show_unpair_prompt(self):
        if self._state.source_tab == "CD":
            self._cd.show_unpair_prompt()

    def hide_unpair_prompt(self):
        if self._state.source_tab == "CD":
            self._cd.hide_unpair_prompt()

    def update_footer(self, stats, battery):
        active = self._active()
        if hasattr(active, "update_footer"):
            active.update_footer(stats, battery)

    def paint(self):
        active = self._active()
        if hasattr(active, "paint"):
            active.paint()

    def switch_tab(self):
        # Called by claude_buddy on Tab key. Repaint the now-active
        # renderer from scratch.
        self.paint()


def make_ui(state):
    return _UIDispatcher(state)
```

Audit the existing `BuddyUI` to make sure it has all the methods the dispatcher forwards. If any are missing (e.g., `set_heartbeat`), add them — Task 6 already required `set_heartbeat` on the UI; if you haven't yet, do it now (it should just stash `hb` on `self._hb` for the next repaint).

- [ ] **Step 3: Smoke check**

Create `buddy/device/tests/test_smoke_dispatcher.py`:

```python
"""On-device smoke: the dispatcher routes paint() to the right renderer
based on source_tab, and switch_tab repaints after the tab changes.
"""

import buddy_state, buddy_ui_cp
from M5 import Lcd

state = buddy_state.BuddyState()
state.set_daily_budget_tokens(200_000)
ui = buddy_ui_cp.make_ui(state)

state.set_source_tab("CC")
state.record_graph_sample(0, 0)
state.record_graph_sample(60_000, 50_000)
ui.set_heartbeat({"daily_total": 50_000, "cache_read": 1_000, "cache_create": 500, "cache_uncached": 200})
ui.paint()
# After CC paint, top-left should NOT have orange (the prompt-box color
# from the CD renderer) — quick sanity that we're on the CC layout.
print("CC tab pixel at (0,4):", hex(Lcd.readPixel(50, 4)))

state.set_source_tab("CD")
ui.paint()
print("CD tab painted ok")
print("PASS test_smoke_dispatcher")
```

- [ ] **Step 4: Run + commit**

```bash
python3 buddy/scripts/repl_run.py --port COM5 --script buddy/device/tests/test_smoke_dispatcher.py 2>&1 | tail -8
git add buddy/device/buddy_ui_cp.py buddy/device/tests/test_smoke_dispatcher.py
git commit -m "Add UI dispatcher that routes to CD or CC renderer

make_ui(state) returns a _UIDispatcher with the same surface area as
the old BuddyUI plus paint() and switch_tab(). The active renderer is
picked by state.source_tab. CD-only methods (permission prompts,
passkey, unpair) are no-ops when CC is active.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: Wire dispatcher + Tab key + source_switch into claude_buddy.py

**Files:**
- Modify: `buddy/device/apps/claude_buddy.py`

- [ ] **Step 1: Replace direct BuddyUI construction with `make_ui`**

In `apps/claude_buddy.py`, find where `BuddyUI(...)` is constructed and replace with:

```python
import buddy_ui_cp

ui = buddy_ui_cp.make_ui(state)
```

Drop any direct `import buddy_ui_cp as ...` / `from buddy_ui_cp import BuddyUI` line that's now redundant.

- [ ] **Step 2: Add `_switch_source` helper**

In `apps/claude_buddy.py`, near the top after the imports and before `def main()`, add:

```python
def _switch_source(state, ble, ui):
    """Toggle source_tab between CD and CC. Tears down the current BLE
    advertise/connection, persists the new tab, repaints the UI on the
    new renderer, and restarts BLE with the matching name prefix so the
    correct host re-pairs.
    """
    new_tab = "CC" if state.source_tab == "CD" else "CD"
    state.set_source_tab(new_tab)
    # Graph data accumulated on the old source would lie if drawn under
    # the new source's renderer — they're different scales.
    state.reset_graph()
    prefix = "Claude" if new_tab == "CD" else "ClaudeCC"
    ble.restart_with_prefix(prefix)
    ui.paint()
```

- [ ] **Step 3: Bind Tab key in the keyboard polling loop**

Find the keyboard polling block (uses `MatrixKeyboard` and `kb.get_key()` or similar). Inside the loop, after the existing key dispatches but before fall-through, add:

```python
        elif key == "\t":  # Tab — switch source
            _switch_source(state, ble, ui)
```

If the Cardputer keyboard layer reports Tab with a different code (e.g., `"TAB"`, integer `0x09`), check `buddy/device/buddy_ui_cp.py` or the M5 keyboard module for the canonical key name and use that.

- [ ] **Step 4: Restart BLE with the persisted source on app start**

After `ble = buddy_ble.BuddyBLE(...)` construction in `main()`, immediately call:

```python
    # If the user previously set source_tab to CC, restore that on boot.
    if state.source_tab == "CC":
        ble.restart_with_prefix("ClaudeCC")
```

- [ ] **Step 5: Full-cycle on-device smoke**

Push the modified bundle and exercise Tab:

```bash
python3 .claude/skills/m5-onboard/scripts/install_apps.py --port COM5 --src buddy
```

Then on the device:
1. Launch `claude_buddy` from the menu.
2. Verify the CD layout shows (default).
3. Press Tab on the Cardputer keyboard.
4. Verify the CC layout appears (header says "CC", different color scheme, '--%' since no data yet).
5. Press Tab again.
6. Verify CD layout returns.

Document any rendering glitches in a follow-up file; do NOT fix them here unless they block usability — visual polish is a separate task.

- [ ] **Step 6: Commit**

```bash
git add buddy/device/apps/claude_buddy.py
git commit -m "Wire Tab key to source switch in claude_buddy

Tab toggles source_tab, persists it, resets the graph (samples from
the other source would lie under a different scale), restarts BLE
with the new name prefix so the matching host re-pairs, and repaints
the UI through the dispatcher. Restored on boot if the user last
left it on CC.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 16: Host package scaffolding

**Files:**
- Create: `buddy/host/__init__.py` (empty)
- Create: `buddy/host/requirements.txt`
- Create: `buddy/host/tests/__init__.py` (empty)
- Create: `buddy/host/README.md` (brief)

- [ ] **Step 1: Create the package**

```bash
mkdir -p buddy/host/tests
touch buddy/host/__init__.py buddy/host/tests/__init__.py
```

- [ ] **Step 2: Write requirements.txt**

Create `buddy/host/requirements.txt`:

```
bleak>=0.21
watchdog>=3.0
pytest>=7.0
```

- [ ] **Step 3: Write README**

Create `buddy/host/README.md`:

```markdown
# Buddy host tools

Two binaries:

- `claude_code_hook.py` — registered as Claude Code's `Stop` hook. Appends
  one JSON line per assistant turn to `~/.buddyd/events.jsonl`. Quick (~50 ms)
  and idempotent.

- `buddyd.py` — daemon. Watches `~/.buddyd/events.jsonl`, maintains per-day
  token + cache aggregates, and pushes a heartbeat over BLE to the Cardputer
  whenever the device advertises `ClaudeCC_*`. Stop with Ctrl-C or systemd
  unit teardown.

## Install

    pip install -r buddy/host/requirements.txt
    bash buddy/host/install_hook.sh   # writes the Stop-hook block into ~/.claude/settings.json
    python -m buddy.host.buddyd       # foreground; daemonize via systemd/launchctl if you want

## Status

Beta. Tested on macOS 14 and Ubuntu 24.04 WSL2 (Windows BLE not yet supported
because `bleak`'s WinRT backend has unrelated quirks — workaround: run buddyd
inside WSL with usbipd or on the Windows side via the upcoming
`buddyd_windows.py` once we add it).
```

- [ ] **Step 4: Commit**

```bash
git add buddy/host/__init__.py buddy/host/requirements.txt buddy/host/tests/__init__.py buddy/host/README.md
git commit -m "Scaffold buddy/host package

Empty package marker, requirements (bleak/watchdog/pytest), and a
README describing the two binaries. No logic yet — subsequent tasks
add the aggregator, hook, BLE client, and daemon main.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 17: Daily aggregator (pure logic)

**Files:**
- Create: `buddy/host/aggregator.py`
- Create: `buddy/host/tests/test_aggregator.py`

- [ ] **Step 1: Write the failing tests**

Create `buddy/host/tests/test_aggregator.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/gregh/projects/build-with-claude
python3 -m unittest buddy.host.tests.test_aggregator -v 2>&1 | head -15
```

Expected: `ModuleNotFoundError: No module named 'buddy.host.aggregator'`.

- [ ] **Step 3: Implement DailyAggregator**

Create `buddy/host/aggregator.py`:

```python
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
```

- [ ] **Step 4: Run tests**

```bash
python3 -m unittest buddy.host.tests.test_aggregator -v 2>&1 | tail -10
```

Expected: `OK` (5 tests).

- [ ] **Step 5: Commit**

```bash
git add buddy/host/aggregator.py buddy/host/tests/test_aggregator.py
git commit -m "Add DailyAggregator pure-logic class

Consumes Claude Code hook events, maintains per-local-date token +
cache aggregates, returns a device-shaped heartbeat dict per event.
Date rollover detection is per-event (no timer); midnight crossing
during an active session just resets on the next event after midnight.
Defensive against negative-uncached and missing-usage payloads.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 18: Claude Code Stop hook

**Files:**
- Create: `buddy/host/claude_code_hook.py`
- Create: `buddy/host/tests/test_hook.py`

- [ ] **Step 1: Write the failing test**

Create `buddy/host/tests/test_hook.py`:

```python
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
```

- [ ] **Step 2: Run to verify fail**

```bash
python3 -m unittest buddy.host.tests.test_hook -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'buddy.host.claude_code_hook'`.

- [ ] **Step 3: Implement the hook**

Create `buddy/host/claude_code_hook.py`:

```python
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
```

- [ ] **Step 4: Run tests**

```bash
python3 -m unittest buddy.host.tests.test_hook -v 2>&1 | tail -5
```

Expected: `OK` (4 tests).

- [ ] **Step 5: Make script executable + commit**

```bash
chmod +x buddy/host/claude_code_hook.py
git add buddy/host/claude_code_hook.py buddy/host/tests/test_hook.py
git commit -m "Add Stop-hook script for Claude Code

Reads hook payload from stdin, extracts the last assistant usage
block, appends one JSON line to ~/.buddyd/events.jsonl. Always
exits 0 — a hook that fails noisily would degrade the CC UX.
Filesystem errors are swallowed; loss of a single stats event is
preferable to surfacing tool-call latency to the user.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 19: BLE client (bleak-based central)

**Files:**
- Create: `buddy/host/ble_client.py`

- [ ] **Step 1: Implement the BLE client**

Create `buddy/host/ble_client.py`:

```python
"""bleak-based BLE central for the Cardputer.

Responsibilities:
- Continuously scan for an advertised name starting with `ClaudeCC_`.
- When seen, connect to the buddy GATT service.
- Maintain the connection while the device keeps advertising CC.
- Expose a single coroutine `push(heartbeat_dict)` that writes a JSON
  line to the writable characteristic.
- Reconnect on disconnect with exponential backoff (1s, 2s, 4s, cap 30s).

The exact UUIDs come from buddy_protocol.md / buddy_chars.py — see
the references/protocol.md doc.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from bleak import BleakClient, BleakScanner

# These match the characteristics buddy_ble.py exposes. Verified by
# running `bleak.discover()` against a flashed device.
BUDDY_SERVICE_UUID = "fe000000-0001-0001-0001-000000000001"  # placeholder; verify against actual service
BUDDY_WRITE_CHAR_UUID = "fe000000-0001-0001-0001-000000000002"  # placeholder; verify

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
                    # Hold the connection — the push() coroutine writes
                    # whenever it has data. We just stay alive until
                    # disconnect.
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
            await self._client.write_gatt_char(BUDDY_WRITE_CHAR_UUID, line, response=False)
            return True
        except Exception as e:
            _logger.warning("write failed: %s", e)
            return False
```

- [ ] **Step 2: Verify UUIDs against the device's GATT table**

```bash
grep -rn "UUID\|uuid" buddy/device/buddy_chars.py buddy/device/buddy_ble.py | head -20
```

Replace the placeholder UUIDs in `ble_client.py` with the actual values from `buddy_chars.py`. If the protocol uses 16-bit UUIDs (e.g., `0xFE00`), expand them to the canonical 128-bit form: `0000fe00-0000-1000-8000-00805f9b34fb`.

If you can't find a stable write characteristic in the existing code, add a smoke step that uses `BleakClient.services` to print every characteristic and pick by property (writable, notify-able). Add a comment in `ble_client.py` documenting which UUID you chose and why.

- [ ] **Step 3: Manual smoke**

With a flashed device on the CC tab (Tab once after starting the app):

```bash
python3 - <<'PY'
import asyncio, logging
from buddy.host.ble_client import CCBleClient

logging.basicConfig(level=logging.INFO)
client = CCBleClient()

async def main():
    runner = asyncio.create_task(client.run())
    await asyncio.sleep(8)  # let it find + connect
    ok = await client.push({"source": "CC", "daily_total": 12345, "cache_read": 999, "cache_create": 100, "cache_uncached": 50})
    print("push result:", ok)
    runner.cancel()

asyncio.run(main())
PY
```

Expected: `connected`, `push result: True`. The device's stats screen should show `12'345` in the subtitle.

- [ ] **Step 4: Commit**

```bash
git add buddy/host/ble_client.py
git commit -m "Add bleak-based BLE central for the CC tab

CCBleClient scans for ClaudeCC_* devices, connects, holds the
connection while the device advertises, reconnects with exponential
backoff. push() writes one JSON line per heartbeat. UUIDs documented
inline (resolved from buddy_chars.py — adjust here if device-side
UUIDs change).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 20: Daemon main loop

**Files:**
- Create: `buddy/host/buddyd.py`

- [ ] **Step 1: Implement the daemon**

Create `buddy/host/buddyd.py`:

```python
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
```

- [ ] **Step 2: Manual smoke (full round-trip)**

With the device on the CC tab, in one terminal:

```bash
python3 -m buddy.host.buddyd 2>&1
```

In another terminal, simulate a hook firing:

```bash
mkdir -p ~/.buddyd
echo '{"ts":"'$(date -u +"%Y-%m-%dT%H:%M:%S+00:00")'","usage":{"input_tokens":10000,"output_tokens":5000,"cache_creation_input_tokens":3000,"cache_read_input_tokens":2000},"msg":"manual smoke"}' >> ~/.buddyd/events.jsonl
```

Expected in the daemon log: `connected`, `consumed event` (or DEBUG-level write success). On the device CC tab: subtitle now shows `15'000 / 500'000 tok`, the `%` should be around 3%, and the cache split line shows `r:2.0K c:3.0K u:5.0K`.

- [ ] **Step 3: Commit**

```bash
git add buddy/host/buddyd.py
git commit -m "Add buddyd daemon main loop

Polling tail of ~/.buddyd/events.jsonl (2s interval — watchdog can be
added later if event rate justifies it). Each event flows through
DailyAggregator to a heartbeat dict, then through CCBleClient.push().
Heartbeats dropped when the device isn't connected are NOT queued —
the next event's cumulative aggregate already supersedes them.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 21: install_hook.sh installer

**Files:**
- Create: `buddy/host/install_hook.sh`

- [ ] **Step 1: Implement the installer**

Create `buddy/host/install_hook.sh`:

```bash
#!/usr/bin/env bash
# Install the Claude Code Stop-hook for buddyd.
#
# Idempotent: re-running won't duplicate the entry. Backs up settings.json
# before edit. Writes the hook block under .hooks.Stop pointing at the
# script in this repo.

set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")"/../.. && pwd)"
HOOK_SCRIPT="${REPO_DIR}/buddy/host/claude_code_hook.py"

if [ ! -x "${HOOK_SCRIPT}" ]; then
    chmod +x "${HOOK_SCRIPT}"
fi

SETTINGS="${HOME}/.claude/settings.json"
mkdir -p "$(dirname "${SETTINGS}")"

if [ ! -f "${SETTINGS}" ]; then
    echo "{}" > "${SETTINGS}"
fi

cp "${SETTINGS}" "${SETTINGS}.buddyd-backup-$(date +%s)"

python3 - <<PY
import json, sys
from pathlib import Path

path = Path("${SETTINGS}")
data = json.loads(path.read_text() or "{}")
hooks = data.setdefault("hooks", {})
stop = hooks.setdefault("Stop", [])

entry = {
    "type": "command",
    "command": "${HOOK_SCRIPT}",
    "name": "buddyd-stats-hook",
}

# Idempotent: replace existing entry with the same name, else append.
for i, h in enumerate(stop):
    if isinstance(h, dict) and h.get("name") == "buddyd-stats-hook":
        stop[i] = entry
        break
else:
    stop.append(entry)

path.write_text(json.dumps(data, indent=2))
print("Installed Stop hook at", path)
PY

echo ""
echo "Now start the daemon:"
echo "  python3 -m buddy.host.buddyd"
echo ""
echo "And verify on the Cardputer (Tab to CC tab) that stats arrive after each Claude Code turn."
```

- [ ] **Step 2: Make executable + test (dry on a temp HOME)**

```bash
chmod +x buddy/host/install_hook.sh
# Dry test on a temp HOME so we don't mess with the real settings.json.
tmphome=$(mktemp -d)
HOME="${tmphome}" bash buddy/host/install_hook.sh
cat "${tmphome}/.claude/settings.json"
```

Expected: the printed JSON contains a `hooks.Stop` array with one entry whose `name` is `"buddyd-stats-hook"`. Running it twice in a row should keep the array length at 1.

```bash
HOME="${tmphome}" bash buddy/host/install_hook.sh
python3 -c "import json; d=json.load(open('${tmphome}/.claude/settings.json')); print(len(d['hooks']['Stop']))"
rm -rf "${tmphome}"
```

Expected output: `1`.

- [ ] **Step 3: Commit**

```bash
git add buddy/host/install_hook.sh
git commit -m "Add idempotent install_hook.sh

Writes a Stop-hook entry into ~/.claude/settings.json pointing at
the in-repo claude_code_hook.py. Idempotent (re-runs replace the
existing entry by name rather than duplicating). Backs up the
existing settings file with a timestamp suffix before editing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 22: End-to-end smoke + docs update

**Files:**
- Modify: `buddy/README.md`

- [ ] **Step 1: Run full end-to-end on a live device**

Pre-conditions:
- Cardputer-Adv flashed with the new bundle (`python3 .claude/skills/m5-onboard/scripts/install_apps.py --port COM5 --src buddy`).
- Hook installed (`bash buddy/host/install_hook.sh`).
- Daemon running (`python3 -m buddy.host.buddyd`).
- A Claude Code session active in another terminal.

Sequence:
1. On the device, launch `claude_buddy` from the menu.
2. Press Tab to switch to CC. Verify `--%` shows initially.
3. Send a prompt to Claude in the other terminal. Wait for it to finish.
4. Within 2 s the device should show updated %, subtitle, and cache split.
5. Send 4 more prompts at varied sizes.
6. Verify the cumul graph populates after the first 15-min sample, OR — for quick smoke — temporarily set the sample interval to 30s by editing `apps/claude_buddy.py` (revert before commit).
7. Press Tab again to return to CD. Verify Claude Desktop's Hardware Buddy reconnects within 5 s and the original Buddy UI returns.

Document any discrepancies in `docs/superpowers/specs/2026-05-25-cardputer-session-stats-design.md` as a "known issues" appendix; do NOT silently leave them.

- [ ] **Step 2: Update README**

Append a section to `buddy/README.md`:

```markdown
## Claude Code stats integration

The device's `claude_buddy` app supports two sources, toggled by the
Tab key on the Cardputer keyboard:

- **CD** — the original Claude Desktop / Hardware Buddy integration.
- **CC** — Claude Code CLI sessions via the `buddyd` daemon.

Install the daemon + hook:

    pip install -r buddy/host/requirements.txt
    bash buddy/host/install_hook.sh
    python -m buddy.host.buddyd

See `buddy/host/README.md` for details. Set `daily_budget_tokens` via
REPL if you want a different default than 500K:

    import buddy_state; s = buddy_state.BuddyState(); s.set_daily_budget_tokens(2_000_000)
```

- [ ] **Step 3: Commit**

```bash
git add buddy/README.md
git commit -m "Document CC stats integration in buddy/README

Adds an install + Tab-key recipe. Points readers at buddy/host/README
for the daemon and hook details.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Final Validation

- [ ] All unit tests pass:
  ```bash
  cd /home/gregh/projects/build-with-claude
  python3 -m unittest discover buddy/device/tests -v 2>&1 | tail -10
  python3 -m unittest discover buddy/host/tests -v 2>&1 | tail -10
  ```
  Both should report `OK`.

- [ ] Device smoke tests pass against a connected device:
  ```bash
  for t in buddy/device/tests/test_smoke_*.py; do
    python3 buddy/scripts/repl_run.py --port COM5 --script "$t" 2>&1 | tail -3
  done
  ```
  Each test prints a `PASS test_smoke_*` line.

- [ ] End-to-end live test (Task 22 Step 1) completed successfully.

- [ ] No remaining `TODO` / placeholder comments in the new code:
  ```bash
  grep -rn "TODO\|FIXME\|XXX" buddy/host buddy/device/buddy_ui_stats.py 2>&1
  ```
  Expected: no matches (or only comments referencing a deliberate follow-up issue).

---

## Self-review notes (post-write check)

1. **Spec coverage** — Each spec section maps to tasks:
   - Architecture & switcher → Tasks 7, 15
   - Renderer split + dispatch → Tasks 8-14
   - Protocol extension → Tasks 5-6
   - Ring buffer + budget → Tasks 1-4
   - Hook + daemon + BLE client → Tasks 17-21
   - Tests (unit + smoke + e2e) → embedded per task + Task 22
   - Error-table cases → handled in Tasks 3 (budget=0), 6 (source mismatch), 17 (missing usage / negative uncached), 20 (FS errors, queue drop)

2. **Placeholder scan** — Two unavoidable placeholders flagged inline with the resolution step:
   - `BUDDY_SERVICE_UUID` / `BUDDY_WRITE_CHAR_UUID` in Task 19 are flagged as placeholder + Step 2 explicitly resolves them from `buddy_chars.py`. This is not a missing-content failure; it's "verify against the actual device GATT table" which can only be done at implementation time with a real device.
   - `apps/claude_buddy.py` keyboard polling location in Task 15 says "Find the keyboard polling block" — that's a code-location lookup, not a content gap.

3. **Type consistency** — `daily_budget_tokens` is used consistently as the field, getter, and NVS key. `source_tab` is consistently `"CD" | "CC"`. `set_heartbeat` is the canonical UI method (added in Task 6 if missing). `restart_with_prefix` is the canonical BLE switcher (Task 7). `_fmt_short` and `_looks_like_heartbeat` are module-private helpers — single source.

End of plan.
