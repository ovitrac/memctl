#!/usr/bin/env bash
# memctl_safety_guard.sh — PreToolUse hook for Claude Code
#
# Blocks dangerous shell commands before execution.
# Only inspects Bash tool calls; all other tools pass through.
#
# Exit codes:
#   0 — allowed (tool may proceed)
#   2 — blocked (tool execution prevented, stderr explains why)
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio

set -euo pipefail

# Read JSON from stdin
INPUT="$(cat)"

# Extract tool name
TOOL_NAME="$(printf '%s' "$INPUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('tool_name', ''))
" 2>/dev/null)" || exit 0

# Only inspect Bash tool calls
if [[ "$TOOL_NAME" != "Bash" ]]; then
    exit 0
fi

# Extract command input
COMMAND="$(printf '%s' "$INPUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
inp = d.get('tool_input', {})
print(inp.get('command', ''))
" 2>/dev/null)" || exit 0

# Denylist patterns — destructive commands
DENY_PATTERNS=(
    'rm -rf'
    'rm -r /'
    'rmdir --ignore'
    'dd if='
    'mkfs\.'
    'fdisk'
    'sudo '
    'su -'
    'curl.*|.*sh'
    'wget.*|.*sh'
    'curl.*|.*bash'
    'shutdown'
    'reboot'
    'init 0'
)

# Git-destructive patterns
GIT_DENY=(
    'git push --force'
    'git push -f '
    'git reset --hard'
    'git clean -fd'
    'git branch -D'
)

for pattern in "${DENY_PATTERNS[@]}" "${GIT_DENY[@]}"; do
    if printf '%s' "$COMMAND" | grep -qE "$pattern"; then
        printf '[memctl guard] BLOCKED: command matches deny pattern "%s"\n' "$pattern" >&2
        exit 2
    fi
done

exit 0
