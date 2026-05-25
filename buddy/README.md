# buddy

MicroPython app bundle for the M5Stack Cardputer-Adv. Installed onto `/flash/` by the [`m5-onboard`](../onboard/) skill — see the [monorepo README](../README.md) for the end-to-end flow.

## What's on the device

```
/flash/
├── main.py              launcher menu (replaces UIFlow's boot flow)
├── buddy_*.py           shared libs (BLE, UI, state, protocol, chars)
├── burst_frames.py      sprite frames
└── apps/
    ├── claude_buddy.py  BLE client that pairs with Claude.app's Hardware Buddy
    ├── hello_cardputer.py
    └── snake.py
```

`main.py` scans `/flash/apps/` at boot and shows every `.py` as a menu entry. Drop a new file in there, re-run `m5-onboard go` (or `install_apps.py --src buddy`), and it shows up.

## Claude Buddy (BLE)

Open Claude → Developer menu → **Hardware Buddy** → Connect. BLE-only. Stats (approvals / denials / level) persist across reboots via NVS under the `buddy` namespace.

## Iterating on device code

`scripts/` has dev tooling for editing device sources without re-running the full onboard flow:

```bash
# Push a subset of files over USB-serial
python3 scripts/push.py --port /dev/cu.usbmodem1101 --files apps/snake.py

# Watch device logs
python3 scripts/tail_serial.py --port /dev/cu.usbmodem1101

# One-shot REPL exec
python3 scripts/repl_run.py --port /dev/cu.usbmodem1101 --script "import os; print(os.listdir('/flash'))"
```

`gen_burst_frames.py` regenerates `burst_frames.py` from source sprites.

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

## References

- `references/` — BLE protocol notes for the Claude Buddy app

## License

Apache 2.0 — see the [root LICENSE](../LICENSE) and [LICENSE-THIRD-PARTY.md](../LICENSE-THIRD-PARTY.md).
