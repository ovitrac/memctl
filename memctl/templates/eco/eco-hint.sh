#!/bin/bash
# eco-hint.sh — Inject eco mode context into Claude Code (UserPromptSubmit)
#
# Hook event: UserPromptSubmit
# Toggle: touch/rm .memory/.eco-disabled
#
# 3-way branch:
#   1. Disabled  → empty context (zero overhead)
#   2. No DB     → bootstrap hint (suggests /scan)
#   3. DB exists → situational hint (item count + escalation ladder)
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

# Branch 3: DB exists — count items for scale-aware hint
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

cat <<JSON
{
  "additionalContext": "memctl eco mode is active — ${ITEM_COUNT} indexed items.\n\nFor codebase exploration (architecture, cross-file patterns, conventions), follow this order:\n1. memory_inspect — structural overview (file tree, sizes, observations)\n2. memory_recall or /recall <keywords> — FTS5 search, token-budgeted (use 2-3 identifiers or domain terms)\n3. Native Grep/Glob — only after recall returns 0 results despite query refinement\n4. Native Read — for editing or line-level precision on a specific known file\n\nEco wins over native tools when: exploring architecture across many files, searching cross-file patterns, querying binary formats (.docx/.pdf/.pptx), resuming prior session knowledge.\n\nWhen eco is ON, structure answers as: Retrieved (statements supported by recalled chunks, cite source paths) then Analysis (your reasoning, hypotheses, next steps).\n\nBypass eco for: editing files, reading a single known small file, git operations, targeted symbol lookup in a known file."
}
JSON
