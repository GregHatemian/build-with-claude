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
