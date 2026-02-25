#!/bin/bash
# eco-hint.sh — Inject eco mode context into Claude Code (UserPromptSubmit)
#
# Hook event: UserPromptSubmit
# Overhead: ~80 tokens per turn when enabled, 0 when disabled
# Toggle: touch/rm .memory/.eco-disabled (single syscall, no subprocess)
#
# Default: ON (no flag file = enabled)
# /eco off → creates .memory/.eco-disabled → hook injects empty context
# /eco on  → removes .memory/.eco-disabled → hook injects eco hint
#
# This is the reference/documentation version. The installable template
# is at memctl/templates/eco/eco-hint.sh (4-branch, config-aware).
#
# Author: Olivier Vitrac, PhD, HDR | Adservio Innovation Lab

if [ -f ".memory/.eco-disabled" ]; then
  echo '{"additionalContext": ""}'
elif [ ! -f ".memory/memory.db" ]; then
  cat <<'JSON'
{
  "additionalContext": "memctl eco mode is active but no memory database exists yet. Run /scan to index your project and bootstrap memory. Example: /scan ."
}
JSON
else
  # Count items to detect cold-start
  ITEM_COUNT=$(python3 -c "
import sqlite3, sys
try:
    c = sqlite3.connect(sys.argv[1])
    n = c.execute('SELECT COUNT(*) FROM memory_items').fetchone()[0]
    print(n)
except Exception:
    print(0)
" ".memory/memory.db" 2>/dev/null)
  ITEM_COUNT="${ITEM_COUNT:-0}"
  if [ "$ITEM_COUNT" -lt 10 ]; then
    cat <<JSON
{
  "additionalContext": "memctl eco mode is active but memory is nearly empty (${ITEM_COUNT} items).\n\nCold start — ingest your codebase first:\n  memctl push 'project description' --source /path/to/codebase/\nor use the slash command:\n  /scan /path/to/codebase/\n\nThis indexes all files into memory. After ingestion, use /recall <keywords> to search.\n\nDo NOT explore with find, ls, Grep, or Glob before ingesting — memory tools are faster and persist across sessions."
}
JSON
  else
    cat <<JSON
{
  "additionalContext": "memctl eco mode is active — ${ITEM_COUNT} indexed items.\n\nFor codebase exploration (architecture, cross-file patterns, conventions), follow this order:\n0. memctl push --source <path> — ingest first if exploring a new directory not yet indexed\n1. memory_inspect — structural overview (file tree, sizes, observations)\n2. memory_recall or /recall <keywords> — FTS5 search, token-budgeted (use 2-3 identifiers or domain terms)\n3. Native Grep/Glob — only after recall returns 0 results despite query refinement\n4. Native Read — for editing or line-level precision on a specific known file\n\nEco wins over native tools when: exploring architecture across many files, searching cross-file patterns, querying binary formats (.docx/.pdf/.pptx), resuming prior session knowledge.\n\nWhen eco is ON, structure answers as: Retrieved (statements supported by recalled chunks, cite source paths) then Analysis (your reasoning, hypotheses, next steps).\n\nBypass eco for: editing files, reading a single known small file, git operations, targeted symbol lookup in a known file."
}
JSON
  fi
fi
