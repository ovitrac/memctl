#!/usr/bin/env bash
# memctl_audit_logger.sh — PostToolUse hook for Claude Code
#
# Logs all tool actions to .agent_logs/memctl_commands.log.
# Always exits 0 (never blocks tool execution).
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio

set -euo pipefail

LOG_DIR=".agent_logs"
LOG_FILE="${LOG_DIR}/memctl_commands.log"

# Read JSON from stdin
INPUT="$(cat)"

# Extract tool name and input summary
ENTRY="$(printf '%s' "$INPUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
tool = d.get('tool_name', 'unknown')
inp = d.get('tool_input', {})
# Summarize input (first 120 chars of first string value)
summary = ''
if isinstance(inp, dict):
    for v in inp.values():
        if isinstance(v, str) and v.strip():
            summary = v[:120].replace('\n', ' ')
            break
print(f'{tool}: {summary}')
" 2>/dev/null)" || exit 0

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Append timestamped entry
printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$ENTRY" >> "$LOG_FILE"

exit 0
