#!/bin/bash
# eco-hint.sh — Inject eco mode context into Claude Code (UserPromptSubmit)
#
# Hook event: UserPromptSubmit
# Toggle: touch/rm .memory/.eco-disabled
#
# 4-way branch:
#   1. Disabled    → empty context (zero overhead)
#   2. No DB       → bootstrap hint (suggests /scan)
#   3. Cold start  → memory nearly empty (< 10 items), suggests memctl push
#   4. DB populated → situational hint (item count + escalation ladder)
#
# Author: Olivier Vitrac, PhD, HDR | Adservio Innovation Lab

# Resolve DB path from eco config (default: .memory/memory.db)
ECO_CONFIG=".claude/eco/config.json"
if [ -f "$ECO_CONFIG" ]; then
  DB_PATH=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['db_path'])" "$ECO_CONFIG" 2>/dev/null)
fi
DB_PATH="${DB_PATH:-.memory/memory.db}"

# Branch 1: eco disabled
if [ -f ".memory/.eco-disabled" ]; then
  echo '{"additionalContext": ""}'
  exit 0
fi

# Branch 2: no DB yet
if [ ! -f "$DB_PATH" ]; then
  cat <<'JSON'
{
  "additionalContext": "memctl eco mode is active but no memory database exists yet. Run /scan to index your project and bootstrap memory. Example: /scan ."
}
JSON
  exit 0
fi

# Count items for branch selection
ITEM_COUNT=$(python3 -c "
import sqlite3, sys
try:
    c = sqlite3.connect(sys.argv[1])
    n = c.execute('SELECT COUNT(*) FROM memory_items').fetchone()[0]
    print(n)
except Exception:
    print(0)
" "$DB_PATH" 2>/dev/null)
ITEM_COUNT="${ITEM_COUNT:-0}"

# Branch 3: Cold start — DB exists but nearly empty
if [ "$ITEM_COUNT" -lt 10 ]; then
  cat <<JSON
{
  "additionalContext": "memctl eco mode is active but memory is nearly empty (${ITEM_COUNT} items).\n\nCold start — ingest your codebase first:\n  memctl push 'project description' --source /path/to/codebase/\nor use the slash command:\n  /scan /path/to/codebase/\n\nThis indexes all files into memory. After ingestion, use /recall <keywords> to search.\n\nDo NOT explore with find, ls, Grep, or Glob before ingesting — memory tools are faster and persist across sessions."
}
JSON
  exit 0
fi

# Branch 4: DB populated — situational hint (item count + escalation ladder)
cat <<JSON
{
  "additionalContext": "memctl eco mode is active — ${ITEM_COUNT} indexed items.\n\nFor codebase exploration (architecture, cross-file patterns, conventions), follow this order:\n0. memctl push --source <path> — ingest first if exploring a new directory not yet indexed\n1. memory_inspect — structural overview (file tree, sizes, observations)\n2. memory_recall or /recall <keywords> — FTS5 search, token-budgeted (use 2-3 identifiers or domain terms)\n3. Native Grep/Glob — only after recall returns 0 results despite query refinement\n4. Native Read — for editing or line-level precision on a specific known file\n\nEco wins over native tools when: exploring architecture across many files, searching cross-file patterns, querying binary formats (.docx/.pdf/.pptx), resuming prior session knowledge.\n\nWhen eco is ON, structure answers as: Retrieved (statements supported by recalled chunks, cite source paths) then Analysis (your reasoning, hypotheses, next steps).\n\nBypass eco for: editing files, reading a single known small file, git operations, targeted symbol lookup in a known file."
}
JSON
