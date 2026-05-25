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
