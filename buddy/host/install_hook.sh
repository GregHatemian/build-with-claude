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
import json
from pathlib import Path

path = Path("${SETTINGS}")
data = json.loads(path.read_text() or "{}")
hooks = data.setdefault("hooks", {})
stop = hooks.setdefault("Stop", [])

cmd = "${HOOK_SCRIPT}"
hook_def = {"type": "command", "command": cmd}

# Claude Code schema: each Stop entry is a wrapper with matcher + hooks
# array. Idempotency key is the command path inside any wrapper's hooks.
for wrapper in stop:
    inner = wrapper.get("hooks", []) if isinstance(wrapper, dict) else []
    for i, h in enumerate(inner):
        if isinstance(h, dict) and h.get("command") == cmd:
            inner[i] = hook_def
            break
    else:
        continue
    break
else:
    stop.append({"matcher": "", "hooks": [hook_def]})

path.write_text(json.dumps(data, indent=2))
print("Installed Stop hook at", path)
PY

echo ""
echo "Now start the daemon:"
echo "  python3 -m buddy.host.buddyd"
echo ""
echo "And verify on the Cardputer (Tab to CC tab) that stats arrive after each Claude Code turn."
