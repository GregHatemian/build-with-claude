# Cardputer Session Stats — Design Spec

**Date**: 2026-05-25
**Status**: Design approved, awaiting implementation plan
**Scope**: Add a Claude Code (CC) data source and a richer stats view to the existing `claude_buddy` MicroPython app on the M5Stack Cardputer-Adv, while keeping the existing Claude Desktop (CD) integration intact.

## Goal

Let the user see, on the Cardputer LCD:

- The **daily token budget consumption** as a percentage (`tokens_today / daily_budget_tokens`).
- A **cumulative line graph** of `tokens_today` over the day, with a horizontal cap line at the configured budget.
- A **prompt cache token split** — `cache_read` / `cache_create` / `uncached` — when the active source can provide it (Claude Code only; Claude Desktop's Hardware Buddy heartbeat does not expose cache fields).
- Both Claude Desktop *and* Claude Code as data sources, with a **Tab key on the Cardputer keyboard switching the active source** (one connected at a time over BLE).

## Non-goals

- We do **not** modify Claude Desktop's Hardware Buddy code. Whatever it sends today is what we get on the CD tab.
- We do **not** track session cost in dollars. Tokens only — cost varies by model and the device shouldn't pretend to know pricing.
- We do **not** persist the cumulative graph across device reboots. The graph buffer is in-RAM; reboot wipes it. `daily_budget_tokens` *is* persisted (NVS).
- We do **not** build a configuration UI on-device beyond a single key for budget editing. Initial value is set via REPL or daemon push.

## Architecture overview

```
┌──────────────────────┐                       ┌────────────────────────┐
│ Claude Desktop       │                       │                        │
│ (existing Hardware   │── BLE (unchanged) ───▶│                        │
│  Buddy heartbeat)    │                       │                        │
└──────────────────────┘                       │   Cardputer-Adv        │
                                               │   claude_buddy app     │
┌──────────────────────┐                       │                        │
│ Claude Code hook     │                       │   "Tab" key toggles    │
│ (PostToolUse / Stop) │── writes JSON ──┐     │   active source CD/CC. │
└──────────────────────┘                 │     │                        │
                                         ▼     │   Two renderers:       │
                       ┌─────────────────────┐ │   - CD: current buddy  │
                       │ buddyd.py daemon    │ │     UI (unchanged)     │
                       │ (Python, host-side) │─┼─▶ BLE (new pairing) ──▶│
                       │ - reads JSON sink   │ │   - CC: stats UI       │
                       │ - holds BLE link    │ │     (new — %, graph,   │
                       │   to device on CC   │ │      cache split)      │
                       │   tab               │ │                        │
                       └─────────────────────┘ │                        │
                                               └────────────────────────┘
```

**Connection model.** Only one BLE central can be paired/connected to the device at a time. When the user presses Tab on the Cardputer, the device:

1. Disconnects the current central (CD or CC).
2. Switches its advertised name suffix between `Claude_<mac>` (CD-friendly) and `ClaudeCC_<mac>` (CC daemon scans for this specifically) so the right host re-connects automatically. The MAC suffix stays so paired bonds survive.
3. Updates the renderer to the chosen source's view.

The current source label is shown in the header so the user always knows which tab is active.

## Component breakdown

### Device side (MicroPython, lives in `buddy/device/`)

#### `claude_buddy.py` (modified)

Add a `source_tab` state field (`"CD"` or `"CC"`, default `"CD"` at app start). Bind the `Tab` key in the existing keyboard polling loop to call `_switch_source()`. On switch:

- Tear down current BLE advertise + disconnect any central.
- Re-init advertise with the appropriate `name_prefix` (`"Claude"` or `"ClaudeCC"`).
- Repaint UI with the chosen renderer.
- Reset the graph buffer (a switch is a fresh observation window — keeping mixed CD/CC samples would lie).

Existing permission-prompt logic (the orange box) only runs on the CD tab, since CC source doesn't emit permission prompts.

#### `buddy_ui_cp.py` (modified) and new `buddy_ui_stats.py`

`buddy_ui_cp.py` keeps its current logic for the CD renderer (`update_state` etc.). It exposes a small dispatcher that picks the renderer module based on `source_tab`.

`buddy_ui_stats.py` is new. It draws the CC layout at 240×135:

```
┌───────────────────────────────┐ y=0..17
│ ClaudeCC_2f1c        ● CC     │  source header, link dot
├───────────────────────────────┤
│  32%                          │ y=18..50  big % (size 4, cyan)
│  64'200 / 200'000 tok         │ y=52..62  small subtitle (gray)
│                               │
│  ╱──────────────── cap        │ y=66..96  cumul graph
│ /     graph here              │            cyan line, dashed cap
│/                              │
├───────────────────────────────┤
│ cache  r:1.2M c:180K u:320K   │ y=98..110  one line, three colors
├───────────────────────────────┤
│ Tab=CD                        │ y=120..134 hint strip
└───────────────────────────────┘
```

Color budget:
- `%` number: cyan if `<70%`, yellow if `70..90%`, red if `>=90%`.
- `r:` (cache read) green — money saved.
- `c:` (cache create) cyan — cost-bearing input.
- `u:` (uncached) gray — informational.

#### `buddy_state.py` (modified)

Add a ring buffer for the cumulative graph:

- 60 slots of `(timestamp_ms_int, cum_tokens_int)` pairs.
- Sample frequency: every 15 minutes (60 × 15 min = 15 h of horizon, comfortably covering a workday).
- On wrap: oldest sample overwritten.
- Resets to empty on Tab source switch (don't mix CD- and CC-derived points on the same line).

Add a `daily_budget_tokens` accessor that reads NVS key `daily_budget_tokens` (uint32, namespace `buddy`). Default if absent: `500_000`. A setter `set_daily_budget(n)` calls `nvs.set_u32` + `nvs.commit` (note the `set_u32` type — not `set_str` — and matches our existing `boot_option` pattern; the SKILL.md gotcha about `set_str` vs `set_blob` is about *value tag mismatch with UIFlow's reader*, and since `daily_budget_tokens` is our own key never read by UIFlow, `set_u32` is correct).

#### `buddy_protocol.py` (modified)

Extend the heartbeat shape to accept new optional fields:

- `cache_read` (int) — cumulative cache-read tokens for the day (local-time bucket). Null if source doesn't provide it (CD).
- `cache_create` (int) — cumulative cache-create tokens for the day. Null on CD.
- `cache_uncached` (int) — cumulative uncached input tokens for the day. Null on CD.
- `daily_total` (int) — authoritative cumulative tokens today as reported by the source. If absent, falls back to `tokens_today` (existing field) — this keeps CD backward-compatible.
- `source` (string `"CD"` or `"CC"`) — sanity check: the device rejects heartbeats whose `source` doesn't match its current `source_tab`. Defensive against the wrong daemon writing to the wrong tab.

Heartbeat detection still keys on absence of `cmd`/`ack` plus presence of any heartbeat-shape field (existing logic, unchanged).

### Host side (Python, new directory `buddy/host/`)

#### `buddy/host/claude_code_hook.py`

A Claude Code hook script registered in `~/.claude/settings.json` (or local `.claude/settings.json`) for the `Stop` event (fired when the assistant finishes a turn — this is where the cumulative `usage` block is final for the turn). A `PostToolUse` hook is *not* registered for v1: tool usage tokens are already included in the next `Stop`'s aggregate, so duplicating per-tool events would risk double-counting unless deduplicated, and the user has not asked for per-tool granularity.

The hook:

- Reads the hook payload from stdin (JSON).
- Extracts the `usage` block from the assistant's last response (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`).
- Appends a record to `~/.buddyd/events.jsonl` (one JSON object per line, atomic append).
- Exits 0 quickly (< 50 ms). The daemon reads from the JSONL file asynchronously.

We chose a JSONL file over a Unix socket because (a) hooks are short-lived and may run before the daemon starts; (b) the file gives a recoverable audit trail; (c) no socket-not-found error race.

#### `buddy/host/buddyd.py`

A small Python daemon. Started on-demand by a `launchctl` (macOS) / `systemd --user` (Linux) / NSSM-style (Windows) unit, or simply launched manually with `python -m buddy.host.buddyd`. Responsibilities:

- Tail `~/.buddyd/events.jsonl` (uses inotify on Linux, `kqueue` on macOS, `ReadDirectoryChangesW` on Windows — abstracted via `watchdog`).
- Maintain in-memory cumulative aggregates per day: `daily_total`, `cache_read`, `cache_create`, `cache_uncached`. Resets at local midnight.
- On each event, build a heartbeat dict matching the device's protocol shape and queue it.
- When the device advertises as `ClaudeCC_*` (i.e., CC tab is active), connect (using the same buddy-protocol GATT service the device exposes), write the queued heartbeat, stay connected as long as the device advertises CC.
- When the device disappears or switches away (advertise stops matching), drop the connection and wait.

The daemon does **not** poll Claude Code or Anthropic APIs — it only consumes what hooks deliver. This keeps it simple and side-effect-free.

#### `buddy/host/install_hook.sh`

One-shot installer that writes the hook configuration into `~/.claude/settings.json` (idempotent — checks for existing block) and prints the daemon launch command.

### Cross-cutting

- **Protocol versioning**: the heartbeat already implicitly uses field-presence detection. We add new fields without bumping a version — old CD heartbeats remain valid (just missing the new fields). The new `source` field's absence is treated as `"CD"` for backward compatibility with vanilla Hardware Buddy.

## Data flow

### CC tab active, Claude Code session in progress

```
PostToolUse hook fires
        │
        ▼
hook reads $CLAUDE_HOOK_PAYLOAD JSON
        │
        ▼
hook appends JSON line to ~/.buddyd/events.jsonl
        │
        ▼
buddyd file-watcher wakes
        │
        ▼
buddyd updates in-memory daily aggregates
        │
        ▼
buddyd builds heartbeat dict:
{
  "source": "CC",
  "daily_total": 64200,
  "cache_read": 1200000,
  "cache_create": 180000,
  "cache_uncached": 320000,
  "tokens_today": 64200,
  "tokens": <last call's total>,
  "msg": "tool: Bash"
}
        │
        ▼
buddyd writes JSON line over BLE GATT characteristic
        │
        ▼
device's buddy_protocol.parse_line() dispatches as heartbeat
        │
        ▼
buddy_state updates ring buffer + UI updates % + graph + cache line
```

### Tab key pressed (CD → CC)

```
keyboard poller in claude_buddy.py reads Tab
        │
        ▼
claude_buddy._switch_source() called
        │
        ▼
buddy_ble.disconnect_central() + buddy_ble.stop_advertise()
buddy_state.reset_graph()
source_tab = "CC"
        │
        ▼
buddy_ble.start_advertise(name_prefix="ClaudeCC")
        │
        ▼
renderer = buddy_ui_stats; full repaint
        │
        ▼
within ~2 sec, buddyd's scanner sees the new name, connects
        │
        ▼
first heartbeat arrives; UI starts populating
```

## Error handling

| Failure | Behavior |
|---|---|
| `~/.buddyd/events.jsonl` doesn't exist when daemon starts | Daemon creates it; no error. |
| Hook payload missing `usage` | Hook writes a record with `usage: null`; daemon ignores those for aggregation but logs them. No crash. |
| Daemon can't find the device advertising `ClaudeCC_*` | Daemon waits forever, logs `device not seen` every 30 s. No retry-storm. |
| Device receives malformed heartbeat | Existing protocol logger writes `unknown msg` and continues. No crash. |
| `daily_budget_tokens` is `0` in NVS (user error) | UI shows `--%` instead of dividing by zero. |
| Tab pressed while no central paired yet | Switch is instant; just re-advertises with the other prefix. No-op for the disconnect step. |
| Midnight crossing during a CC session | Daemon detects local date change, zeroes daily aggregates, sends a zeroed heartbeat. Device repaints with `0%`. The cumul graph keeps its in-RAM history (does NOT reset on midnight — the user might still want to see the past hours of the previous day until they reboot or Tab-switch). |

## Testing

### Unit tests (device side, MicroPython)

Run via `mpy_repl.paste` against a connected device with `--variant cardputer-adv` already flashed. Test plan:

1. `buddy_state.RingBuffer` — push 70 samples into a 60-slot buffer, assert oldest 10 are gone and order is preserved.
2. `buddy_protocol._parse_heartbeat` — feed a heartbeat with all new fields, assert they reach state; feed one missing `source`, assert it defaults to CD.
3. `buddy_state.set_daily_budget(0)` and read percentage — must return `None`, not a division error.
4. `claude_buddy._switch_source` — bind Tab, simulate keypress, assert `source_tab` toggled and graph buffer was reset.

### Integration test (host + device)

1. Start `buddyd.py` against the running device on the CC tab.
2. Synthesize a hook payload with a known `usage` shape, write to `~/.buddyd/events.jsonl`.
3. Read the device's UI state via REPL (`import claude_buddy; claude_buddy._debug_snapshot()`) and confirm `daily_total`, `cache_read`, etc. match what was sent.
4. Tab to CD, assert daemon disconnects and stops pushing.

### Manual smoke

- Run a real Claude Code session (this very conversation) with the hook installed. Watch the `%` rise on the device. Confirm the cumul graph populates after the first 15-min sample period.
- Trigger a permission prompt in Claude Desktop while on CD tab — confirm the orange permission box still works (no regression).

## Open questions, deliberately left for the writing-plans step

- Exact GATT characteristic UUIDs reused vs. new ones for the CC tab — the writing-plans step will pick.
- Whether to vendor `watchdog` into the daemon's deps or fall back to polling — depends on user's Python install footprint preference.
- Whether to add a fifth color band (`>=95%` flashing red) — visual polish, decide during implementation review.
- Whether to allow the user to **edit** `daily_budget_tokens` via a Cardputer keyboard combo (e.g., long-press `+` and `-`) or keep it daemon/REPL-only for v1.

## Risks

- **BLE re-pairing on Tab switch may not always succeed** if either host's BLE stack caches a stale GATT discovery. The daemon must do a fresh scan-and-connect, and Claude Desktop's Hardware Buddy is observed to handle reconnects gracefully (tested during the m5-onboard skill development).
- **`watchdog` (Python lib) has historical issues on WSL2** — file events from Windows-mounted paths sometimes don't fire. Mitigation: the daemon also polls the file size every 2 s as a fallback, so a missed inotify event delays a heartbeat by ≤ 2 s.
- **Cardputer NimBLE memory pressure** — adding a second advertise name + a 60-slot ring buffer + an extra renderer pushes RAM usage up. Profiled estimate: ~3 KB additional. Headroom should be fine but we'll check `gc.mem_free()` during the device-side smoke test.

## Out of scope (saved for follow-ups)

- Multiple-day history view (we keep ≤ 15 h of in-RAM samples).
- Per-tool token attribution (only aggregate session totals).
- Notifications when crossing 90% / 100% (could be added later as a beep + a toast).
- Anthropic API direct mode (no Claude Code involvement) — possible follow-up where `buddyd.py` reads from a separate JSONL the user appends to from any script.

---

End of design.
