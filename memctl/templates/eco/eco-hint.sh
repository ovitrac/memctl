#!/bin/bash
# eco-hint.sh — Inject eco mode hint into Claude Code context
#
# Hook event: UserPromptSubmit
# Toggle: touch/rm .claude/eco/.disabled
#
# 3-way branch:
#   1. Disabled  → empty context
#   2. No DB     → bootstrap hint (suggests /scan)
#   3. DB exists → concise hint (~30 tokens)
#
# Author: Olivier Vitrac, PhD, HDR | Adservio Innovation Lab

# Resolve DB path from eco config (default: .memory/memory.db)
ECO_CONFIG=".claude/eco/config.json"
if [ -f "$ECO_CONFIG" ]; then
  DB_PATH=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['db_path'])" "$ECO_CONFIG" 2>/dev/null)
fi
DB_PATH="${DB_PATH:-.memory/memory.db}"

if [ -f ".claude/eco/.disabled" ]; then
  echo '{"additionalContext": ""}'
elif [ ! -f "$DB_PATH" ]; then
  cat <<'JSON'
{
  "additionalContext": "memctl eco mode is active but no memory database exists yet. Use /scan to index your project and bootstrap memory. Example: /scan . — For the full strategy, read .claude/eco/ECO.md"
}
JSON
else
  cat <<'JSON'
{
  "additionalContext": "memctl eco mode is active. Use memory_inspect and memory_recall for token-efficient exploration. For the full strategy, read .claude/eco/ECO.md"
}
JSON
fi
