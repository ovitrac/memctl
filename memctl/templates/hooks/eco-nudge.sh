#!/usr/bin/env bash
# eco-nudge.sh — PreToolUse hook: contextual eco reminder for search tools
#
# Fires before Grep, Glob, and Bash(find) tool calls. Does NOT block — always exits 0.
# Injects a short stderr reminder when ALL of these conditions are true:
#
# Trigger conditions:
#   1. eco is ON (no .memory/.eco-disabled sentinel)
#   2. DB exists and is non-trivial (items >= 200)
#   3. Tool is Grep, Glob, or Bash containing a find command
#   4. The search looks like exploration (not a narrow single-file lookup):
#      - Grep: pattern length >= 6 OR pattern contains whitespace
#      - Grep: NOT targeting a single specific file (path does not end in a file ext)
#      - Glob: pattern contains ** or expands broadly
#      - Bash: command starts with or pipes through find/ls on broad paths
#
# Design:
#   - Never blocks (exit 0 always) — guidance, not enforcement
#   - Writes to stderr only — visible as hook feedback, not stdout
#   - Extracts search pattern for contextual /recall suggestion
#   - Silent on small projects, narrow lookups, and editing workflows
#   - One line output max — no spam, no shaming
#
# Exit codes:
#   0 — always (tool proceeds, with optional stderr guidance)
#
# Author: Olivier Vitrac, PhD, HDR | Adservio Innovation Lab

set -euo pipefail

# Read JSON from stdin
INPUT="$(cat)"

# Extract tool name and inputs in one python call (minimize subprocess overhead)
TOOL_INFO="$(printf '%s' "$INPUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
name = d.get('tool_name', '')
inp = d.get('tool_input', {})
# Grep/Glob fields
pattern = inp.get('pattern', '')
path = inp.get('path', '')
# Bash fields
command = inp.get('command', '')
print(f'{name}\t{pattern}\t{path}\t{command}')
" 2>/dev/null)" || exit 0

TOOL_NAME="${TOOL_INFO%%	*}"
REST="${TOOL_INFO#*	}"
PATTERN="${REST%%	*}"
REST="${REST#*	}"
PATH_ARG="${REST%%	*}"
BASH_CMD="${REST#*	}"

# Only inspect search-like tools — everything else passes through
case "$TOOL_NAME" in
    Grep|Glob) ;;
    Bash)
        # Only inspect Bash calls that look like find/ls exploration
        case "$BASH_CMD" in
            find\ *|*\|\ find\ *|*\|find\ *|ls\ -*R*|*"find ."*|*"find /"*)
                ;; # fall through to eco check
            *)
                exit 0 ;;
        esac
        ;;
    *) exit 0 ;;
esac

# Check eco state: sentinel absent = eco ON
if [ -f ".memory/.eco-disabled" ]; then
    exit 0
fi

# Resolve DB path from eco config
ECO_CONFIG=".claude/eco/config.json"
DB_PATH=".memory/memory.db"
if [ -f "$ECO_CONFIG" ]; then
    DB_PATH=$(python3 -c "
import json, sys
try:
    print(json.load(open(sys.argv[1]))['db_path'])
except Exception:
    print('.memory/memory.db')
" "$ECO_CONFIG" 2>/dev/null)
fi

# Check DB exists — if no DB, nudge toward ingestion
if [ ! -f "$DB_PATH" ]; then
    printf '[eco] No memory database. Ingest first: memctl push "description" --source /path/ or /scan /path/\n' >&2
    exit 0
fi

# Count indexed items
ITEM_COUNT=$(python3 -c "
import sqlite3, sys
try:
    c = sqlite3.connect(sys.argv[1])
    n = c.execute('SELECT COUNT(*) FROM memory_items').fetchone()[0]
    print(n)
except Exception:
    print(0)
" "$DB_PATH" 2>/dev/null)

# Cold start: memory exists but is nearly empty — strong nudge toward push
if [ "${ITEM_COUNT:-0}" -lt 10 ]; then
    printf '[eco] Memory nearly empty (%s items). Ingest first: memctl push "description" --source /path/ or /scan /path/\n' \
        "${ITEM_COUNT:-0}" >&2
    exit 0
fi

# Small stores (< 200 items): no nudge for search tools — not enough data to justify
if [ "${ITEM_COUNT:-0}" -lt 200 ]; then
    exit 0
fi

# ── Grep: check if this looks like exploration ──
if [ "$TOOL_NAME" = "Grep" ]; then
    # Skip narrow lookups: targeting a single specific file
    # (path ends with a file extension like .py, .java, .ts, .md, etc.)
    if printf '%s' "$PATH_ARG" | grep -qE '\.[a-zA-Z]{1,6}$' 2>/dev/null; then
        exit 0
    fi

    # Skip trivial patterns: very short single-token lookups
    PLEN=${#PATTERN}
    HAS_SPACE=false
    case "$PATTERN" in *" "*|*"	"*) HAS_SPACE=true ;; esac

    if [ "$PLEN" -lt 6 ] && [ "$HAS_SPACE" = "false" ]; then
        exit 0
    fi

    printf '[eco] %s indexed items. For cross-file search, try: /recall %s\n' \
        "$ITEM_COUNT" "$PATTERN" >&2
fi

# ── Glob: check if this is a broad traversal ──
if [ "$TOOL_NAME" = "Glob" ]; then
    # Only nudge for broad patterns (contains ** or wide wildcards)
    case "$PATTERN" in
        *"**"*|*"*.*"*|*"*/"*)
            printf '[eco] %s indexed items. For structural overview, try: memory_inspect\n' \
                "$ITEM_COUNT" >&2
            ;;
        # Narrow glob (e.g., "src/config/*.py") — no nudge
        *) ;;
    esac
fi

# ── Bash(find): intercept find/ls exploration ──
if [ "$TOOL_NAME" = "Bash" ]; then
    printf '[eco] %s indexed items. memory_inspect provides structural overview without find/ls. Try: memory_inspect or /recall <keywords>\n' \
        "$ITEM_COUNT" >&2
fi

# Never block — always allow the tool to proceed
exit 0
