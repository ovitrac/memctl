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
# is at memctl/templates/eco/eco-hint.sh (3-branch, config-aware).
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
  cat <<'JSON'
{
  "additionalContext": "memctl eco mode is active — indexed project.\n\nFor codebase exploration (architecture, cross-file patterns, conventions), follow this order:\n1. memory_inspect — structural overview (file tree, sizes, observations)\n2. memory_recall or /recall <keywords> — FTS5 search, token-budgeted (use 2-3 identifiers or domain terms)\n3. Native Grep/Glob — only after recall returns 0 results despite query refinement\n4. Native Read — for editing or line-level precision on a specific known file\n\nEco wins over native tools when: exploring architecture across many files, searching cross-file patterns, querying binary formats (.docx/.pdf/.pptx), resuming prior session knowledge.\n\nWhen eco is ON, structure answers as: Retrieved (statements supported by recalled chunks, cite source paths) then Analysis (your reasoning, hypotheses, next steps).\n\nBypass eco for: editing files, reading a single known small file, git operations, targeted symbol lookup in a known file."
}
JSON
fi
