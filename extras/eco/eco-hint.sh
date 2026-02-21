#!/bin/bash
# eco-hint.sh — Inject eco mode hint into Claude Code context
#
# Hook event: UserPromptSubmit
# Overhead: ~50 tokens per turn when enabled, 0 when disabled
# Toggle: touch/rm .claude/eco/.disabled (single syscall, no subprocess)
#
# Default: ON (no flag file = enabled)
# /eco off → creates .claude/eco/.disabled → hook injects empty context
# /eco on  → removes .claude/eco/.disabled → hook injects eco hint
#
# Author: Olivier Vitrac, PhD, HDR | Adservio Innovation Lab

if [ -f ".claude/eco/.disabled" ]; then
  echo '{"additionalContext": ""}'
else
  cat <<'JSON'
{
  "additionalContext": "memctl eco mode is active. You have 14 memory_* MCP tools for token-efficient file exploration, selective recall, and persistent memory. This is the PREFERRED mode for file operations. Before using native Read/View on large directories or binary files, prefer memory_inspect and memory_recall. For the full strategy, read .claude/eco/ECO.md"
}
JSON
fi
